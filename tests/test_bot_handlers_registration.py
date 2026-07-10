"""Regression tests for bot.py handler registration — specifically that no
CommandHandler/MessageHandler reacts to an edited message. CommandHandler
dispatches on update.effective_message (which includes edited_message), but
every handler body reads update.message directly, which is None for an
edited update — without a filter excluding edits, that's an AttributeError
crash in production. See docs/AUDIT-2026-07-10.md, findings C1/H1."""
from types import SimpleNamespace

from telegram.ext import CommandHandler, MessageHandler

import bot


def _duck_message(chat_id, chat_type):
    return SimpleNamespace(
        chat=SimpleNamespace(id=chat_id, type=chat_type),
        message_thread_id=1,
        is_topic_message=True,
        text="/x",
        caption=None,
        entities=[],
        caption_entities=[],
    )


def _duck_update(message_slot, chat_id, chat_type):
    """Minimal object satisfying BaseFilter.check_update's attribute reads,
    with the *edited* message in `edited_message` and `message` left None —
    exactly what a real edited-message Update looks like."""
    msg = _duck_message(chat_id, chat_type)
    return SimpleNamespace(
        message=None if message_slot == "edited" else msg,
        edited_message=msg if message_slot == "edited" else None,
        channel_post=None, edited_channel_post=None,
        business_message=None, edited_business_message=None,
        effective_message=msg,
        effective_chat=msg.chat if msg else None,
    )


class TestNoHandlerReactsToEditedMessage:
    def test_every_command_and_message_handler_rejects_edits(self):
        from config import ADMIN_GROUP_ID

        app = bot.build_application()
        # Try both a private-chat and an admin-group edited update against
        # every handler — whichever context a handler actually cares about,
        # neither variant of an edit should ever get through.
        edited_variants = [
            _duck_update("edited", chat_id=42, chat_type="private"),
            _duck_update("edited", chat_id=ADMIN_GROUP_ID, chat_type="supergroup"),
        ]

        reacting = []
        for handler in app.handlers[0]:  # group 0: everything add_handler'd with no explicit group
            if isinstance(handler, (CommandHandler, MessageHandler)):
                if handler.filters is None:
                    label = getattr(handler.callback, "__name__", repr(handler.callback))
                    reacting.append(label)
                    continue
                for edited in edited_variants:
                    if handler.filters.check_update(edited):
                        label = getattr(handler.callback, "__name__", repr(handler.callback))
                        reacting.append(label)
                        break

        assert reacting == [], (
            f"these handlers still react to edited_message updates: {reacting}"
        )

    def test_a_normal_message_still_passes_its_handlers_own_filters(self):
        """Sanity check that the duck objects above aren't accidentally
        rejected for reasons unrelated to being an edit (e.g. wrong chat
        type) — a matching non-edited update must pass at least one variant
        for every handler that has real filters beyond NOT_EDITED alone."""
        from config import ADMIN_GROUP_ID

        app = bot.build_application()
        normal_variants = [
            _duck_update("message", chat_id=42, chat_type="private"),
            _duck_update("message", chat_id=ADMIN_GROUP_ID, chat_type="supergroup"),
        ]
        never_matched = []
        for handler in app.handlers[0]:
            if isinstance(handler, (CommandHandler, MessageHandler)) and handler.filters is not None:
                if not any(handler.filters.check_update(u) for u in normal_variants):
                    label = getattr(handler.callback, "__name__", repr(handler.callback))
                    never_matched.append(label)

        assert never_matched == [], (
            f"these handlers reject even a normal (non-edited) update — "
            f"duck object mismatch, not a real filter problem: {never_matched}"
        )
