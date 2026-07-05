"""Tests for broadcast preview/confirm flow and @TAG targeting."""
import handlers.broadcast as broadcast_mod
from handlers.broadcast import (
    _parse_tag_target, _resolve_recipients, _stage_broadcast,
    cb_broadcast_confirm, cb_broadcast_cancel,
)
from handlers.relay import handle_admin_group_message
from database.settings import db_set_setting
from database.tags import db_add_tag
from database.users import db_upsert_user, db_set_broadcast_opt
from tests.conftest import (
    ADMIN_GROUP_ID, make_bot, make_callback_query, make_context, make_message,
    make_tg_user, make_update,
)

ADMIN_ID = 1000


def _admin():
    return make_tg_user(ADMIN_ID, "Admin")


class TestTagParsing:
    def test_tag_on_first_line(self):
        assert _parse_tag_target(make_message("@VIP\nhello everyone")) == "VIP"

    def test_tag_alone(self):
        assert _parse_tag_target(make_message("@gold")) == "gold"

    def test_first_line_with_spaces_is_not_tag(self):
        assert _parse_tag_target(make_message("@VIP members only\nhello")) is None

    def test_no_tag(self):
        assert _parse_tag_target(make_message("plain broadcast")) is None
        assert _parse_tag_target(make_message("@")) is None

    def test_caption_checked_for_media(self):
        msg = make_message(text=None, caption="@VIP\npic for you", photo=[object()])
        assert _parse_tag_target(msg) == "VIP"


class TestResolveRecipients:
    def test_all_with_optout_count(self):
        for uid in (1, 2, 3):
            db_upsert_user(make_tg_user(uid))
        db_set_broadcast_opt(3, False)
        recips, label, opted_out = _resolve_recipients(make_message("news"))
        assert label == "all"
        assert {r["user_id"] for r in recips} == {1, 2}
        assert opted_out == 1

    def test_tag_targets_tagged_only(self):
        for uid in (1, 2):
            db_upsert_user(make_tg_user(uid))
        db_add_tag(1, "vip")
        recips, label, opted_out = _resolve_recipients(make_message("@VIP\nhi"))
        assert label == "tag VIP"
        assert {r["user_id"] for r in recips} == {1}
        assert opted_out == 0


class TestStagedFlow:
    async def test_broadcast_topic_stages_by_default(self, bot, tg_user):
        db_set_setting("broadcast_topic_id", "900")
        db_upsert_user(tg_user)
        msg = make_message("big news", thread_id=900, message_id=42)
        await handle_admin_group_message(
            make_update(user=_admin(), message=msg, chat_type="group"), make_context(bot))
        # nothing sent to the user yet
        assert all(c.kwargs.get("chat_id") != tg_user.id
                   for c in bot.send_message.await_args_list)
        # preview with confirm/cancel buttons posted in the topic
        preview = bot.send_message.await_args_list[-1].kwargs
        assert "preview" in preview["text"].lower()
        data = {b.callback_data for row in preview["reply_markup"].inline_keyboard for b in row}
        assert data == {"bc_go_42", "bc_no_42"}
        assert 42 in broadcast_mod._pending_broadcasts

    async def test_confirm_sends_broadcast(self, bot, tg_user):
        db_set_setting("broadcast_topic_id", "900")
        db_upsert_user(tg_user)
        msg = make_message("big news", thread_id=900, message_id=42)
        await _stage_broadcast(bot, msg)

        q = make_callback_query(_admin(), data="bc_go_42")
        await cb_broadcast_confirm(make_update(user=_admin(), callback_query=q), make_context(bot))
        sends = [c for c in bot.send_message.await_args_list
                 if c.kwargs.get("chat_id") == tg_user.id]
        assert len(sends) == 1
        assert sends[0].kwargs["text"] == "big news"
        assert 42 not in broadcast_mod._pending_broadcasts

    async def test_cancel_sends_nothing(self, bot, tg_user):
        db_upsert_user(tg_user)
        msg = make_message("oops", thread_id=900, message_id=42)
        await _stage_broadcast(bot, msg)

        q = make_callback_query(_admin(), data="bc_no_42")
        await cb_broadcast_cancel(make_update(user=_admin(), callback_query=q), make_context(bot))
        assert 42 not in broadcast_mod._pending_broadcasts
        assert all(c.kwargs.get("chat_id") != tg_user.id
                   for c in bot.send_message.await_args_list)
        assert "cancelled" in q.edit_message_text.await_args.args[0].lower()

    async def test_expired_draft_reports_and_sends_nothing(self, bot, tg_user):
        db_upsert_user(tg_user)
        q = make_callback_query(_admin(), data="bc_go_999")
        await cb_broadcast_confirm(make_update(user=_admin(), callback_query=q), make_context(bot))
        assert "expired" in q.edit_message_text.await_args.args[0].lower()
        assert all(c.kwargs.get("chat_id") != tg_user.id
                   for c in bot.send_message.await_args_list)

    async def test_non_admin_cannot_confirm(self, bot, tg_user):
        db_upsert_user(tg_user)
        msg = make_message("x", thread_id=900, message_id=42)
        await _stage_broadcast(bot, msg)
        q = make_callback_query(make_tg_user(666), data="bc_go_42")
        await cb_broadcast_confirm(make_update(user=make_tg_user(666), callback_query=q),
                                   make_context(bot))
        assert 42 in broadcast_mod._pending_broadcasts  # untouched
        q.edit_message_text.assert_not_awaited()

    async def test_tag_broadcast_immediate_mode(self, bot):
        db_set_setting("broadcast_topic_id", "900")
        db_set_setting("broadcast_confirm", "off")
        db_upsert_user(make_tg_user(1))
        db_upsert_user(make_tg_user(2))
        db_add_tag(1, "vip")
        msg = make_message("@VIP\nexclusive news", thread_id=900)
        await handle_admin_group_message(
            make_update(user=_admin(), message=msg, chat_type="group"), make_context(bot))
        user_sends = [c.kwargs["chat_id"] for c in bot.send_message.await_args_list
                      if c.kwargs.get("chat_id") in (1, 2)]
        assert user_sends == [1]

    async def test_preview_warns_on_zero_recipients(self, bot):
        msg = make_message("into the void", thread_id=900, message_id=42)
        await _stage_broadcast(bot, msg)
        text = bot.send_message.await_args.kwargs["text"]
        assert "0" in text and "nobody" in text.lower()
