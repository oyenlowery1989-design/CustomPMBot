"""Tests for the database layer: migrations, users, messages, tags, bans,
settings, canned, topics, wallets."""
from datetime import datetime, timedelta, timezone

import database.connection as db_connection
from database.migrations import SCHEMA_VERSION, _get_schema_version, _run_migrations
from database.users import (
    db_upsert_user, db_get_user, db_set_topic, db_get_user_by_topic,
    db_get_all_subscribers, db_get_reachable_users, db_set_broadcast_opt,
    db_set_relay_paused, db_mark_blocked, db_mark_unblocked, db_user_count,
    db_full_stats, db_get_subscribers_by_tag, db_force_broadcast_all,
)
from database.messages import db_log_message, db_export_messages
from database.tags import db_add_tag, db_remove_tag, db_get_tags
from database.bans import (
    db_is_banned, db_ban, db_unban, db_get_banned, db_get_expired_bans,
    cleanup_expired_bans,
)
from database.settings import db_set_setting, db_get_setting
from database.canned import db_canned_set, db_canned_get, db_canned_delete, db_canned_list
from database.topics import (
    db_create_custom_topic, db_delete_custom_topic, db_get_custom_topic,
    db_list_custom_topics, db_bind_topic, db_unbind_topic, db_get_binding,
    db_list_bindings,
)
from database.wallets import (
    db_add_wallet, db_get_user_wallets, db_delete_wallet, db_set_wallet_verified,
    db_store_key, db_get_key, db_get_wallet_count, db_get_wallet_by_id,
    db_create_verification, db_get_pending_verifications, db_delete_verification,
    db_all_wallets,
)
from tests.conftest import make_tg_user


def _future(minutes=5):
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()


def _past(minutes=5):
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()


class TestMigrations:
    def test_fresh_db_reaches_target_version(self, fresh_db):
        assert _get_schema_version(fresh_db) == SCHEMA_VERSION

    def test_all_tables_created(self, fresh_db):
        tables = {r["name"] for r in fresh_db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        expected = {"settings", "users", "bans", "messages", "tags", "canned",
                    "custom_topics", "topic_bindings", "wallets", "wallet_keys",
                    "wallet_verifications", "scheduled_broadcasts",
                    "message_map", "auto_replies"}
        assert expected <= tables

    def test_rerun_is_idempotent(self, fresh_db):
        _run_migrations(fresh_db)
        _run_migrations(fresh_db)
        assert _get_schema_version(fresh_db) == SCHEMA_VERSION

    def test_v6_to_v7_preserves_data(self, fresh_db):
        # Simulate an existing v6 install with data, then re-migrate.
        db_upsert_user(make_tg_user(1))
        fresh_db.execute("INSERT OR REPLACE INTO settings (key,value) VALUES ('schema_version','6')")
        fresh_db.commit()
        _run_migrations(fresh_db)
        assert _get_schema_version(fresh_db) == SCHEMA_VERSION
        assert db_get_user(1) is not None

    def test_relay_paused_column_exists(self, fresh_db):
        cols = {r["name"] for r in fresh_db.execute("PRAGMA table_info(users)").fetchall()}
        assert "relay_paused" in cols

    def test_indexes_created(self, fresh_db):
        indexes = {r["name"] for r in fresh_db.execute(
            "SELECT name FROM sqlite_master WHERE type='index'").fetchall()}
        assert {"idx_messages_user", "idx_messages_ts", "idx_tags_tag", "idx_users_topic"} <= indexes


class TestUsers:
    def test_upsert_inserts_new_user(self):
        db_upsert_user(make_tg_user(1, "Bob", "Smith", "bob"))
        row = db_get_user(1)
        assert row["first_name"] == "Bob"
        assert row["last_name"] == "Smith"
        assert row["username"] == "bob"
        assert row["broadcast_opt"] == 1
        assert row["blocked"] == 0
        assert row["first_seen"] and row["last_seen"]

    def test_upsert_updates_existing_keeps_topic(self):
        db_upsert_user(make_tg_user(1, "Bob"), topic_id=55)
        db_upsert_user(make_tg_user(1, "Bobby"))  # no topic passed
        row = db_get_user(1)
        assert row["first_name"] == "Bobby"
        assert row["topic_id"] == 55

    def test_upsert_with_topic_updates_topic(self):
        db_upsert_user(make_tg_user(1), topic_id=55)
        db_upsert_user(make_tg_user(1), topic_id=99)
        assert db_get_user(1)["topic_id"] == 99

    def test_get_missing_user_returns_none(self):
        assert db_get_user(404) is None

    def test_set_topic_and_lookup_by_topic(self):
        db_upsert_user(make_tg_user(1))
        db_set_topic(1, 123)
        assert db_get_user_by_topic(123)["user_id"] == 1
        assert db_get_user_by_topic(999) is None

    def test_subscribers_exclude_optout_blocked_banned(self):
        for uid in (1, 2, 3, 4):
            db_upsert_user(make_tg_user(uid))
        db_set_broadcast_opt(2, False)
        db_mark_blocked(3)
        db_ban(4, "test")
        subs = {r["user_id"] for r in db_get_all_subscribers()}
        assert subs == {1}

    def test_reachable_ignores_optout_but_not_banned(self):
        for uid in (1, 2, 3):
            db_upsert_user(make_tg_user(uid))
        db_set_broadcast_opt(2, False)
        db_ban(3, "test")
        reach = {r["user_id"] for r in db_get_reachable_users()}
        assert reach == {1, 2}

    def test_relay_paused_roundtrip(self):
        db_upsert_user(make_tg_user(1))
        db_set_relay_paused(1, True)
        assert db_get_user(1)["relay_paused"] == 1
        db_set_relay_paused(1, False)
        assert db_get_user(1)["relay_paused"] == 0

    def test_blocked_roundtrip(self):
        db_upsert_user(make_tg_user(1))
        db_mark_blocked(1)
        assert db_get_user(1)["blocked"] == 1
        db_mark_unblocked(1)
        assert db_get_user(1)["blocked"] == 0

    def test_user_count(self):
        assert db_user_count() == 0
        db_upsert_user(make_tg_user(1))
        db_upsert_user(make_tg_user(2))
        assert db_user_count() == 2

    def test_full_stats(self):
        for uid in (1, 2, 3):
            db_upsert_user(make_tg_user(uid))
        db_mark_blocked(2)
        db_set_broadcast_opt(3, False)
        db_ban(9, "ghost ban")  # ban for a user not in users table
        db_log_message(1, "in", "text", "hello")
        db_log_message(1, "out", "text", "reply")
        s = db_full_stats()
        assert s["total"] == 3
        assert s["active"] == 2
        assert s["blocked"] == 1
        assert s["banned"] == 1
        assert s["subs_on"] == 1
        assert s["subs_off"] == 1
        assert s["msg_total"] == 2 and s["msg_in"] == 1 and s["msg_out"] == 1

    def test_subscribers_by_tag_case_insensitive(self):
        db_upsert_user(make_tg_user(1))
        db_upsert_user(make_tg_user(2))
        db_add_tag(1, "vip")
        assert {r["user_id"] for r in db_get_subscribers_by_tag("vip")} == {1}
        assert {r["user_id"] for r in db_get_subscribers_by_tag("VIP")} == {1}

    def test_force_broadcast_all(self):
        for uid in (1, 2):
            db_upsert_user(make_tg_user(uid))
        db_set_broadcast_opt(1, False)
        db_set_broadcast_opt(2, False)
        count = db_force_broadcast_all(True)
        assert count == 2
        assert len(db_get_all_subscribers()) == 2
        db_force_broadcast_all(False)
        assert len(db_get_all_subscribers()) == 0


class TestMessages:
    def test_log_and_export(self):
        db_log_message(1, "in", "text", "first")
        db_log_message(1, "out", "text", "second")
        db_log_message(2, "in", "photo", "")
        rows = db_export_messages(1)
        assert len(rows) == 2
        assert rows[0]["text"] == "second"  # DESC order, newest first
        assert rows[1]["direction"] == "in"

    def test_export_respects_limit(self):
        for i in range(10):
            db_log_message(1, "in", "text", f"m{i}")
        assert len(db_export_messages(1, limit=3)) == 3

    def test_none_text_stored_as_empty(self):
        db_log_message(1, "in", "photo", None)
        assert db_export_messages(1)[0]["text"] == ""


class TestTags:
    def test_add_uppercases_and_dedupes(self):
        db_add_tag(1, "vip")
        db_add_tag(1, "VIP")
        assert db_get_tags(1) == ["VIP"]

    def test_remove(self):
        db_add_tag(1, "gold")
        assert db_remove_tag(1, "gold") is True
        assert db_remove_tag(1, "gold") is False
        assert db_get_tags(1) == []

    def test_list_sorted(self):
        db_add_tag(1, "zeta")
        db_add_tag(1, "alpha")
        assert db_get_tags(1) == ["ALPHA", "ZETA"]


class TestBans:
    def test_permanent_ban(self):
        db_ban(1, "spam")
        assert db_is_banned(1) is True

    def test_not_banned(self):
        assert db_is_banned(1) is False

    def test_expired_ban_auto_removed_on_check(self):
        db_ban(1, "temp", expires_at=_past())
        assert db_is_banned(1) is False
        assert db_get_banned() == []  # row deleted

    def test_future_ban_still_active(self):
        db_ban(1, "temp", expires_at=_future())
        assert db_is_banned(1) is True

    def test_unban(self):
        db_ban(1, "x")
        assert db_unban(1) is True
        assert db_unban(1) is False
        assert db_is_banned(1) is False

    def test_get_banned_joins_user_info(self):
        db_upsert_user(make_tg_user(1, "Bob", username="bob"))
        db_ban(1, "reason1")
        rows = db_get_banned()
        assert len(rows) == 1
        assert rows[0]["first_name"] == "Bob"
        assert rows[0]["reason"] == "reason1"

    def test_get_expired_bans(self):
        db_ban(1, "old", expires_at=_past())
        db_ban(2, "fresh", expires_at=_future())
        db_ban(3, "perm")
        expired = {r["user_id"] for r in db_get_expired_bans()}
        assert expired == {1}

    def test_cleanup_expired_bans(self):
        db_ban(1, "old", expires_at=_past())
        db_ban(2, "fresh", expires_at=_future())
        db_ban(3, "perm")
        assert cleanup_expired_bans() == 1
        remaining = {r["user_id"] for r in db_get_banned()}
        assert remaining == {2, 3}

    def test_cleanup_restores_broadcast_eligibility(self):
        db_upsert_user(make_tg_user(1))
        db_ban(1, "old", expires_at=_past())
        assert db_get_all_subscribers() == []  # expired ban still excludes
        cleanup_expired_bans()
        assert len(db_get_all_subscribers()) == 1


class TestSettings:
    def test_set_get(self):
        db_set_setting("welcome_message", "hello!")
        assert db_get_setting("welcome_message") == "hello!"

    def test_get_default(self):
        assert db_get_setting("missing", "fallback") == "fallback"

    def test_overwrite(self):
        db_set_setting("k", "v1")
        db_set_setting("k", "v2")
        assert db_get_setting("k") == "v2"


class TestCanned:
    def test_set_get_lowercases_name(self):
        db_canned_set("Hello", "Hi there!")
        assert db_canned_get("hello")["body"] == "Hi there!"
        assert db_canned_get("HELLO")["body"] == "Hi there!"

    def test_default_is_text(self):
        db_canned_set("x", "y")
        row = db_canned_get("x")
        assert row["content_type"] == "text"
        assert row["file_id"] is None

    def test_media_roundtrip(self):
        db_canned_set("pic", "a caption", content_type="photo", file_id="FID123")
        row = db_canned_get("pic")
        assert row["content_type"] == "photo"
        assert row["file_id"] == "FID123"
        assert row["body"] == "a caption"

    def test_get_missing(self):
        assert db_canned_get("nope") is None

    def test_delete(self):
        db_canned_set("x", "y")
        assert db_canned_delete("X") is True
        assert db_canned_delete("x") is False

    def test_list_sorted(self):
        db_canned_set("b", "2")
        db_canned_set("a", "1")
        assert [r["name"] for r in db_canned_list()] == ["a", "b"]


class TestCustomTopics:
    def test_create_get(self):
        db_create_custom_topic("Logs", 10, "log topic")
        row = db_get_custom_topic("logs")
        assert row["topic_id"] == 10
        assert row["description"] == "log topic"

    def test_delete_cascades_bindings(self):
        db_create_custom_topic("logs", 10)
        db_bind_topic("event", "new_user", "logs")
        assert db_delete_custom_topic("logs") is True
        assert db_get_binding("event", "new_user") is None
        assert db_list_bindings() == []

    def test_delete_missing(self):
        assert db_delete_custom_topic("ghost") is False

    def test_list(self):
        db_create_custom_topic("b", 2)
        db_create_custom_topic("a", 1)
        assert [t["name"] for t in db_list_custom_topics()] == ["a", "b"]

    def test_bind_get_unbind(self):
        db_create_custom_topic("stats", 33)
        db_bind_topic("command", "stats", "stats")
        assert db_get_binding("command", "stats") == 33
        assert db_unbind_topic("command", "stats") is True
        assert db_unbind_topic("command", "stats") is False
        assert db_get_binding("command", "stats") is None

    def test_binding_to_missing_topic_returns_none(self):
        db_bind_topic("event", "new_user", "nonexistent")
        assert db_get_binding("event", "new_user") is None


class TestWallets:
    ADDR = "G" + "A" * 55

    def test_add_and_list(self):
        assert db_add_wallet(1, self.ADDR, "Main") is True
        rows = db_get_user_wallets(1)
        assert len(rows) == 1
        assert rows[0]["address"] == self.ADDR
        assert rows[0]["verified"] == 0

    def test_duplicate_rejected(self):
        db_add_wallet(1, self.ADDR)
        assert db_add_wallet(1, self.ADDR) is False
        assert db_get_wallet_count(1) == 1

    def test_same_address_different_users_ok(self):
        assert db_add_wallet(1, self.ADDR) is True
        assert db_add_wallet(2, self.ADDR) is True

    def test_delete_removes_wallet_and_key(self, fresh_db):
        db_add_wallet(1, self.ADDR)
        db_store_key(self.ADDR, "SSECRET123")
        assert db_delete_wallet(1, self.ADDR) is True
        assert db_get_user_wallets(1) == []
        assert db_get_key(self.ADDR) is None
        assert fresh_db.execute("SELECT COUNT(*) c FROM wallet_keys").fetchone()["c"] == 0

    def test_set_verified(self):
        db_add_wallet(1, self.ADDR)
        db_set_wallet_verified(1, self.ADDR, 2)
        row = db_get_user_wallets(1)[0]
        assert row["verified"] == 2
        assert row["verified_at"] is not None

    def test_key_encrypted_at_rest_and_roundtrips(self, fresh_db):
        secret = "SBTESTSECRETKEYVALUE"
        db_store_key(self.ADDR, secret)
        stored = fresh_db.execute("SELECT encrypted_key FROM wallet_keys").fetchone()["encrypted_key"]
        assert secret not in stored  # never plaintext in DB
        assert db_get_key(self.ADDR) == secret

    def test_get_key_missing(self):
        assert db_get_key("GNOPE") is None

    def test_wallet_by_id(self):
        db_add_wallet(1, self.ADDR)
        wid = db_get_user_wallets(1)[0]["id"]
        assert db_get_wallet_by_id(wid)["address"] == self.ADDR
        assert db_get_wallet_by_id(9999) is None

    def test_verification_lifecycle(self):
        db_create_verification(1, self.ADDR, "123456")
        pending = db_get_pending_verifications()
        assert len(pending) == 1
        assert pending[0]["challenge"] == "123456"
        expires = datetime.fromisoformat(pending[0]["expires_at"])
        assert expires > datetime.now(timezone.utc)
        db_delete_verification(1, self.ADDR)
        assert db_get_pending_verifications() == []

    def test_verification_replaced_on_retry(self):
        db_create_verification(1, self.ADDR, "111111")
        db_create_verification(1, self.ADDR, "222222")
        pending = db_get_pending_verifications()
        assert len(pending) == 1
        assert pending[0]["challenge"] == "222222"

    def test_all_wallets(self):
        db_add_wallet(1, "G" + "A" * 55)
        db_add_wallet(2, "G" + "B" * 55)
        assert len(db_all_wallets()) == 2
