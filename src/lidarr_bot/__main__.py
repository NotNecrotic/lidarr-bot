"""Entry point: `python -m lidarr_bot` or the `lidarr-bot` script."""

from __future__ import annotations

import logging
import sys

from pydantic import ValidationError

from .bot import LidarrBot
from .config import Settings


def main() -> None:
    try:
        settings = Settings()  # type: ignore[call-arg]
    except ValidationError as exc:
        print("Configuration error — check your .env file:\n", exc, file=sys.stderr)
        raise SystemExit(2) from exc

    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    bot = LidarrBot(settings)
    bot.run(settings.discord_token, log_handler=None)


if __name__ == "__main__":
    main()
