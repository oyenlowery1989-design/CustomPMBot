# AI-Drafted Replies — Design Spec (v1, lean)

**Date:** 2026-07-05
**Status:** Approved for implementation
**Supersedes for v1 scope:** the "Design: AI-Drafted Replies (Human-Approved)" section in `docs/TODO.md` (that section describes the full future spec — automatic triggering, `/ai rules`, few-shot learning from edits; this doc is the smaller v1 cut actually being built now).

## Goal

Give admins an optional AI-drafted reply for any user message, reviewed and sent by a human — never auto-sent. Admin taps a button, sees a draft, and chooses Send / Edit / Dismiss.

## Scope decisions (confirmed with user)

- **Single provider: Anthropic only.** No OpenAI/Gemini adapter in v1 — `docs/RUNNING.md` §8 already documents the multi-provider intent for later; this build only wires up Option A (Anthropic).
- **Manual trigger, not automatic.** A "🤖 Draft reply" button appears on every forwarded user message in the admin topic. Relay behavior is completely unchanged otherwise — no per-message API call unless an admin opts in by tapping.
- **Escalate path kept.** The model can decline to draft (e.g. guidelines say "never discuss refunds") — surfaced as plain text, no Send/Edit buttons.
- **Cut from v1:** `/ai rules` hard-refusal list, few-shot learning from approved/edited drafts, per-topic mute. These can be added later without reshaping what's built here.

## Verified model/pricing info (checked against the claude-api skill, 2026-07-05)

`claude-haiku-4-5` is a real, current model. Pricing $1.00/$5.00 per 1M input/output tokens — matches what `docs/RUNNING.md` already states. No doc correction needed. Use the bare alias `claude-haiku-4-5` as the model string (not a dated snapshot ID).

Haiku 4.5 does **not** support the `effort` parameter (errors) — do not set `output_config.effort` or `thinking` on these calls. Plain `messages.create()` call, no thinking/effort config.

## Data model

New table, migration v12 in `database/migrations.py`:

```sql
CREATE TABLE ai_drafts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       INTEGER NOT NULL,
    topic_id      INTEGER NOT NULL,   -- admin-group message_thread_id the draft lives in
    topic_msg_id  INTEGER,            -- message_id of the draft message itself (for editing/removing buttons)
    draft_text    TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'pending',  -- pending | sent | edited | dismissed | awaiting_edit
    created_at    TEXT
);
CREATE INDEX idx_ai_drafts_topic ON ai_drafts(topic_id, status);
```

`/ai on|off`, `/ai guidelines <text>` reuse the existing `database/settings.py` key-value store — no new table for those. Setting keys: `ai_enabled` (`"on"`/`"off"`, default off/absent), `ai_guidelines` (free text, default empty).

New file `database/ai_drafts.py` (follow the exact style of `database/wallets.py` — module-level functions, `get_db()`, docstrings only where non-obvious):

- `db_create_draft(user_id, topic_id, draft_text) -> int` — inserts with `status='pending'` and `created_at=_now_iso()` (same convention as every other table, e.g. `database/wallets.py:db_add_wallet`), returns new row id via `cursor.lastrowid`.
- `db_get_draft(draft_id) -> Optional[sqlite3.Row]`
- `db_set_draft_topic_msg_id(draft_id, topic_msg_id) -> None` — called right after the draft message is actually sent, to record its own message_id.
- `db_set_draft_status(draft_id, status) -> None`
- `db_update_draft_text(draft_id, new_text) -> None` — used on edit.
- `db_get_awaiting_edit_draft(topic_id) -> Optional[sqlite3.Row]` — `SELECT * FROM ai_drafts WHERE topic_id=? AND status='awaiting_edit'` (there should only ever be one at a time per topic; if somehow more than one row matches, use the most recent).

## `services/ai_draft.py` — the Anthropic call

```python
import anthropic
from config import AI_API_KEY, AI_MODEL, log

_client = None

def _get_client():
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=AI_API_KEY)
    return _client

DRAFT_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["draft", "escalate"]},
        "text": {"type": "string", "description": "The drafted reply text if action=draft, or empty string if action=escalate"},
        "reason": {"type": "string", "description": "Why escalated, if action=escalate; empty string if action=draft"},
    },
    "required": ["action", "text", "reason"],
    "additionalProperties": False,
}

def generate_draft(guidelines: str, conversation: list[tuple[str, str]], canned_responses: list[tuple[str, str]]) -> dict:
    """
    conversation: list of (direction, text) tuples, oldest first, direction is "in"/"out" (matches messages.direction column).
    canned_responses: list of (name, body) tuples for context.

    Both database.messages.db_export_messages() and database.canned.db_canned_list()
    return List[sqlite3.Row] (newest-first for messages), NOT tuples — the caller
    (cb_ai_draft) is responsible for converting before calling this function:
        rows = list(reversed(db_export_messages(user_id, limit=10)))
        conversation = [(r["direction"], r["text"]) for r in rows]
        canned_responses = [(r["name"], r["body"]) for r in db_canned_list()]
    generate_draft() itself takes plain tuples only — it must not import or know
    about sqlite3.Row, keeping it decoupled from the DB layer for easier testing.

    Returns {"action": "draft"|"escalate", "text": str, "reason": str}.
    On ANY exception (auth, network, rate limit, bad response), returns
    {"action": "escalate", "text": "", "reason": "AI draft unavailable"} and logs the real error —
    a provider outage must never block the relay or crash a handler.
    """
```

Implementation notes for the builder:
- Use `client.messages.create(model=AI_MODEL, max_tokens=1024, system=<assembled from guidelines + canned responses>, messages=[{"role": "user", "content": <rendered conversation + "Draft a reply to the user's most recent message, or escalate.">}], output_config={"format": {"type": "json_schema", "schema": DRAFT_SCHEMA}})` — structured output guarantees a parseable shape, no free-text ESCALATE sentinel to sniff for.
- Parse the guaranteed-valid JSON from `response.content[0].text` (or the first `text`-type block) with `json.loads`.
- Wrap the whole call in `try/except Exception as e: log.error(...); return {"action": "escalate", "text": "", "reason": "AI draft unavailable"}` — matches the safety invariant already documented in `docs/TODO.md`'s AI design section ("API failure or an ESCALATE verdict silently degrades to today's manual workflow").
- `AI_API_KEY` may be unset (feature not configured) — `generate_draft` should return the same escalate-shaped dict immediately if `not AI_API_KEY`, without calling the SDK at all.

`config.py` additions:
```python
AI_API_KEY = os.environ.get("AI_API_KEY")
AI_MODEL = os.environ.get("AI_MODEL", "claude-haiku-4-5")
```

`requirements.txt` addition: `anthropic`

## `handlers/ai_reply.py` — new module

Commands (admin-only, same `_is_admin(update.effective_user.id, ADMIN_IDS)` guard used everywhere else):

- `/ai on` / `/ai off` — `db_set_setting("ai_enabled", "on"/"off")`, confirm.
- `/ai guidelines <text>` — `db_set_setting("ai_guidelines", " ".join(args))`, confirm. No args → show current guidelines.
- `/ai status` — show on/off + first ~200 chars of guidelines.

Callback handlers (`callback_data` patterns, register in `bot.py` alongside the other `CallbackQueryHandler`s):

- `cb_ai_draft` — pattern `^ai_draft_` (`ai_draft_<user_id>_<fwd_msg_id>`).
  1. `await q.answer()`
  2. Parse `user_id` from `q.data`.
  3. `guidelines = db_get_setting("ai_guidelines", "")`
  4. `conversation = db_export_messages(user_id, limit=10)` (already exists in `database/messages.py`, returns newest-first — reverse it before rendering so the model sees oldest-first).
  5. `canned = db_canned_list()` (already exists in `database/canned.py`).
  6. `result = generate_draft(guidelines, conversation, canned)`
  7. If `result["action"] == "escalate"`: reply in the topic with the reason as plain text (no buttons), done. Do not create an `ai_drafts` row.
  8. Else: `draft_id = db_create_draft(user_id, topic_id, result["text"])`, send the draft as a new message in the topic (`reply_to_message_id=<the fwd_msg_id from callback_data>` so it's visually anchored under the forwarded message) with an inline keyboard `[✅ Send (ad_s_<id>)] [✏️ Edit (ad_e_<id>)] [❌ Dismiss (ad_d_<id>)]`, then `db_set_draft_topic_msg_id(draft_id, sent_message.message_id)`.

- `cb_ai_send` — pattern `^ad_s_`.
  1. Parse `draft_id`, `draft = db_get_draft(draft_id)`.
  2. `await ctx.bot.send_message(chat_id=draft["user_id"], text=draft["draft_text"])`
  3. `db_log_message(draft["user_id"], "out", "text", draft["draft_text"])`
  4. `db_set_draft_status(draft_id, "sent")`
  5. Edit the draft message to remove buttons and show a sent indicator (`q.edit_message_text(text + "\n\n✅ Sent", reply_markup=None)`).

- `cb_ai_edit` — pattern `^ad_e_`.
  1. Parse `draft_id`, `db_set_draft_status(draft_id, "awaiting_edit")`.
  2. Edit the draft message: replace buttons with a plain "✏️ Reply in this topic with the corrected text." note (remove the keyboard so it can't be double-tapped).

- `cb_ai_dismiss` — pattern `^ad_d_`.
  1. `db_set_draft_status(draft_id, "dismissed")`, edit message to remove buttons + show "❌ Dismissed".

## Wiring into existing files

**`handlers/relay.py` — attach the button (in `handle_private_message`, right after the existing `_forward_to_topic` call and its message-map bookkeeping):**

> ⚠️ Telegram's `forwardMessage` API has no `reply_markup` field — you cannot attach a button to the forwarded copy directly. Send a small follow-up message replying to the forward instead.

```python
if fwd and db_get_setting("ai_enabled", "off") == "on":
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🤖 Draft reply", callback_data=f"ai_draft_{user.id}_{fwd.message_id}")]])
    await ctx.bot.send_message(
        chat_id=ADMIN_GROUP_ID, message_thread_id=topic_id,
        text="🤖", reply_markup=keyboard, reply_to_message_id=fwd.message_id,
    )
```

**`handlers/relay.py` — intercept edits (in `handle_admin_group_message`, BEFORE the existing broadcast-topic check and `db_get_user_by_topic` lookup):**

```python
from database.ai_drafts import db_get_awaiting_edit_draft, db_update_draft_text, db_set_draft_status

awaiting = db_get_awaiting_edit_draft(thread_id)
if awaiting:
    corrected = msg.text or msg.caption or ""
    await ctx.bot.send_message(chat_id=awaiting["user_id"], text=corrected)
    db_log_message(awaiting["user_id"], "out", "text", corrected)
    db_update_draft_text(awaiting["id"], corrected)
    db_set_draft_status(awaiting["id"], "edited")
    return
```

This must come before the broadcast-topic and normal-reply-to-forward logic, exactly like the wallet secret-key intercept in `handle_private_message` comes before the standard relay path — same "check first, fall through only if not intercepted" shape.

**`bot.py`:**
- Import and register `cmd_ai` (or split `/ai on|off|guidelines|status` as one `cmd_ai` dispatcher, matching the `/topic create|list|bind|...` subcommand style already used in `handlers/topics.py` — reuse that pattern, don't invent a new one).
- Register the four new `CallbackQueryHandler`s: `cb_ai_draft` (`^ai_draft_`), `cb_ai_send` (`^ad_s_`), `cb_ai_edit` (`^ad_e_`), `cb_ai_dismiss` (`^ad_d_`).
- Add `/ai` to the admin command list (`BotCommand("ai", "AI-drafted reply settings")`).

## Error handling / safety invariants (must hold)

- `AI_API_KEY` unset → feature silently unavailable; `/ai on` should still work (toggle just does nothing useful until a key is set) but `generate_draft` never raises.
- Any Anthropic API exception → escalate, logged, never propagates to crash a handler or block the relay path.
- Draft/edit/send/dismiss buttons must always `q.answer()` even on error (mirror the broad-except-with-logging pattern already fixed in `handlers/wallet.py:cb_wallet_remove` after the code review — these callbacks have the same "must always ack + never leave the topic in a stuck state" requirement).
- The AI feature must never be able to send anything to a user without an explicit admin tap (Send or Edit-then-implicit-send). No code path calls `ctx.bot.send_message(chat_id=<user>...)` from the AI flow except inside `cb_ai_send` and the edit-intercept in `handle_admin_group_message`.

## Testing

Mirror existing test file conventions (`tests/test_services.py` style for the AI service, `tests/test_handlers_admin.py` / `tests/test_handlers_relay.py` style for handlers). Monkeypatch `services.ai_draft._get_client` (or the module-level `anthropic.Anthropic` constructor) to return a `MagicMock` whose `.messages.create(...)` returns a canned structured response — do not hit the real Anthropic API in tests.

Cover at minimum:
- `generate_draft` returns escalate shape when `AI_API_KEY` unset (no API call attempted).
- `generate_draft` returns escalate shape on a simulated API exception.
- `generate_draft` returns the parsed draft shape on a normal successful call.
- `cb_ai_draft` creates an `ai_drafts` row and posts a message with 3 buttons on a normal draft; posts plain text with no row created on escalate.
- `cb_ai_send` relays text to the user, logs it, marks `sent`.
- `cb_ai_edit` → admin's next message in that topic is intercepted, relayed, marks `edited`; a normal admin reply in a topic with no pending edit falls through to the existing relay behavior unchanged.
- `cb_ai_dismiss` marks `dismissed`, no message sent to the user.
- `/ai on|off|guidelines|status` — value round-trips through `database/settings.py`.
- The "🤖 Draft reply" button is attached only when `ai_enabled == "on"`; absent when off (default).

Run the full existing suite (`.venv/bin/python -m pytest`) after implementation — must stay green; this feature must not regress the existing relay/wallet/broadcast tests.
