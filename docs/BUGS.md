# NoPMsBot — Known Bugs & Issues

**Last updated:** 2026-07-04

---

## 🐛 Active Bugs

### BUG-001: User messages not appearing in group topic
- **Status:** ✅ Fixed
- **Severity:** Critical
- **Description:** When a user writes to the bot, the message doesn't show up in the admin forum group topic. Admin replies from the topic DO reach the user.
- **Root cause:** `sqlite3.Row` object has no `.get()` method. Error occurs in `handle_private_message` at the line `if u and u.get("blocked"):` — `sqlite3.Row` uses `u["blocked"]` syntax, not `.get()`.
- **Error log:** `AttributeError: 'sqlite3.Row' object has no attribute 'get'`
- **Fix:** Change `u.get("blocked")` to `u["blocked"]` (or wrap in try/except) in `handle_private_message` around line 1766.

---

## ⚠️ Potential Issues (Not Yet Confirmed)

### ISSUE-002: Broadcast topic may duplicate on restart
- **Status:** ✅ Fixed (2026-07-04)
- **Description:** If the broadcast topic setting gets lost, a new one is created. Old one stays orphaned.
- **Fix:** `_find_broadcast_topic` now probes the stored topic id with a chat action; if the topic was deleted it recreates and re-stores. All failure paths logged (no more bare `except`).

### ISSUE-003: Topic creation may fail silently for long names
- **Status:** ✅ Fixed (2026-07-04)
- **Description:** Telegram topic names are limited to 128 chars. Name is truncated but not logged.
- **Fix:** Truncation now logged in `_ensure_topic`; `/topic create` truncates and notifies the admin.

### BUG-009: /close never actually paused relay
- **Status:** ✅ Fixed (2026-07-04)
- **Severity:** Medium
- **Description:** `/close` set `blocked=1` to pause relay, but `handle_private_message` auto-clears `blocked` on any incoming message (that flag tracks "user blocked the bot"). So the first user message after `/close` un-paused itself and relayed into the archived topic.
- **Fix:** New `relay_paused` column (migration v7). `/close`/`/reopen` toggle it; relay drops paused users' messages with a "conversation closed" notice (`relay.closed` text key). `blocked` is now exclusively Forbidden-tracking.

### BUG-010: Fresh start crashed — base schema never created
- **Status:** ✅ Fixed (2026-07-04)
- **Severity:** Critical
- **Description:** Migration v0→v1 only logged; no CREATE TABLE for users/messages/tags/bans/settings/canned existed anywhere in the modular codebase. A fresh DB crashed at v2 (`no such table: messages`). Only worked in production because the DB was imported from v1.
- **Fix:** Base schema DDL added to migration v1 (ported from NSALF variant). Verified: fresh v0→v7 run and v6→v7 upgrade both pass, existing data preserved.

### ISSUE-004: DB connection thread safety
- **Description:** Single global SQLite connection with `check_same_thread=False`. Could cause issues under heavy async load.
- **Severity:** Medium (not observed yet)

### ISSUE-005: Owner messages forwarded to admin group
- **Description:** If the owner sends DMs to the bot, they get treated as a regular user (topic created, messages forwarded). v1 skipped owner messages.
- **Severity:** Low

### ISSUE-006: _send_user_info_card is defined but never called
- **Status:** ✅ N/A — function exists only in `legacy_monolith/bot.py`, was never ported to the modular codebase. No action needed.
- **Severity:** None

### ISSUE-007: `/canned <name>` doesn't support media
- **Status:** ✅ Fixed (2026-07-05)
- **Description:** Canned responses are text-only. Can't save/send photos or media as canned.
- **Fix:** Reply to any media message with `/canned add <name> [caption]` — file_id + type stored (schema v10), replayed on send.

### ISSUE-008: Graceful shutdown
- **Description:** DB connection never explicitly closed on shutdown.
- **Severity:** Low

---

## 💡 Improvement Ideas (For Future Versions)

### UI/UX
- Add timestamp to stats output (done in v2.1) ✅
- Custom topic system (done in v2.1) ✅
- Auto-create default topics on first run (stats, logs)
- `/users` command — list all users with filters (active, banned, tagged) ✅ (2026-07-05)
- `/search <query>` — search messages by text ✅ (2026-07-05)

### Broadcasting
- Scheduled broadcasts — `/schedule <duration> <message>` ✅ (2026-07-05)
- Broadcast preview — confirm button before sending, on by default (`/setmsg broadcast_confirm off` to disable) ✅ (2026-07-05)
- Broadcast cancel — `/schedule cancel <id>` for scheduled ones ✅ (2026-07-05)
- Broadcast history — `/schedule list` shows recently sent ✅ (2026-07-05)

### Administration
- Web dashboard
- Multiple bot token support (manage several bots)
- Webhook mode (lower latency)
- Auto-backup DB on schedule

### Technical
- Connection pool / per-request DB connections
- Unit tests
- Rate limit on Telegram API calls (respect flood limits)
- Prometheus metrics endpoint
- Health check endpoint

---

## 📝 Fix Log

| Date | Issue | Fix |
|---|---|---|
| 2026-02-16 | Broadcast blocked count always 0 | Added `raise_on_block` param to `_relay_to_user` |
| 2026-02-16 | Info card fragile ternary | Replaced with list builder |
| 2026-02-16 | Tagged broadcast race condition | Added `asyncio.Lock` to counters |
| 2026-02-16 | No global error handler | Added `error_handler` |
| 2026-02-16 | `import io` inside function | Moved to top-level |
| 2026-02-27 | DB thread safety (ISSUE-004) | Added `threading.Lock` to singleton init + `close_db()` |
| 2026-02-27 | Memo watcher stub (ISSUE-002 related) | Implemented full payment+tx fetch logic with expiry checks |
| 2026-02-27 | Owner DMs relayed (ISSUE-005) | Added ADMIN_IDS guard at top of `handle_private_message` |
| 2026-02-27 | No graceful shutdown (ISSUE-008) | Registered `close_db` via `atexit` in `bot.py` |
| 2026-02-27 | Dead code ISSUE-006 | Confirmed N/A — only in legacy monolith, not in modular codebase |
| 2026-07-04 | Missing base schema (BUG-010) | v1 migration now creates all base tables |
| 2026-07-04 | /close relay-pause conflict (BUG-009) | `relay_paused` column (v7), `blocked` reserved for Forbidden tracking |
| 2026-07-04 | Broadcast topic orphan (ISSUE-002) | Stored id validated at startup, recreated if dead, logged |
| 2026-07-04 | Silent topic-name truncation (ISSUE-003) | Logged in relay + admin notified in /topic create |
| 2026-07-04 | Secret key left in chat on failed verify | Message deleted regardless of verify outcome |
| 2026-07-04 | Python 3.9 incompat in connection.py | `from __future__ import annotations` added |
| 2026-07-04 | StellarWatcher never started — memo verification dead | Started as background task in `post_init`, stopped in `post_shutdown` |
| 2026-07-04 | Expired bans only cleaned lazily — kept excluding users from broadcasts | `cleanup_expired_bans()` + 5-min background loop in bot.py |
| 2026-07-04 | Zero test coverage | 234-test pytest suite in `tests/` covering every feature (DB, services, utils, all handlers) |
| 2026-07-05 | Tagged broadcasts (`@TAG` first line) lost in modular rewrite — `db_get_subscribers_by_tag` imported but never called | `_parse_tag_target`/`_resolve_recipients` restored in broadcast path |
| 2026-07-05 | Canned media (ISSUE-007) | `content_type`/`file_id` columns (v10), reply-to-media `/canned add` |

---

## 🆕 Feature Requests (2026-02-16)

### FEAT-001: Topic icons — colored circles like MillionPlus bot
- **Status:** ✅ Done (2026-07-05) — random `ForumIconColor` passed to `create_forum_topic` for user and custom topics
- **Description:** Topics should have colored circle icons (like the MillionPlus Support bot screenshot). Currently small squares.
- **Solution:** Use `icon_custom_emoji_id` when creating topics. Bot can assign random colored emoji icons per user. Also the group itself needs "Topics" mode enabled with proper settings.

### FEAT-002: Unread message counter on topics
- **Status:** Open  
- **Description:** When a user messages, the topic should show unread count badge.
- **Note:** This is automatic Telegram behavior IF the admin hasn't opened the topic yet. May be a group notification settings issue. Check: Group → Notifications → ensure not muted.

### FEAT-003: Stellar wallet button + address storage
- **Status:** In Progress
- **Description:** Add "Add Stellar Wallet" button. When pressed, bot asks for public address. Store user_id ↔ wallet mapping. Future: query XLM balance and custom assets.

### FEAT-004: Bot similar to MillionPlus Support style
- **Description:** Make the bot behave more like the MillionPlus Support (PM) bot shown in the screenshot — professional support desk style with user topics, message previews, timestamps.
