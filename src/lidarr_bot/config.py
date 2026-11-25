"""Settings loaded from environment variables (or a .env file)."""

from __future__ import annotations

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

VALID_ARTIST_MONITOR = {"all", "future", "missing", "existing", "first", "latest", "none"}


def parse_ids(raw: str) -> frozenset[int]:
    """Parse a comma/semicolon/space separated list of Discord snowflake IDs."""
    parts = raw.replace(";", ",").replace(" ", ",").split(",")
    return frozenset(int(p) for p in (part.strip() for part in parts) if p)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Discord
    discord_token: str
    discord_guild_id: int
    # Comma-separated role IDs. Members need at least one of these to use the bot at all.
    request_role_ids: str
    # Comma-separated channel IDs the bot answers in. Empty = any channel.
    allowed_channel_ids: str = ""
    # Post a message when a requested artist/release has finished downloading.
    notify_available: bool = False
    notify_channel_id: int | None = None

    # Lidarr
    lidarr_url: str
    lidarr_api_key: str
    # Name or numeric ID. Empty = the root folder's default, else the first profile.
    lidarr_quality_profile: str = ""
    lidarr_metadata_profile: str = "Standard"
    # Path of a Lidarr root folder. Empty = the first root folder.
    lidarr_root_folder: str = ""
    # Which albums to monitor when a whole artist is requested.
    artist_monitor: str = "all"

    # Misc
    database_path: str = "data/requests.db"
    library_refresh_seconds: int = 300
    queue_refresh_seconds: int = 60
    log_level: str = "INFO"

    @field_validator("notify_channel_id", mode="before")
    @classmethod
    def _empty_to_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("artist_monitor")
    @classmethod
    def _check_monitor(cls, value: str) -> str:
        value = value.strip().lower()
        if value not in VALID_ARTIST_MONITOR:
            raise ValueError(f"must be one of {sorted(VALID_ARTIST_MONITOR)}")
        return value

    @field_validator("request_role_ids")
    @classmethod
    def _require_roles(cls, value: str) -> str:
        if not parse_ids(value):
            raise ValueError("at least one role ID is required")
        return value

    @field_validator("request_role_ids", "allowed_channel_ids")
    @classmethod
    def _check_ids(cls, value: str) -> str:
        parse_ids(value)  # raises ValueError on junk
        return value

    @model_validator(mode="after")
    def _check_notify(self) -> Settings:
        if self.notify_available and self.notify_channel_id is None:
            raise ValueError("NOTIFY_CHANNEL_ID is required when NOTIFY_AVAILABLE is on")
        return self

    @property
    def request_roles(self) -> frozenset[int]:
        return parse_ids(self.request_role_ids)

    @property
    def allowed_channels(self) -> frozenset[int]:
        return parse_ids(self.allowed_channel_ids)
