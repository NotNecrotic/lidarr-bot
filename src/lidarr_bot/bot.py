"""Bot setup: access-checked command tree, background refresh loops, guild sync."""

from __future__ import annotations

import asyncio
import logging

import discord
from discord import app_commands
from discord.ext import commands

from .access import denial_reason
from .config import Settings
from .lidarr import LidarrError
from .notifier import Notifier
from .services import Services

log = logging.getLogger(__name__)


class GuardedTree(app_commands.CommandTree):
    """Every slash command (and autocomplete) needs the request role."""

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        bot: LidarrBot = self.client  # type: ignore[assignment]
        reason = denial_reason(interaction, bot.services.settings)
        if reason is None:
            return True
        if interaction.type is discord.InteractionType.autocomplete:
            await interaction.response.autocomplete([])
        else:
            await interaction.response.send_message(reason, ephemeral=True)
        return False

    async def on_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        log.exception("Command error", exc_info=error)
        text = "Something went wrong running that command."
        try:
            if interaction.response.is_done():
                await interaction.followup.send(text, ephemeral=True)
            else:
                await interaction.response.send_message(text, ephemeral=True)
        except discord.HTTPException:
            pass


class LidarrBot(commands.Bot):
    def __init__(self, settings: Settings) -> None:
        intents = discord.Intents.none()
        intents.guilds = True  # interactions carry member roles; no privileged intents needed
        super().__init__(
            command_prefix=commands.when_mentioned,
            intents=intents,
            tree_cls=GuardedTree,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        self.services = Services.create(settings)
        self.notifier = Notifier(self, self.services)
        self._loops: list[asyncio.Task[None]] = []

    async def setup_hook(self) -> None:
        from .cog import RequestCog

        await self.services.log.open()
        try:
            await self.services.library.refresh()
        except LidarrError as exc:
            log.error("Could not load the Lidarr library at startup: %s (will retry)", exc)

        guild = discord.Object(id=self.services.settings.discord_guild_id)
        await self.add_cog(RequestCog(self), guilds=[guild])
        synced = await self.tree.sync(guild=guild)
        log.info("Synced %d commands to guild %s", len(synced), guild.id)

        s = self.services.settings
        self._loops = [
            asyncio.create_task(self._every(s.library_refresh_seconds, self._refresh_library)),
            asyncio.create_task(self._every(s.queue_refresh_seconds, self._refresh_queue)),
        ]

    async def _refresh_library(self) -> None:
        await self.services.library.refresh()
        await self.notifier.check()

    async def _refresh_queue(self) -> None:
        library = self.services.library
        before = set(library.queued_album_ids)
        await library.refresh_queue()
        if before - library.queued_album_ids:
            # Something left the download queue: it may have just been imported.
            await self._refresh_library()

    async def _every(self, seconds: int, func) -> None:
        while True:
            await asyncio.sleep(seconds)
            try:
                await func()
            except LidarrError as exc:
                log.warning("Background refresh failed: %s", exc)
            except Exception:
                log.exception("Background refresh crashed")

    async def on_ready(self) -> None:
        log.info("Logged in as %s (%s)", self.user, getattr(self.user, "id", "?"))

    async def close(self) -> None:
        for task in self._loops:
            task.cancel()
        await super().close()
        await self.services.close()
