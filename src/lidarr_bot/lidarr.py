"""Thin async wrapper around the Lidarr v1 API."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from .models import ArtistResult, ReleaseResult, year_from_date

log = logging.getLogger(__name__)


class LidarrError(Exception):
    """Lidarr could not be reached or rejected a call. The message is safe to show users."""


def _error_message(response: httpx.Response) -> str:
    try:
        data = response.json()
    except ValueError:
        text = response.text.strip()[:200]
        return f"Lidarr returned HTTP {response.status_code}" + (f": {text}" if text else "")
    if isinstance(data, list):  # validation failures
        msgs = [str(item.get("errorMessage")) for item in data if isinstance(item, dict)]
        msgs = [m for m in msgs if m and m != "None"]
        if msgs:
            return "; ".join(msgs)
    if isinstance(data, dict):
        for key in ("message", "title", "error"):
            if data.get(key):
                return str(data[key])
    return f"Lidarr returned HTTP {response.status_code}"


def _image(images: list[dict[str, Any]] | None, *cover_types: str) -> str | None:
    images = images or []
    for wanted in cover_types:
        for img in images:
            if str(img.get("coverType", "")).lower() == wanted:
                url = img.get("remoteUrl") or img.get("url") or ""
                if url.startswith("http"):
                    return url
    return None


def parse_artist(data: dict[str, Any]) -> ArtistResult:
    return ArtistResult(
        mbid=data.get("foreignArtistId", ""),
        name=data.get("artistName", "Unknown artist"),
        disambiguation=data.get("disambiguation") or "",
        artist_type=data.get("artistType") or "",
        overview=data.get("overview") or "",
        genres=tuple(data.get("genres") or ()),
        image=_image(data.get("images"), "poster", "fanart", "banner"),
        raw=data,
    )


def parse_album(data: dict[str, Any]) -> ReleaseResult:
    artist = data.get("artist") or {}
    return ReleaseResult(
        mbid=data.get("foreignAlbumId", ""),
        title=data.get("title", "Unknown release"),
        artist_name=artist.get("artistName", "Unknown artist"),
        artist_mbid=artist.get("foreignArtistId", ""),
        album_type=data.get("albumType") or "Other",
        secondary_types=tuple(data.get("secondaryTypes") or ()),
        year=year_from_date(data.get("releaseDate")),
        disambiguation=data.get("disambiguation") or "",
        image=_image(data.get("images"), "cover", "disc") or _image(artist.get("images"), "poster"),
        raw=data,
    )


class LidarrClient:
    def __init__(self, base_url: str, api_key: str, *, timeout: float = 30.0) -> None:
        self._http = httpx.AsyncClient(
            base_url=base_url.rstrip("/") + "/api/v1",
            headers={"X-Api-Key": api_key, "Accept": "application/json"},
            timeout=timeout,
        )

    async def close(self) -> None:
        await self._http.aclose()

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            response = await self._http.request(method, path, **kwargs)
        except httpx.TimeoutException as exc:
            raise LidarrError("Lidarr took too long to respond") from exc
        except httpx.HTTPError as exc:
            raise LidarrError(f"Could not reach Lidarr ({type(exc).__name__})") from exc
        if response.status_code == 401:
            raise LidarrError("Lidarr rejected the API key")
        if response.is_error:
            message = _error_message(response)
            log.warning("Lidarr %s %s failed: %s", method, path, message)
            raise LidarrError(message)
        if not response.content:
            return None
        return response.json()

    # --- reads ---------------------------------------------------------------

    async def system_status(self) -> dict[str, Any]:
        return await self._request("GET", "/system/status")

    async def artists(self) -> list[dict[str, Any]]:
        return await self._request("GET", "/artist") or []

    async def albums(self) -> list[dict[str, Any]]:
        return await self._request("GET", "/album") or []

    async def queue(self) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        page = 1
        while True:
            data = await self._request(
                "GET",
                "/queue",
                params={"page": page, "pageSize": 500, "includeUnknownArtistItems": False},
            )
            batch = (data or {}).get("records") or []
            records.extend(batch)
            total = (data or {}).get("totalRecords") or 0
            if not batch or len(records) >= total:
                return records
            page += 1

    async def quality_profiles(self) -> list[dict[str, Any]]:
        return await self._request("GET", "/qualityprofile") or []

    async def metadata_profiles(self) -> list[dict[str, Any]]:
        return await self._request("GET", "/metadataprofile") or []

    async def root_folders(self) -> list[dict[str, Any]]:
        return await self._request("GET", "/rootfolder") or []

    async def lookup_artists(self, term: str) -> list[ArtistResult]:
        data = await self._request("GET", "/artist/lookup", params={"term": term}) or []
        return [parse_artist(d) for d in data if d.get("foreignArtistId")]

    async def lookup_albums(self, term: str) -> list[ReleaseResult]:
        data = await self._request("GET", "/album/lookup", params={"term": term}) or []
        return [parse_album(d) for d in data if d.get("foreignAlbumId")]

    async def artist_by_mbid(self, mbid: str) -> ArtistResult | None:
        results = await self.lookup_artists(f"lidarr:{mbid}")
        return next((r for r in results if r.mbid == mbid), results[0] if results else None)

    async def album_by_mbid(self, mbid: str) -> ReleaseResult | None:
        results = await self.lookup_albums(f"lidarr:{mbid}")
        return next((r for r in results if r.mbid == mbid), results[0] if results else None)

    # --- writes --------------------------------------------------------------

    async def add_artist(self, body: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", "/artist", json=body)

    async def update_artist(self, artist: dict[str, Any]) -> dict[str, Any]:
        return await self._request("PUT", f"/artist/{artist['id']}", json=artist)

    async def add_album(self, body: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", "/album", json=body)

    async def monitor_albums(self, album_ids: list[int], monitored: bool = True) -> None:
        await self._request(
            "PUT", "/album/monitor", json={"albumIds": album_ids, "monitored": monitored}
        )

    async def command(self, name: str, **body: Any) -> dict[str, Any]:
        return await self._request("POST", "/command", json={"name": name, **body})
