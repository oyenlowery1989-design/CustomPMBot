import logging
from telegram import Bot, Message
from telegram.error import Forbidden, TelegramError
from config import ADMIN_GROUP_ID, log
from database.users import db_mark_blocked

async def _forward_to_topic(bot: Bot, msg: Message, topic_id: int) -> None:
    try:
        await msg.forward(chat_id=ADMIN_GROUP_ID, message_thread_id=topic_id)
    except TelegramError as e:
        log.error("Failed to forward message to topic %s: %s", topic_id, e)

async def _relay_to_user(bot: Bot, msg: Message, user_id: int, raise_on_block: bool = False) -> None:
    try:
        if msg.text:
            await bot.send_message(chat_id=user_id, text=msg.text, entities=msg.entities)
        elif msg.photo:
            await bot.send_photo(chat_id=user_id, photo=msg.photo[-1].file_id, caption=msg.caption, caption_entities=msg.caption_entities)
        elif msg.video:
            await bot.send_video(chat_id=user_id, video=msg.video.file_id, caption=msg.caption, caption_entities=msg.caption_entities)
        elif msg.document:
            await bot.send_document(chat_id=user_id, document=msg.document.file_id, caption=msg.caption, caption_entities=msg.caption_entities)
        elif msg.sticker:
            await bot.send_sticker(chat_id=user_id, sticker=msg.sticker.file_id)
        elif msg.voice:
            await bot.send_voice(chat_id=user_id, voice=msg.voice.file_id, caption=msg.caption, caption_entities=msg.caption_entities)
        elif msg.video_note:
            await bot.send_video_note(chat_id=user_id, video_note=msg.video_note.file_id)
        elif msg.animation:
            await bot.send_animation(chat_id=user_id, animation=msg.animation.file_id, caption=msg.caption, caption_entities=msg.caption_entities)
        elif msg.audio:
            await bot.send_audio(chat_id=user_id, audio=msg.audio.file_id, caption=msg.caption, caption_entities=msg.caption_entities)
        elif msg.contact:
            await bot.send_contact(chat_id=user_id, phone_number=msg.contact.phone_number, first_name=msg.contact.first_name, last_name=msg.contact.last_name)
        elif msg.location:
            await bot.send_location(chat_id=user_id, latitude=msg.location.latitude, longitude=msg.location.longitude)
        else:
            await msg.forward(chat_id=user_id)
    except Forbidden:
        log.warning("User %s has blocked the bot", user_id)
        db_mark_blocked(user_id)
        if raise_on_block: raise
    except TelegramError as e:
        log.error("Failed to relay to user %s: %s", user_id, e)
        if raise_on_block: raise
