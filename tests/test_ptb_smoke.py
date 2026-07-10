"""Real-PTB-object smoke tests. Every other test file drives handlers with
lightweight SimpleNamespace/AsyncMock doubles (tests/conftest.py) — fast, but
give zero signal on an actual python-telegram-bot behavior change, since the
installed version (22.5) doesn't match what's documented as running in
prod (M6, docs/AUDIT-2026-07-10.md — see also H7, now resolved by pinning
requirements.txt to 22.5). This file builds genuine telegram.Update/Message/
Bot objects and drives them through real PTB filter/handler machinery.

Also serves as a real-object regression test for C1/H1: CommandHandler's
own default filter (`filters.UpdateType.MESSAGES`, plural) admits edited
messages — that PTB default is exactly why the original bug was possible.
Our bot.py registers every handler with an explicit `NOT_EDITED` filter
override instead of relying on that default."""
from datetime import datetime, timezone

from telegram import Bot, Chat, Message, MessageEntity, Update, User
from telegram.ext import CommandHandler, MessageHandler, filters

import bot as bot_module


def _fake_bot(bot_id=999, username="test_bot"):
    """A real telegram.Bot with a locally cached username — enough for
    CommandHandler's own command-matching (which calls message.get_bot()),
    without any network call."""
    b = Bot(token="123456:TEST-TOKEN-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA")
    b._bot_user = User(id=bot_id, first_name="TestBot", is_bot=True, username=username)
    return b


def _real_message(text, chat_id, chat_type, bot, thread_id=None, is_topic_message=False):
    entities = None
    if text.startswith("/"):
        command_len = len(text.split()[0])
        entities = [MessageEntity(type=MessageEntity.BOT_COMMAND, offset=0, length=command_len)]
    msg = Message(
        message_id=1, date=datetime.now(timezone.utc),
        chat=Chat(id=chat_id, type=chat_type), from_user=User(id=42, first_name="Alice", is_bot=False),
        text=text, entities=entities, message_thread_id=thread_id, is_topic_message=is_topic_message,
    )
    msg.set_bot(bot)
    return msg


class TestPTBDefaultFilterBehavior:
    """Documents the actual root cause of C1/H1: PTB's own default, not a
    mistake unique to this codebase."""

    def test_command_handler_default_filter_admits_edited_messages(self):
        bot = _fake_bot()
        msg = _real_message("/start", chat_id=42, chat_type=Chat.PRIVATE, bot=bot)
        edited_update = Update(update_id=1, edited_message=msg)

        async def cb(u, c): pass
        handler_with_no_explicit_filter = CommandHandler("start", cb)

        assert handler_with_no_explicit_filter.check_update(edited_update)


class TestRegisteredHandlersRejectRealEditedUpdates:
    """bot.build_application()'s actual registered handlers, driven with
    real Update/Message/Bot objects instead of the duck-typed ones in
    test_bot_handlers_registration.py."""

    def test_start_command_rejects_a_real_edited_update(self):
        app = bot_module.build_application()
        fake_bot = _fake_bot()
        msg = _real_message("/start", chat_id=42, chat_type=Chat.PRIVATE, bot=fake_bot)
        edited_update = Update(update_id=1, edited_message=msg)

        start_handlers = [h for h in app.handlers[0]
                          if isinstance(h, CommandHandler) and "start" in h.commands]
        assert len(start_handlers) == 1
        assert not start_handlers[0].check_update(edited_update)

    def test_start_command_accepts_a_real_normal_update(self):
        app = bot_module.build_application()
        fake_bot = _fake_bot()
        msg = _real_message("/start", chat_id=42, chat_type=Chat.PRIVATE, bot=fake_bot)
        normal_update = Update(update_id=1, message=msg)

        start_handlers = [h for h in app.handlers[0]
                          if isinstance(h, CommandHandler) and "start" in h.commands]
        assert start_handlers[0].check_update(normal_update)

    def test_admin_relay_message_handler_rejects_a_real_edited_group_message(self):
        from config import ADMIN_GROUP_ID

        app = bot_module.build_application()
        fake_bot = _fake_bot()
        msg = _real_message(
            "we can help", chat_id=ADMIN_GROUP_ID, chat_type=Chat.SUPERGROUP, bot=fake_bot,
            thread_id=55, is_topic_message=True,
        )
        edited_update = Update(update_id=1, edited_message=msg)
        normal_update = Update(update_id=2, message=msg)

        relay_handlers = [
            h for h in app.handlers[0]
            if isinstance(h, MessageHandler) and getattr(h.callback, "__name__", "") == "handle_admin_group_message"
        ]
        assert len(relay_handlers) == 1
        assert not relay_handlers[0].check_update(edited_update)
        assert relay_handlers[0].check_update(normal_update)
