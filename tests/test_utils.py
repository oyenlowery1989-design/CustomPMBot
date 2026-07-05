"""Tests for utils: helpers, strings (branding texts), media relay, events."""
from datetime import datetime
from unittest.mock import AsyncMock

import pytest
from telegram.error import Forbidden, TelegramError

import utils.strings as strings
from utils.helpers import (
    _now_iso, _parse_duration, _format_duration, _user_link, _user_display,
    _content_type_of, _is_admin,
)
from utils.media import _forward_to_topic, _relay_to_user
from utils.events import _get_output_topic, _send_event
from database.users import db_upsert_user, db_get_user
from database.topics import db_create_custom_topic, db_bind_topic
from tests.conftest import make_tg_user, make_message, make_bot, ADMIN_GROUP_ID


class TestHelpers:
    def test_now_iso_is_utc_isoformat(self):
        ts = _now_iso()
        parsed = datetime.fromisoformat(ts)
        assert parsed.tzinfo is not None
        assert parsed.utcoffset().total_seconds() == 0

    @pytest.mark.parametrize("s,expected", [
        ("5m", 300), ("2h", 7200), ("7d", 604800), ("1w", 604800),
        (" 3 d ", 259200), ("10M", 600),
    ])
    def test_parse_duration_valid(self, s, expected):
        assert _parse_duration(s) == expected

    @pytest.mark.parametrize("s", ["", "abc", "5", "m5", "5y", "5 minutes", "-5m"])
    def test_parse_duration_invalid(self, s):
        assert _parse_duration(s) is None

    @pytest.mark.parametrize("seconds,expected", [
        (604800, "1w"), (1209600, "2w"), (86400, "1d"), (172800, "2d"),
        (3600, "1h"), (7200, "2h"), (300, "5m"), (90, "1m"),
    ])
    def test_format_duration(self, seconds, expected):
        assert _format_duration(seconds) == expected

    def test_parse_format_roundtrip(self):
        for s in ("5m", "2h", "3d", "1w"):
            assert _format_duration(_parse_duration(s)) == s

    def test_user_link_escapes_html(self):
        link = _user_link(42, "<Evil> & Co")
        assert 'tg://user?id=42' in link
        assert "&lt;Evil&gt; &amp; Co" in link
        assert "<Evil>" not in link

    def test_user_display(self):
        db_upsert_user(make_tg_user(1, "Bob", "Smith"))
        assert _user_display(db_get_user(1)) == "Bob Smith"
        db_upsert_user(make_tg_user(2, "Solo"))
        assert _user_display(db_get_user(2)) == "Solo"

    def test_user_display_unknown(self):
        db_upsert_user(make_tg_user(3, None))
        assert _user_display(db_get_user(3)) == "Unknown"

    @pytest.mark.parametrize("field,expected", [
        ("photo", "photo"), ("video", "video"), ("document", "document"),
        ("sticker", "sticker"), ("voice", "voice"), ("video_note", "video_note"),
        ("animation", "animation"), ("audio", "audio"), ("contact", "contact"),
        ("location", "location"),
    ])
    def test_content_type_media(self, field, expected):
        msg = make_message(text=None, **{field: object()})
        assert _content_type_of(msg) == expected

    def test_content_type_text_and_other(self):
        assert _content_type_of(make_message(text="hi")) == "text"
        assert _content_type_of(make_message(text=None)) == "other"

    def test_is_admin(self):
        assert _is_admin(1, {1, 2}) is True
        assert _is_admin(3, {1, 2}) is False


class TestStrings:
    def test_load_texts_from_branding(self):
        strings.load_texts()
        assert strings._texts_cache.get("bot_name") == "NoPMsBot"

    def test_get_text_nested_key(self):
        assert strings.get_text("settings.status_on") != "settings.status_on"

    def test_get_text_missing_returns_default(self):
        assert strings.get_text("no.such.key", default="fb") == "fb"

    def test_get_text_missing_no_default_returns_keypath(self):
        assert strings.get_text("no.such.key") == "no.such.key"

    def test_get_text_placeholder_formatting(self):
        out = strings.get_text("wallet.saved", address="GABC", label="Main")
        assert "GABC" in out
        assert "Main" in out

    def test_get_text_missing_placeholder_falls_back(self):
        # wallet.saved needs {address}/{label}; omitting them must not raise
        assert strings.get_text("wallet.saved", default="fb") == "fb"

    def test_bot_name_injected(self, monkeypatch):
        monkeypatch.setitem(strings._texts_cache, "test_key", "I am {bot_name}")
        strings._texts_cache.setdefault("bot_name", "NoPMsBot")
        assert strings.get_text("test_key") == "I am NoPMsBot"


class TestForwardToTopic:
    async def test_forwards(self, bot):
        msg = make_message()
        await _forward_to_topic(bot, msg, 55)
        msg.forward.assert_awaited_once_with(chat_id=ADMIN_GROUP_ID, message_thread_id=55)

    async def test_error_swallowed(self, bot):
        msg = make_message()
        msg.forward = AsyncMock(side_effect=TelegramError("boom"))
        await _forward_to_topic(bot, msg, 55)  # must not raise


class TestRelayToUser:
    async def test_text(self, bot):
        await _relay_to_user(bot, make_message(text="hello"), 9)
        bot.send_message.assert_awaited_once()
        assert bot.send_message.await_args.kwargs["chat_id"] == 9
        assert bot.send_message.await_args.kwargs["text"] == "hello"

    async def test_photo_uses_largest_size(self, bot):
        photo = [type("P", (), {"file_id": "small"})(), type("P", (), {"file_id": "big"})()]
        await _relay_to_user(bot, make_message(text=None, caption="cap", photo=photo), 9)
        assert bot.send_photo.await_args.kwargs["photo"] == "big"
        assert bot.send_photo.await_args.kwargs["caption"] == "cap"

    async def test_unknown_type_falls_back_to_forward(self, bot):
        msg = make_message(text=None)
        await _relay_to_user(bot, msg, 9)
        msg.forward.assert_awaited_once_with(chat_id=9)

    async def test_forbidden_marks_blocked(self, bot):
        db_upsert_user(make_tg_user(9))
        bot.send_message = AsyncMock(side_effect=Forbidden("blocked"))
        await _relay_to_user(bot, make_message(text="x"), 9)
        assert db_get_user(9)["blocked"] == 1

    async def test_forbidden_raises_when_asked(self, bot):
        db_upsert_user(make_tg_user(9))
        bot.send_message = AsyncMock(side_effect=Forbidden("blocked"))
        with pytest.raises(Forbidden):
            await _relay_to_user(bot, make_message(text="x"), 9, raise_on_block=True)

    async def test_other_error_swallowed_unless_raise(self, bot):
        bot.send_message = AsyncMock(side_effect=TelegramError("api down"))
        await _relay_to_user(bot, make_message(text="x"), 9)  # swallowed
        with pytest.raises(TelegramError):
            await _relay_to_user(bot, make_message(text="x"), 9, raise_on_block=True)


class TestEvents:
    async def test_output_topic_prefers_binding(self, bot):
        db_create_custom_topic("stats", 70)
        db_bind_topic("command", "stats", "stats")
        assert await _get_output_topic(bot, "stats", fallback_thread_id=5) == 70

    async def test_output_topic_fallback(self, bot):
        assert await _get_output_topic(bot, "stats", fallback_thread_id=5) == 5

    async def test_send_event_with_binding(self, bot):
        db_create_custom_topic("logs", 80)
        db_bind_topic("event", "new_user", "logs")
        await _send_event(bot, "new_user", "user joined")
        kwargs = bot.send_message.await_args.kwargs
        assert kwargs["message_thread_id"] == 80
        assert kwargs["text"] == "user joined"

    async def test_send_event_without_binding_is_noop(self, bot):
        await _send_event(bot, "new_user", "user joined")
        bot.send_message.assert_not_awaited()

    async def test_send_event_error_swallowed(self, bot):
        db_create_custom_topic("logs", 80)
        db_bind_topic("event", "new_user", "logs")
        bot.send_message = AsyncMock(side_effect=TelegramError("boom"))
        await _send_event(bot, "new_user", "x")  # must not raise
