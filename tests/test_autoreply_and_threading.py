"""Tests for keyword auto-replies and reply threading (inline reply preview)."""
from types import SimpleNamespace

from database.autoreplies import (
    db_autoreply_set, db_autoreply_delete, db_autoreply_list, db_autoreply_match,
)
from database.messages import db_map_message, db_get_mapped_user_msg, db_export_messages
from database.users import db_upsert_user
from handlers.autoreply import cmd_autoreply
from handlers.relay import handle_private_message, handle_admin_group_message
from tests.conftest import (
    ADMIN_GROUP_ID, make_bot, make_context, make_message, make_tg_user, make_update,
)

ADMIN_ID = 1000


def admin_update(thread_id=None):
    return make_update(user=make_tg_user(ADMIN_ID, "Admin"),
                       message=make_message("/autoreply", thread_id=thread_id),
                       chat_type="group")


class TestAutoreplyDb:
    def test_set_get_lowercases(self):
        db_autoreply_set("HOURS", "We're open 9-5 UTC")
        rows = db_autoreply_list()
        assert len(rows) == 1
        assert rows[0]["keyword"] == "hours"

    def test_delete(self):
        db_autoreply_set("x", "y")
        assert db_autoreply_delete("X") is True
        assert db_autoreply_delete("x") is False

    def test_match_whole_word_case_insensitive(self):
        db_autoreply_set("refund", "Refund policy: ...")
        assert db_autoreply_match("I want a REFUND now") == ("refund", "Refund policy: ...")
        assert db_autoreply_match("refundable items") is None  # not whole word
        assert db_autoreply_match("no keywords here") is None

    def test_match_multiword_keyword(self):
        db_autoreply_set("reset password", "Use /start then Settings")
        assert db_autoreply_match("how do I reset password?") is not None

    def test_regex_special_chars_safe(self):
        db_autoreply_set("c++", "We don't support C++")
        # must not raise on regex metacharacters
        assert db_autoreply_match("anything at all") is None


class TestAutoreplyCommand:
    async def test_add_del_list(self, bot):
        update = admin_update()
        await cmd_autoreply(update, make_context(bot, args=["add", "hours", "Open", "9-5"]))
        assert db_autoreply_list()[0]["response"] == "Open 9-5"

        await cmd_autoreply(update, make_context(bot, args=["list"]))
        listing = update.message.reply_text.await_args.args[0]
        assert "hours" in listing

        await cmd_autoreply(update, make_context(bot, args=["del", "hours"]))
        assert db_autoreply_list() == []

    async def test_add_missing_args_usage(self, bot):
        update = admin_update()
        await cmd_autoreply(update, make_context(bot, args=["add", "hours"]))
        assert "Usage" in update.message.reply_text.await_args.args[0]

    async def test_non_admin_ignored(self, bot):
        update = make_update(user=make_tg_user(666), message=make_message(),
                             chat_type="group")
        await cmd_autoreply(update, make_context(bot, args=["add", "x", "y"]))
        update.message.reply_text.assert_not_awaited()
        assert db_autoreply_list() == []


class TestAutoreplyInRelay:
    async def test_keyword_triggers_reply_and_admin_note(self, bot, tg_user):
        db_autoreply_set("refund", "Refunds within 14 days.")
        msg = make_message("Can I get a refund?")
        await handle_private_message(make_update(user=tg_user, message=msg), make_context(bot))
        # still forwarded to admins first
        msg.forward.assert_awaited_once()
        # auto-reply sent to user
        msg.reply_text.assert_awaited_once_with("Refunds within 14 days.")
        # logged as outgoing
        logged = db_export_messages(tg_user.id)
        assert logged[0]["direction"] == "out"
        assert logged[0]["text"] == "Refunds within 14 days."
        # admins notified in topic
        notes = [c for c in bot.send_message.await_args_list
                 if "Auto-replied" in c.kwargs.get("text", "")]
        assert len(notes) == 1

    async def test_no_keyword_no_reply(self, bot, tg_user):
        db_autoreply_set("refund", "...")
        msg = make_message("hello there")
        await handle_private_message(make_update(user=tg_user, message=msg), make_context(bot))
        msg.reply_text.assert_not_awaited()


class TestMessageMapDb:
    def test_map_roundtrip(self):
        db_map_message(user_id=1, user_msg_id=10, topic_msg_id=500)
        assert db_get_mapped_user_msg(500) == 10
        assert db_get_mapped_user_msg(999) is None

    def test_remap_overwrites(self):
        db_map_message(1, 10, 500)
        db_map_message(1, 11, 500)
        assert db_get_mapped_user_msg(500) == 11


class TestReplyThreading:
    async def test_forward_creates_mapping(self, bot, tg_user):
        msg = make_message("original question", message_id=123)
        await handle_private_message(make_update(user=tg_user, message=msg), make_context(bot))
        assert db_get_mapped_user_msg(8800) == 123  # fake forward returns id 8800

    async def test_admin_reply_quotes_original(self, bot, tg_user):
        db_upsert_user(tg_user, topic_id=55)
        db_map_message(tg_user.id, user_msg_id=123, topic_msg_id=8800)
        admin = make_tg_user(ADMIN_ID)
        reply = make_message("here's your answer", thread_id=55,
                             reply_to_message=SimpleNamespace(message_id=8800))
        await handle_admin_group_message(
            make_update(user=admin, message=reply, chat_type="group"), make_context(bot))
        sends = [c for c in bot.send_message.await_args_list
                 if c.kwargs.get("chat_id") == tg_user.id]
        assert len(sends) == 1
        assert sends[0].kwargs["reply_to_message_id"] == 123
        assert sends[0].kwargs["allow_sending_without_reply"] is True

    async def test_reply_to_unmapped_message_sends_plain(self, bot, tg_user):
        """Replying to the info card or a bot note — no mapping — plain send."""
        db_upsert_user(tg_user, topic_id=55)
        admin = make_tg_user(ADMIN_ID)
        reply = make_message("answer", thread_id=55,
                             reply_to_message=SimpleNamespace(message_id=1))
        await handle_admin_group_message(
            make_update(user=admin, message=reply, chat_type="group"), make_context(bot))
        sends = [c for c in bot.send_message.await_args_list
                 if c.kwargs.get("chat_id") == tg_user.id]
        assert len(sends) == 1
        assert "reply_to_message_id" not in sends[0].kwargs

    async def test_non_reply_message_sends_plain(self, bot, tg_user):
        db_upsert_user(tg_user, topic_id=55)
        admin = make_tg_user(ADMIN_ID)
        msg = make_message("plain answer", thread_id=55)
        await handle_admin_group_message(
            make_update(user=admin, message=msg, chat_type="group"), make_context(bot))
        sends = [c for c in bot.send_message.await_args_list
                 if c.kwargs.get("chat_id") == tg_user.id]
        assert "reply_to_message_id" not in sends[0].kwargs
