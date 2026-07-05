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
- [ ] **AI-drafted replies with human approval** — see design below
- [ ] Web dashboard (user list, stats, ban management)
- [x] Scheduled broadcasts — `/schedule <duration> <message>` + list/cancel/history (2026-07-05)
- [x] Inline reply preview — admin reply to a forwarded message quotes the user's original (message_map, v9)

### Done 2026-07-05
- [x] `/users` — list users with filters (active, blocked, banned, paused, tag)
- [x] `/search <query>` — search message logs, topic-scoped inside user topics
- [x] FEAT-001 — random colored circle icons on user + custom topics
- [x] Custom auto-replies — `/autoreply add/del/list`, whole-word keyword match, admin notified when fired
- [x] Canned media (ISSUE-007) — reply to media with `/canned add <name> [caption]` (v10)
- [x] Broadcast preview/confirm — default ON; `@TAG` targeting restored (was dead since modular rewrite)
- [x] `/analytics [days]` — messages/day, new users/day, top users, busiest hours

### Medium Priority
- [ ] Auto-translate incoming messages (detect language)
- [ ] Webhook mode (lower latency vs polling)
- [x] Custom auto-replies (keyword → response) (2026-07-05)
- [x] Analytics — `/analytics [days]`: messages/day, new users, top users, busiest hours (2026-07-05)
- [ ] Connection pool or per-request DB (thread safety)

### Low Priority
- [ ] Multi-language welcome messages
- [ ] Prometheus metrics
- [ ] Health check endpoint
- [x] Unit tests (2026-07-04 — 234 tests in `tests/`, run: `.venv/bin/python -m pytest`)
- [x] Graceful shutdown hook (atexit close_db + post_shutdown cancels background tasks)

---

## Design: AI-Drafted Replies (Human-Approved)

**Goal:** when a user messages, an AI drafts the answer; admins only review and
approve. The AI NEVER sends anything to a user directly — every outgoing
message passes a human.

**Flow:**
1. User message arrives → relayed to their topic as today.
2. Bot calls the Claude API with:
   - admin-written guidelines (what to answer, tone, what to refuse/escalate)
   - the user's recent conversation history (from the `messages` table)
   - existing canned responses + auto-reply keywords as a knowledge base
3. AI returns either a draft reply or `ESCALATE` (out of scope per guidelines).
4. Draft is posted **into the user's topic only** (user sees nothing) with buttons:
   - ✅ **Send** — relays the draft to the user, logs it as `out`
   - ✏️ **Edit** — admin replies to the draft message with corrected text; that gets sent instead (and the pair draft→correction is stored as a future few-shot example)
   - ❌ **Dismiss** — draft discarded, admin answers manually
5. If `ESCALATE` or the API fails → normal manual workflow, silently.

**Teaching the AI:**
- `/ai guidelines <text>` — persistent system instructions (stored in `settings`)
- `/ai rules` — hard refusal list ("never discuss refunds over $100, never make legal claims, never promise dates")
- Approved and edited drafts accumulate as examples: the last N approved pairs are injected into the prompt so the AI converges on the admin's style.
- `/ai on|off` — global toggle; per-topic mute possible later.

**Schema (future migration):** `ai_drafts(id, user_id, draft, status[pending|sent|edited|dismissed], created_at)` — status log doubles as training data.

**Config:** `ANTHROPIC_API_KEY` env var; model `claude-haiku-4-5` for cost, upgradeable to Sonnet. Rough cost: a support reply ≈ 1-2k tokens — fractions of a cent per draft.

**Safety invariants:** no auto-send ever; drafts live only in the admin group; API errors degrade to manual mode; user PII already in the DB, nothing new leaves except what's sent to the Claude API for drafting.

---

## Infrastructure Notes

- **VPS:** <vps-host> (Ubuntu 24.04)
- **DB:** SQLite at `/opt/nopmsbot-v2/state.db`
- **Library:** python-telegram-bot (20.7 in prod; tests run against 22.x)
- **Admin group ID:** <admin-group-id>
- **Owner ID:** <owner-id>
- **Schema version:** 10
- ⚠️ Bot token needs rotation (was shared in chat)
