# Lidarr Discord Bot

Search for music and request it on [Lidarr](https://lidarr.audio) from Discord.

- **One command.** `/request type:<Artist|Album|EP|Single> query:<…>`. Suggestions
  appear as you type, already narrowed to the type you picked. Pick one and press Enter.
  That's the request; there's no confirmation step.
- **Knows your library.** Every suggestion has a status icon: ✅ available,
  ⏳ requested (or downloading), ➕ can be requested. Picking something that's already
  there just tells you so, and nothing is requested twice.
- **Role-gated.** Only members with a configured role can use the bot, including the
  suggestions.
- **Logs who asked.** Every request (and every failed one) is stored in SQLite.
- **"Now Available" posts.** Optionally, when a request finishes downloading, the bot
  posts an embed in a channel with who requested it.

## Commands

| Command | What it does |
|---|---|
| `/request type query` | Request an artist, album, EP or single |
| `/myrequests` | Your last 10 requests with their status icons (only you see it) |

**How a request looks:**

1. `/request` → choose **type** → start typing in **query**.
2. Pick a suggestion and press Enter. You get an *"Album Requested: OK Computer (1997)"*
   embed that only you can see and can dismiss.
3. If you press Enter on plain text instead, you get a private dropdown of every match.
   Choosing one requests it.
4. Everything the bot replies with is private to you. Only the optional "Now Available"
   post below is public.
5. With `NOTIFY_AVAILABLE=true`, a green *"Album Request Now Available: …"* embed is posted
   in `NOTIFY_CHANNEL_ID` once Lidarr has imported it. An artist request counts as
   available once its first tracks are imported.

### What a request does in Lidarr

| You request | Bot does |
|---|---|
| A new artist | Adds the artist (monitor: `ARTIST_MONITOR`, new releases monitored) and searches for missing albums |
| A release whose artist isn't in Lidarr | Adds the artist with *nothing* monitored, then adds and searches only that release |
| A release whose artist is in Lidarr | Monitors that release (and the artist, if unmonitored) and runs an album search |

## Setup

### 1. Create the Discord bot

1. Go to <https://discord.com/developers/applications>, click **New Application**, then open **Bot** and **Reset Token**. Copy the token.
2. No privileged intents are needed.
3. Under **OAuth2 → URL Generator**, tick the scopes `bot` and `applications.commands`, and the
   permissions *View Channels*, *Send Messages* and *Embed Links*. Open the generated URL to invite the bot.
4. In Discord, turn on **Developer Mode** (User Settings → Advanced). Then right-click to
   copy your server ID, the request role ID(s), and optionally a notification channel ID.

### 2. Configure

```sh
cp .env.example .env
```

Fill in `DISCORD_TOKEN`, `DISCORD_GUILD_ID`, `REQUEST_ROLE_IDS`, `LIDARR_URL` and `LIDARR_API_KEY`.
Find the API key in Lidarr under *Settings → General*. By default, requests use the **Standard**
metadata profile and your first root folder. Change these with `LIDARR_METADATA_PROFILE` /
`LIDARR_ROOT_FOLDER` (names or IDs).

> **Singles and EPs:** Lidarr's metadata profile decides which release types it keeps for an
> artist. If you want `[SINGLE]`/`[EP]` requests to stick, make sure the profile you use has
> *Single* and *EP* ticked under primary types (Lidarr → Settings → Profiles). Otherwise
> Lidarr may drop the release on its next artist refresh.

### 3. Run it

**Docker (any host on your LAN, or a Docker LXC):**

```sh
docker compose up -d --build
docker compose logs -f
```

**Run in an LXC without Docker (Debian/Ubuntu):**

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

Slash commands are registered for your server on startup and appear immediately.
If Lidarr can't be reached, the startup log says so.

## Request log

Requests are stored in `DATABASE_PATH` (default `data/requests.db`), table `requests`:
who (Discord ID and name), when, where, which command and search text, the MusicBrainz
ID, title, artist, release type, the Lidarr ID, and
whether it worked (with the error if not), and when the download finished (`available_at`).

```sh
sqlite3 data/requests.db "select created_at, user_name, title, artist_name, outcome from requests order by id desc limit 20"
```

## Development

```sh
python -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pytest
.venv/bin/ruff check .
```

Layout (`src/lidarr_bot/`):

- `lidarr.py`: Lidarr v1 API client
- `library.py`: cached library and queue, plus status logic
- `requester.py`: turns a request into Lidarr calls
- `search.py`: cached, de-duplicated searches for autocomplete
- `cog.py`: `/request` (with autocomplete) and `/myrequests`
- `ui.py`: the request action and the fallback result dropdown
- `notifier.py`: "Now Available" posts
- `formatting.py`: every message and embed
- `db.py`: request log
- `access.py`: role and channel checks
