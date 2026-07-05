"""Tests for user-facing commands (/start, /help, /settings) and the wallet
menu callback flow."""
from unittest.mock import AsyncMock

import handlers.wallet as wallet_mod
from handlers.user import cmd_start, cmd_help, cmd_settings
from handlers.wallet import (
    cmd_wallet, cmd_wallets, cmd_cancel,
    cb_wallet_add, cb_wallet_view, cb_wallet_remove, cb_wallet_verify,
    cb_verify_memo, cb_verify_key, cb_settings, cb_toggle_broadcast,
    cb_help, cb_back_start, cb_appeal,
)
from database.settings import db_set_setting
from database.users import db_upsert_user, db_get_user
from database.wallets import (
    db_add_wallet, db_get_user_wallets, db_get_pending_verifications,
    db_set_awaiting_key, db_get_awaiting_key,
)
from tests.conftest import (
    ADMIN_GROUP_ID, make_bot, make_callback_query, make_context, make_message,
    make_tg_user, make_update,
)

ADMIN_ID = 1000
USER_ID = 500
VALID_ADDR = "G" + "A" * 55


def _buttons(reply_markup):
    return [b for row in reply_markup.inline_keyboard for b in row]


class TestStart:
    async def test_registers_user_and_shows_menu(self, bot, tg_user):
        update = make_update(user=tg_user, message=make_message("/start"))
        await cmd_start(update, make_context(bot))
        assert db_get_user(tg_user.id) is not None
        markup = update.message.reply_text.await_args.kwargs["reply_markup"]
        assert "wallet_add" in {b.callback_data for b in _buttons(markup)}

    async def test_existing_wallet_shows_view_button(self, bot, tg_user):
        db_add_wallet(tg_user.id, VALID_ADDR)
        update = make_update(user=tg_user, message=make_message("/start"))
        await cmd_start(update, make_context(bot))
        markup = update.message.reply_text.await_args.kwargs["reply_markup"]
        assert "wallet_view" in {b.callback_data for b in _buttons(markup)}

    async def test_custom_welcome_used(self, bot, tg_user):
        db_set_setting("welcome_message", "Custom hello!")
        update = make_update(user=tg_user, message=make_message("/start"))
        await cmd_start(update, make_context(bot))
        assert update.message.reply_text.await_args.args[0] == "Custom hello!"

    async def test_ignored_outside_private(self, bot, tg_user):
        update = make_update(user=tg_user, message=make_message("/start"), chat_type="group")
        await cmd_start(update, make_context(bot))
        update.message.reply_text.assert_not_awaited()


class TestHelp:
    async def test_user_help_has_no_admin_section(self, bot, tg_user):
        update = make_update(user=tg_user, message=make_message("/help"))
        await cmd_help(update, make_context(bot))
        text = update.message.reply_text.await_args.args[0]
        assert "/start" in text
        assert "/ban" not in text

    async def test_admin_help_includes_admin_commands(self, bot):
        admin = make_tg_user(ADMIN_ID)
        update = make_update(user=admin, message=make_message("/help"))
        await cmd_help(update, make_context(bot))
        text = update.message.reply_text.await_args.args[0]
        assert "/ban" in text


class TestSettingsCmd:
    async def test_direct_on_off(self, bot, tg_user):
        db_upsert_user(tg_user)
        update = make_update(user=tg_user, message=make_message("/settings"))
        await cmd_settings(update, make_context(bot, args=["off"]))
        assert db_get_user(tg_user.id)["broadcast_opt"] == 0
        await cmd_settings(update, make_context(bot, args=["on"]))
        assert db_get_user(tg_user.id)["broadcast_opt"] == 1

    async def test_menu_shows_toggle(self, bot, tg_user):
        db_upsert_user(tg_user)
        update = make_update(user=tg_user, message=make_message("/settings"))
        await cmd_settings(update, make_context(bot))
        markup = update.message.reply_text.await_args.kwargs["reply_markup"]
        assert "toggle_broadcast" in {b.callback_data for b in _buttons(markup)}


class TestWalletMenu:
    async def test_no_wallets_offers_add(self, bot, tg_user):
        update = make_update(user=tg_user, message=make_message("/wallet"))
        await cmd_wallet(update, make_context(bot))
        markup = update.message.reply_text.await_args.kwargs["reply_markup"]
        assert {b.callback_data for b in _buttons(markup)} == {"wallet_add"}

    async def test_lists_wallets_with_verify_remove(self, bot, tg_user):
        db_add_wallet(tg_user.id, VALID_ADDR, "Main")
        update = make_update(user=tg_user, message=make_message("/wallet"))
        await cmd_wallet(update, make_context(bot))
        wid = db_get_user_wallets(tg_user.id)[0]["id"]
        data = {b.callback_data for b in _buttons(update.message.reply_text.await_args.kwargs["reply_markup"])}
        assert f"w_v_{wid}" in data
        assert f"w_r_{wid}" in data
        assert "wallet_add" in data  # under the 5-wallet cap

    async def test_verified_wallet_has_no_verify_button(self, bot, tg_user):
        from database.wallets import db_set_wallet_verified
        db_add_wallet(tg_user.id, VALID_ADDR)
        db_set_wallet_verified(tg_user.id, VALID_ADDR, 2)
        update = make_update(user=tg_user, message=make_message("/wallet"))
        await cmd_wallet(update, make_context(bot))
        wid = db_get_user_wallets(tg_user.id)[0]["id"]
        data = {b.callback_data for b in _buttons(update.message.reply_text.await_args.kwargs["reply_markup"])}
        assert f"w_v_{wid}" not in data

    async def test_admin_wallets_list(self, bot):
        db_add_wallet(1, "G" + "A" * 55, "Main")
        db_add_wallet(2, "G" + "B" * 55, "Fun<d>s")
        admin = make_tg_user(ADMIN_ID)
        update = make_update(user=admin, message=make_message("/wallets"), chat_type="group")
        await cmd_wallets(update, make_context(bot))
        text = update.message.reply_text.await_args.args[0]
        assert "G" + "A" * 55 in text
        assert "Fun&lt;d&gt;s" in text  # labels HTML-escaped

    async def test_wallets_non_admin_ignored(self, bot, tg_user):
        update = make_update(user=tg_user, message=make_message("/wallets"))
        await cmd_wallets(update, make_context(bot))
        update.message.reply_text.assert_not_awaited()


class TestCancel:
    async def test_clears_all_pending_state(self, bot, tg_user):
        wallet_mod._awaiting_wallet_addr.add(tg_user.id)
        wallet_mod._awaiting_wallet_label[tg_user.id] = VALID_ADDR
        db_set_awaiting_key(tg_user.id, VALID_ADDR)
        update = make_update(user=tg_user, message=make_message("/cancel"))
        await cmd_cancel(update, make_context(bot))
        assert tg_user.id not in wallet_mod._awaiting_wallet_addr
        assert tg_user.id not in wallet_mod._awaiting_wallet_label
        assert db_get_awaiting_key(tg_user.id) is None


class TestWalletCallbacks:
    async def test_add_starts_waiting_for_address(self, bot, tg_user):
        q = make_callback_query(tg_user, data="wallet_add")
        await cb_wallet_add(make_update(user=tg_user, callback_query=q), make_context(bot))
        assert tg_user.id in wallet_mod._awaiting_wallet_addr

    async def test_add_blocked_at_limit(self, bot, tg_user):
        for i in range(5):
            db_add_wallet(tg_user.id, "G" + chr(65 + i) * 55)
        q = make_callback_query(tg_user, data="wallet_add")
        await cb_wallet_add(make_update(user=tg_user, callback_query=q), make_context(bot))
        assert tg_user.id not in wallet_mod._awaiting_wallet_addr

    async def test_remove_own_wallet(self, bot, tg_user):
        db_add_wallet(tg_user.id, VALID_ADDR)
        wid = db_get_user_wallets(tg_user.id)[0]["id"]
        q = make_callback_query(tg_user, data=f"w_r_{wid}")
        await cb_wallet_remove(make_update(user=tg_user, callback_query=q), make_context(bot))
        assert db_get_user_wallets(tg_user.id) == []

    async def test_cannot_remove_others_wallet(self, bot, tg_user):
        db_add_wallet(999, VALID_ADDR)
        wid = db_get_user_wallets(999)[0]["id"]
        q = make_callback_query(tg_user, data=f"w_r_{wid}")
        await cb_wallet_remove(make_update(user=tg_user, callback_query=q), make_context(bot))
        assert len(db_get_user_wallets(999)) == 1

    async def test_verify_shows_method_choice(self, bot, tg_user):
        db_add_wallet(tg_user.id, VALID_ADDR)
        wid = db_get_user_wallets(tg_user.id)[0]["id"]
        q = make_callback_query(tg_user, data=f"w_v_{wid}")
        await cb_wallet_verify(make_update(user=tg_user, callback_query=q), make_context(bot))
        markup = q.edit_message_text.await_args.kwargs["reply_markup"]
        data = {b.callback_data for b in _buttons(markup)}
        assert f"v_m_{wid}" in data and f"v_k_{wid}" in data

    async def test_verify_without_verify_wallet_configured(self, bot, tg_user, monkeypatch):
        monkeypatch.setattr(wallet_mod, "VERIFY_WALLET_PUBLIC", None)
        db_add_wallet(tg_user.id, VALID_ADDR)
        wid = db_get_user_wallets(tg_user.id)[0]["id"]
        q = make_callback_query(tg_user, data=f"w_v_{wid}")
        await cb_wallet_verify(make_update(user=tg_user, callback_query=q), make_context(bot))
        # falls back to the "not available" text, no method keyboard
        assert "reply_markup" not in q.edit_message_text.await_args.kwargs

    async def test_memo_creates_challenge(self, bot, tg_user):
        db_add_wallet(tg_user.id, VALID_ADDR)
        wid = db_get_user_wallets(tg_user.id)[0]["id"]
        q = make_callback_query(tg_user, data=f"v_m_{wid}")
        await cb_verify_memo(make_update(user=tg_user, callback_query=q), make_context(bot))
        pending = db_get_pending_verifications()
        assert len(pending) == 1
        challenge = pending[0]["challenge"]
        assert len(challenge) == 6 and challenge.isdigit()
        assert challenge in q.edit_message_text.await_args.args[0]

    async def test_key_verify_starts_waiting(self, bot, tg_user):
        db_add_wallet(tg_user.id, VALID_ADDR)
        wid = db_get_user_wallets(tg_user.id)[0]["id"]
        q = make_callback_query(tg_user, data=f"v_k_{wid}")
        await cb_verify_key(make_update(user=tg_user, callback_query=q), make_context(bot))
        assert db_get_awaiting_key(tg_user.id) == VALID_ADDR

    async def test_cannot_start_memo_verify_on_others_wallet(self, bot, tg_user):
        db_add_wallet(999, VALID_ADDR)
        wid = db_get_user_wallets(999)[0]["id"]
        q = make_callback_query(tg_user, data=f"v_m_{wid}")
        await cb_verify_memo(make_update(user=tg_user, callback_query=q), make_context(bot))
        assert db_get_pending_verifications() == []

    async def test_cannot_start_key_verify_on_others_wallet(self, bot, tg_user):
        db_add_wallet(999, VALID_ADDR)
        wid = db_get_user_wallets(999)[0]["id"]
        q = make_callback_query(tg_user, data=f"v_k_{wid}")
        await cb_verify_key(make_update(user=tg_user, callback_query=q), make_context(bot))
        assert db_get_awaiting_key(tg_user.id) is None


class TestSettingsCallbacks:
    async def test_toggle_flips_optin(self, bot, tg_user):
        db_upsert_user(tg_user)
        q = make_callback_query(tg_user, data="toggle_broadcast")
        await cb_toggle_broadcast(make_update(user=tg_user, callback_query=q), make_context(bot))
        assert db_get_user(tg_user.id)["broadcast_opt"] == 0
        await cb_toggle_broadcast(make_update(user=tg_user, callback_query=q), make_context(bot))
        assert db_get_user(tg_user.id)["broadcast_opt"] == 1

    async def test_settings_menu_renders(self, bot, tg_user):
        db_upsert_user(tg_user)
        q = make_callback_query(tg_user, data="settings")
        await cb_settings(make_update(user=tg_user, callback_query=q), make_context(bot))
        q.edit_message_text.assert_awaited_once()

    async def test_help_callback_renders(self, bot, tg_user):
        q = make_callback_query(tg_user, data="help")
        await cb_help(make_update(user=tg_user, callback_query=q), make_context(bot))
        assert "/start" in q.edit_message_text.await_args.args[0]

    async def test_back_start_renders_menu(self, bot, tg_user):
        q = make_callback_query(tg_user, data="back_start")
        await cb_back_start(make_update(user=tg_user, callback_query=q), make_context(bot))
        q.edit_message_text.assert_awaited_once()
        assert db_get_user(tg_user.id) is not None


class TestAppeal:
    async def test_appeal_alerts_admin_topic(self, bot, tg_user):
        q = make_callback_query(tg_user, data=f"appeal_{tg_user.id}")
        await cb_appeal(make_update(user=tg_user, callback_query=q), make_context(bot))
        # topic ensured + alert sent into admin group
        alerts = [c for c in bot.send_message.await_args_list
                  if c.kwargs.get("chat_id") == ADMIN_GROUP_ID
                  and "BAN APPEAL" in c.kwargs.get("text", "")]
        assert len(alerts) == 1
        q.edit_message_text.assert_awaited_once()
