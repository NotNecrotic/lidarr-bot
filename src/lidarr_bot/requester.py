"""Turns a chosen artist or release into the right Lidarr calls."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from .config import Settings
from .library import Library
from .lidarr import LidarrClient, LidarrError
from .models import ArtistResult, ReleaseResult

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Defaults:
    quality_profile_id: int
    metadata_profile_id: int
    root_folder_path: str


@dataclass(frozen=True, slots=True)
class RequestOutcome:
    lidarr_id: int | None
    message: str


def _pick(items: list[dict[str, Any]], wanted: str, key: str, what: str) -> dict[str, Any] | None:
    """Find an item by numeric id or case-insensitive `key`. None if `wanted` is empty."""
    if not wanted:
        return None
    for item in items:
        if wanted.isdigit() and item.get("id") == int(wanted):
            return item
        if str(item.get(key, "")).rstrip("/\\").lower() == wanted.rstrip("/\\").lower():
            return item
    names = ", ".join(str(i.get(key)) for i in items) or "none"
    raise LidarrError(f"{what} '{wanted}' not found in Lidarr (available: {names})")


class Requester:
    def __init__(self, client: LidarrClient, library: Library, settings: Settings) -> None:
        self._client = client
        self._library = library
        self._settings = settings
        self._defaults: Defaults | None = None

    async def defaults(self) -> Defaults:
        if self._defaults is None:
            self._defaults = await self._resolve_defaults()
        return self._defaults

    async def _resolve_defaults(self) -> Defaults:
        s = self._settings
        roots = await self._client.root_folders()
        if not roots:
            raise LidarrError("Lidarr has no root folders configured")
        root = _pick(roots, s.lidarr_root_folder, "path", "Root folder") or roots[0]

        qualities = await self._client.quality_profiles()
        quality = _pick(qualities, s.lidarr_quality_profile, "name", "Quality profile")
        quality_id = (quality or {}).get("id") or root.get("defaultQualityProfileId")
        if not quality_id:
            if not qualities:
                raise LidarrError("Lidarr has no quality profiles")
            quality_id = qualities[0]["id"]

        metas = await self._client.metadata_profiles()
        meta = _pick(metas, s.lidarr_metadata_profile, "name", "Metadata profile")
        meta_id = (meta or {}).get("id") or root.get("defaultMetadataProfileId")
        if not meta_id:
            if not metas:
                raise LidarrError("Lidarr has no metadata profiles")
            meta_id = metas[0]["id"]

        defaults = Defaults(int(quality_id), int(meta_id), str(root["path"]))
        log.info("Using Lidarr defaults: %s", defaults)
        return defaults

    async def _artist_fields(
        self, *, monitor: str, search: bool, monitor_new: str
    ) -> dict[str, Any]:
        d = await self.defaults()
        return {
            "qualityProfileId": d.quality_profile_id,
            "metadataProfileId": d.metadata_profile_id,
            "rootFolderPath": d.root_folder_path,
            "monitored": True,
            "monitorNewItems": monitor_new,
            "addOptions": {"monitor": monitor, "searchForMissingAlbums": search},
        }

    # --- artist --------------------------------------------------------------

    def build_artist_body(self, artist: ArtistResult, fields: dict[str, Any]) -> dict[str, Any]:
        if not artist.raw:
            raise LidarrError("Missing Lidarr data for this artist; search again")
        body = dict(artist.raw)
        body.pop("id", None)
        body.update(fields)
        return body

    async def request_artist(self, artist: ArtistResult) -> RequestOutcome:
        if artist.mbid in self._library.artists_by_mbid:
            raise LidarrError(f"{artist.name} is already in the library")
        fields = await self._artist_fields(
            monitor=self._settings.artist_monitor, search=True, monitor_new="all"
        )
        created = await self._client.add_artist(self.build_artist_body(artist, fields))
        await self._safe_refresh()
        return RequestOutcome(
            (created or {}).get("id"),
            f"Added **{artist.name}** to Lidarr and started searching for their releases.",
        )

    # --- release -------------------------------------------------------------

    async def request_release(self, release: ReleaseResult) -> RequestOutcome:
        existing = self._library.albums_by_mbid.get(release.mbid)
        if existing is not None:
            album_id = await self._monitor_existing(existing)
        else:
            body = await self.build_new_album_body(release)
            created = await self._client.add_album(body)
            album_id = (created or {}).get("id")
        await self._safe_refresh()
        return RequestOutcome(
            album_id,
            f"Requested **{release.title}** by **{release.artist_name}**. "
            "Lidarr is searching for it now.",
        )

    async def _monitor_existing(self, album: dict[str, Any]) -> int:
        album_id = int(album["id"])
        artist = self._library.artists_by_id.get(album.get("artistId"))
        if artist is not None and not artist.get("monitored"):
            await self._client.update_artist({**artist, "monitored": True})
        await self._client.monitor_albums([album_id], True)
        await self._client.command("AlbumSearch", albumIds=[album_id])
        return album_id

    async def build_new_album_body(self, release: ReleaseResult) -> dict[str, Any]:
        if not release.raw:
            fetched = await self._client.album_by_mbid(release.mbid)
            if fetched is None or not fetched.raw:
                raise LidarrError("Lidarr's metadata server doesn't know this release yet")
            release = fetched
        body = dict(release.raw)
        body.pop("id", None)
        body.update(
            {"monitored": True, "anyReleaseOk": True, "addOptions": {"searchForNewAlbum": True}}
        )
        lookup_artist = dict(body.get("artist") or {})
        existing_artist = self._library.artists_by_mbid.get(
            lookup_artist.get("foreignArtistId", "")
        )
        if existing_artist is not None:
            body["artist"] = existing_artist
            body["artistId"] = existing_artist["id"]
        else:
            # New artist: add it, but only monitor the release that was asked for.
            lookup_artist.pop("id", None)
            lookup_artist.update(
                await self._artist_fields(monitor="none", search=False, monitor_new="none")
            )
            body["artist"] = lookup_artist
        return body

    async def _safe_refresh(self) -> None:
        try:
            await self._library.refresh()
        except LidarrError:
            log.warning("Library refresh after request failed", exc_info=True)
