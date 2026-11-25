"""Everything the Discord layer needs, bundled so it can be passed around as one object."""

from __future__ import annotations

from dataclasses import dataclass

from .config import Settings
from .db import RequestLog
from .library import Library
from .lidarr import LidarrClient
from .requester import Requester
from .search import SearchService


@dataclass(slots=True)
class Services:
    settings: Settings
    lidarr: LidarrClient
    library: Library
    search: SearchService
    requester: Requester
    log: RequestLog

    @classmethod
    def create(cls, settings: Settings) -> Services:
        lidarr = LidarrClient(settings.lidarr_url, settings.lidarr_api_key)
        library = Library(lidarr)
        return cls(
            settings=settings,
            lidarr=lidarr,
            library=library,
            search=SearchService(lidarr),
            requester=Requester(lidarr, library, settings),
            log=RequestLog(settings.database_path),
        )

    async def close(self) -> None:
        await self.lidarr.close()
        await self.log.close()
