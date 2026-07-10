"""Tests for the relay core: user DM → topic, admin topic → user, broadcasts,
wallet input flows intercepted in the DM handler."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

from stellar_sdk import Keypair
from telegram.error import Forbidden, TelegramError

import handlers.wallet as wallet_mod
import services.spam as spam
from config import SPAM_MAX_MSGS
from handlers.relay import _ensure_topic, handle_private_message, handle_admin_group_message
from handlers.broadcast import _find_broadcast_topic, _do_broadcast
import handlers.broadcast as broadcast_mod
from database.ai_drafts import db_create_draft, db_get_draft, db_set_draft_status
from database.bans import db_ban, db_is_banned
from database.messages import db_export_messages
from database.settings import db_get_setting, db_set_setting
from database.users import (
    db_upsert_user, db_get_user, db_set_topic, db_set_relay_paused,
    db_mark_blocked, db_get_all_subscribers,
)
from database.wallets import db_get_user_wallets, db_get_key, db_set_awaiting_key
from tests.conftest import (
    ADMIN_GROUP_ID, make_bot, make_context, make_message, make_tg_user, make_update,
)

USER_ID = 500
ADMIN_ID = 1000
VALID_ADDR = "G" + "A" * 55


class TestEnsureTopic:
    async def test_reuses_existing_topic(self, bot, tg_user):
        db_upsert_user(tg_user, topic_id=42)
        assert await _ensure_topic(bot, tg_user) == 42
        bot.create_forum_topic.assert_not_awaited()

    async def test_creates_topic_and_info_card(self, bot, tg_user):
        topic_id = await _ensure_topic(bot, tg_user)
        assert topic_id == 777
        bot.create_forum_topic.assert_awaited_once()
        assert bot.create_forum_topic.await_args.kwargs["name"] == "Alice"
        assert db_get_user(tg_user.id)["topic_id"] == 777
        bot.send_message.assert_awaited()  # info card posted into the topic
        assert bot.send_message.await_args_list[0].kwargs["message_thread_id"] == 777

    async def test_long_name_truncated_to_128(self, bot):
        user = make_tg_user(1, first_name="X" * 200)
        await _ensure_topic(bot, user)
        assert len(bot.create_forum_topic.await_args.kwargs["name"]) == 128

    async def test_topic_gets_colored_icon(self, bot, tg_user):
        from telegram.constants import ForumIconColor
        await _ensure_topic(bot, tg_user)
        icon = bot.create_forum_topic.await_args.kwargs["icon_color"]
        assert icon in set(ForumIconColor)


class TestHandlePrivateMessage:
    async def test_admin_dms_not_relayed(self, bot):
        admin = make_tg_user(ADMIN_ID)
        update = make_update(user=admin, message=make_message("hi"))
        await handle_private_message(update, make_context(bot))
        bot.create_forum_topic.assert_not_awaited()
        assert db_get_user(ADMIN_ID) is None

    async def test_normal_message_forwarded_and_logged(self, bot, tg_user):
        msg = make_message("hello support")
        update = make_update(user=tg_user, message=msg)
        await handle_private_message(update, make_context(bot))
        msg.forward.assert_awaited_once_with(chat_id=ADMIN_GROUP_ID, message_thread_id=777)
        logged = db_export_messages(tg_user.id)
        assert len(logged) == 1
        assert logged[0]["direction"] == "in"
        assert logged[0]["text"] == "hello support"

    async def test_banned_user_dropped(self, bot, tg_user):
        db_ban(tg_user.id, "spam")
        msg = make_message("let me in")
        await handle_private_message(make_update(user=tg_user, message=msg), make_context(bot))
        msg.forward.assert_not_awaited()
        msg.reply_text.assert_not_awaited()

    async def test_relay_paused_gets_closed_notice(self, bot, tg_user):
        db_upsert_user(tg_user, topic_id=42)
        db_set_relay_paused(tg_user.id, True)
        msg = make_message("anyone there?")
        await handle_private_message(make_update(user=tg_user, message=msg), make_context(bot))
        msg.forward.assert_not_awaited()
        msg.reply_text.assert_awaited_once()

    async def test_incoming_message_clears_blocked_flag(self, bot, tg_user):
        db_upsert_user(tg_user)
        db_mark_blocked(tg_user.id)
        await handle_private_message(make_update(user=tg_user, message=make_message("back")),
                                     make_context(bot))
        assert db_get_user(tg_user.id)["blocked"] == 0

    async def test_forward_failure_recovers_by_recreating_topic(self, bot, tg_user):
        """H2 (docs/AUDIT-2026-07-10.md): without recovery, a topic deleted
        in Telegram means this user's messages vanish forever — nothing
        ever clears the stale topic_id or retries."""
        db_upsert_user(tg_user, topic_id=999)  # topic since deleted in Telegram
        msg = make_message("are you there?")
        msg.forward = AsyncMock(side_effect=[
            TelegramError("message thread not found"),
            SimpleNamespace(message_id=8801),
        ])

        await handle_private_message(make_update(user=tg_user, message=msg), make_context(bot))

        assert msg.forward.await_count == 2
        bot.create_forum_topic.assert_awaited_once()
        assert db_get_user(tg_user.id)["topic_id"] == 777  # new topic persisted
        assert all("Could not relay" not in c.kwargs.get("text", "")
                  for c in bot.send_message.await_args_list)

    async def test_forward_failure_alerts_admin_group_when_recovery_also_fails(self, bot, tg_user):
        db_upsert_user(tg_user, topic_id=999)
        msg = make_message("hello?")
        msg.forward = AsyncMock(side_effect=TelegramError("message thread not found"))

        await handle_private_message(make_update(user=tg_user, message=msg), make_context(bot))

        assert msg.forward.await_count == 2  # original attempt + retry after recreate
        bot.create_forum_topic.assert_awaited_once()
        alerts = [c for c in bot.send_message.await_args_list
                 if c.kwargs.get("message_thread_id") is None and "Could not relay" in c.kwargs.get("text", "")]
        assert len(alerts) == 1
        assert str(tg_user.id) in alerts[0].kwargs["text"]

    async def test_spam_warn_then_autoban(self, bot, tg_user):
        ctx = make_context(bot)
        for _ in range(SPAM_MAX_MSGS):
            await handle_private_message(
                make_update(user=tg_user, message=make_message("x")), ctx)

        warn_msg = make_message("x")
        await handle_private_message(make_update(user=tg_user, message=warn_msg), ctx)
        warn_msg.reply_text.assert_awaited_once()
        warn_msg.forward.assert_not_awaited()
        assert not db_is_banned(tg_user.id)

        ban_msg = make_message("x")
        await handle_private_message(make_update(user=tg_user, message=ban_msg), ctx)
        ban_msg.reply_text.assert_awaited_once()
        assert db_is_banned(tg_user.id)


class TestAiDraftButton:
    async def test_attached_when_ai_enabled_on(self, bot, tg_user):
        db_set_setting("ai_enabled", "on")
        msg = make_message("need help")
        await handle_private_message(make_update(user=tg_user, message=msg), make_context(bot))
        draft_calls = [c for c in bot.send_message.await_args_list
                       if c.kwargs.get("text") == "🤖" and c.kwargs.get("reply_markup")]
        assert len(draft_calls) == 1
        buttons = [b for row in draft_calls[0].kwargs["reply_markup"].inline_keyboard for b in row]
        assert len(buttons) == 1
        assert buttons[0].callback_data == f"ai_draft_{tg_user.id}_8800"
        assert draft_calls[0].kwargs["reply_to_message_id"] == 8800

    async def test_absent_when_ai_off_by_default(self, bot, tg_user):
        msg = make_message("need help")
        await handle_private_message(make_update(user=tg_user, message=msg), make_context(bot))
        assert all(c.kwargs.get("text") != "🤖" for c in bot.send_message.await_args_list)


class TestWalletInputFlow:
    async def test_valid_address_advances_to_label(self, bot, tg_user):
        addr = Keypair.random().public_key
        wallet_mod._awaiting_wallet_addr.add(tg_user.id)
        msg = make_message(addr)
        await handle_private_message(make_update(user=tg_user, message=msg), make_context(bot))
        assert tg_user.id not in wallet_mod._awaiting_wallet_addr
        assert wallet_mod._awaiting_wallet_label[tg_user.id] == addr
        msg.reply_text.assert_awaited_once()
        msg.forward.assert_not_awaited()  # never relayed to admins

    async def test_invalid_address_rejected_stays_waiting(self, bot, tg_user):
        wallet_mod._awaiting_wallet_addr.add(tg_user.id)
        msg = make_message("not-an-address")
        await handle_private_message(make_update(user=tg_user, message=msg), make_context(bot))
        assert tg_user.id in wallet_mod._awaiting_wallet_addr
        msg.reply_text.assert_awaited_once()

    async def test_bad_checksum_address_rejected(self, bot, tg_user):
        """Right length, right prefix, garbage checksum — StrKey must catch it."""
        wallet_mod._awaiting_wallet_addr.add(tg_user.id)
        msg = make_message("G" + "A" * 55)
        await handle_private_message(make_update(user=tg_user, message=msg), make_context(bot))
        assert tg_user.id in wallet_mod._awaiting_wallet_addr

    async def test_duplicate_wallet_reported_not_saved_twice(self, bot, tg_user):
        from database.wallets import db_add_wallet, db_get_wallet_count
        db_add_wallet(tg_user.id, VALID_ADDR, "Old")
        wallet_mod._awaiting_wallet_label[tg_user.id] = VALID_ADDR
        msg = make_message("New Label")
        await handle_private_message(make_update(user=tg_user, message=msg), make_context(bot))
        assert db_get_wallet_count(tg_user.id) == 1
        assert "already" in msg.reply_text.await_args.args[0]
        bot.create_forum_topic.assert_not_awaited()  # no admin notification

    async def test_label_saves_wallet_and_notifies_admins(self, bot, tg_user):
        wallet_mod._awaiting_wallet_label[tg_user.id] = VALID_ADDR
        msg = make_message("Savings")
        await handle_private_message(make_update(user=tg_user, message=msg), make_context(bot))
        wallets = db_get_user_wallets(tg_user.id)
        assert len(wallets) == 1
        assert wallets[0]["label"] == "Savings"
        assert tg_user.id not in wallet_mod._awaiting_wallet_label
        # Admin topic created + notification sent into it
        bot.create_forum_topic.assert_awaited_once()

    async def test_label_truncated_to_20_chars(self, bot, tg_user):
        wallet_mod._awaiting_wallet_label[tg_user.id] = VALID_ADDR
        await handle_private_message(
            make_update(user=tg_user, message=make_message("L" * 30)), make_context(bot))
        assert db_get_user_wallets(tg_user.id)[0]["label"] == "L" * 20

    async def test_correct_secret_key_verifies_and_stores(self, bot, tg_user):
        kp = Keypair.random()
        from database.wallets import db_add_wallet
        db_add_wallet(tg_user.id, kp.public_key)
        db_set_awaiting_key(tg_user.id, kp.public_key)
        msg = make_message(kp.secret)
        await handle_private_message(make_update(user=tg_user, message=msg), make_context(bot))
        msg.delete.assert_awaited_once()  # secret never left in chat
        assert db_get_user_wallets(tg_user.id)[0]["verified"] == 2
        assert db_get_key(tg_user.id, kp.public_key) == kp.secret

    async def test_wrong_secret_key_fails_but_still_deletes(self, bot, tg_user):
        kp = Keypair.random()
        from database.wallets import db_add_wallet
        db_add_wallet(tg_user.id, kp.public_key)
        db_set_awaiting_key(tg_user.id, kp.public_key)
        msg = make_message(Keypair.random().secret)
        await handle_private_message(make_update(user=tg_user, message=msg), make_context(bot))
        msg.delete.assert_awaited_once()
        assert db_get_user_wallets(tg_user.id)[0]["verified"] == 0
        assert db_get_key(tg_user.id, kp.public_key) is None

    async def test_pasted_secret_key_deleted_even_without_awaiting_flow(self, bot, tg_user):
        """Defense in depth: a valid Stellar secret seed is always intercepted
        and deleted, even if the user never started the verify-by-key flow —
        it must never reach the relay/log path."""
        kp = Keypair.random()
        msg = make_message(kp.secret)
        await handle_private_message(make_update(user=tg_user, message=msg), make_context(bot))
        msg.delete.assert_awaited_once()
        msg.forward.assert_not_awaited()
        assert db_get_key(tg_user.id, kp.public_key) is None


class TestAdminGroupMessage:
    async def test_admin_reply_relayed_to_user_and_logged(self, bot, tg_user):
        db_upsert_user(tg_user, topic_id=55)
        admin = make_tg_user(ADMIN_ID)
        msg = make_message("we can help", thread_id=55)
        await handle_admin_group_message(make_update(user=admin, message=msg, chat_type="group"),
                                         make_context(bot))
        sends = [c for c in bot.send_message.await_args_list
                 if c.kwargs.get("chat_id") == tg_user.id]
        assert len(sends) == 1
        assert sends[0].kwargs["text"] == "we can help"
        logged = db_export_messages(tg_user.id)
        assert logged[0]["direction"] == "out"

    async def test_non_admin_in_group_ignored(self, bot, tg_user):
        db_upsert_user(tg_user, topic_id=55)
        rando = make_tg_user(666, "Rando")
        msg = make_message("fake reply", thread_id=55)
        await handle_admin_group_message(make_update(user=rando, message=msg, chat_type="group"),
                                         make_context(bot))
        assert all(c.kwargs.get("chat_id") != tg_user.id
                   for c in bot.send_message.await_args_list)

    async def test_unknown_topic_ignored(self, bot):
        admin = make_tg_user(ADMIN_ID)
        msg = make_message("into the void", thread_id=12345)
        await handle_admin_group_message(make_update(user=admin, message=msg, chat_type="group"),
                                         make_context(bot))
        # No broadcast topic match, no user match — nothing relayed
        assert all(c.kwargs.get("chat_id") == ADMIN_GROUP_ID
                   for c in bot.send_message.await_args_list)

    async def test_message_in_broadcast_topic_broadcasts(self, bot, tg_user):
        db_set_setting("broadcast_topic_id", "900")
        db_set_setting("broadcast_confirm", "off")  # instant mode
        db_upsert_user(tg_user)
        admin = make_tg_user(ADMIN_ID)
        msg = make_message("big news", thread_id=900)
        await handle_admin_group_message(make_update(user=admin, message=msg, chat_type="group"),
                                         make_context(bot))
        sends = [c for c in bot.send_message.await_args_list
                 if c.kwargs.get("chat_id") == tg_user.id]
        assert len(sends) == 1
        assert sends[0].kwargs["text"] == "big news"

    async def test_no_pending_ai_edit_falls_through_to_normal_relay(self, bot, tg_user):
        """AI-draft edit intercept sits ahead of the normal relay path in
        handle_admin_group_message; with no draft awaiting_edit in this
        topic, an ordinary admin reply must behave exactly as before."""
        db_upsert_user(tg_user, topic_id=55)
        admin = make_tg_user(ADMIN_ID)
        msg = make_message("we can help", thread_id=55)
        await handle_admin_group_message(make_update(user=admin, message=msg, chat_type="group"),
                                         make_context(bot))
        sends = [c for c in bot.send_message.await_args_list
                 if c.kwargs.get("chat_id") == tg_user.id]
        assert len(sends) == 1
        assert sends[0].kwargs["text"] == "we can help"
        assert db_export_messages(tg_user.id)[0]["direction"] == "out"

    async def test_awaiting_ai_edit_intercepts_next_admin_message(self, bot, tg_user):
        db_upsert_user(tg_user, topic_id=55)
        draft_id = db_create_draft(tg_user.id, 55, "original draft")
        db_set_draft_status(draft_id, "awaiting_edit")

        admin = make_tg_user(ADMIN_ID)
        msg = make_message("corrected reply text", thread_id=55)
        await handle_admin_group_message(make_update(user=admin, message=msg, chat_type="group"),
                                         make_context(bot))

        sends = [c for c in bot.send_message.await_args_list
                 if c.kwargs.get("chat_id") == tg_user.id]
        assert len(sends) == 1
        assert sends[0].kwargs["text"] == "corrected reply text"
        logged = db_export_messages(tg_user.id)
        assert logged[0]["direction"] == "out"
        assert logged[0]["text"] == "corrected reply text"
        row = db_get_draft(draft_id)
        assert row["status"] == "edited"
        assert row["draft_text"] == "corrected reply text"

    async def test_awaiting_ai_edit_send_failure_reverts_to_pending(self, bot, tg_user):
        """H3 (docs/AUDIT-2026-07-10.md): a failed send must not leave the
        draft stuck awaiting_edit forever — every future admin message in
        the topic would otherwise keep being intercepted and swallowed."""
        db_upsert_user(tg_user, topic_id=55)
        draft_id = db_create_draft(tg_user.id, 55, "original draft")
        db_set_draft_status(draft_id, "awaiting_edit")
        bot.send_message.side_effect = TelegramError("bot was blocked by the user")

        admin = make_tg_user(ADMIN_ID)
        msg = make_message("corrected reply text", thread_id=55)
        await handle_admin_group_message(make_update(user=admin, message=msg, chat_type="group"),
                                         make_context(bot))

        row = db_get_draft(draft_id)
        assert row["status"] == "pending"
        assert row["draft_text"] == "original draft"  # never overwritten on failure
        assert db_export_messages(tg_user.id) == []  # never logged as sent
        msg.reply_text.assert_awaited_once()
        assert "Failed to send" in msg.reply_text.await_args.args[0]

    async def test_awaiting_ai_edit_empty_correction_is_rejected(self, bot, tg_user):
        db_upsert_user(tg_user, topic_id=55)
        draft_id = db_create_draft(tg_user.id, 55, "original draft")
        db_set_draft_status(draft_id, "awaiting_edit")

        admin = make_tg_user(ADMIN_ID)
        msg = make_message(text=None, thread_id=55)  # e.g. a sticker with no text/caption
        await handle_admin_group_message(make_update(user=admin, message=msg, chat_type="group"),
                                         make_context(bot))

        assert db_get_draft(draft_id)["status"] == "awaiting_edit"
        bot.send_message.assert_not_awaited()

    async def test_awaiting_ai_edit_takes_priority_over_broadcast_topic(self, bot, tg_user):
        """Ordering guard: the intercept must run before the broadcast-topic
        check, exactly like the wallet secret-key intercept ordering already
        enforced in handle_private_message."""
        db_set_setting("broadcast_topic_id", "55")
        db_upsert_user(tg_user, topic_id=55)
        draft_id = db_create_draft(tg_user.id, 55, "original draft")
        db_set_draft_status(draft_id, "awaiting_edit")

        admin = make_tg_user(ADMIN_ID)
        msg = make_message("corrected text", thread_id=55)
        await handle_admin_group_message(make_update(user=admin, message=msg, chat_type="group"),
                                         make_context(bot))
        assert db_get_draft(draft_id)["status"] == "edited"
        sends = [c for c in bot.send_message.await_args_list
                 if c.kwargs.get("chat_id") == tg_user.id]
        assert len(sends) == 1  # went straight to the user, never treated as a broadcast blast


class TestFindBroadcastTopic:
    async def test_uses_stored_live_topic(self, bot):
        db_set_setting("broadcast_topic_id", "321")
        assert await _find_broadcast_topic(bot) == 321
        bot.create_forum_topic.assert_not_awaited()
        bot.send_chat_action.assert_awaited_once()

    async def test_dead_stored_topic_recreated(self, bot):
        db_set_setting("broadcast_topic_id", "321")
        bot.send_chat_action = AsyncMock(side_effect=TelegramError("topic deleted"))
        assert await _find_broadcast_topic(bot) == 777
        assert db_get_setting("broadcast_topic_id") == "777"

    async def test_no_stored_topic_creates_one(self, bot):
        assert await _find_broadcast_topic(bot) == 777
        assert db_get_setting("broadcast_topic_id") == "777"

    async def test_result_cached_in_memory(self, bot):
        await _find_broadcast_topic(bot)
        await _find_broadcast_topic(bot)
        bot.create_forum_topic.assert_awaited_once()

    async def test_creation_failure_returns_none(self, bot):
        bot.create_forum_topic = AsyncMock(side_effect=TelegramError("no rights"))
        assert await _find_broadcast_topic(bot) is None


class TestDoBroadcast:
    async def test_no_recipients_reports_and_stops(self, bot):
        await _do_broadcast(bot, make_message("hi", thread_id=900), [], "all", opted_out_count=3)
        text = bot.send_message.await_args.kwargs["text"]
        assert "No recipients" in text
        assert "3" in text

    async def test_counts_sent_blocked_failed(self, bot):
        for uid in (1, 2, 3):
            db_upsert_user(make_tg_user(uid))
        recipients = db_get_all_subscribers()

        progress = make_message()

        async def send_message(chat_id=None, **kwargs):
            if chat_id == 2:
                raise Forbidden("blocked")
            if chat_id == 3:
                raise TelegramError("boom")
            return progress

        bot.send_message = AsyncMock(side_effect=send_message)
        await _do_broadcast(bot, make_message("news", thread_id=900), recipients, "all",
                            opted_out_count=1)

        final = progress.edit_text.await_args_list[-1].args[0]
        assert "Sent: <b>1</b>" in final
        assert "Blocked: <b>1</b>" in final
        assert "Failed: <b>1</b>" in final
        assert "Opted-out: <b>1</b>" in final
        # Forbidden also marks the user blocked in DB
        assert db_get_user(2)["blocked"] == 1
