import logging
from typing import Optional
from telegram import Bot, Message
from telegram.error import Forbidden, TelegramError
from config import ADMIN_GROUP_ID, log
from database.users import db_mark_blocked

async def _forward_to_topic(bot: Bot, msg: Message, topic_id: int) -> Optional[Message]:
    """Forward a user message into their topic. Returns the forwarded Message
    (so the caller can map topic_msg_id → user_msg_id for reply threading)."""
    try:
        return await msg.forward(chat_id=ADMIN_GROUP_ID, message_thread_id=topic_id)
    except TelegramError as e:
        log.error("Failed to forward message to topic %s: %s", topic_id, e)
        return None

async def _relay_to_user(bot: Bot, msg: Message, user_id: int, raise_on_block: bool = False,
                         reply_to_message_id: Optional[int] = None) -> None:
    # reply_to_message_id (not reply_parameters): works on both PTB 20.7 (prod)
    # and 22.x. allow_sending_without_reply: if the user deleted their original
    # message, deliver as a plain send instead of erroring.
    reply_kwargs = {}
    if reply_to_message_id is not None:
        reply_kwargs = {"reply_to_message_id": reply_to_message_id,
                        "allow_sending_without_reply": True}
    try:
        if msg.text:
            await bot.send_message(chat_id=user_id, text=msg.text, entities=msg.entities, **reply_kwargs)
        elif msg.photo:
            await bot.send_photo(chat_id=user_id, photo=msg.photo[-1].file_id, caption=msg.caption, caption_entities=msg.caption_entities, **reply_kwargs)
        elif msg.video:
            await bot.send_video(chat_id=user_id, video=msg.video.file_id, caption=msg.caption, caption_entities=msg.caption_entities, **reply_kwargs)
        elif msg.document:
            await bot.send_document(chat_id=user_id, document=msg.document.file_id, caption=msg.caption, caption_entities=msg.caption_entities, **reply_kwargs)
        elif msg.sticker:
            await bot.send_sticker(chat_id=user_id, sticker=msg.sticker.file_id, **reply_kwargs)
        elif msg.voice:
            await bot.send_voice(chat_id=user_id, voice=msg.voice.file_id, caption=msg.caption, caption_entities=msg.caption_entities, **reply_kwargs)
        elif msg.video_note:
            await bot.send_video_note(chat_id=user_id, video_note=msg.video_note.file_id, **reply_kwargs)
        elif msg.animation:
            await bot.send_animation(chat_id=user_id, animation=msg.animation.file_id, caption=msg.caption, caption_entities=msg.caption_entities, **reply_kwargs)
        elif msg.audio:
            await bot.send_audio(chat_id=user_id, audio=msg.audio.file_id, caption=msg.caption, caption_entities=msg.caption_entities, **reply_kwargs)
        elif msg.contact:
            await bot.send_contact(chat_id=user_id, phone_number=msg.contact.phone_number, first_name=msg.contact.first_name, last_name=msg.contact.last_name, **reply_kwargs)
        elif msg.location:
            await bot.send_location(chat_id=user_id, latitude=msg.location.latitude, longitude=msg.location.longitude, **reply_kwargs)
        else:
            await msg.forward(chat_id=user_id)
    except Forbidden:
        log.warning("User %s has blocked the bot", user_id)
        db_mark_blocked(user_id)
        if raise_on_block: raise
    except TelegramError as e:
        log.error("Failed to relay to user %s: %s", user_id, e)
        if raise_on_block: raise
