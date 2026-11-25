from __future__ import annotations

import pytest

from lidarr_bot import formatting as fmt
from lidarr_bot.access import has_any_role
from lidarr_bot.cog import parse_pick
from lidarr_bot.config import Settings, parse_ids
from lidarr_bot.db import RequestLog
from lidarr_bot.library import Library, Status
from lidarr_bot.lidarr import LidarrError, parse_album, parse_artist
from lidarr_bot.models import ReleaseResult
from lidarr_bot.requester import Requester
from lidarr_bot.search import filter_results, source_for

BASE_ENV = {
    "DISCORD_TOKEN": "x",
    "DISCORD_GUILD_ID": "1",
    "REQUEST_ROLE_IDS": "10, 11;12",
    "LIDARR_URL": "http://lidarr:8686",
    "LIDARR_API_KEY": "k",
    "NOTIFY_CHANNEL_ID": "",
}


def make_settings(monkeypatch, **extra) -> Settings:
    for key, value in {**BASE_ENV, **extra}.items():
        monkeypatch.setenv(key, value)
    return Settings(_env_file=None)  # type: ignore[call-arg]


# --- config / access ---------------------------------------------------------


def test_parse_ids():
    assert parse_ids("1, 2;3  4") == {1, 2, 3, 4}
    assert parse_ids("") == frozenset()


def test_settings(monkeypatch):
    s = make_settings(monkeypatch)
    assert s.request_roles == {10, 11, 12}
    assert s.notify_channel_id is None and not s.notify_available
    assert s.lidarr_metadata_profile == "Standard"


def test_settings_notify_needs_channel(monkeypatch):
    with pytest.raises(ValueError):
        make_settings(monkeypatch, NOTIFY_AVAILABLE="true")
    s = make_settings(monkeypatch, NOTIFY_AVAILABLE="true", NOTIFY_CHANNEL_ID="55")
    assert s.notify_available and s.notify_channel_id == 55


def test_settings_requires_role(monkeypatch):
    with pytest.raises(ValueError):
        make_settings(monkeypatch, REQUEST_ROLE_IDS="")


def test_has_any_role():
    assert has_any_role([1, 2], {2})
    assert not has_any_role([1, 2], {3})
    assert not has_any_role([], {3})


# --- library status ----------------------------------------------------------

ARTIST = {
    "id": 1,
    "foreignArtistId": "art-1",
    "monitored": True,
    "statistics": {"albumCount": 3, "percentOfTracks": 50},
}


def album(mbid, *, id, files=0, pct=0, monitored=False):
    return {
        "id": id,
        "artistId": 1,
        "foreignAlbumId": mbid,
        "monitored": monitored,
        "statistics": {"trackFileCount": files, "percentOfTracks": pct},
    }


@pytest.fixture
def library():
    lib = Library(client=None)  # type: ignore[arg-type]
    lib.load(
        [ARTIST],
        [
            album("done", id=1, files=10, pct=100),
            album("wanted", id=2, monitored=True),
            album("partial", id=3, files=4, pct=40),
            album("unmonitored", id=4),
            album("queued", id=5, monitored=True),
        ],
        [{"albumId": 5}],
    )
    return lib


@pytest.mark.parametrize(
    ("mbid", "artist", "status", "requestable"),
    [
        ("done", "art-1", Status.AVAILABLE, False),
        ("wanted", "art-1", Status.WANTED, False),
        ("partial", "art-1", Status.PARTIAL, True),
        ("unmonitored", "art-1", Status.ARTIST_KNOWN, True),
        ("queued", "art-1", Status.DOWNLOADING, False),
        ("not-there", "art-1", Status.ARTIST_KNOWN, True),
        ("not-there", "other", Status.NEW, True),
    ],
)
def test_album_status(library, mbid, artist, status, requestable):
    info = library.album_status(mbid, artist)
    assert info.status is status
    assert info.requestable is requestable


def test_artist_status(library):
    info = library.artist_status("art-1")
    assert info.status is Status.DOWNLOADING  # album 5 is queued
    assert not info.requestable
    assert library.artist_status("nope").status is Status.NEW
    library.queued_album_ids.clear()
    assert library.artist_status("art-1").status is Status.WANTED  # no files yet
    library.artists_by_mbid["art-1"]["statistics"]["trackFileCount"] = 3
    assert library.artist_status("art-1").status is Status.AVAILABLE


def test_status_unknown_before_load():
    lib = Library(client=None)  # type: ignore[arg-type]
    assert lib.album_status("x").status is Status.UNKNOWN


# --- formatting --------------------------------------------------------------


def test_choice_name_has_status_icon(library):
    r = ReleaseResult(
        mbid="done", title="Pablo Honey", artist_name="Radiohead", artist_mbid="rh",
        album_type="Album", year=1993,
    )  # fmt: skip
    assert fmt.choice_name(r, library.album_status("done")) == "✅ Pablo Honey (1993) — Radiohead"
    single = ReleaseResult(
        mbid="new", title="Creep", artist_name="Radiohead", artist_mbid="rh", album_type="Single"
    )
    assert fmt.choice_name(single, library.album_status("new")) == "➕ Creep — Radiohead"
    wanted = ReleaseResult(mbid="wanted", title="W", artist_name="A", artist_mbid="art-1")
    assert fmt.choice_name(wanted, library.album_status("wanted")).startswith("⏳ ")
    queued = ReleaseResult(mbid="queued", title="Q", artist_name="A", artist_mbid="art-1")
    assert fmt.choice_name(queued, library.album_status("queued")).startswith("⏳ ")


def test_choice_name_truncates_to_100(library):
    r = ReleaseResult(mbid="done", title="x" * 200, artist_name="A", artist_mbid="a")
    text = fmt.choice_name(r, library.album_status("done"))
    assert len(text) == 100 and text.startswith("✅ xxx")


def test_embeds_look_like_overseerr():
    r = ReleaseResult(
        mbid="m", title="OK Computer", artist_name="Radiohead", artist_mbid="rh",
        album_type="Album", year=1997, image="https://img/c.jpg",
    )  # fmt: skip
    req = fmt.request_embed(r).to_dict()
    assert req["title"] == "Album Requested: OK Computer (1997)"
    assert "fields" not in req and req["thumbnail"]["url"] == "https://img/c.jpg"
    done = fmt.available_embed(r, [42, 43], "An album.").to_dict()
    assert done["title"] == "Album Request Now Available: OK Computer (1997)"
    assert done["fields"][0]["value"] == "<@42>, <@43>"
    assert done["fields"][1]["value"] == "Available"
    assert "An album." in done["description"] and done["thumbnail"]["url"] == "https://img/c.jpg"


def test_filter_and_source():
    rs = [
        ReleaseResult(mbid=t, title=t, artist_name="a", artist_mbid="a", album_type=t)
        for t in ("Album", "EP", "Single")
    ]
    assert [r.mbid for r in filter_results("single", rs)] == ["Single"]
    assert [r.mbid for r in filter_results("ep", rs)] == ["EP"]
    assert filter_results("artist", rs) == rs and filter_results(None, rs) == rs
    assert (source_for("ep"), source_for("artist"), source_for(None)) == (
        "album",
        "artist",
        "album",
    )


def test_parse_pick():
    mbid = "a74b1b7f-71a5-4011-9441-d0b5e4122711"
    assert parse_pick(f"release:{mbid}") == ("release", mbid)
    assert parse_pick(f"artist:{mbid}") == ("artist", mbid)
    assert parse_pick("radiohead: creep") is None
    assert parse_pick("ok computer") is None


def test_truncate():
    assert fmt.truncate("abc", 5) == "abc"
    assert fmt.truncate("abcdefgh", 5) == "abcd…"


# --- Lidarr parsing ----------------------------------------------------------


def test_parse_album_and_artist():
    a = parse_album(
        {
            "foreignAlbumId": "rg",
            "title": "OK Computer",
            "albumType": "Album",
            "releaseDate": "1997-05-21T00:00:00Z",
            "artist": {"artistName": "Radiohead", "foreignArtistId": "rh"},
            "images": [
                {
                    "coverType": "cover",
                    "url": "/MediaCover/1.jpg",
                    "remoteUrl": "https://img/cover.jpg",
                }
            ],
        }
    )
    assert (a.year, a.image, a.artist_mbid) == (
        1997,
        "https://img/cover.jpg",
        "rh",
    )
    ar = parse_artist(
        {
            "foreignArtistId": "rh",
            "artistName": "Radiohead",
            "images": [{"coverType": "poster", "url": "/local.jpg"}],
        }
    )
    assert ar.image is None  # local-only URLs aren't reachable from Discord


# --- requester ---------------------------------------------------------------


class FakeLidarr:
    def __init__(self):
        self.calls = []

    async def root_folders(self):
        return [{"path": "/music/", "defaultQualityProfileId": 7, "defaultMetadataProfileId": 99}]

    async def quality_profiles(self):
        return [{"id": 1, "name": "Any"}, {"id": 7, "name": "Lossless"}]

    async def metadata_profiles(self):
        return [{"id": 3, "name": "Standard"}, {"id": 4, "name": "None"}]

    async def add_album(self, body):
        self.calls.append(("add_album", body))
        return {"id": 50}

    async def add_artist(self, body):
        self.calls.append(("add_artist", body))
        return {"id": 60}

    async def monitor_albums(self, ids, monitored=True):
        self.calls.append(("monitor", ids))

    async def command(self, name, **kw):
        self.calls.append(("command", name, kw))

    async def update_artist(self, artist):
        self.calls.append(("update_artist", artist["id"], artist["monitored"]))

    async def artists(self):
        return []

    async def albums(self):
        return []

    async def queue(self):
        return []


@pytest.fixture
def requester(monkeypatch):
    client = FakeLidarr()
    lib = Library(client)  # type: ignore[arg-type]
    lib.load([{**ARTIST, "monitored": False}], [album("unmonitored", id=4)], [])
    lib.refresh = _noop  # keep the fixture's library state
    return Requester(client, lib, make_settings(monkeypatch)), client  # type: ignore[arg-type]


async def _noop():
    return None


async def test_defaults_use_standard_and_root_default_quality(requester):
    req, _ = requester
    d = await req.defaults()
    assert (d.quality_profile_id, d.metadata_profile_id, d.root_folder_path) == (7, 3, "/music/")


async def test_request_existing_album_monitors_and_searches(requester):
    req, client = requester
    rel = ReleaseResult(mbid="unmonitored", title="T", artist_name="A", artist_mbid="art-1")
    out = await req.request_release(rel)
    assert out.lidarr_id == 4
    assert ("update_artist", 1, True) in client.calls
    assert ("monitor", [4]) in client.calls
    assert ("command", "AlbumSearch", {"albumIds": [4]}) in client.calls


async def test_request_new_album_for_new_artist_only_monitors_that_release(requester):
    req, client = requester
    raw = {
        "foreignAlbumId": "new-rg",
        "title": "Single",
        "id": 0,
        "artist": {"foreignArtistId": "new-artist", "artistName": "New"},
    }
    rel = parse_album(raw)
    await req.request_release(rel)
    ((name, body),) = client.calls
    assert name == "add_album"
    assert body["monitored"] is True and body["addOptions"] == {"searchForNewAlbum": True}
    assert "id" not in body
    artist = body["artist"]
    assert artist["addOptions"] == {"monitor": "none", "searchForMissingAlbums": False}
    assert artist["metadataProfileId"] == 3 and artist["rootFolderPath"] == "/music/"


async def test_request_new_album_for_existing_artist_reuses_artist(requester):
    req, client = requester
    rel = parse_album(
        {
            "foreignAlbumId": "rg2",
            "title": "X",
            "artist": {"foreignArtistId": "art-1", "artistName": "A"},
        }
    )
    await req.request_release(rel)
    ((_, body),) = client.calls
    assert body["artistId"] == 1


async def test_request_artist(requester):
    req, client = requester
    with pytest.raises(LidarrError):
        await req.request_artist(parse_artist({"foreignArtistId": "art-1", "artistName": "A"}))
    out = await req.request_artist(
        parse_artist({"foreignArtistId": "z", "artistName": "Z", "id": 0})
    )
    assert out.lidarr_id == 60
    ((_, body),) = client.calls
    assert body["addOptions"] == {"monitor": "all", "searchForMissingAlbums": True}
    assert body["monitorNewItems"] == "all" and "id" not in body


# --- request log -------------------------------------------------------------


async def test_request_log_roundtrip():
    db = RequestLog(":memory:")
    await db.open()
    common = dict(
        guild_id=1,
        channel_id=2,
        command="single",
        search_query="creep",
        kind="release",
        title="Creep",
        artist_name="Radiohead",
        release_type="Single",
    )
    await db.add(user_id=5, user_name="alice", mbid="rg", outcome="requested", **common)
    await db.add(user_id=6, user_name="bob", mbid="rg", outcome="failed", error="nope", **common)
    await db.add(user_id=5, user_name="alice", mbid="rg2", outcome="requested", **common)
    mine = await db.recent(user_id=5)
    assert [r.mbid for r in mine] == ["rg2", "rg"]
    assert await db.requesters_for("rg") == [5]
    pending = await db.pending()
    assert [r.mbid for r in pending] == ["rg", "rg2"]
    await db.mark_available([pending[0].id])
    assert [r.mbid for r in await db.pending()] == ["rg2"]
    await db.close()


# --- download notifications --------------------------------------------------


class FakeChannel:
    def __init__(self):
        self.sent = []

    async def send(self, embed):
        self.sent.append(embed)


class FakeClient:
    def __init__(self, channel):
        self.channel = channel

    def get_channel(self, channel_id):
        return self.channel


async def test_notifier_announces_finished_requests_once(monkeypatch, library):
    from lidarr_bot.notifier import Notifier

    settings = make_settings(monkeypatch, NOTIFY_AVAILABLE="true", NOTIFY_CHANNEL_ID="9")
    db = RequestLog(":memory:")
    await db.open()
    common = dict(guild_id=1, channel_id=2, command="album", search_query="q", kind="release",
                  artist_name="Band", release_type="Album", outcome="requested")  # fmt: skip
    await db.add(user_id=5, user_name="a", mbid="done", title="Done", **common)
    await db.add(user_id=6, user_name="b", mbid="done", title="Done", **common)
    await db.add(user_id=5, user_name="a", mbid="wanted", title="Wanted", **common)

    class S:  # minimal stand-in for Services
        pass

    services = S()
    services.settings, services.library, services.log = settings, library, db
    channel = FakeChannel()
    notifier = Notifier(FakeClient(channel), services)  # type: ignore[arg-type]

    await notifier.check()
    assert len(channel.sent) == 1
    embed = channel.sent[0].to_dict()
    assert "Now Available" in embed["title"] and embed["fields"][0]["value"] == "<@5>, <@6>"
    assert [r.mbid for r in await db.pending()] == ["wanted"]

    await notifier.check()  # nothing new
    assert len(channel.sent) == 1
    await db.close()
