"""Text and embeds shown in Discord. Kept free of I/O so it is easy to test."""

from __future__ import annotations

from datetime import UTC, datetime

import discord

from .library import StatusInfo
from .models import ArtistResult, ReleaseResult

REQUESTED = discord.Colour.blurple()
AVAILABLE = discord.Colour.from_rgb(34, 197, 94)


def truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def heading(item: ArtistResult | ReleaseResult) -> str:
    if isinstance(item, ArtistResult):
        return f"{item.name} ({item.disambiguation})" if item.disambiguation else item.name
    return f"{item.title} ({item.year})" if item.year else item.title


def type_name(item: ArtistResult | ReleaseResult) -> str:
    """'Artist', 'Album', 'Single', 'Live Album', 'Compilation Album'..."""
    if isinstance(item, ArtistResult):
        return "Artist"
    return " ".join([*item.secondary_types, item.album_type or "Release"])


# --- autocomplete & select menu ----------------------------------------------


def choice_name(item: ArtistResult | ReleaseResult, status: StatusInfo) -> str:
    """One autocomplete line (max 100 chars): '✅ Creep (1992) — Radiohead'.

    No type prefix: the user already chose the type in the `type` option.
    """
    if isinstance(item, ArtistResult):
        main = heading(item)
    else:
        main = f"{heading(item)} — {item.artist_name}"
    return truncate(f"{status.icon} {main}", 100)


def option_label(item: ArtistResult | ReleaseResult) -> str:
    return truncate(heading(item), 100)


def option_description(item: ArtistResult | ReleaseResult) -> str:
    if isinstance(item, ArtistResult):
        return truncate(item.artist_type or "Artist", 100)
    return truncate(item.artist_name, 100)


# --- messages ----------------------------------------------------------------


def not_requestable(item: ArtistResult | ReleaseResult, status: StatusInfo) -> str:
    name = f"**{heading(item)}**"
    return {
        "Available": f"{name} is already available.",
        "Downloading": f"{name} is downloading right now.",
        "Requested": f"{name} has already been requested.",
    }.get(status.label, f"{name} can't be requested right now.")


def _description(item: ArtistResult | ReleaseResult, overview: str | None = None) -> str:
    lines: list[str] = []
    if isinstance(item, ReleaseResult):
        lines.append(f"by **{item.artist_name}**")
    text = overview if overview is not None else getattr(item, "overview", "")
    if text:
        lines.append("")
        lines.append(truncate(text, 350))
    return "\n".join(lines)


def _card(
    title: str,
    item: ArtistResult | ReleaseResult,
    *,
    requested_by: list[int],
    status: str,
    colour: discord.Colour,
    overview: str | None = None,
) -> discord.Embed:
    embed = discord.Embed(
        title=truncate(title, 256),
        description=_description(item, overview) or None,
        colour=colour,
        timestamp=datetime.now(UTC),
    )
    if requested_by:
        embed.add_field(
            name="Requested By", value=truncate(", ".join(f"<@{u}>" for u in requested_by), 1024)
        )
    embed.add_field(name="Request Status", value=status)
    if item.image:
        embed.set_thumbnail(url=item.image)
    return embed


def request_embed(item: ArtistResult | ReleaseResult) -> discord.Embed:
    """Private confirmation shown only to the requester."""
    embed = discord.Embed(
        title=truncate(f"{type_name(item)} Requested: {heading(item)}", 256),
        description=_description(item) or None,
        colour=REQUESTED,
    )
    if item.image:
        embed.set_thumbnail(url=item.image)
    return embed


def available_embed(
    item: ArtistResult | ReleaseResult, requested_by: list[int], overview: str | None = None
) -> discord.Embed:
    return _card(
        f"{type_name(item)} Request Now Available: {heading(item)}",
        item,
        requested_by=requested_by,
        status="Available",
        colour=AVAILABLE,
        overview=overview,
    )
