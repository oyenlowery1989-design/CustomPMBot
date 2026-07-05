# CustomPMBot — Complete Manual

Everything the bot does and how to use it. In-bot version: `/help` (overview)
and `/help <command>` (details).

---

## 1. What this bot is

A support-desk bot. Users write to the bot in private; every user gets their
own **forum topic** in your admin group. You answer inside the topic; the bot
relays your reply back to the user. Users never see the group, other users, or
which admin answered.

```
User DM  ──►  Bot  ──►  Topic "Alice" in admin group
User DM  ◄──  Bot  ◄──  Admin reply inside topic "Alice"
```

---

## 2. Setup from zero

### 2.1 Create the bot
1. Talk to [@BotFather](https://t.me/BotFather) → `/newbot` → save the token.
2. Optionally `/setprivacy` → keep default (bot only sees commands + DMs).

### 2.2 Create the admin group
1. New group → convert to **forum**: Group Settings → Topics → enable.
2. Add the bot as **administrator** with at least **Manage Topics**, Pin
   Messages, Delete Messages.
3. Get the group id: forward any group message to [@userinfobot](https://t.me/userinfobot),
   or check the bot log on first start. Forum group ids look like `-100…`.

### 2.3 Configure environment
Copy `env.example`, fill in:

| Variable | Required | Meaning |
|---|---|---|
| `BOT_TOKEN` | ✅ | from BotFather |
| `OWNER_ID` | ✅ | your Telegram user id |
| `ADMIN_IDS` | ✅ | comma-separated admin ids (include yourself) |
| `ADMIN_GROUP_ID` | ✅ | the forum group (`-100…`) |
| `DB_PATH` | — | SQLite file (default `state.db`) |
| `BROADCAST_TOPIC_NAME` | — | name of the broadcast topic (default `📢 Broadcast`) |
| `MAX_CONCURRENT` | — | parallel sends during broadcast (default 15) |
| `VERIFY_WALLET_PUBLIC` | for wallet verify | Stellar wallet that receives verification memo payments |
| `WALLET_ENCRYPTION_KEY` | for wallet verify | Fernet key: `python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |
| `HEALTH_PORT` | — | HTTP health endpoint port (disabled if unset) |

### 2.4 Run
```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python bot.py
```
First start runs all DB migrations automatically (schema v10) and creates the
broadcast topic. For production use a systemd unit with `Restart=always`.

---

## 3. The relay (core feature)

- **User → you:** each DM is forwarded into that user's topic. New users get a
  topic automatically (random colored icon), with an info card: name, id,
  username, tags, first-seen date.
- **You → user:** write anything inside the topic — text, photos, files,
  stickers, voice — it's relayed. **Reply to a specific forwarded message** and
  the user sees their original message quoted (reply threading).
- **Spam guard:** more than 5 messages in 10 seconds → warning; continuing →
  automatic 24h ban. Expired bans lift automatically.
- **Blocked tracking:** if a user blocks the bot, they're marked; unmarked when
  they return.
- Admins' own DMs to the bot are ignored (no self-topics).

## 4. Moderation

| Command | Usage |
|---|---|
| `/ban <id> [reason]` | ban by id, or just `/ban [reason]` inside a topic. Banned users get an **Appeal** button; appeals land in their topic. |
| `/unban <id>` | or `/unban` inside the topic |
| `/banned` | list active bans with reasons |
| `/close` | inside a topic: archive it, pause relay. User gets a "conversation closed" notice if they write. |
| `/reopen` | undo `/close` |
| `/note <text>` | pin an admin-only note in the topic |

## 5. Broadcasts

Post any message in the **📢 Broadcast** topic → it goes to every subscriber.

- **Confirm step (default on):** the bot replies with recipient count and
  📢 Send / ❌ Cancel buttons. Nothing goes out until you press Send.
  Disable: `/setmsg broadcast_confirm off`.
- **Tag targeting:** first line `@VIP` (alone) → only users tagged VIP.
- **Scheduled:** `/schedule 2h Big news!` (durations `10m 2h 1d 1w`), also with
  `@TAG` first line. `/schedule list` shows pending + recently sent,
  `/schedule cancel <id>` cancels. Fires within 30s of due time.
- **Progress:** live sent/blocked/failed counter, final report in the topic.
- `/forcebroadcast on|off` — override every user's subscription setting.
- Users opt in/out themselves via `/settings`.

## 6. Organization

- **Tags:** `/tag vip` inside a topic (or `/tag <id> vip`), `/tag remove …`.
  Tags drive `@TAG` broadcasts and `/users tag vip`.
- **Canned responses:** `/canned add hours We're open 9-5 UTC` then
  `/canned hours` inside any topic sends it. **Media:** reply to a
  photo/video/file with `/canned add promo [caption]` — it replays the media.
  `/canned list` (📷 icons mark media), `/canned del <name>`.
- **Auto-replies:** `/autoreply add refund Our refund policy: …` — when a
  user's message contains "refund" as a whole word, the bot answers instantly.
  The message still reaches you, with a 🤖 note. `/autoreply list`, `del`.
- **Custom topics:** `/topic create Team Chat`, `/topic list`.
- **Search:** `/search shipping` — all logged messages; inside a topic it
  searches only that user.
- **Users:** `/users`, `/users active|blocked|banned|paused`, `/users tag vip`.
- **Export:** `/export` inside a topic (or `/export <id>`) — conversation log,
  as a .txt file when long.

## 7. Statistics

- `/stats` — headline numbers: users, active, banned, subscriptions, messages.
- `/analytics [days]` — messages per day (in/out), new users per day, top 5
  most active users, busiest UTC hours. Default 7 days, max 90.

## 8. Stellar wallets

Users manage wallets via `/start` → 💳 or `/wallet` (max 5 each).

- **Add:** bot asks for the public address (`G…`, checksum-validated), then a
  label.
- **Verify by memo:** bot shows a 6-digit code; user sends any tiny payment to
  your `VERIFY_WALLET_PUBLIC` with that code as the memo. A background watcher
  polls Horizon every 10s and confirms automatically (15 min window).
- **Verify by secret key:** user pastes their secret key; the bot checks it
  matches the address, **deletes the message immediately** (success or fail),
  and stores the key Fernet-encrypted (`WALLET_ENCRYPTION_KEY`).
- Admin view: `/wallets` — every wallet with owner and label. You're notified
  in the user's topic when they add a wallet.

## 9. Settings & customization

- `/setmsg welcome_message <text>` — the `/start` greeting.
- `/setmsg broadcast_confirm on|off` — broadcast preview step.
- All user-facing strings live in `branding/texts.json` — edit to rebrand.
- Bot command menus are set automatically (user commands for everyone, full
  admin menu inside the admin group).

## 10. The manual itself

- `/manual` — this document as a file, with an ⚡ Instant View button once published.
- `/manual publish` — publish/update the Telegraph version (native in-app
  article). The URL stays stable across updates; re-run after editing
  `docs/MANUAL.md`.

## 11. Operations

- **Health endpoint:** set `HEALTH_PORT=8080` → `curl host:8080/health` returns
  `{"status":"ok","users":N,"schema_version":10,"uptime_seconds":N}`.
- **Background jobs:** Stellar payment watcher (10s), expired-ban cleanup
  (5 min), scheduled broadcast dispatcher (30s). All start with the bot and
  stop cleanly on shutdown.
- **Database:** single SQLite file; migrations are versioned and idempotent —
  old databases upgrade automatically on start. Back up `DB_PATH` regularly.
- **Tests:** `pip install -r requirements-dev.txt && python -m pytest` — 328
  tests, fully offline.

## 12. Troubleshooting

| Symptom | Check |
|---|---|
| User messages don't appear | Bot admin in group? Topics enabled? Correct `ADMIN_GROUP_ID` (`-100…`)? |
| Replies don't reach the user | Are you writing **inside the user's topic** (not General)? |
| Broadcast goes nowhere | Users opted out? Check preview count; `/users active`. |
| Memo verification never confirms | `VERIFY_WALLET_PUBLIC` set? Watcher logs errors? Memo must match the code exactly. |
| "conversation closed" to a user who should be active | `/reopen` in their topic. |
| Bot won't start | All 4 required env vars set? `BOT_TOKEN` valid? |
