"""Announce requests once Lidarr has finished downloading them.

Runs after every library refresh. Each logged request is checked until its
artist/release shows up as available, then it's marked so it is only announced once.
With NOTIFY_AVAILABLE off, requests are still marked (silently) so turning it on
later doesn't flood the channel with old downloads.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import TYPE_CHECKING

import discord

from . import formatting as fmt
from .db import RequestRecord
from .library import Status
from .lidarr import parse_album, parse_artist
from .models import ArtistResult, ReleaseResult

if TYPE_CHECKING:
    from .services import Services

log = logging.getLogger(__name__)


class Notifier:
    def __init__(self, client: discord.Client, services: Services) -> None:
        self._client = client
        self._services = services

    def finished(self, rec: RequestRecord) -> tuple[ArtistResult | ReleaseResult, str] | None:
        """If this request's item is now available, return it with its overview text."""
        lib = self._services.library
        if rec.kind == "artist":
            if lib.artist_status(rec.mbid).status is not Status.AVAILABLE:
                return None
            artist = parse_artist(lib.artists_by_mbid[rec.mbid])
            return artist, artist.overview
        if lib.album_status(rec.mbid).status is not Status.AVAILABLE:
            return None
        album = dict(lib.albums_by_mbid[rec.mbid])
        artist = lib.artists_by_id.get(album.get("artistId"))
        if artist and not album.get("artist"):
            album["artist"] = artist
        release = parse_album(album)
        if release.artist_name == "Unknown artist":
            release.artist_name = rec.artist_name
        return release, album.get("overview") or ""

    async def check(self) -> None:
        services = self._services
        pending = await services.log.pending()
        if not pending:
            return
        by_item: dict[str, list[RequestRecord]] = defaultdict(list)
        for rec in pending:
            by_item[rec.mbid].append(rec)

        for records in by_item.values():
            found = self.finished(records[0])
            if found is None:
                continue
            item, overview = found
            if services.settings.notify_available:
                requesters = list(dict.fromkeys(r.user_id for r in records))
                if not await self._post(fmt.available_embed(item, requesters, overview)):
                    continue  # try again after the next refresh
            await services.log.mark_available([r.id for r in records])
            log.info("Request available: %s (%d requests)", fmt.heading(item), len(records))

    async def _post(self, embed: discord.Embed) -> bool:
        channel_id = self._services.settings.notify_channel_id
        if channel_id is None:
            return False
        channel = self._client.get_channel(channel_id)
        try:
            if channel is None:
                channel = await self._client.fetch_channel(channel_id)
            await channel.send(embed=embed)  # type: ignore[union-attr]
        except (discord.HTTPException, AttributeError):
            log.warning("Could not post to notify channel %s", channel_id, exc_info=True)
            return False
        return True
