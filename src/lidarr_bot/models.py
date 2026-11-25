"""Search results returned by Lidarr lookups."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class ArtistResult:
    mbid: str
    name: str
    disambiguation: str = ""
    artist_type: str = ""
    overview: str = ""
    genres: tuple[str, ...] = ()
    image: str | None = None
    raw: dict[str, Any] | None = field(default=None, repr=False)


@dataclass(slots=True)
class ReleaseResult:
    """A release group (album, EP, single...). This is what actually gets requested."""

    mbid: str  # MusicBrainz release-group ID == Lidarr foreignAlbumId
    title: str
    artist_name: str
    artist_mbid: str
    album_type: str = "Album"
    secondary_types: tuple[str, ...] = ()
    year: int | None = None
    disambiguation: str = ""
    image: str | None = None
    raw: dict[str, Any] | None = field(default=None, repr=False)


def year_from_date(value: str | None) -> int | None:
    if not value or len(value) < 4 or not value[:4].isdigit():
        return None
    year = int(value[:4])
    return year if year > 1000 else None
