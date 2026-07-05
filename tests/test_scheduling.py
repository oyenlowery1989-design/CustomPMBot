"""Tests for scheduled broadcasts: DB layer, /schedule command, due-processing."""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

from telegram.error import Forbidden

from database.broadcasts import (
    db_schedule_broadcast, db_get_due_broadcasts, db_list_pending_broadcasts,
    db_get_sent_broadcasts, db_cancel_scheduled, db_mark_broadcast_sent,
)
from database.settings import db_set_setting
from database.users import db_upsert_user, db_get_user
from handlers.broadcast import cmd_schedule, process_due_broadcasts, _broadcast_text
from tests.conftest import make_bot, make_context, make_message, make_tg_user, make_update

ADMIN_ID = 1000


def _at(minutes: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()


def admin_update(thread_id=None):
    return make_update(user=make_tg_user(ADMIN_ID, "Admin"),
                       message=make_message("/schedule", thread_id=thread_id),
                       chat_type="group")


class TestScheduleDb:
    def test_schedule_and_list_pending(self):
        bid = db_schedule_broadcast("hello", _at(10), ADMIN_ID)
        pending = db_list_pending_broadcasts()
        assert len(pending) == 1
        assert pending[0]["id"] == bid
        assert pending[0]["text"] == "hello"
        assert pending[0]["sent"] == 0

    def test_due_only_returns_past_unsent(self):
        db_schedule_broadcast("past", _at(-5), ADMIN_ID)
        db_schedule_broadcast("future", _at(5), ADMIN_ID)
        due = db_get_due_broadcasts()
        assert [b["text"] for b in due] == ["past"]

    def test_mark_sent_removes_from_due_and_pending(self):
        bid = db_schedule_broadcast("x", _at(-1), ADMIN_ID)
        db_mark_broadcast_sent(bid)
        assert db_get_due_broadcasts() == []
        assert db_list_pending_broadcasts() == []
        sent = db_get_sent_broadcasts()
        assert len(sent) == 1
        assert sent[0]["sent_at"] is not None

    def test_cancel_pending(self):
        bid = db_schedule_broadcast("x", _at(10), ADMIN_ID)
        assert db_cancel_scheduled(bid) is True
        assert db_list_pending_broadcasts() == []

    def test_cannot_cancel_sent(self):
        bid = db_schedule_broadcast("x", _at(-1), ADMIN_ID)
        db_mark_broadcast_sent(bid)
        assert db_cancel_scheduled(bid) is False
        assert len(db_get_sent_broadcasts()) == 1  # history kept


class TestScheduleCommand:
    async def test_schedule_with_duration(self, bot):
        update = admin_update()
        await cmd_schedule(update, make_context(bot, args=["2h", "Big", "news!"]))
        pending = db_list_pending_broadcasts()
        assert len(pending) == 1
        assert pending[0]["text"] == "Big news!"
        run_at = datetime.fromisoformat(pending[0]["run_at"])
        delta = run_at - datetime.now(timezone.utc)
        assert timedelta(hours=1, minutes=59) < delta < timedelta(hours=2, minutes=1)
        assert "#1" in update.message.reply_text.await_args.args[0]

    async def test_bad_duration_shows_usage(self, bot):
        update = admin_update()
        await cmd_schedule(update, make_context(bot, args=["soon", "hi"]))
        assert "Usage" in update.message.reply_text.await_args.args[0]
        assert db_list_pending_broadcasts() == []

    async def test_missing_text_shows_usage(self, bot):
        update = admin_update()
        await cmd_schedule(update, make_context(bot, args=["2h"]))
        assert "Usage" in update.message.reply_text.await_args.args[0]

    async def test_no_args_shows_usage(self, bot):
        update = admin_update()
        await cmd_schedule(update, make_context(bot))
        assert "Usage" in update.message.reply_text.await_args.args[0]

    async def test_list_shows_pending_and_sent(self, bot):
        db_schedule_broadcast("upcoming thing", _at(10), ADMIN_ID)
        old = db_schedule_broadcast("already out", _at(-10), ADMIN_ID)
        db_mark_broadcast_sent(old)
        update = admin_update()
        await cmd_schedule(update, make_context(bot, args=["list"]))
        text = update.message.reply_text.await_args.args[0]
        assert "upcoming thing" in text
        assert "Recently sent" in text

    async def test_cancel_via_command(self, bot):
        bid = db_schedule_broadcast("x", _at(10), ADMIN_ID)
        update = admin_update()
        await cmd_schedule(update, make_context(bot, args=["cancel", str(bid)]))
        assert db_list_pending_broadcasts() == []

    async def test_cancel_bad_id(self, bot):
        update = admin_update()
        await cmd_schedule(update, make_context(bot, args=["cancel", "abc"]))
        assert "Usage" in update.message.reply_text.await_args.args[0]

    async def test_non_admin_ignored(self, bot):
        update = make_update(user=make_tg_user(666), message=make_message("/schedule"),
                             chat_type="group")
        await cmd_schedule(update, make_context(bot, args=["2h", "spam"]))
        update.message.reply_text.assert_not_awaited()
        assert db_list_pending_broadcasts() == []


class TestProcessDue:
    async def test_due_broadcast_sent_to_subscribers(self, bot, tg_user):
        db_set_setting("broadcast_topic_id", "900")
        db_upsert_user(tg_user)
        db_schedule_broadcast("scheduled hello", _at(-1), ADMIN_ID)

        ran = await process_due_broadcasts(bot)
        assert ran == 1
        sends = [c for c in bot.send_message.await_args_list
                 if c.kwargs.get("chat_id") == tg_user.id]
        assert len(sends) == 1
        assert sends[0].kwargs["text"] == "scheduled hello"
        # moved to history, won't fire again
        assert db_get_due_broadcasts() == []
        assert await process_due_broadcasts(bot) == 0

    async def test_scheduled_tag_broadcast_targets_and_strips(self, bot):
        from database.tags import db_add_tag
        db_upsert_user(make_tg_user(1))
        db_upsert_user(make_tg_user(2))
        db_add_tag(1, "vip")
        db_schedule_broadcast("@VIP\nexclusive drop", _at(-1), ADMIN_ID)

        await process_due_broadcasts(bot)
        user_sends = [c for c in bot.send_message.await_args_list
                      if c.kwargs.get("chat_id") in (1, 2)]
        assert [c.kwargs["chat_id"] for c in user_sends] == [1]
        assert user_sends[0].kwargs["text"] == "exclusive drop"  # tag line stripped

    async def test_future_broadcast_not_sent(self, bot, tg_user):
        db_upsert_user(tg_user)
        db_schedule_broadcast("not yet", _at(10), ADMIN_ID)
        assert await process_due_broadcasts(bot) == 0
        assert all(c.kwargs.get("chat_id") != tg_user.id
                   for c in bot.send_message.await_args_list)

    async def test_marked_sent_even_if_sending_fails(self, bot, tg_user):
        """At-most-once: crash mid-send must not re-spam on next tick."""
        db_upsert_user(tg_user)
        db_schedule_broadcast("x", _at(-1), ADMIN_ID)
        bot.send_message = AsyncMock(side_effect=Exception("network gone"))
        bot.create_forum_topic = AsyncMock(side_effect=Exception("network gone"))
        try:
            await process_due_broadcasts(bot)
        except Exception:
            pass
        assert db_get_due_broadcasts() == []


class TestBroadcastText:
    async def test_counts_and_report(self, bot):
        db_set_setting("broadcast_topic_id", "900")
        for uid in (1, 2, 3):
            db_upsert_user(make_tg_user(uid))
        from database.users import db_get_all_subscribers
        recipients = db_get_all_subscribers()

        async def send_message(chat_id=None, **kwargs):
            if chat_id == 2:
                raise Forbidden("blocked")
            if chat_id == 3:
                raise Exception("boom")
            return make_message()

        bot.send_message = AsyncMock(side_effect=send_message)
        sent, blocked, failed = await _broadcast_text(bot, "hi", recipients, "test")
        assert (sent, blocked, failed) == (1, 1, 1)
        assert db_get_user(2)["blocked"] == 1
        # report posted into broadcast topic
        report = [c for c in bot.send_message.await_args_list
                  if c.kwargs.get("message_thread_id") == 900]
        assert len(report) == 1
        assert "Sent: <b>1</b>" in report[0].kwargs["text"]
