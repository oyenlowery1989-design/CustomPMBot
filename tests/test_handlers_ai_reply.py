"""Tests for handlers/ai_reply.py: the /ai command dispatcher and the
draft/send/edit/dismiss callback flow.

The "next admin message after Edit is intercepted" half of the edit flow
lives in handle_admin_group_message (handlers/relay.py), so that part is
covered in tests/test_handlers_relay.py alongside the rest of that
function's tests — this file only covers what's actually defined in
handlers/ai_reply.py."""
from unittest.mock import MagicMock

import handlers.ai_reply as ai_reply
from handlers.ai_reply import cmd_ai, cb_ai_draft, cb_ai_send, cb_ai_edit, cb_ai_dismiss
from database.ai_drafts import db_create_draft, db_get_draft
from database.messages import db_export_messages, db_log_message
from database.settings import db_get_setting, db_set_setting
from database.users import db_upsert_user
from tests.conftest import (
    ADMIN_GROUP_ID, make_callback_query, make_context, make_message, make_tg_user, make_update,
)

ADMIN_ID = 1000


def _admin():
    return make_tg_user(ADMIN_ID, "Admin")


def admin_update(text="/ai", thread_id=None):
    return make_update(user=_admin(), message=make_message(text, thread_id=thread_id), chat_type="group")


def rando_update(text="/ai", thread_id=None):
    return make_update(user=make_tg_user(666, "Rando"),
                       message=make_message(text, thread_id=thread_id), chat_type="group")


class TestCmdAi:
    async def test_no_args_shows_usage(self, bot):
        update = admin_update()
        await cmd_ai(update, make_context(bot))
        assert "AI Draft Replies" in update.message.reply_text.await_args.args[0]

    async def test_on_off_roundtrip_through_settings(self, bot):
        await cmd_ai(admin_update(), make_context(bot, args=["on"]))
        assert db_get_setting("ai_enabled") == "on"
        await cmd_ai(admin_update(), make_context(bot, args=["off"]))
        assert db_get_setting("ai_enabled") == "off"

    async def test_guidelines_set_then_shown(self, bot):
        await cmd_ai(admin_update(), make_context(bot, args=["guidelines", "Never", "discuss", "refunds"]))
        assert db_get_setting("ai_guidelines") == "Never discuss refunds"

        update = admin_update()
        await cmd_ai(update, make_context(bot, args=["guidelines"]))
        assert "Never discuss refunds" in update.message.reply_text.await_args.args[0]
        # no-arg call only displays current guidelines, never clears them
        assert db_get_setting("ai_guidelines") == "Never discuss refunds"

    async def test_status_reports_on_and_guidelines(self, bot):
        db_set_setting("ai_enabled", "on")
        db_set_setting("ai_guidelines", "Be concise and polite")
        update = admin_update()
        await cmd_ai(update, make_context(bot, args=["status"]))
        text = update.message.reply_text.await_args.args[0]
        assert "🟢 on" in text
        assert "Be concise and polite" in text

    async def test_status_defaults_off_with_no_guidelines(self, bot):
        update = admin_update()
        await cmd_ai(update, make_context(bot, args=["status"]))
        text = update.message.reply_text.await_args.args[0]
        assert "🔴 off" in text
        assert "(none)" in text

    async def test_non_admin_ignored(self, bot):
        update = rando_update()
        await cmd_ai(update, make_context(bot, args=["on"]))
        update.message.reply_text.assert_not_awaited()
        assert db_get_setting("ai_enabled", "off") == "off"


class TestCbAiDraft:
    async def test_creates_row_and_posts_three_buttons(self, bot, tg_user, monkeypatch):
        db_upsert_user(tg_user, topic_id=55)
        db_log_message(tg_user.id, "in", "text", "first message")
        db_log_message(tg_user.id, "out", "text", "we replied")
        fake_generate = MagicMock(return_value={
            "action": "draft", "text": "Let's sort that out for you.", "reason": "",
        })
        monkeypatch.setattr(ai_reply, "generate_draft", fake_generate)

        q = make_callback_query(_admin(), data=f"ai_draft_{tg_user.id}_900",
                                message=make_message(thread_id=55))
        await cb_ai_draft(make_update(user=_admin(), callback_query=q), make_context(bot))

        q.answer.assert_awaited_once()
        sent = bot.send_message.await_args
        assert sent.kwargs["chat_id"] == ADMIN_GROUP_ID
        assert sent.kwargs["message_thread_id"] == 55
        assert sent.kwargs["text"] == "Let's sort that out for you."
        assert sent.kwargs["reply_to_message_id"] == 900

        buttons = [b for row in sent.kwargs["reply_markup"].inline_keyboard for b in row]
        assert len(buttons) == 3
        assert {b.text for b in buttons} == {"✅ Send", "✏️ Edit", "❌ Dismiss"}

        draft_id = int(buttons[0].callback_data.replace("ad_s_", ""))
        row = db_get_draft(draft_id)
        assert row["status"] == "pending"
        assert row["draft_text"] == "Let's sort that out for you."
        # bot.send_message's canned return value (make_message()) has message_id=42;
        # cb_ai_draft must persist it as the draft's own topic_msg_id.
        assert row["topic_msg_id"] == 42

        # generate_draft must see the conversation oldest-first, as plain tuples
        conversation_arg = fake_generate.call_args.args[1]
        assert conversation_arg == [("in", "first message"), ("out", "we replied")]

    async def test_escalate_posts_plain_text_and_creates_no_row(self, bot, tg_user, monkeypatch):
        db_upsert_user(tg_user, topic_id=55)
        monkeypatch.setattr(ai_reply, "generate_draft", MagicMock(return_value={
            "action": "escalate", "text": "", "reason": "Guidelines say never discuss refunds",
        }))

        q = make_callback_query(_admin(), data=f"ai_draft_{tg_user.id}_900",
                                message=make_message(thread_id=55))
        await cb_ai_draft(make_update(user=_admin(), callback_query=q), make_context(bot))

        sent = bot.send_message.await_args
        assert "Guidelines say never discuss refunds" in sent.kwargs["text"]
        assert "reply_markup" not in sent.kwargs
        assert db_get_draft(1) is None  # no ai_drafts row ever created


class TestCbAiSend:
    async def test_send_relays_logs_and_marks_sent(self, bot, tg_user):
        draft_id = db_create_draft(tg_user.id, 55, "Sure, here's the fix.")
        q = make_callback_query(_admin(), data=f"ad_s_{draft_id}")
        await cb_ai_send(make_update(user=_admin(), callback_query=q), make_context(bot))

        sends = [c for c in bot.send_message.await_args_list if c.kwargs.get("chat_id") == tg_user.id]
        assert len(sends) == 1
        assert sends[0].kwargs["text"] == "Sure, here's the fix."

        logged = db_export_messages(tg_user.id)
        assert logged[0]["direction"] == "out"
        assert logged[0]["text"] == "Sure, here's the fix."

        assert db_get_draft(draft_id)["status"] == "sent"
        q.answer.assert_awaited_with("Sent")
        q.edit_message_text.assert_awaited_once_with("Sure, here's the fix.\n\n✅ Sent", reply_markup=None)

    async def test_missing_draft_answers_error_and_sends_nothing(self, bot, tg_user):
        q = make_callback_query(_admin(), data="ad_s_999")
        await cb_ai_send(make_update(user=_admin(), callback_query=q), make_context(bot))
        q.answer.assert_awaited_with("Draft not found")
        assert all(c.kwargs.get("chat_id") != tg_user.id for c in bot.send_message.await_args_list)


class TestCbAiEdit:
    async def test_marks_awaiting_edit_and_strips_buttons(self, bot, tg_user):
        draft_id = db_create_draft(tg_user.id, 55, "Draft text")
        q = make_callback_query(_admin(), data=f"ad_e_{draft_id}")
        await cb_ai_edit(make_update(user=_admin(), callback_query=q), make_context(bot))

        assert db_get_draft(draft_id)["status"] == "awaiting_edit"
        q.answer.assert_awaited_once()
        q.edit_message_text.assert_awaited_once_with(
            "✏️ Reply in this topic with the corrected text.", reply_markup=None)


class TestCbAiDismiss:
    async def test_marks_dismissed_and_sends_nothing_to_user(self, bot, tg_user):
        draft_id = db_create_draft(tg_user.id, 55, "Draft text")
        q = make_callback_query(_admin(), data=f"ad_d_{draft_id}")
        await cb_ai_dismiss(make_update(user=_admin(), callback_query=q), make_context(bot))

        assert db_get_draft(draft_id)["status"] == "dismissed"
        assert bot.send_message.await_args_list == []
        q.edit_message_text.assert_awaited_once_with("❌ Dismissed", reply_markup=None)
