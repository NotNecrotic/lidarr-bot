"""The request action and the one Discord component the bot uses: a result picker."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord

from . import formatting as fmt
from .access import denial_reason
from .library import StatusInfo
from .lidarr import LidarrError
from .models import ArtistResult
from .search import Result

if TYPE_CHECKING:
    from .services import Services

log = logging.getLogger(__name__)

PICKER_TIMEOUT = 300


def status_for(services: Services, item: Result) -> StatusInfo:
    if isinstance(item, ArtistResult):
        return services.library.artist_status(item.mbid)
    return services.library.album_status(item.mbid, item.artist_mbid)


async def submit(
    interaction: discord.Interaction,
    services: Services,
    item: Result,
    *,
    request_type: str,
    query: str,
) -> discord.Embed | str:
    """Send the request to Lidarr and log it.

    Returns the confirmation embed, or a message explaining why nothing was requested.
    """
    status = status_for(services, item)
    if not status.requestable:
        return fmt.not_requestable(item, status)

    is_artist = isinstance(item, ArtistResult)
    record = dict(
        user_id=interaction.user.id,
        user_name=str(interaction.user),
        guild_id=interaction.guild_id,
        channel_id=interaction.channel_id,
        command=request_type,
        search_query=query,
        kind="artist" if is_artist else "release",
        mbid=item.mbid,
        title=item.name if is_artist else item.title,
        artist_name=item.name if is_artist else item.artist_name,
        release_type=None if is_artist else fmt.type_name(item),
    )
    try:
        if is_artist:
            outcome = await services.requester.request_artist(item)
        else:
            outcome = await services.requester.request_release(item)
    except LidarrError as exc:
        await services.log.add(**record, outcome="failed", error=str(exc))
        return f"Lidarr couldn't add **{fmt.heading(item)}**: {fmt.truncate(str(exc), 300)}"

    await services.log.add(**record, lidarr_id=outcome.lidarr_id, outcome="requested")
    log.info("%s (%s) requested %s %s", record["user_name"], record["user_id"],
             record["kind"], record["mbid"])  # fmt: skip
    return fmt.request_embed(item)


class ResultPicker(discord.ui.View):
    """Shown when someone submits text instead of picking an autocomplete suggestion.

    Choosing an option requests it straight away.
    """

    def __init__(
        self,
        services: Services,
        owner_id: int,
        request_type: str,
        query: str,
        results: list[Result],
    ) -> None:
        super().__init__(timeout=PICKER_TIMEOUT)
        self.services = services
        self.owner_id = owner_id
        self.request_type = request_type
        self.query = query
        self.results = results[:25]
        self.last_interaction: discord.Interaction | None = None

        select = discord.ui.Select(
            placeholder="Choose what to request",
            options=[
                discord.SelectOption(
                    label=fmt.option_label(item),
                    description=fmt.option_description(item),
                    emoji=status_for(services, item).icon,
                    value=str(i),
                )
                for i, item in enumerate(self.results)
            ],
        )
        select.callback = self._picked
        self.select = select
        self.add_item(select)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Use `/request` to search.", ephemeral=True)
            return False
        if reason := denial_reason(interaction, self.services.settings):
            await interaction.response.send_message(reason, ephemeral=True)
            return False
        self.last_interaction = interaction
        return True

    async def _picked(self, interaction: discord.Interaction) -> None:
        item = self.results[int(self.select.values[0])]
        status = status_for(self.services, item)
        if not status.requestable:
            # Stay on the picker so they can choose something else.
            await interaction.response.edit_message(
                content=fmt.not_requestable(item, status), view=self
            )
            return

        await interaction.response.edit_message(
            content=f"Requesting **{fmt.heading(item)}**…", view=None
        )
        result = await submit(
            interaction, self.services, item, request_type=self.request_type, query=self.query
        )
        self.stop()
        if isinstance(result, discord.Embed):
            await interaction.edit_original_response(content=None, embed=result, view=None)
        else:
            await interaction.edit_original_response(content=result, view=None)

    async def on_timeout(self) -> None:
        if self.last_interaction is None:
            return
        try:
            await self.last_interaction.edit_original_response(
                content="Search expired. Run `/request` again.", view=None
            )
        except discord.HTTPException:
            pass

    async def on_error(
        self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item
    ) -> None:
        log.exception("Picker error", exc_info=error)
        text = "Something went wrong. Try `/request` again."
        if interaction.response.is_done():
            await interaction.followup.send(text, ephemeral=True)
        else:
            await interaction.response.send_message(text, ephemeral=True)
