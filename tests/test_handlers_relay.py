"""Tests for the relay core: user DM → topic, admin topic → user, broadcasts,
wallet input flows intercepted in the DM handler."""
from unittest.mock import AsyncMock

from stellar_sdk import Keypair
from telegram.error import Forbidden, TelegramError

import handlers.wallet as wallet_mod
import services.spam as spam
from config import SPAM_MAX_MSGS
from handlers.relay import _ensure_topic, handle_private_message, handle_admin_group_message
from handlers.broadcast import _find_broadcast_topic, _do_broadcast
import handlers.broadcast as broadcast_mod
from database.bans import db_ban, db_is_banned
from database.messages import db_export_messages
from database.settings import db_get_setting, db_set_setting
from database.users import (
    db_upsert_user, db_get_user, db_set_topic, db_set_relay_paused,
    db_mark_blocked, db_get_all_subscribers,
)
from database.wallets import db_get_user_wallets, db_get_key
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


class TestWalletInputFlow:
    async def test_valid_address_advances_to_label(self, bot, tg_user):
        wallet_mod._awaiting_wallet_addr.add(tg_user.id)
        msg = make_message(VALID_ADDR)
        await handle_private_message(make_update(user=tg_user, message=msg), make_context(bot))
        assert tg_user.id not in wallet_mod._awaiting_wallet_addr
        assert wallet_mod._awaiting_wallet_label[tg_user.id] == VALID_ADDR
        msg.reply_text.assert_awaited_once()
        msg.forward.assert_not_awaited()  # never relayed to admins

    async def test_invalid_address_rejected_stays_waiting(self, bot, tg_user):
        wallet_mod._awaiting_wallet_addr.add(tg_user.id)
        msg = make_message("not-an-address")
        await handle_private_message(make_update(user=tg_user, message=msg), make_context(bot))
        assert tg_user.id in wallet_mod._awaiting_wallet_addr
        msg.reply_text.assert_awaited_once()

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
        wallet_mod._awaiting_secret_key[tg_user.id] = kp.public_key
        msg = make_message(kp.secret)
        await handle_private_message(make_update(user=tg_user, message=msg), make_context(bot))
        msg.delete.assert_awaited_once()  # secret never left in chat
        assert db_get_user_wallets(tg_user.id)[0]["verified"] == 2
        assert db_get_key(kp.public_key) == kp.secret

    async def test_wrong_secret_key_fails_but_still_deletes(self, bot, tg_user):
        kp = Keypair.random()
        from database.wallets import db_add_wallet
        db_add_wallet(tg_user.id, kp.public_key)
        wallet_mod._awaiting_secret_key[tg_user.id] = kp.public_key
        msg = make_message(Keypair.random().secret)
        await handle_private_message(make_update(user=tg_user, message=msg), make_context(bot))
        msg.delete.assert_awaited_once()
        assert db_get_user_wallets(tg_user.id)[0]["verified"] == 0
        assert db_get_key(kp.public_key) is None


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
        db_upsert_user(tg_user)
        admin = make_tg_user(ADMIN_ID)
        msg = make_message("big news", thread_id=900)
        await handle_admin_group_message(make_update(user=admin, message=msg, chat_type="group"),
                                         make_context(bot))
        sends = [c for c in bot.send_message.await_args_list
                 if c.kwargs.get("chat_id") == tg_user.id]
        assert len(sends) == 1
        assert sends[0].kwargs["text"] == "big news"


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
