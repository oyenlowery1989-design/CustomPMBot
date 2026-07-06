import html
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import ContextTypes
from config import ADMIN_GROUP_ID, ADMIN_IDS, log
from database.settings import db_get_setting, db_set_setting
from database.ai_drafts import (
    db_create_draft, db_get_draft, db_set_draft_topic_msg_id, db_set_draft_status,
)
from database.messages import db_export_messages, db_log_message
from database.canned import db_canned_list
from services.ai_draft import generate_draft
from utils.helpers import _is_admin

async def cmd_ai(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not _is_admin(update.effective_user.id, ADMIN_IDS): return
    args = ctx.args or []
    if not args:
        await update.message.reply_text(
            "🤖 <b>AI Draft Replies</b>:\n"
            "/ai on\n"
            "/ai off\n"
            "/ai guidelines <text>\n"
            "/ai status",
            parse_mode=ParseMode.HTML)
        return
    subcmd = args[0].lower()
    if subcmd == "on":
        db_set_setting("ai_enabled", "on")
        await update.message.reply_text("✅ AI draft replies are now <b>on</b>.", parse_mode=ParseMode.HTML)
    elif subcmd == "off":
        db_set_setting("ai_enabled", "off")
        await update.message.reply_text("✅ AI draft replies are now <b>off</b>.", parse_mode=ParseMode.HTML)
    elif subcmd == "guidelines":
        if len(args) >= 2:
            guidelines = " ".join(args[1:])
            db_set_setting("ai_guidelines", guidelines)
            await update.message.reply_text("✅ Guidelines updated.")
        else:
            current = db_get_setting("ai_guidelines", "")
            shown = html.escape(current) if current else "(no guidelines set)"
            await update.message.reply_text(f"📋 <b>Current guidelines:</b>\n{shown}", parse_mode=ParseMode.HTML)
    elif subcmd == "status":
        enabled = db_get_setting("ai_enabled", "off")
        guidelines = db_get_setting("ai_guidelines", "")
        preview = html.escape(guidelines[:200]) if guidelines else "(none)"
        status_text = "🟢 on" if enabled == "on" else "🔴 off"
        await update.message.reply_text(
            f"🤖 <b>AI Draft Replies:</b> {status_text}\n"
            f"📋 <b>Guidelines:</b> {preview}",
            parse_mode=ParseMode.HTML)

async def cb_ai_draft(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not q: return
    await q.answer()
    try:
        rest = q.data.replace("ai_draft_", "", 1)
        user_id_str, fwd_msg_id_str = rest.split("_")
        user_id = int(user_id_str)
        fwd_msg_id = int(fwd_msg_id_str)
    except (ValueError, AttributeError) as e:
        log.error("cb_ai_draft: bad callback data %r: %s", q.data, e)
        return

    topic_id = q.message.message_thread_id

    guidelines = db_get_setting("ai_guidelines", "")
    # db_export_messages returns newest-first; reverse so the model sees the
    # conversation oldest-first. Both this and db_canned_list return
    # sqlite3.Row objects — convert to plain tuples here, generate_draft()
    # itself must not know about the DB layer.
    rows = list(reversed(db_export_messages(user_id, limit=10)))
    conversation = [(r["direction"], r["text"]) for r in rows]
    canned_responses = [(r["name"], r["body"]) for r in db_canned_list()]

    result = generate_draft(guidelines, conversation, canned_responses)

    if result["action"] == "escalate":
        reason = result["reason"] or "AI draft unavailable"
        await ctx.bot.send_message(
            chat_id=ADMIN_GROUP_ID, message_thread_id=topic_id,
            text=f"🤖 {reason}",
        )
        return

    draft_id = db_create_draft(user_id, topic_id, result["text"])
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Send", callback_data=f"ad_s_{draft_id}"),
        InlineKeyboardButton("✏️ Edit", callback_data=f"ad_e_{draft_id}"),
        InlineKeyboardButton("❌ Dismiss", callback_data=f"ad_d_{draft_id}"),
    ]])
    sent = await ctx.bot.send_message(
        chat_id=ADMIN_GROUP_ID, message_thread_id=topic_id,
        text=result["text"], reply_markup=keyboard, reply_to_message_id=fwd_msg_id,
    )
    db_set_draft_topic_msg_id(draft_id, sent.message_id)

async def cb_ai_send(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not q: return
    try:
        draft_id = int(q.data.replace("ad_s_", ""))
        draft = db_get_draft(draft_id)
        if not draft:
            await q.answer("Draft not found")
            return
        await ctx.bot.send_message(chat_id=draft["user_id"], text=draft["draft_text"])
        db_log_message(draft["user_id"], "out", "text", draft["draft_text"])
        db_set_draft_status(draft_id, "sent")
        await q.answer("Sent")
        await q.edit_message_text(draft["draft_text"] + "\n\n✅ Sent", reply_markup=None)
    except Exception as e:
        # Broad on purpose: must always answer the callback even on failure
        # (Telegram API error, missing draft, etc.) — mirrors
        # handlers/wallet.py:cb_wallet_remove.
        log.error("cb_ai_send failed: %s", e)
        await q.answer("Error")

async def cb_ai_edit(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not q: return
    try:
        draft_id = int(q.data.replace("ad_e_", ""))
        db_set_draft_status(draft_id, "awaiting_edit")
        await q.answer()
        await q.edit_message_text("✏️ Reply in this topic with the corrected text.", reply_markup=None)
    except Exception as e:
        log.error("cb_ai_edit failed: %s", e)
        await q.answer("Error")

async def cb_ai_dismiss(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not q: return
    try:
        draft_id = int(q.data.replace("ad_d_", ""))
        db_set_draft_status(draft_id, "dismissed")
        await q.answer()
        await q.edit_message_text("❌ Dismissed", reply_markup=None)
    except Exception as e:
        log.error("cb_ai_dismiss failed: %s", e)
        await q.answer("Error")
