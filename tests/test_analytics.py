"""Tests for /analytics and its DB queries."""
from datetime import datetime, timedelta, timezone

from database.analytics import (
    db_messages_per_day, db_new_users_per_day, db_top_users, db_busiest_hours,
)
from database.messages import db_log_message
from database.users import db_upsert_user
from handlers.admin import cmd_analytics
from tests.conftest import make_bot, make_context, make_message, make_tg_user, make_update

ADMIN_ID = 1000
TODAY = datetime.now(timezone.utc).isoformat()[:10]


def _insert_msg(db, user_id, direction, ts):
    db.execute(
        "INSERT INTO messages (user_id, direction, content_type, text, timestamp) "
        "VALUES (?,?,?,?,?)", (user_id, direction, "text", "x", ts))
    db.commit()


def _days_ago(n, hour=12):
    return (datetime.now(timezone.utc) - timedelta(days=n)) \
        .replace(hour=hour, minute=0, second=0).isoformat()


class TestAnalyticsDb:
    def test_messages_per_day_counts_directions(self, fresh_db):
        db_log_message(1, "in", "text", "a")
        db_log_message(1, "in", "text", "b")
        db_log_message(1, "out", "text", "c")
        rows = db_messages_per_day(7)
        assert len(rows) == 1
        assert rows[0]["day"] == TODAY
        assert rows[0]["msgs_in"] == 2
        assert rows[0]["msgs_out"] == 1

    def test_old_messages_excluded(self, fresh_db):
        _insert_msg(fresh_db, 1, "in", _days_ago(30))
        db_log_message(1, "in", "text", "recent")
        rows = db_messages_per_day(7)
        assert len(rows) == 1
        assert rows[0]["msgs_in"] == 1

    def test_new_users_per_day(self):
        db_upsert_user(make_tg_user(1))
        db_upsert_user(make_tg_user(2))
        rows = db_new_users_per_day(7)
        assert rows[0]["day"] == TODAY
        assert rows[0]["count"] == 2

    def test_top_users_ordered_incoming_only(self):
        db_upsert_user(make_tg_user(1, "Chatty"))
        db_upsert_user(make_tg_user(2, "Quiet"))
        for _ in range(3):
            db_log_message(1, "in", "text", "x")
        db_log_message(2, "in", "text", "x")
        for _ in range(10):
            db_log_message(2, "out", "text", "x")  # outgoing must not count
        rows = db_top_users(7)
        assert [r["user_id"] for r in rows] == [1, 2]
        assert rows[0]["count"] == 3
        assert rows[0]["first_name"] == "Chatty"

    def test_busiest_hours(self, fresh_db):
        for _ in range(3):
            _insert_msg(fresh_db, 1, "in", _days_ago(1, hour=14))
        _insert_msg(fresh_db, 1, "in", _days_ago(1, hour=9))
        rows = db_busiest_hours(7)
        assert rows[0]["hour"] == "14"
        assert rows[0]["count"] == 3


class TestAnalyticsCmd:
    async def test_report_sections(self, bot):
        db_upsert_user(make_tg_user(1, "Bob"))
        db_log_message(1, "in", "text", "hi")
        update = make_update(user=make_tg_user(ADMIN_ID), message=make_message(),
                             chat_type="group")
        await cmd_analytics(update, make_context(bot))
        text = update.message.reply_text.await_args.args[0]
        assert "Messages per day" in text
        assert "New users" in text
        assert "Most active" in text
        assert "Bob" in text

    async def test_days_argument_clamped(self, bot):
        update = make_update(user=make_tg_user(ADMIN_ID), message=make_message(),
                             chat_type="group")
        await cmd_analytics(update, make_context(bot, args=["500"]))
        assert "last 90 day(s)" in update.message.reply_text.await_args.args[0]

    async def test_non_admin_ignored(self, bot):
        update = make_update(user=make_tg_user(666), message=make_message(),
                             chat_type="group")
        await cmd_analytics(update, make_context(bot))
        update.message.reply_text.assert_not_awaited()
