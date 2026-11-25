"""SQLite log of every request made through the bot."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS requests (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at    TEXT    NOT NULL,           -- ISO 8601, UTC
    user_id       INTEGER NOT NULL,
    user_name     TEXT    NOT NULL,
    guild_id      INTEGER,
    channel_id    INTEGER,
    command       TEXT    NOT NULL,           -- type picked: artist | album | ep | single
    search_query  TEXT,
    kind          TEXT    NOT NULL,           -- artist | release
    mbid          TEXT    NOT NULL,
    title         TEXT    NOT NULL,
    artist_name   TEXT    NOT NULL,
    release_type  TEXT,
    lidarr_id     INTEGER,
    outcome       TEXT    NOT NULL,           -- requested | failed
    error         TEXT,
    available_at  TEXT                        -- set once the download finished
);
CREATE INDEX IF NOT EXISTS idx_requests_user ON requests(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_requests_mbid ON requests(mbid);
"""


@dataclass(slots=True)
class RequestRecord:
    id: int
    created_at: datetime
    user_id: int
    user_name: str
    command: str
    kind: str
    mbid: str
    title: str
    artist_name: str
    release_type: str | None
    outcome: str
    error: str | None


_COLUMNS = (
    "id, created_at, user_id, user_name, command, kind, mbid, title, artist_name, "
    "release_type, outcome, error"
)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _record(row: aiosqlite.Row) -> RequestRecord:
    values = list(row)
    values[1] = datetime.fromisoformat(values[1])
    return RequestRecord(*values)


class RequestLog:
    def __init__(self, path: str) -> None:
        self._path = path
        self._db: aiosqlite.Connection | None = None

    async def open(self) -> None:
        if self._path != ":memory:":
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self._path)
        await self._db.executescript(SCHEMA)
        await self._db.commit()

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    @property
    def db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("RequestLog.open() was not called")
        return self._db

    async def add(
        self,
        *,
        user_id: int,
        user_name: str,
        guild_id: int | None,
        channel_id: int | None,
        command: str,
        search_query: str | None,
        kind: str,
        mbid: str,
        title: str,
        artist_name: str,
        release_type: str | None = None,
        lidarr_id: int | None = None,
        outcome: str,
        error: str | None = None,
    ) -> int:
        cursor = await self.db.execute(
            """INSERT INTO requests (created_at, user_id, user_name, guild_id, channel_id, command,
                   search_query, kind, mbid, title, artist_name, release_type, lidarr_id,
                   outcome, error)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                _now(), user_id, user_name, guild_id, channel_id, command, search_query, kind,
                mbid, title, artist_name, release_type, lidarr_id, outcome, error,
            ),
        )  # fmt: skip
        await self.db.commit()
        return int(cursor.lastrowid or 0)

    async def recent(self, *, user_id: int, limit: int = 10) -> list[RequestRecord]:
        async with self.db.execute(
            f"SELECT {_COLUMNS} FROM requests WHERE user_id = ? ORDER BY id DESC LIMIT ?",
            (user_id, limit),
        ) as cursor:
            return [_record(row) async for row in cursor]

    async def requesters_for(self, mbid: str) -> list[int]:
        """IDs of users who successfully requested this item, oldest first."""
        async with self.db.execute(
            "SELECT user_id FROM requests WHERE mbid = ? AND outcome = 'requested' "
            "GROUP BY user_id ORDER BY MIN(id)",
            (mbid,),
        ) as cursor:
            return [row[0] async for row in cursor]

    async def pending(self) -> list[RequestRecord]:
        """Successful requests whose download hasn't been seen finishing yet."""
        async with self.db.execute(
            f"SELECT {_COLUMNS} FROM requests "
            "WHERE outcome = 'requested' AND available_at IS NULL ORDER BY id"
        ) as cursor:
            return [_record(row) async for row in cursor]

    async def mark_available(self, ids: list[int]) -> None:
        if not ids:
            return
        marks = ",".join("?" * len(ids))
        await self.db.execute(
            f"UPDATE requests SET available_at = ? WHERE id IN ({marks})", (_now(), *ids)
        )
        await self.db.commit()
