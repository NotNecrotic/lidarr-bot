"""Cached snapshot of the Lidarr library and download queue, plus status lookup."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any

from .lidarr import LidarrClient

log = logging.getLogger(__name__)


class Status(Enum):
    AVAILABLE = "available"  # all tracks on disk
    DOWNLOADING = "downloading"  # in Lidarr's download queue
    WANTED = "wanted"  # monitored, not complete yet: already requested
    PARTIAL = "partial"  # some tracks on disk, not monitored
    ARTIST_KNOWN = "artist_known"  # artist in Lidarr, this release not monitored
    NEW = "new"  # nothing in Lidarr
    UNKNOWN = "unknown"  # library not loaded yet


LABELS = {
    Status.AVAILABLE: "Available",
    Status.DOWNLOADING: "Downloading",
    Status.WANTED: "Requested",
    Status.PARTIAL: "Partially in library",
    Status.ARTIST_KNOWN: "",
    Status.NEW: "",
    Status.UNKNOWN: "",
}


@dataclass(frozen=True, slots=True)
class StatusInfo:
    status: Status
    requestable: bool

    @property
    def icon(self) -> str:
        """✅ available, ⏳ requested (or downloading), ➕ can be requested."""
        if self.status is Status.AVAILABLE:
            return "✅"
        if self.status in (Status.WANTED, Status.DOWNLOADING):
            return "⏳"
        return "➕"

    @property
    def label(self) -> str:
        """Short status text; empty when there is nothing to say (requestable)."""
        return LABELS[self.status]


def _pct(stats: dict[str, Any]) -> float:
    try:
        return float(stats.get("percentOfTracks") or 0)
    except (TypeError, ValueError):
        return 0.0


class Library:
    def __init__(self, client: LidarrClient) -> None:
        self._client = client
        self.artists_by_mbid: dict[str, dict[str, Any]] = {}
        self.artists_by_id: dict[int, dict[str, Any]] = {}
        self.albums_by_mbid: dict[str, dict[str, Any]] = {}
        self.queued_album_ids: set[int] = set()
        self.loaded = False
        self.loaded_at = 0.0
        self._lock = asyncio.Lock()

    def load(
        self,
        artists: list[dict[str, Any]],
        albums: list[dict[str, Any]],
        queue: list[dict[str, Any]] | None = None,
    ) -> None:
        self.artists_by_mbid = {
            a["foreignArtistId"]: a for a in artists if a.get("foreignArtistId")
        }
        self.artists_by_id = {a["id"]: a for a in artists if "id" in a}
        self.albums_by_mbid = {a["foreignAlbumId"]: a for a in albums if a.get("foreignAlbumId")}
        if queue is not None:
            self.load_queue(queue)
        self.loaded = True
        self.loaded_at = time.time()

    def load_queue(self, queue: list[dict[str, Any]]) -> None:
        self.queued_album_ids = {q["albumId"] for q in queue if q.get("albumId")}

    async def refresh(self) -> None:
        async with self._lock:
            artists, albums, queue = await asyncio.gather(
                self._client.artists(), self._client.albums(), self._client.queue()
            )
            self.load(artists, albums, queue)
            log.info(
                "Library refreshed: %d artists, %d albums, %d queued",
                len(self.artists_by_mbid),
                len(self.albums_by_mbid),
                len(self.queued_album_ids),
            )

    async def refresh_queue(self) -> None:
        self.load_queue(await self._client.queue())

    # --- status --------------------------------------------------------------

    def album_status(self, album_mbid: str, artist_mbid: str = "") -> StatusInfo:
        if not self.loaded:
            return StatusInfo(Status.UNKNOWN, True)
        album = self.albums_by_mbid.get(album_mbid)
        if album is not None:
            stats = album.get("statistics") or {}
            files = int(stats.get("trackFileCount") or 0)
            if album.get("id") in self.queued_album_ids:
                return StatusInfo(Status.DOWNLOADING, False)
            if files > 0 and _pct(stats) >= 100:
                return StatusInfo(Status.AVAILABLE, False)
            if album.get("monitored"):
                return StatusInfo(Status.WANTED, False)
            if files > 0:
                return StatusInfo(Status.PARTIAL, True)
            return StatusInfo(Status.ARTIST_KNOWN, True)
        if artist_mbid and artist_mbid in self.artists_by_mbid:
            return StatusInfo(Status.ARTIST_KNOWN, True)
        return StatusInfo(Status.NEW, True)

    def artist_status(self, artist_mbid: str) -> StatusInfo:
        if not self.loaded:
            return StatusInfo(Status.UNKNOWN, True)
        artist = self.artists_by_mbid.get(artist_mbid)
        if artist is None:
            return StatusInfo(Status.NEW, True)
        artist_id = artist.get("id")
        if any(
            a.get("artistId") == artist_id and a.get("id") in self.queued_album_ids
            for a in self.albums_by_mbid.values()
        ):
            return StatusInfo(Status.DOWNLOADING, False)
        if int((artist.get("statistics") or {}).get("trackFileCount") or 0) > 0:
            return StatusInfo(Status.AVAILABLE, False)
        return StatusInfo(Status.WANTED, False)
