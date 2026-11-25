"""Who may use the bot, and where."""

from __future__ import annotations

from collections.abc import Iterable

import discord

from .config import Settings


def has_any_role(member_role_ids: Iterable[int], allowed: Iterable[int]) -> bool:
    return not set(member_role_ids).isdisjoint(allowed)


def denial_reason(interaction: discord.Interaction, settings: Settings) -> str | None:
    """None if the user may use the bot here, else a message explaining why not."""
    if interaction.guild_id != settings.discord_guild_id:
        return "This bot only works in its home server."
    member = interaction.user
    if not isinstance(member, discord.Member):
        return "This bot only works inside the server."

    channels = settings.allowed_channels
    if channels:
        channel = interaction.channel
        parent_id = getattr(channel, "parent_id", None)
        if interaction.channel_id not in channels and parent_id not in channels:
            mentions = ", ".join(f"<#{c}>" for c in sorted(channels))
            return f"Music requests go in {mentions}."

    if not has_any_role((r.id for r in member.roles), settings.request_roles):
        roles = ", ".join(f"<@&{r}>" for r in sorted(settings.request_roles))
        return f"You need one of these roles to use the music bot: {roles}"
    return None
