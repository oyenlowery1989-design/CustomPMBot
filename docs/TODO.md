# NoPMsBot — Project TODO

## v1.0 ✅ (Done — 2026-02-16)

- [x] User DMs → dedicated forum topic per user
- [x] Owner replies in topic → relayed back to user
- [x] Broadcast topic → message sent to all subscribers
- [x] `/start` welcome message
- [x] `/settings` — user opt-in/out of broadcasts
- [x] `/ban` `/unban` `/banned` — block users with reason
- [x] `/stats` — user statistics
- [x] `/help` — command list
- [x] `/setmsg` — change welcome message
- [x] Concurrent broadcast with live progress
- [x] New user logging in admin group
- [x] User info card in each topic
- [x] SQLite persistent state
- [x] systemd service (auto-start, auto-restart)

**Location:** `/opt/nopmsbot/`

---

## v2.0 ✅ (Done — 2026-02-16)

- [x] Ban with expiry time (`/ban <id> 7d reason`) + auto-unban background job
- [x] `/close` — close/archive a user's topic
- [x] `/reopen` — reopen a closed topic
- [x] `/note <text>` — pinned note in topic
- [x] Spam filter — 5 msgs/10s, 2 warnings → auto-ban 24h
- [x] Full media support (photos, videos, docs, stickers, voice, etc.) both directions
- [x] User tags — `/tag`, `/tag remove`
- [x] Multiple admins via `ADMIN_IDS`
- [x] `/export` — conversation dump (text or file)
- [x] Broadcast to tag — `@VIP` first line targets tag
- [x] Canned responses — `/canned add/list/del/<name>`
- [x] Message logging — all messages in DB with direction/type/timestamp
- [x] DB migration system (versioned, incremental, idempotent)
- [x] v1 → v2 import script

**Location:** `/opt/nopmsbot-v2/`

---

## v2.1 ✅ (Done — 2026-02-16)

### Bug Fixes
- [x] Broadcast blocked count always 0 — fixed with `raise_on_block` param
- [x] Info card fragile ternary — replaced with list builder
- [x] Tagged broadcast race condition — added asyncio.Lock
- [x] `import io` inside function — moved to top-level
- [x] No global error handler — added `error_handler`

### New Features
- [x] Blocked user tracking — auto-mark when Forbidden, auto-unmark when they return
- [x] Enhanced `/stats` — total, active, blocked, banned, subscribed, opted-out, messages in/out
- [x] `/forcebroadcast on/off` — force-enable/disable broadcasts for ALL users
- [x] Performance indexes on all tables
- [x] Comprehensive README.md documentation

---

## v3.0 — Future Ideas

### High Priority
- [ ] Web dashboard (user list, stats, ban management)
- [x] Scheduled broadcasts — `/schedule <duration> <message>` + list/cancel/history (2026-07-05)
- [ ] Inline reply preview — show user's original message when relying

### Done 2026-07-05
- [x] `/users` — list users with filters (active, blocked, banned, paused, tag)
- [x] `/search <query>` — search message logs, topic-scoped inside user topics
- [x] FEAT-001 — random colored circle icons on user + custom topics

### Medium Priority
- [ ] Auto-translate incoming messages (detect language)
- [ ] Webhook mode (lower latency vs polling)
- [ ] Custom auto-replies (keyword → response)
- [ ] Analytics — messages per day, active hours, user growth chart
- [ ] Connection pool or per-request DB (thread safety)

### Low Priority
- [ ] Multi-language welcome messages
- [ ] Prometheus metrics
- [ ] Health check endpoint
- [x] Unit tests (2026-07-04 — 234 tests in `tests/`, run: `.venv/bin/python -m pytest`)
- [x] Graceful shutdown hook (atexit close_db + post_shutdown cancels background tasks)

---

## Infrastructure Notes

- **VPS:** <vps-host> (Ubuntu 24.04)
- **DB:** SQLite at `/opt/nopmsbot-v2/state.db`
- **Library:** python-telegram-bot (20.7 in prod; tests run against 22.x)
- **Admin group ID:** <admin-group-id>
- **Owner ID:** <owner-id>
- **Schema version:** 8
- ⚠️ Bot token needs rotation (was shared in chat)
