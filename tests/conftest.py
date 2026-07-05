# Test env must exist BEFORE importing config (config.py reads os.environ at import time).
import os
import sys

os.environ.setdefault("BOT_TOKEN", "123456:TEST-TOKEN")
os.environ.setdefault("OWNER_ID", "1000")
os.environ.setdefault("ADMIN_IDS", "1000,1001")
os.environ.setdefault("ADMIN_GROUP_ID", "-1009999")
# Valid Fernet key, generated once for tests only.
os.environ.setdefault("WALLET_ENCRYPTION_KEY", "FlOtZfckJ2iKvP5o1HFQz8XwtA6EYp5cvKCqj-yrg18=")
os.environ.setdefault("VERIFY_WALLET_PUBLIC", "GBVERIFYWALLETPUBLICTESTADDRESSXXXXXXXXXXXXXXXXXXXXXXXXX")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import database.connection as db_connection
from database.migrations import _run_migrations

ADMIN_ID = 1000
ADMIN_GROUP_ID = -1009999


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    """Fresh migrated SQLite DB per test; closes the singleton afterwards."""
    db_connection.close_db()
    monkeypatch.setattr(db_connection, "DB_PATH", str(tmp_path / "test.db"))
    db = db_connection.get_db()
    _run_migrations(db)
    yield db
    db_connection.close_db()


@pytest.fixture(autouse=True)
def reset_module_state():
    """Reset in-memory singletons that leak state between tests."""
    import handlers.broadcast as broadcast
    import handlers.wallet as wallet
    import services.spam as spam
    import utils.strings as strings

    broadcast._broadcast_topic_id = None
    broadcast._pending_broadcasts.clear()
    wallet._awaiting_wallet_addr.clear()
    wallet._awaiting_wallet_label.clear()
    wallet._awaiting_secret_key.clear()
    spam._spam_timestamps.clear()
    spam._spam_warnings.clear()
    strings._texts_cache.clear()
    yield


# ---------------------------------------------------------------------------
# Fake Telegram objects. Handlers only read attributes and await bot methods,
# so lightweight namespaces + AsyncMock are enough — no real network objects.
# ---------------------------------------------------------------------------

def make_tg_user(user_id=500, first_name="Alice", last_name=None, username="alice"):
    return SimpleNamespace(id=user_id, first_name=first_name, last_name=last_name, username=username)


MEDIA_FIELDS = (
    "photo", "video", "document", "sticker", "voice", "video_note",
    "animation", "audio", "contact", "location",
)


def make_message(text="hi", thread_id=None, caption=None, message_id=42,
                 reply_to_message=None, **media):
    msg = SimpleNamespace(
        text=text,
        caption=caption,
        message_thread_id=thread_id,
        message_id=message_id,
        reply_to_message=reply_to_message,
        entities=None,
        caption_entities=None,
        reply_text=AsyncMock(),
        reply_document=AsyncMock(),
        delete=AsyncMock(),
        # forward returns the forwarded copy — relay maps its message_id
        forward=AsyncMock(return_value=SimpleNamespace(message_id=8800)),
        edit_text=AsyncMock(),
    )
    for f in MEDIA_FIELDS:
        setattr(msg, f, media.get(f))
    return msg


def make_bot(next_topic_id=777):
    bot = MagicMock()
    bot.send_message = AsyncMock(return_value=make_message())
    bot.send_photo = AsyncMock()
    bot.send_video = AsyncMock()
    bot.send_document = AsyncMock()
    bot.send_sticker = AsyncMock()
    bot.send_voice = AsyncMock()
    bot.send_video_note = AsyncMock()
    bot.send_animation = AsyncMock()
    bot.send_audio = AsyncMock()
    bot.send_contact = AsyncMock()
    bot.send_location = AsyncMock()
    bot.send_chat_action = AsyncMock()
    bot.create_forum_topic = AsyncMock(return_value=SimpleNamespace(message_thread_id=next_topic_id))
    bot.edit_forum_topic = AsyncMock()
    bot.close_forum_topic = AsyncMock()
    bot.reopen_forum_topic = AsyncMock()
    bot.pin_chat_message = AsyncMock()
    return bot


def make_update(user=None, message=None, chat_type="private", callback_query=None):
    from telegram.constants import ChatType

    chat = SimpleNamespace(type=ChatType.PRIVATE if chat_type == "private" else ChatType.SUPERGROUP)
    return SimpleNamespace(
        effective_user=user,
        effective_message=message,
        message=message,
        effective_chat=chat,
        callback_query=callback_query,
    )


def make_callback_query(user, data="", message=None):
    return SimpleNamespace(
        from_user=user,
        data=data,
        message=message or make_message(),
        answer=AsyncMock(),
        edit_message_text=AsyncMock(),
    )


def make_context(bot=None, args=None):
    return SimpleNamespace(bot=bot or make_bot(), args=args or [])


@pytest.fixture
def bot():
    return make_bot()


@pytest.fixture
def tg_user():
    return make_tg_user()
