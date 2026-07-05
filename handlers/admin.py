import html
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import ContextTypes
from config import ADMIN_GROUP_ID, log, ADMIN_IDS
from database.users import db_get_user, db_get_user_by_topic
from database.bans import db_ban, db_unban, db_get_banned
from utils.helpers import _is_admin, _now_iso
from datetime import datetime, timedelta, timezone

async def cmd_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not _is_admin(update.effective_user.id, ADMIN_IDS): return
    from database.users import db_full_stats
    s = db_full_stats()
    text = (f"📊 <b>Bot Statistics</b>\n\n"
            f"👥 Total Users: {s['total']}\n"
            f"✅ Active: {s['active']}\n"
            f"🚫 Banned: {s['banned']}\n"
            f"🔔 Subs: {s['subs_on']} ON / {s['subs_off']} OFF\n\n"
            f"💬 Messages: {s['msg_total']} ({s['msg_in']} 📥 / {s['msg_out']} 📤)")
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)

async def cmd_ban(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not _is_admin(update.effective_user.id, ADMIN_IDS): return
    args = ctx.args or []
    tid = update.effective_message.message_thread_id
    user_id = None
    reason = "No reason provided"
    
    if args:
        try:
            user_id = int(args[0])
            if len(args) > 1: reason = " ".join(args[1:])
        except ValueError: reason = " ".join(args)

    if not user_id and tid:
        row = db_get_user_by_topic(tid)
        if row: user_id = row["user_id"]

    if not user_id:
        await update.message.reply_text("Usage: /ban [id] [reason]")
        return

    try:
        db_ban(user_id, reason=reason)
        await update.message.reply_text(f"🚫 User <code>{user_id}</code> banned.\nReason: {reason}", parse_mode=ParseMode.HTML)
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("⚖️ Appeal Ban", callback_data=f"appeal_{user_id}")]])
        await ctx.bot.send_message(
            chat_id=user_id, 
            text=f"🚫 <b>Access Restricted</b>\n\nYou have been banned from using this bot.\nReason: {reason}\n\nIf you believe this is a mistake, click the button below to appeal.",
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard
        )
    except Exception as e: await update.message.reply_text(f"Error: {e}")

async def cmd_unban(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not _is_admin(update.effective_user.id, ADMIN_IDS): return
    args = ctx.args or []
    tid = update.effective_message.message_thread_id
    user_id = None
    
    if args:
        try: user_id = int(args[0])
        except ValueError: pass
    
    if not user_id and tid:
        row = db_get_user_by_topic(tid)
        if row: user_id = row["user_id"]
        
    if not user_id:
        await update.message.reply_text("Usage: /unban [id]")
        return

    if db_unban(user_id):
        await update.message.reply_text(f"✅ User <code>{user_id}</code> unbanned.", parse_mode=ParseMode.HTML)
        try:
            await ctx.bot.send_message(chat_id=user_id, text="✅ <b>Access Restored</b>\n\nYour ban has been lifted. You can now use the bot again.", parse_mode=ParseMode.HTML)
        except: pass
    else:
        await update.message.reply_text("User not found in ban list.")

async def cmd_banned(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not _is_admin(update.effective_user.id, ADMIN_IDS): return
    bans = db_get_banned()
    if not bans:
        await update.message.reply_text("No active bans.")
        return
    lines = ["🚫 <b>Banned Users</b>\n"]
    for b in bans:
        name = b['first_name'] or "Unknown"
        lines.append(f"• <code>{b['user_id']}</code>: {name} (@{b['username'] or 'none'}) - {b['reason']}")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)

async def cmd_setmsg(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not _is_admin(update.effective_user.id, ADMIN_IDS): return
    args = ctx.args
    if not args or len(args) < 2:
        await update.message.reply_text("Usage: /setmsg [welcome_message|help_text] [content]")
        return
    from database.settings import db_set_setting
    db_set_setting(args[0], " ".join(args[1:]))
    await update.message.reply_text(f"✅ Setting <b>{args[0]}</b> updated.", parse_mode=ParseMode.HTML)

async def cmd_forcebroadcast(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not _is_admin(update.effective_user.id, ADMIN_IDS): return
    if not ctx.args: return
    val = ctx.args[0].lower() == "on"
    from database.users import db_force_broadcast_all
    count = db_force_broadcast_all(val)
    status = "ON" if val else "OFF"
    await update.message.reply_text(f"📢 Broadcast override: {status} ({count} users)")
