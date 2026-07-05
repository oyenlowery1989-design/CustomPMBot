"""Tests for admin-side commands: stats, ban/unban, setmsg, forcebroadcast,
topic close/reopen/note, tags, export, canned."""
from unittest.mock import AsyncMock

from telegram.error import TelegramError

from handlers.admin import (
    cmd_stats, cmd_ban, cmd_unban, cmd_banned, cmd_setmsg, cmd_forcebroadcast,
    cmd_users, cmd_search,
)
from handlers.topics import cmd_topic, cmd_close, cmd_reopen, cmd_note
from handlers.tags import cmd_tag
from handlers.export import cmd_export
from handlers.canned import cmd_canned
from database.bans import db_ban, db_is_banned
from database.canned import db_canned_get, db_canned_set
from database.messages import db_log_message, db_export_messages
from database.settings import db_get_setting
from database.tags import db_add_tag, db_get_tags
from database.topics import db_get_custom_topic, db_list_custom_topics
from database.users import db_upsert_user, db_get_user, db_get_all_subscribers
from tests.conftest import make_bot, make_context, make_message, make_tg_user, make_update

ADMIN_ID = 1000
USER_ID = 500


def admin_update(text="/cmd", thread_id=None):
    return make_update(user=make_tg_user(ADMIN_ID, "Admin"),
                       message=make_message(text, thread_id=thread_id),
                       chat_type="group")


def rando_update(text="/cmd", thread_id=None):
    return make_update(user=make_tg_user(666, "Rando"),
                       message=make_message(text, thread_id=thread_id),
                       chat_type="group")


class TestStats:
    async def test_non_admin_ignored(self, bot):
        update = rando_update()
        await cmd_stats(update, make_context(bot))
        update.message.reply_text.assert_not_awaited()

    async def test_stats_output(self, bot):
        db_upsert_user(make_tg_user(1))
        db_log_message(1, "in", "text", "x")
        update = admin_update()
        await cmd_stats(update, make_context(bot))
        text = update.message.reply_text.await_args.args[0]
        assert "Total Users: 1" in text
        assert "Messages: 1 (1 📥 / 0 📤)" in text


class TestBan:
    async def test_ban_by_id_with_reason(self, bot):
        update = admin_update()
        await cmd_ban(update, make_context(bot, args=[str(USER_ID), "being", "rude"]))
        assert db_is_banned(USER_ID)
        # user notified with appeal button
        notif = [c for c in bot.send_message.await_args_list
                 if c.kwargs.get("chat_id") == USER_ID]
        assert len(notif) == 1
        assert "being rude" in notif[0].kwargs["text"]
        assert notif[0].kwargs["reply_markup"] is not None

    async def test_ban_inside_topic_without_id(self, bot):
        db_upsert_user(make_tg_user(USER_ID), topic_id=55)
        update = admin_update(thread_id=55)
        await cmd_ban(update, make_context(bot, args=["spamming"]))
        assert db_is_banned(USER_ID)

    async def test_ban_without_target_shows_usage(self, bot):
        update = admin_update()
        await cmd_ban(update, make_context(bot))
        assert "Usage" in update.message.reply_text.await_args.args[0]
        assert not db_is_banned(USER_ID)

    async def test_non_admin_cannot_ban(self, bot):
        update = rando_update()
        await cmd_ban(update, make_context(bot, args=[str(USER_ID)]))
        assert not db_is_banned(USER_ID)


class TestUnban:
    async def test_unban_by_id(self, bot):
        db_ban(USER_ID, "x")
        update = admin_update()
        await cmd_unban(update, make_context(bot, args=[str(USER_ID)]))
        assert not db_is_banned(USER_ID)

    async def test_unban_inside_topic(self, bot):
        db_upsert_user(make_tg_user(USER_ID), topic_id=55)
        db_ban(USER_ID, "x")
        update = admin_update(thread_id=55)
        await cmd_unban(update, make_context(bot))
        assert not db_is_banned(USER_ID)

    async def test_unban_not_banned(self, bot):
        update = admin_update()
        await cmd_unban(update, make_context(bot, args=[str(USER_ID)]))
        assert "not found" in update.message.reply_text.await_args.args[0].lower()


class TestBannedList:
    async def test_empty(self, bot):
        update = admin_update()
        await cmd_banned(update, make_context(bot))
        assert "No active bans" in update.message.reply_text.await_args.args[0]

    async def test_lists_bans(self, bot):
        db_upsert_user(make_tg_user(USER_ID, "Bob", username="bob"))
        db_ban(USER_ID, "flood")
        update = admin_update()
        await cmd_banned(update, make_context(bot))
        text = update.message.reply_text.await_args.args[0]
        assert str(USER_ID) in text
        assert "flood" in text


class TestSetMsg:
    async def test_sets_setting(self, bot):
        update = admin_update()
        await cmd_setmsg(update, make_context(bot, args=["welcome_message", "Hi", "there!"]))
        assert db_get_setting("welcome_message") == "Hi there!"

    async def test_usage_on_missing_args(self, bot):
        update = admin_update()
        await cmd_setmsg(update, make_context(bot, args=["welcome_message"]))
        assert "Usage" in update.message.reply_text.await_args.args[0]


class TestForceBroadcast:
    async def test_on_off(self, bot):
        for uid in (1, 2):
            db_upsert_user(make_tg_user(uid))
        from database.users import db_set_broadcast_opt
        db_set_broadcast_opt(1, False)
        db_set_broadcast_opt(2, False)

        await cmd_forcebroadcast(admin_update(), make_context(bot, args=["on"]))
        assert len(db_get_all_subscribers()) == 2
        await cmd_forcebroadcast(admin_update(), make_context(bot, args=["off"]))
        assert len(db_get_all_subscribers()) == 0


class TestTopicCommands:
    async def test_create_custom_topic(self, bot):
        update = admin_update()
        await cmd_topic(update, make_context(bot, args=["create", "VIP", "Chat"]))
        bot.create_forum_topic.assert_awaited_once()
        assert db_get_custom_topic("vip chat")["topic_id"] == 777

    async def test_create_uses_colored_icon(self, bot):
        from telegram.constants import ForumIconColor
        update = admin_update()
        await cmd_topic(update, make_context(bot, args=["create", "VIP"]))
        assert bot.create_forum_topic.await_args.kwargs["icon_color"] in set(ForumIconColor)

    async def test_create_truncates_long_name(self, bot):
        update = admin_update()
        await cmd_topic(update, make_context(bot, args=["create", "X" * 200]))
        assert len(bot.create_forum_topic.await_args.kwargs["name"]) <= 128
        replies = [c.args[0] for c in update.message.reply_text.await_args_list]
        assert any("truncated" in r for r in replies)

    async def test_list(self, bot):
        from database.topics import db_create_custom_topic
        db_create_custom_topic("logs", 10)
        update = admin_update()
        await cmd_topic(update, make_context(bot, args=["list"]))
        assert "logs" in update.message.reply_text.await_args.args[0]

    async def test_no_args_shows_usage(self, bot):
        update = admin_update()
        await cmd_topic(update, make_context(bot))
        assert "Topic Manager" in update.message.reply_text.await_args.args[0]


class TestCloseReopen:
    async def test_close_pauses_relay_and_archives(self, bot):
        db_upsert_user(make_tg_user(USER_ID, "Bob"), topic_id=55)
        update = admin_update(thread_id=55)
        await cmd_close(update, make_context(bot))
        assert db_get_user(USER_ID)["relay_paused"] == 1
        bot.close_forum_topic.assert_awaited_once()
        assert "[CLOSED]" in bot.edit_forum_topic.await_args.kwargs["name"]

    async def test_reopen_resumes_relay(self, bot):
        db_upsert_user(make_tg_user(USER_ID, "Bob"), topic_id=55)
        from database.users import db_set_relay_paused
        db_set_relay_paused(USER_ID, True)
        update = admin_update(thread_id=55)
        await cmd_reopen(update, make_context(bot))
        assert db_get_user(USER_ID)["relay_paused"] == 0
        bot.reopen_forum_topic.assert_awaited_once()

    async def test_close_outside_user_topic_warns(self, bot):
        update = admin_update(thread_id=999)
        await cmd_close(update, make_context(bot))
        assert "only works inside a user topic" in update.message.reply_text.await_args.args[0]
        bot.close_forum_topic.assert_not_awaited()

    async def test_close_telegram_error_reported(self, bot):
        db_upsert_user(make_tg_user(USER_ID, "Bob"), topic_id=55)
        bot.close_forum_topic = AsyncMock(side_effect=TelegramError("nope"))
        update = admin_update(thread_id=55)
        await cmd_close(update, make_context(bot))
        assert "Error" in update.message.reply_text.await_args.args[0]


class TestNote:
    async def test_note_pinned_in_topic(self, bot):
        db_upsert_user(make_tg_user(USER_ID), topic_id=55)
        update = admin_update(thread_id=55)
        await cmd_note(update, make_context(bot, args=["call", "back", "monday"]))
        sent = bot.send_message.await_args.kwargs
        assert "call back monday" in sent["text"]
        assert sent["message_thread_id"] == 55
        bot.pin_chat_message.assert_awaited_once()

    async def test_note_escapes_html(self, bot):
        db_upsert_user(make_tg_user(USER_ID), topic_id=55)
        update = admin_update(thread_id=55)
        await cmd_note(update, make_context(bot, args=["<script>"]))
        assert "&lt;script&gt;" in bot.send_message.await_args.kwargs["text"]

    async def test_note_without_text_shows_usage(self, bot):
        db_upsert_user(make_tg_user(USER_ID), topic_id=55)
        update = admin_update(thread_id=55)
        await cmd_note(update, make_context(bot))
        assert "Usage" in update.message.reply_text.await_args.args[0]


class TestTag:
    async def test_tag_by_id(self, bot):
        update = admin_update()
        await cmd_tag(update, make_context(bot, args=[str(USER_ID), "vip"]))
        assert db_get_tags(USER_ID) == ["VIP"]

    async def test_tag_inside_topic(self, bot):
        db_upsert_user(make_tg_user(USER_ID), topic_id=55)
        update = admin_update(thread_id=55)
        await cmd_tag(update, make_context(bot, args=["gold"]))
        assert db_get_tags(USER_ID) == ["GOLD"]

    async def test_tag_remove(self, bot):
        db_add_tag(USER_ID, "vip")
        update = admin_update()
        await cmd_tag(update, make_context(bot, args=["remove", str(USER_ID), "vip"]))
        assert db_get_tags(USER_ID) == []

    async def test_tag_remove_missing(self, bot):
        update = admin_update()
        await cmd_tag(update, make_context(bot, args=["remove", str(USER_ID), "ghost"]))
        assert "not found" in update.message.reply_text.await_args.args[0].lower()

    async def test_list_tags_in_topic(self, bot):
        db_upsert_user(make_tg_user(USER_ID), topic_id=55)
        db_add_tag(USER_ID, "vip")
        update = admin_update(thread_id=55)
        await cmd_tag(update, make_context(bot))
        assert "VIP" in update.message.reply_text.await_args.args[0]

    async def test_usage_without_context(self, bot):
        update = admin_update()
        await cmd_tag(update, make_context(bot))
        assert "Usage" in update.message.reply_text.await_args.args[0]


class TestExport:
    async def test_no_messages(self, bot):
        update = admin_update()
        await cmd_export(update, make_context(bot, args=[str(USER_ID)]))
        assert "No messages" in update.message.reply_text.await_args.args[0]

    async def test_short_export_as_text(self, bot):
        db_upsert_user(make_tg_user(USER_ID, "Bob"))
        db_log_message(USER_ID, "in", "text", "hello")
        db_log_message(USER_ID, "out", "text", "hi back")
        update = admin_update()
        await cmd_export(update, make_context(bot, args=[str(USER_ID)]))
        text = update.message.reply_text.await_args.args[0]
        assert "hello" in text and "hi back" in text
        assert "Bob" in text
        update.message.reply_document.assert_not_awaited()

    async def test_long_export_as_document(self, bot):
        db_upsert_user(make_tg_user(USER_ID))
        for i in range(100):
            db_log_message(USER_ID, "in", "text", "A" * 90)
        update = admin_update()
        await cmd_export(update, make_context(bot, args=[str(USER_ID)]))
        update.message.reply_document.assert_awaited_once()

    async def test_export_in_topic(self, bot):
        db_upsert_user(make_tg_user(USER_ID), topic_id=55)
        db_log_message(USER_ID, "in", "text", "from topic")
        update = admin_update(thread_id=55)
        await cmd_export(update, make_context(bot))
        assert "from topic" in update.message.reply_text.await_args.args[0]

    async def test_bad_arg_shows_usage(self, bot):
        update = admin_update()
        await cmd_export(update, make_context(bot, args=["not-a-number"]))
        assert "Usage" in update.message.reply_text.await_args.args[0]


class TestCanned:
    async def test_add_and_get(self, bot):
        update = admin_update()
        await cmd_canned(update, make_context(bot, args=["add", "greet", "Hello", "friend!"]))
        assert db_canned_get("greet")["body"] == "Hello friend!"

    async def test_list(self, bot):
        db_canned_set("greet", "Hello!")
        update = admin_update()
        await cmd_canned(update, make_context(bot, args=["list"]))
        assert "greet" in update.message.reply_text.await_args.args[0]

    async def test_delete(self, bot):
        db_canned_set("greet", "Hello!")
        update = admin_update()
        await cmd_canned(update, make_context(bot, args=["del", "greet"]))
        assert db_canned_get("greet") is None

    async def test_send_in_topic(self, bot):
        db_upsert_user(make_tg_user(USER_ID), topic_id=55)
        db_canned_set("greet", "Hello friend!")
        update = admin_update(thread_id=55)
        await cmd_canned(update, make_context(bot, args=["greet"]))
        sends = [c for c in bot.send_message.await_args_list
                 if c.kwargs.get("chat_id") == USER_ID]
        assert len(sends) == 1
        assert sends[0].kwargs["text"] == "Hello friend!"
        assert db_export_messages(USER_ID)[0]["direction"] == "out"

    async def test_send_outside_topic_refused(self, bot):
        db_canned_set("greet", "Hello!")
        update = admin_update()
        await cmd_canned(update, make_context(bot, args=["greet"]))
        assert "inside a user's topic" in update.message.reply_text.await_args.args[0]

    async def test_unknown_name(self, bot):
        update = admin_update()
        await cmd_canned(update, make_context(bot, args=["nope"]))
        assert "not found" in update.message.reply_text.await_args.args[0]

    async def test_add_media_via_reply(self, bot):
        from types import SimpleNamespace
        photo = [SimpleNamespace(file_id="small"), SimpleNamespace(file_id="big")]
        update = admin_update()
        update.message.reply_to_message = make_message(text=None, photo=photo)
        await cmd_canned(update, make_context(bot, args=["add", "promo", "Check", "this!"]))
        row = db_canned_get("promo")
        assert row["content_type"] == "photo"
        assert row["file_id"] == "big"  # largest photo size
        assert row["body"] == "Check this!"

    async def test_send_media_canned_in_topic(self, bot):
        db_upsert_user(make_tg_user(USER_ID), topic_id=55)
        db_canned_set("promo", "Look!", content_type="photo", file_id="FID9")
        update = admin_update(thread_id=55)
        await cmd_canned(update, make_context(bot, args=["promo"]))
        bot.send_photo.assert_awaited_once()
        assert bot.send_photo.await_args.kwargs["chat_id"] == USER_ID
        assert bot.send_photo.await_args.kwargs["photo"] == "FID9"
        assert bot.send_photo.await_args.kwargs["caption"] == "Look!"
        assert db_export_messages(USER_ID)[0]["content_type"] == "photo"

    async def test_media_list_shows_type_icon(self, bot):
        db_canned_set("promo", "x", content_type="photo", file_id="F")
        update = admin_update()
        await cmd_canned(update, make_context(bot, args=["list"]))
        assert "🖼" in update.message.reply_text.await_args.args[0]

    async def test_sticker_canned_ignores_caption(self, bot):
        db_upsert_user(make_tg_user(USER_ID), topic_id=55)
        db_canned_set("st", "", content_type="sticker", file_id="STK")
        update = admin_update(thread_id=55)
        await cmd_canned(update, make_context(bot, args=["st"]))
        bot.send_sticker.assert_awaited_once_with(chat_id=USER_ID, sticker="STK")


class TestUsers:
    def _seed(self):
        db_upsert_user(make_tg_user(1, "Active", username="act"))
        db_upsert_user(make_tg_user(2, "Blocked"))
        db_upsert_user(make_tg_user(3, "Banned"))
        db_upsert_user(make_tg_user(4, "Paused"))
        from database.users import db_mark_blocked, db_set_relay_paused
        db_mark_blocked(2)
        db_ban(3, "x")
        db_set_relay_paused(4, True)
        db_add_tag(1, "vip")

    async def test_all_users_listed_with_flags(self, bot):
        self._seed()
        update = admin_update()
        await cmd_users(update, make_context(bot))
        text = update.message.reply_text.await_args.args[0]
        for uid in ("1", "2", "3", "4"):
            assert f"<code>{uid}</code>" in text
        assert "🚫blocked" in text
        assert "📁closed" in text

    async def test_filter_active(self, bot):
        self._seed()
        update = admin_update()
        await cmd_users(update, make_context(bot, args=["active"]))
        text = update.message.reply_text.await_args.args[0]
        assert "<code>1</code>" in text and "<code>4</code>" in text
        assert "<code>2</code>" not in text and "<code>3</code>" not in text

    async def test_filter_banned(self, bot):
        self._seed()
        update = admin_update()
        await cmd_users(update, make_context(bot, args=["banned"]))
        text = update.message.reply_text.await_args.args[0]
        assert "<code>3</code>" in text and "<code>1</code>" not in text

    async def test_filter_tag(self, bot):
        self._seed()
        update = admin_update()
        await cmd_users(update, make_context(bot, args=["tag", "vip"]))
        text = update.message.reply_text.await_args.args[0]
        assert "<code>1</code>" in text and "<code>2</code>" not in text

    async def test_names_html_escaped(self, bot):
        db_upsert_user(make_tg_user(1, "<b>Evil</b>"))
        update = admin_update()
        await cmd_users(update, make_context(bot))
        assert "&lt;b&gt;Evil&lt;/b&gt;" in update.message.reply_text.await_args.args[0]

    async def test_no_match(self, bot):
        update = admin_update()
        await cmd_users(update, make_context(bot, args=["banned"]))
        assert "No users match" in update.message.reply_text.await_args.args[0]

    async def test_bad_filter_shows_usage(self, bot):
        update = admin_update()
        await cmd_users(update, make_context(bot, args=["frobnicate"]))
        assert "Usage" in update.message.reply_text.await_args.args[0]

    async def test_non_admin_ignored(self, bot):
        update = rando_update()
        await cmd_users(update, make_context(bot))
        update.message.reply_text.assert_not_awaited()


class TestSearch:
    async def test_finds_matches_case_insensitive(self, bot):
        db_log_message(1, "in", "text", "I need a REFUND please")
        db_log_message(2, "in", "text", "unrelated")
        update = admin_update()
        await cmd_search(update, make_context(bot, args=["refund"]))
        text = update.message.reply_text.await_args.args[0]
        assert "REFUND" in text
        assert "unrelated" not in text

    async def test_scoped_to_topic_user(self, bot):
        db_upsert_user(make_tg_user(USER_ID), topic_id=55)
        db_log_message(USER_ID, "in", "text", "refund me")
        db_log_message(999, "in", "text", "refund me too")
        update = admin_update(thread_id=55)
        await cmd_search(update, make_context(bot, args=["refund"]))
        text = update.message.reply_text.await_args.args[0]
        assert f"user {USER_ID}" in text
        assert "1 hit(s)" in text

    async def test_like_wildcards_treated_literally(self, bot):
        db_log_message(1, "in", "text", "100% sure")
        db_log_message(2, "in", "text", "totally sure")
        update = admin_update()
        await cmd_search(update, make_context(bot, args=["100%"]))
        assert "1 hit(s)" in update.message.reply_text.await_args.args[0]

    async def test_snippets_html_escaped(self, bot):
        db_log_message(1, "in", "text", "look <script>alert(1)</script>")
        update = admin_update()
        await cmd_search(update, make_context(bot, args=["script"]))
        assert "&lt;script&gt;" in update.message.reply_text.await_args.args[0]

    async def test_no_match(self, bot):
        update = admin_update()
        await cmd_search(update, make_context(bot, args=["ghost"]))
        assert "No messages" in update.message.reply_text.await_args.args[0]

    async def test_no_args_usage(self, bot):
        update = admin_update()
        await cmd_search(update, make_context(bot))
        assert "Usage" in update.message.reply_text.await_args.args[0]
