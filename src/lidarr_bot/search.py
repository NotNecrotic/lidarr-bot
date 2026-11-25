"""Search with a short TTL cache and in-flight de-duplication.

Autocomplete fires on nearly every keystroke and must answer Discord within 3 s.
Lookups run as shared tasks so a slow one that misses the deadline still finishes
and fills the cache for the next keystroke instead of being thrown away.
"""

from __future__ import annotations

import asyncio
import time
from typing import Literal

from .lidarr import LidarrClient
from .models import ArtistResult, ReleaseResult

Kind = Literal["artist", "album"]  # which Lidarr lookup to use
RequestType = Literal["artist", "album", "ep", "single"]  # what the user picked
Result = ArtistResult | ReleaseResult

REQUEST_TYPES: dict[str, str] = {
    "artist": "Artist",
    "album": "Album",
    "ep": "EP",
    "single": "Single",
}
_RELEASE_TYPE = {"album": "Album", "ep": "EP", "single": "Single"}


def source_for(request_type: str | None) -> Kind:
    if request_type == "artist":
        return "artist"
    return "album"  # album / ep / single / not chosen yet


def filter_results(request_type: str | None, results: list[Result]) -> list[Result]:
    """Narrow Lidarr album lookups to the release type the user picked."""
    wanted = _RELEASE_TYPE.get(request_type or "")
    if wanted is None:
        return results
    return [r for r in results if isinstance(r, ReleaseResult) and r.album_type == wanted]


class SearchService:
    def __init__(self, lidarr: LidarrClient, *, ttl: float = 600.0) -> None:
        self._lidarr = lidarr
        self._ttl = ttl
        self._cache: dict[tuple[str, str], tuple[float, list[Result]]] = {}
        self._inflight: dict[tuple[str, str], asyncio.Task[list[Result]]] = {}
        # Every result seen recently, by MBID, so an autocomplete pick can be resolved
        # without searching again.
        self.seen: dict[str, Result] = {}

    def _key(self, kind: Kind, query: str) -> tuple[str, str]:
        return kind, " ".join(query.lower().split())

    def cached(self, kind: Kind, query: str) -> list[Result] | None:
        hit = self._cache.get(self._key(kind, query))
        if hit and time.monotonic() - hit[0] < self._ttl:
            return hit[1]
        return None

    def task(self, kind: Kind, query: str) -> asyncio.Task[list[Result]]:
        key = self._key(kind, query)
        task = self._inflight.get(key)
        if task is None:
            task = asyncio.create_task(self._run(kind, query, key))
            # Mark the exception as retrieved: autocomplete may have stopped waiting.
            task.add_done_callback(lambda t: t.cancelled() or t.exception())
            self._inflight[key] = task
        return task

    async def search(self, kind: Kind, query: str) -> list[Result]:
        cached = self.cached(kind, query)
        if cached is not None:
            return cached
        return await asyncio.shield(self.task(kind, query))

    async def _run(self, kind: Kind, query: str, key: tuple[str, str]) -> list[Result]:
        try:
            results: list[Result]
            if kind == "artist":
                results = list(await self._lidarr.lookup_artists(query))
            else:
                results = list(await self._lidarr.lookup_albums(query))
            self._cache[key] = (time.monotonic(), results)
            self._prune()
            for r in results:
                self.seen[r.mbid] = r
            return results
        finally:
            self._inflight.pop(key, None)

    def _prune(self) -> None:
        if len(self._cache) > 500:
            now = time.monotonic()
            self._cache = {k: v for k, v in self._cache.items() if now - v[0] < self._ttl}
        if len(self.seen) > 5000:
            self.seen.clear()
