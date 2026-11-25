# Lidarr Discord Bot

Search for music and request it on [Lidarr](https://lidarr.audio) from Discord.

- **One command.** `/request type:<Artist|Album|EP|Single> query:<…>`. Suggestions appear as
  you type, already narrowed to the type you picked. Pick one and press Enter; that's the
  request. There's no confirmation step.
- **Knows your library.** Every suggestion has a status icon: ✅ available, ⏳ requested
  (or downloading), ➕ can be requested. Picking something that's already there just tells
  you so, and nothing is requested twice.
- **Role-gated.** Only members with a configured role can use the bot, including the
  suggestions.
- **Logs who asked.** Every request (and every failed one) is stored in SQLite.
- **"Now Available" posts.** Optionally, when a request finishes downloading, the bot posts
  an embed in a channel saying who requested it.

## Commands

| Command | What it does |
|---|---|
| `/request type query` | Request an artist, album, EP or single |
| `/myrequests` | Your last 10 requests with their status (only you see it) |

### How a request works

1. Type `/request`, choose a **type**, then start typing in **query**.
2. Pick a suggestion and press Enter. You get an *"Album Requested: OK Computer (1997)"*
   embed with the artist, cover art and description. Only you can see it, and you can
   dismiss it.
3. If you press Enter on plain text instead of picking a suggestion, you get a private
   dropdown of every match. Choosing one requests it.
4. If it's already available or already requested, or Lidarr refuses it, the bot tells you
   privately and nothing is requested.
5. With `NOTIFY_AVAILABLE=true`, a public *"Album Request Now Available: …"* embed is posted
   in `NOTIFY_CHANNEL_ID` once Lidarr has imported it. It shows the cover, description,
   **Requested By** and **Request Status: Available**. An artist request counts as
   available once its first tracks are imported.

`/myrequests` shows each request with ✅ available, ⏳ requested, ➕ can be requested
(it was removed from Lidarr, or only part of it is there) or ⚠️ (Lidarr refused the request).

### What a request does in Lidarr

| You request | The bot |
|---|---|
| A new artist | Adds the artist (monitoring set by `ARTIST_MONITOR`; new releases are monitored) and searches for missing albums |
| A release whose artist isn't in Lidarr | Adds the artist with *nothing* monitored, then adds and searches for only that release |
| A release whose artist is in Lidarr | Monitors that release (and the artist, if unmonitored) and runs an album search |

## Setup

### 1. Create the Discord bot

1. Go to <https://discord.com/developers/applications>, click **New Application**, then open
   **Bot** and click **Reset Token**. Copy the token.
2. No privileged intents are needed.
3. Under **OAuth2 → URL Generator**, tick the scopes `bot` and `applications.commands`, and
   the permissions *View Channels*, *Send Messages* and *Embed Links*. Open the generated URL
   to invite the bot to your server.
4. In Discord, turn on **Developer Mode** (User Settings → Advanced). Then right-click to
   copy your server ID, the request role ID(s), and, optionally, a notification channel ID.

### 2. Configure

Copy `.env.example` to `.env` and fill it in. At minimum you need `DISCORD_TOKEN`,
`DISCORD_GUILD_ID`, `REQUEST_ROLE_IDS`, `LIDARR_URL` and `LIDARR_API_KEY`.

| Setting | Default | What it does |
|---|---|---|
| `DISCORD_TOKEN` | *required* | Bot token from the developer portal |
| `DISCORD_GUILD_ID` | *required* | Your server ID; commands are registered there |
| `REQUEST_ROLE_IDS` | *required* | Role IDs (comma-separated). Members need one of them to use the bot |
| `ALLOWED_CHANNEL_IDS` | *(any channel)* | Only answer in these channels (comma-separated) |
| `NOTIFY_AVAILABLE` | `false` | Post "Request Now Available" when a request finishes downloading |
| `NOTIFY_CHANNEL_ID` | | Channel for those posts (required if `NOTIFY_AVAILABLE=true`) |
| `LIDARR_URL` | *required* | Lidarr's address as seen from the bot, e.g. `http://192.168.1.50:8686` |
| `LIDARR_API_KEY` | *required* | Lidarr → Settings → General → Security → API Key |
| `LIDARR_QUALITY_PROFILE` | *(root folder's default)* | Quality profile name or ID |
| `LIDARR_METADATA_PROFILE` | `Standard` | Metadata profile name or ID |
| `LIDARR_ROOT_FOLDER` | *(first root folder)* | Root folder path or ID |
| `ARTIST_MONITOR` | `all` | Albums to monitor for artist requests: `all`, `future`, `missing`, `existing`, `first`, `latest`, `none` |
| `DATABASE_PATH` | `data/requests.db` | Where the request log is stored |
| `LIBRARY_REFRESH_SECONDS` | `300` | How often to re-read the Lidarr library |
| `QUEUE_REFRESH_SECONDS` | `60` | How often to check the download queue. Finished downloads are picked up when they leave the queue |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`… |

> **Singles and EPs:** Lidarr's metadata profile decides which release types it keeps for
> an artist. For Single and EP requests to stick, make sure the profile you use has
> *Single* and *EP* ticked under primary types (Lidarr → Settings → Profiles). Otherwise
> Lidarr may drop the release the next time it refreshes the artist.

### 3. Run it

When it starts you should see `Synced 2 commands to guild …` and `Logged in as …`. If Lidarr
can't be reached, the log says so. Commands are registered for your server on startup and
appear immediately.

#### Windows

Needs Python 3.11 or newer. In PowerShell, from the project folder:

```powershell
python -m venv .venv
.\.venv\Scripts\pip install .
Copy-Item .env.example .env
notepad .env
.\.venv\Scripts\lidarr-bot.exe
```

Press Ctrl+C to stop it. It only runs while that window is open. To keep it running, create
a Task Scheduler task that runs `.venv\Scripts\lidarr-bot.exe` at logon, with the project
folder as *Start in*.

#### Docker

Works on any host on your LAN, or in an LXC with Docker:

```sh
docker compose up -d --build
docker compose logs -f
```

The request log is kept in `./data`.

#### Proxmox LXC without Docker (Debian/Ubuntu)

```sh
apt install -y python3 python3-venv git
useradd --system --home /opt/lidarr-bot lidarr-bot
git clone <this repo> /opt/lidarr-bot && cd /opt/lidarr-bot
python3 -m venv .venv && .venv/bin/pip install .
cp .env.example .env && nano .env        # fill it in
chown -R lidarr-bot: /opt/lidarr-bot
cp deploy/lidarr-bot.service /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now lidarr-bot
journalctl -u lidarr-bot -f
```

The bot can run in the same LXC as Lidarr (`LIDARR_URL=http://localhost:8686`) or anywhere
that can reach it. It only makes outbound connections, so no ports need opening.

## Request log

Every request is stored in the `requests` table in `DATABASE_PATH`. Each row records:
- who asked (Discord ID and name), when, and in which channel
- the type they picked and what they searched for
- the MusicBrainz ID, title, artist and release type
- the Lidarr ID, and whether it worked (with the error if not)
- when the download finished (`available_at`)

```sh
sqlite3 data/requests.db "select created_at, user_name, title, artist_name, outcome, available_at from requests order by id desc limit 20"
```

## Development

```sh
python -m venv .venv && .venv/bin/pip install -e ".[dev]"   # Windows: .venv\Scripts\pip
.venv/bin/pytest
.venv/bin/ruff check .
```

Layout (`src/lidarr_bot/`):

- `cog.py`: `/request` (with autocomplete) and `/myrequests`
- `ui.py`: the request action and the fallback result dropdown
- `formatting.py`: every message and embed
- `notifier.py`: "Now Available" posts
- `library.py`: cached library and queue, plus status logic
- `requester.py`: turns a request into Lidarr calls
- `search.py`: cached, de-duplicated Lidarr searches for autocomplete
- `lidarr.py`: Lidarr v1 API client
- `db.py`: request log
- `access.py`: role and channel checks
- `config.py`: settings from `.env`
