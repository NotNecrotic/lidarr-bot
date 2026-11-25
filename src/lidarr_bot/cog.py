"""Slash commands: /request and /myrequests."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from . import formatting as fmt
from .access import denial_reason
from .db import RequestRecord
from .lidarr import LidarrError
from .models import ArtistResult
from .search import REQUEST_TYPES, Result, filter_results, source_for
from .ui import ResultPicker, status_for, submit

if TYPE_CHECKING:
    from .bot import LidarrBot

log = logging.getLogger(__name__)

AUTOCOMPLETE_DEBOUNCE = 0.35
AUTOCOMPLETE_BUDGET = 2.2  # Discord drops autocomplete answers after 3 s

TYPE_CHOICES = [
    app_commands.Choice(name=label, value=value) for value, label in REQUEST_TYPES.items()
]


def pick_value(item: Result) -> str:
    """Autocomplete value for a suggestion: 'artist:<mbid>' or 'release:<mbid>'."""
    return f"{'artist' if isinstance(item, ArtistResult) else 'release'}:{item.mbid}"


def parse_pick(value: str) -> tuple[str, str] | None:
    kind, sep, mbid = value.partition(":")
    if sep and kind in {"artist", "release"} and len(mbid) == 36 and mbid.count("-") == 4:
        return kind, mbid
    return None


def selected_type(interaction: discord.Interaction) -> str | None:
    """The `type` option, read while the user is still typing `query`."""
    value = getattr(interaction.namespace, "type", None)
    if isinstance(value, app_commands.Choice):
        value = value.value
    return value if value in REQUEST_TYPES else None


class RequestCog(commands.Cog):
    def __init__(self, bot: LidarrBot) -> None:
        self.bot = bot
        self.services = bot.services
        self._latest_query: dict[int, tuple[str | None, str]] = {}

    # --- /request ------------------------------------------------------------

    @app_commands.command(name="request", description="Request music on Lidarr")
    @app_commands.describe(
        type="What you want: an artist, an album, an EP or a single",
        query="Start typing, then pick a suggestion",
    )
    @app_commands.choices(type=TYPE_CHOICES)
    async def request(
        self, interaction: discord.Interaction, type: app_commands.Choice[str], query: str
    ) -> None:
        request_type = type.value
        query = query.strip()
        if pick := parse_pick(query):
            await self._request_pick(interaction, request_type, *pick)
        else:
            await self._show_picker(interaction, request_type, query)

    @request.autocomplete("query")
    async def _query_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        if denial_reason(interaction, self.services.settings):
            return []
        query = current.strip()
        if len(query) < 2 or parse_pick(query):
            return []
        request_type = selected_type(interaction)
        source = source_for(request_type)
        search = self.services.search
        results = search.cached(source, query)
        if results is None:
            user = interaction.user.id
            self._latest_query[user] = (request_type, query)
            await asyncio.sleep(AUTOCOMPLETE_DEBOUNCE)
            if self._latest_query.get(user) != (request_type, query):
                return []  # they kept typing; the newer keystroke will answer
            try:
                results = await asyncio.wait_for(
                    asyncio.shield(search.task(source, query)), AUTOCOMPLETE_BUDGET
                )
            except (TimeoutError, LidarrError):
                return []
        return [
            app_commands.Choice(
                name=fmt.choice_name(item, status_for(self.services, item)),
                value=pick_value(item),
            )
            for item in filter_results(request_type, results)[:25]
        ]

    async def _request_pick(
        self, interaction: discord.Interaction, request_type: str, kind: str, mbid: str
    ) -> None:
        """They chose an autocomplete suggestion: request it right away."""
        services = self.services
        item = services.search.seen.get(mbid)
        if item is not None:
            status = status_for(services, item)
            if not status.requestable:
                await interaction.response.send_message(
                    fmt.not_requestable(item, status), ephemeral=True
                )
                return

        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            if item is None:
                lidarr = services.lidarr
                lookup = lidarr.artist_by_mbid if kind == "artist" else lidarr.album_by_mbid
                item = await lookup(mbid)
            if item is None:
                result: discord.Embed | str = "That suggestion expired. Search again."
            else:
                result = await submit(
                    interaction, services, item, request_type=request_type, query=mbid
                )
        except LidarrError as exc:
            result = f"Lidarr problem: {exc}"

        if isinstance(result, discord.Embed):
            await interaction.edit_original_response(embed=result)
        else:
            await interaction.edit_original_response(content=result)

    async def _show_picker(
        self, interaction: discord.Interaction, request_type: str, query: str
    ) -> None:
        """They submitted plain text: show every match in a select menu."""
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            results = await self.services.search.search(source_for(request_type), query)
        except LidarrError as exc:
            await interaction.edit_original_response(content=f"Search failed: {exc}")
            return
        results = filter_results(request_type, results)
        noun = REQUEST_TYPES[request_type].lower()
        if not results:
            await interaction.edit_original_response(
                content=f"No {noun} results for “{fmt.truncate(query, 100)}”. "
                "Try a different spelling or add the artist name."
            )
            return
        view = ResultPicker(self.services, interaction.user.id, request_type, query, results)
        view.last_interaction = interaction
        shown = len(view.results)
        await interaction.edit_original_response(
            content=f"{shown} {noun} result{'s' if shown != 1 else ''} for "
            f"“{fmt.truncate(query, 100)}”",
            view=view,
        )

    # --- /myrequests ---------------------------------------------------------

    @app_commands.command(name="myrequests", description="Your recent requests and their status")
    async def myrequests(self, interaction: discord.Interaction) -> None:
        records = await self.services.log.recent(user_id=interaction.user.id, limit=10)
        if not records:
            await interaction.response.send_message(
                "You haven't requested anything yet. Try `/request`.", ephemeral=True
            )
            return
        embed = discord.Embed(
            title="Your requests",
            description="\n".join(self._line(r) for r in records),
            colour=fmt.REQUESTED,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    def _line(self, rec: RequestRecord) -> str:
        library = self.services.library
        if rec.outcome == "failed":
            icon = "⚠️"
        elif rec.kind == "artist":
            icon = library.artist_status(rec.mbid).icon
        else:
            icon = library.album_status(rec.mbid).icon
        title = f"**{fmt.truncate(rec.title, 60)}**"
        what = (
            f"{title} · Artist"
            if rec.kind == "artist"
            else f"{title} · {rec.release_type or 'Release'} by {fmt.truncate(rec.artist_name, 40)}"
        )
        return f"{icon} {what} · <t:{int(rec.created_at.timestamp())}:R>"
