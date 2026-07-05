import html
import io
from telegram import Update
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import ContextTypes
from config import ADMIN_IDS
from database.users import db_get_user_by_topic
from database.canned import db_canned_list, db_canned_set, db_canned_delete, db_canned_get
from database.messages import db_log_message
from utils.helpers import _is_admin

async def cmd_canned(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not _is_admin(update.effective_user.id, ADMIN_IDS):
        return
    if not update.message:
        return

    args = ctx.args or []
    if not args:
        await update.message.reply_text("Usage:\n/canned list\n/canned add <name> <text>\n/canned del <name>\n/canned <name> — send to user in topic")
        return

    subcmd = args[0].lower()
    if subcmd == "list":
        items = db_canned_list()
        if not items:
            await update.message.reply_text("No canned responses saved.")
            return
        lines = ["📝 <b>Canned Responses</b>\n"]
        for item in items:
            preview = item["body"][:50] + ("…" if len(item["body"]) > 50 else "")
            lines.append(f"• <b>{html.escape(item['name'])}</b>: {html.escape(preview)}")
        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)
        return

    if subcmd == "add":
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
    body = db_canned_get(name)
    if body is None:
        await update.message.reply_text(f"Canned response '{name}' not found. Use /canned list")
        return

    thread_id = update.effective_message.message_thread_id
    if not thread_id:
        await update.message.reply_text("Use /canned <name> inside a user's topic to send it.")
        return

    row = db_get_user_by_topic(thread_id)
    if not row:
        await update.message.reply_text("Can't determine user for this topic.")
        return

    try:
        await ctx.bot.send_message(chat_id=row["user_id"], text=body)
        db_log_message(row["user_id"], "out", "text", body)
        await update.message.reply_text(f"✅ Sent canned response '{name}' to user.")
    except TelegramError as e:
        await update.message.reply_text(f"Failed to send: {e}")
