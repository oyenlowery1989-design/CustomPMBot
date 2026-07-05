"""Tests for /help overview and /help <command> details."""
from handlers.help_topics import HELP_TOPICS, admin_overview
from handlers.user import cmd_help
from tests.conftest import make_bot, make_context, make_message, make_tg_user, make_update

ADMIN_ID = 1000

# Every admin command registered in bot.py must have a help topic —
# this list is the contract; extend it when adding commands.
REGISTERED_ADMIN_COMMANDS = {
    "stats", "topic", "wallets", "ban", "unban", "banned", "setmsg",
    "forcebroadcast", "schedule", "autoreply", "users", "search",
    "analytics", "tag", "export", "canned", "close", "reopen", "note",
}
REGISTERED_USER_COMMANDS = {"start", "help", "settings", "wallet", "cancel"}


class TestTopicCoverage:
    def test_every_admin_command_documented(self):
        missing = REGISTERED_ADMIN_COMMANDS - set(HELP_TOPICS)
        assert not missing, f"Admin commands without help topics: {missing}"

    def test_every_user_command_documented(self):
        missing = REGISTERED_USER_COMMANDS - set(HELP_TOPICS)
        assert not missing, f"User commands without help topics: {missing}"

    def test_overview_mentions_new_commands(self):
        text = admin_overview()
        for cmd in ("/schedule", "/autoreply", "/users", "/search", "/analytics"):
            assert cmd in text

    def test_overview_stays_under_telegram_limit(self):
        assert len(admin_overview()) < 3000  # leaves room for the user section


class TestHelpCommand:
    async def test_admin_overview_lists_everything(self, bot):
        update = make_update(user=make_tg_user(ADMIN_ID), message=make_message("/help"))
        await cmd_help(update, make_context(bot))
        text = update.message.reply_text.await_args.args[0]
        assert "/schedule" in text and "/autoreply" in text
        assert len(text) < 4096  # Telegram message limit

    async def test_user_overview_hides_admin_commands(self, bot, tg_user):
        update = make_update(user=tg_user, message=make_message("/help"))
        await cmd_help(update, make_context(bot))
        text = update.message.reply_text.await_args.args[0]
        assert "/schedule" not in text and "/ban" not in text

    async def test_detail_topic(self, bot):
        update = make_update(user=make_tg_user(ADMIN_ID), message=make_message("/help"))
        await cmd_help(update, make_context(bot, args=["schedule"]))
        text = update.message.reply_text.await_args.args[0]
        assert "/schedule cancel" in text
        assert "2h" in text

    async def test_detail_accepts_leading_slash(self, bot):
        update = make_update(user=make_tg_user(ADMIN_ID), message=make_message("/help"))
        await cmd_help(update, make_context(bot, args=["/ban"]))
        assert "Appeal" in update.message.reply_text.await_args.args[0]

    async def test_admin_detail_hidden_from_users(self, bot, tg_user):
        update = make_update(user=tg_user, message=make_message("/help"))
        await cmd_help(update, make_context(bot, args=["ban"]))
        assert "No help available" in update.message.reply_text.await_args.args[0]

    async def test_user_detail_available_to_users(self, bot, tg_user):
        update = make_update(user=tg_user, message=make_message("/help"))
        await cmd_help(update, make_context(bot, args=["wallet"]))
        assert "max 5" in update.message.reply_text.await_args.args[0]

    async def test_unknown_topic(self, bot):
        update = make_update(user=make_tg_user(ADMIN_ID), message=make_message("/help"))
        await cmd_help(update, make_context(bot, args=["frobnicate"]))
        assert "No help available" in update.message.reply_text.await_args.args[0]

    async def test_detail_texts_fit_telegram_limit(self):
        for name, t in HELP_TOPICS.items():
            assert len(t["detail"]) < 3500, f"{name} detail too long"
