import html
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes
from config import ADMIN_IDS
from database.autoreplies import db_autoreply_set, db_autoreply_delete, db_autoreply_list
from utils.helpers import _is_admin

async def cmd_autoreply(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """/autoreply add <keyword> <response> | del <keyword> | list"""
    if not update.effective_user or not _is_admin(update.effective_user.id, ADMIN_IDS):
        return
    if not update.message:
        return

    args = ctx.args or []
    if not args:
        await update.message.reply_text(
            "Usage:\n/autoreply add <keyword> <response>\n"
            "/autoreply del <keyword>\n/autoreply list\n\n"
            "Fires when a user's message contains the keyword as a whole word.")
        return

    sub = args[0].lower()
    if sub == "add":
        if len(args) < 3:
            await update.message.reply_text("Usage: /autoreply add <keyword> <response>")
            return
        keyword, response = args[1], " ".join(args[2:])
        db_autoreply_set(keyword, response)
        await update.message.reply_text(
            f"🤖 Auto-reply saved for <b>{html.escape(keyword.lower())}</b>",
            parse_mode=ParseMode.HTML)
        return

    if sub == "del":
        if len(args) < 2:
            await update.message.reply_text("Usage: /autoreply del <keyword>")
            return
        if db_autoreply_delete(args[1]):
            await update.message.reply_text(f"🗑 Auto-reply '{args[1].lower()}' deleted.")
        else:
            await update.message.reply_text("Not found.")
        return

    if sub == "list":
        rows = db_autoreply_list()
        if not rows:
            await update.message.reply_text("No auto-replies configured.")
            return
        lines = ["🤖 <b>Auto-Replies</b>\n"]
        for r in rows:
            preview = r["response"][:60] + ("…" if len(r["response"]) > 60 else "")
            lines.append(f"• <b>{html.escape(r['keyword'])}</b> → {html.escape(preview)}")
        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)
        return

    await update.message.reply_text("Usage: /autoreply [add|del|list]")
