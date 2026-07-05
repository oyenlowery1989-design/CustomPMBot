import html
import io
import sqlite3
from telegram import Bot, Update
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import ContextTypes
from config import ADMIN_IDS
from database.users import db_get_user_by_topic
from database.canned import db_canned_list, db_canned_set, db_canned_delete, db_canned_get
from database.messages import db_log_message
from utils.helpers import _is_admin, _media_of

_TYPE_ICONS = {"photo": "🖼", "video": "🎬", "document": "📄", "animation": "🎞",
               "audio": "🎵", "voice": "🎤", "sticker": "💠"}

async def _send_canned_to(bot: Bot, chat_id: int, row: sqlite3.Row) -> None:
    """Deliver a canned response — plain text or replayed media by file_id."""
    ct = row["content_type"] or "text"
    fid = row["file_id"]
    body = row["body"]
    if ct == "text" or not fid:
        await bot.send_message(chat_id=chat_id, text=body)
    elif ct == "photo":
        await bot.send_photo(chat_id=chat_id, photo=fid, caption=body or None)
    elif ct == "video":
        await bot.send_video(chat_id=chat_id, video=fid, caption=body or None)
    elif ct == "document":
        await bot.send_document(chat_id=chat_id, document=fid, caption=body or None)
    elif ct == "animation":
        await bot.send_animation(chat_id=chat_id, animation=fid, caption=body or None)
    elif ct == "audio":
        await bot.send_audio(chat_id=chat_id, audio=fid, caption=body or None)
    elif ct == "voice":
        await bot.send_voice(chat_id=chat_id, voice=fid, caption=body or None)
    elif ct == "sticker":
        await bot.send_sticker(chat_id=chat_id, sticker=fid)
    else:
        await bot.send_message(chat_id=chat_id, text=body)

async def cmd_canned(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not _is_admin(update.effective_user.id, ADMIN_IDS):
        return
    if not update.message:
        return

    args = ctx.args or []
    if not args:
        await update.message.reply_text(
            "Usage:\n/canned list\n/canned add <name> <text>\n"
            "/canned add <name> [caption] — as a reply to a photo/video/file to save media\n"
            "/canned del <name>\n/canned <name> — send to user in topic")
        return

    subcmd = args[0].lower()
    if subcmd == "list":
        items = db_canned_list()
        if not items:
            await update.message.reply_text("No canned responses saved.")
            return
        lines = ["📝 <b>Canned Responses</b>\n"]
        for item in items:
            icon = _TYPE_ICONS.get(item["content_type"] or "text", "")
            preview = item["body"][:50] + ("…" if len(item["body"]) > 50 else "")
            lines.append(f"• <b>{html.escape(item['name'])}</b>{icon}: {html.escape(preview)}")
        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)
        return

    if subcmd == "add":
        # Media variant: reply to a media message with /canned add <name> [caption]
        media = _media_of(update.message.reply_to_message) if update.message.reply_to_message else None
        if media:
            if len(args) < 2:
                await update.message.reply_text("Usage: /canned add <name> [caption] (as a reply to media)")
                return
            name, body = args[1], " ".join(args[2:])
            ct, fid = media
            db_canned_set(name, body, content_type=ct, file_id=fid)
            await update.message.reply_text(
                f"✅ Saved canned {ct}: <b>{html.escape(name)}</b>", parse_mode=ParseMode.HTML)
            return
        if len(args) < 3:
            await update.message.reply_text("Usage: /canned add <name> <text>")
            return
        name, body = args[1], " ".join(args[2:])
        db_canned_set(name, body)
        await update.message.reply_text(f"✅ Saved canned response: <b>{html.escape(name)}</b>", parse_mode=ParseMode.HTML)
        return

    if subcmd == "del":
        if len(args) < 2:
            await update.message.reply_text("Usage: /canned del <name>")
            return
        if db_canned_delete(args[1]): await update.message.reply_text(f"🗑 Deleted canned response: {args[1]}")
        else: await update.message.reply_text("Not found.")
        return

    name = subcmd
    row = db_canned_get(name)
    if row is None:
        await update.message.reply_text(f"Canned response '{name}' not found. Use /canned list")
        return

    thread_id = update.effective_message.message_thread_id
    if not thread_id:
        await update.message.reply_text("Use /canned <name> inside a user's topic to send it.")
        return

    user_row = db_get_user_by_topic(thread_id)
    if not user_row:
        await update.message.reply_text("Can't determine user for this topic.")
        return

    try:
        await _send_canned_to(ctx.bot, user_row["user_id"], row)
        db_log_message(user_row["user_id"], "out", row["content_type"] or "text", row["body"])
        await update.message.reply_text(f"✅ Sent canned response '{name}' to user.")
    except TelegramError as e:
        await update.message.reply_text(f"Failed to send: {e}")
