# CustomPMBot

Telegram support-desk bot: every user who DMs the bot gets a dedicated forum
topic in your admin group; admin replies in the topic relay back to the user.

## Features

- **Relay** — user DMs ↔ per-user forum topic, full media support both directions; admin replies to a forwarded message quote the user's original
- **Auto-replies** — `/autoreply add <keyword> <response>`: whole-word keyword match answers instantly, admins see a note in the topic
- **Broadcasts** — post in the broadcast topic to message all subscribers, with a preview + confirm button (disable via `/setmsg broadcast_confirm off`); `@TAG` first line targets a tag; live progress with sent/blocked/failed counts; `/schedule 2h <message>` for delayed broadcasts with list/cancel/history
- **Moderation** — `/ban` (with expiry + auto-unban), `/unban`, `/banned`, spam auto-ban, `/close`/`/reopen` topics, pinned `/note`
- **Organization** — user tags, custom topics with command/event bindings, canned responses (text and media), conversation `/export`, `/users` filters, `/search` over message logs, `/analytics` activity reports, colored topic icons
- **Stellar wallets** — users register wallets, verify by payment memo (background watcher) or secret key (stored Fernet-encrypted)

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp env.example .env   # fill in your values, then export them
.venv/bin/python bot.py
```

Requires a Telegram bot token (@BotFather), a forum-enabled admin group where
the bot is admin with "Manage Topics" rights, and Python 3.9+.

## Tests

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest
```

234 tests cover the database layer, services (spam, encryption, Stellar,
payment watcher), and all command/callback handlers — fully offline, no
Telegram or Horizon network access needed.

## Project layout

```
bot.py            entrypoint: handlers, background tasks (watcher, ban expiry)
config.py         env-driven configuration
database/         SQLite access + versioned migrations (schema v7)
handlers/         command, callback, and relay handlers
services/         spam throttle, Fernet encryption, Stellar, payment watcher
utils/            helpers, branding texts, media relay, event routing
branding/         texts.json — all user-facing strings
tests/            pytest suite
docs/             plans, roadmap, bug log
```
