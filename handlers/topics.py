import html
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import ContextTypes
from config import ADMIN_GROUP_ID, log, ADMIN_IDS
from database.users import db_get_user_by_topic, db_set_relay_paused
from database.topics import (
    db_list_custom_topics, db_create_custom_topic
)
from utils.helpers import _is_admin

async def _is_user_topic(tid: int) -> bool:
    """Helper to check if a thread ID belongs to a user session."""
    if not tid: return False
    # If it's in the users table, it's a user session
    return db_get_user_by_topic(tid) is not None

async def cmd_topic(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not _is_admin(update.effective_user.id, ADMIN_IDS): return
    args = ctx.args or []
    if not args:
        await update.message.reply_text("🗂 <b>Topic Manager</b>: /topic [create|list] <name>", parse_mode=ParseMode.HTML)
        return
    subcmd = args[0].lower()
    if subcmd == "create" and len(args) >= 2:
        name = " ".join(args[1:])
        if len(name) > 128:
            name = name[:128]
            log.warning("Custom topic name truncated to 128 chars: %r", name)
            await update.message.reply_text("⚠️ Topic name was longer than 128 chars — truncated.")
        try:
            topic = await ctx.bot.create_forum_topic(chat_id=ADMIN_GROUP_ID, name=name.title())
            db_create_custom_topic(name.lower(), topic.message_thread_id)
            await update.message.reply_text(f"✅ Created topic <b>{name}</b>", parse_mode=ParseMode.HTML)
        except Exception as e: await update.message.reply_text(f"Error: {e}")
    elif subcmd == "list":
        topics = db_list_custom_topics()
        text = "\n".join([f"• {t['name']} (ID: {t['topic_id']})" for t in topics]) if topics else "No custom topics."
        await update.message.reply_text(f"🗂 <b>Custom Topics</b>\n\n{text}", parse_mode=ParseMode.HTML)

async def cmd_close(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not _is_admin(update.effective_user.id, ADMIN_IDS): return
    tid = update.effective_message.message_thread_id
    if not tid or not await _is_user_topic(tid):
        await update.message.reply_text("⚠️ This command only works inside a user topic.")
        return
    
    user_row = db_get_user_by_topic(tid)
    try:
        new_name = f"✅ {user_row['first_name']} [CLOSED]"
        await ctx.bot.edit_forum_topic(chat_id=ADMIN_GROUP_ID, message_thread_id=tid, name=new_name)
        await ctx.bot.close_forum_topic(chat_id=ADMIN_GROUP_ID, message_thread_id=tid)
        db_set_relay_paused(user_row["user_id"], True) # Stop relaying until reopened
        await update.message.reply_text("📁 Topic archived and user relay paused.")
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")

async def cmd_reopen(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not _is_admin(update.effective_user.id, ADMIN_IDS): return
    tid = update.effective_message.message_thread_id
    if not tid or not await _is_user_topic(tid):
        await update.message.reply_text("⚠️ This command only works inside a user topic.")
        return
    
    user_row = db_get_user_by_topic(tid)
    try:
        new_name = f"{user_row['first_name']}"
        await ctx.bot.reopen_forum_topic(chat_id=ADMIN_GROUP_ID, message_thread_id=tid)
        await ctx.bot.edit_forum_topic(chat_id=ADMIN_GROUP_ID, message_thread_id=tid, name=new_name)
        db_set_relay_paused(user_row["user_id"], False)
        await update.message.reply_text("🔓 Topic reopened and relay active.")
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")

async def cmd_note(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not _is_admin(update.effective_user.id, ADMIN_IDS): return
    tid = update.effective_message.message_thread_id
    if not tid or not await _is_user_topic(tid):
        await update.message.reply_text("⚠️ This command only works inside a user topic.")
        return
    if not ctx.args:
        await update.message.reply_text("Usage: /note [text]")
        return
    
    text = " ".join(ctx.args)
    try:
        msg = await ctx.bot.send_message(
            chat_id=ADMIN_GROUP_ID, message_thread_id=tid, 
            text=f"📌 <b>Admin Note:</b>\n{html.escape(text)}", 
            parse_mode=ParseMode.HTML
        )
        await ctx.bot.pin_chat_message(chat_id=ADMIN_GROUP_ID, message_id=msg.message_id)
    except Exception as e:
        log.error("Note pin failed: %s", e)
