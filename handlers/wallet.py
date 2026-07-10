import html
import random
import string
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode, ChatType
from telegram.error import TelegramError
from telegram.ext import ContextTypes
from config import ADMIN_IDS, VERIFY_WALLET_PUBLIC, log, ADMIN_GROUP_ID
from database.users import db_get_user, db_set_broadcast_opt, db_upsert_user
from database.settings import db_get_setting
from database.wallets import (
    db_get_user_wallets, db_delete_wallet,
    db_get_wallet_count, db_create_verification, db_all_wallets,
    db_get_wallet_by_id, db_set_awaiting_key, db_clear_awaiting_key
)
from utils.helpers import _is_admin, _user_link
from utils.strings import get_text

# In-memory state
_awaiting_wallet_addr = set()
_awaiting_wallet_label = {}

async def cmd_wallet(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat or update.effective_chat.type != ChatType.PRIVATE: return
    if not update.message: return
    await _show_wallet_menu(update.message, update.effective_user.id)

async def cmd_wallets(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not _is_admin(update.effective_user.id, ADMIN_IDS): return
    if not update.message: return
    wallets = db_all_wallets()
    if not wallets:
        await update.message.reply_text("No wallets registered.")
        return
    lines = ["💳 <b>Registered Wallets</b>\n"]
    for w in wallets:
        safe_label = html.escape(w['label'])
        # Masked the same way _show_wallet_menu shows it to the wallet's own
        # owner — this admin-facing list has no reason to expose the full
        # address either (L2, docs/AUDIT-2026-07-10.md).
        addr_display = w['address'][:6] + "..." + w['address'][-4:]
        lines.append(f"• <code>{w['user_id']}</code>: <code>{addr_display}</code> ({safe_label})")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)

async def cmd_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat or update.effective_chat.type != ChatType.PRIVATE: return
    if not update.message: return
    user = update.effective_user
    if not user: return
    _awaiting_wallet_addr.discard(user.id)
    _awaiting_wallet_label.pop(user.id, None)
    db_clear_awaiting_key(user.id)
    await update.message.reply_text("Action cancelled.")

async def _show_wallet_menu(msg_obj, user_id: int, is_edit: bool = False):
    wallets = db_get_user_wallets(user_id)
    max_wallets = 5
    
    if not wallets:
        text = get_text("wallet.no_wallet")
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton(get_text("wallet.btn_add"), callback_data="wallet_add")]])
    else:
        text = get_text("wallet.has_wallet", count=len(wallets), max_wallets=max_wallets) + "\n\n"
        buttons = []
        for i, w in enumerate(wallets, 1):
            status = get_text("wallet.status_verified") if w["verified"] else get_text("wallet.status_unverified")
            addr_display = w["address"][:6] + "..." + w["address"][-4:]
            safe_label = html.escape(w["label"])
            text += get_text("wallet.wallet_item", index=i, status_icon=status, address=addr_display, label=safe_label) + "\n"
            
            row = []
            if not w["verified"]:
                row.append(InlineKeyboardButton(get_text("wallet.btn_verify", index=i), callback_data=f"w_v_{w['id']}"))
            row.append(InlineKeyboardButton(get_text("wallet.btn_remove", index=i), callback_data=f"w_r_{w['id']}"))
            buttons.append(row)
            
        if len(wallets) < max_wallets:
            buttons.append([InlineKeyboardButton(get_text("wallet.btn_add"), callback_data="wallet_add")])
        buttons.append([InlineKeyboardButton(get_text("wallet.btn_back"), callback_data="back_start")])
        keyboard = InlineKeyboardMarkup(buttons)

    try:
        if is_edit: await msg_obj.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
        else: await msg_obj.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
    except Exception as e:
        log.warning("Menu render failed: %s", e)

# --- Callbacks ---

async def cb_wallet_add(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not q: return
    await q.answer()
    if db_get_wallet_count(q.from_user.id) >= 5:
        await q.edit_message_text(get_text("wallet.limit_reached", max_wallets=5), parse_mode=ParseMode.HTML)
        return
    _awaiting_wallet_addr.add(q.from_user.id)
    await q.edit_message_text(get_text("wallet.prompt_address"), parse_mode=ParseMode.HTML)

async def cb_wallet_view(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not q: return
    await q.answer()
    await _show_wallet_menu(q.message, q.from_user.id, is_edit=True)

async def cb_wallet_remove(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not q: return
    try:
        w_id = int(q.data.replace("w_r_", ""))
        wallet = db_get_wallet_by_id(w_id)
        if wallet and wallet["user_id"] == q.from_user.id:
            db_delete_wallet(q.from_user.id, wallet["address"])
            await q.answer("Wallet removed")
        else: await q.answer("Wallet not found")
    except Exception as e:
        # Broad on purpose: db_delete_wallet can now raise (rollback+reraise,
        # see M5) and this handler must still answer the callback and
        # refresh the menu below rather than leave the user's client stuck.
        log.error("cb_wallet_remove failed: %s", e)
        await q.answer("Error")
    await _show_wallet_menu(q.message, q.from_user.id, is_edit=True)

async def cb_wallet_verify(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not q: return
    await q.answer()
    try:
        w_id = int(q.data.replace("w_v_", ""))
        wallet = db_get_wallet_by_id(w_id)
        if not wallet or wallet["user_id"] != q.from_user.id: return
        if not VERIFY_WALLET_PUBLIC:
            await q.edit_message_text(get_text("wallet.verify_no_wallet"))
            return
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(get_text("wallet.verify_memo_btn"), callback_data=f"v_m_{w_id}")],
            [InlineKeyboardButton(get_text("wallet.verify_key_btn"), callback_data=f"v_k_{w_id}")],
            [InlineKeyboardButton(get_text("wallet.btn_back"), callback_data="wallet_view")]
        ])
        await q.edit_message_text(get_text("wallet.verify_method_prompt", label=html.escape(wallet["label"])), parse_mode=ParseMode.HTML, reply_markup=keyboard)
    except (ValueError, TelegramError) as e:
        log.warning("cb_wallet_verify failed: %s", e)

async def cb_verify_memo(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not q: return
    await q.answer()
    try:
        w_id = int(q.data.replace("v_m_", ""))
        wallet = db_get_wallet_by_id(w_id)
        if not wallet or wallet["user_id"] != q.from_user.id: return
        challenge = ''.join(random.choices(string.digits, k=6))
        db_create_verification(q.from_user.id, wallet["address"], challenge)
        text = get_text("wallet.verify_memo_instructions", verify_addr=VERIFY_WALLET_PUBLIC, challenge=challenge)
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton(get_text("wallet.verify_cancel_btn"), callback_data="wallet_view")]])
        await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
    except (ValueError, TelegramError) as e:
        log.warning("cb_verify_memo failed: %s", e)

async def cb_verify_key(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not q: return
    await q.answer()
    try:
        w_id = int(q.data.replace("v_k_", ""))
        wallet = db_get_wallet_by_id(w_id)
        if not wallet or wallet["user_id"] != q.from_user.id: return
        db_set_awaiting_key(q.from_user.id, wallet["address"])
        await q.edit_message_text(get_text("wallet.verify_key_prompt"), parse_mode=ParseMode.HTML)
    except (ValueError, TelegramError) as e:
        log.warning("cb_verify_key failed: %s", e)

async def cb_settings(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not q: return
    await q.answer()
    row = db_get_user(q.from_user.id)
    current = bool(row["broadcast_opt"]) if row else True
    status_text = get_text("settings.status_on") if current else get_text("settings.status_off")
    btn_label = get_text("settings.btn_turn_off") if current else get_text("settings.btn_turn_on")
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton(btn_label, callback_data="toggle_broadcast")], [InlineKeyboardButton(get_text("settings.btn_back"), callback_data="back_start")]])
    await q.edit_message_text(f"⚙️ <b>Settings</b>\n\n{get_text('settings.current_status', status_text=status_text)}", parse_mode=ParseMode.HTML, reply_markup=keyboard)

async def cb_toggle_broadcast(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not q: return
    await q.answer()
    user_id = q.from_user.id
    row = db_get_user(user_id)
    new_val = not bool(row["broadcast_opt"]) if row else False
    db_set_broadcast_opt(user_id, new_val)
    await cb_settings(update, ctx)

async def cb_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not q: return
    await q.answer()
    log.info("Help button clicked by %s", q.from_user.id)
    user = q.from_user
    is_admin = _is_admin(user.id, ADMIN_IDS)
    divider = get_text('user_commands.help_divider')
    text = (f"{get_text('user_commands.help_header')}\n{divider}\n{get_text('user_commands.help_user_section')}\n{divider}\n\n{get_text('user_commands.cmd_start_desc')}\n{get_text('user_commands.cmd_help_desc')}\n{get_text('user_commands.cmd_settings_desc')}\n{get_text('user_commands.cmd_wallet_desc')}\n{get_text('user_commands.cmd_cancel_desc')}")
    if is_admin:
        from handlers.help_topics import admin_overview
        text += (f"\n\n{divider}\n{get_text('user_commands.help_admin_section')}\n{divider}\n"
                 f"{admin_overview()}")
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton(get_text("settings.btn_back"), callback_data="back_start")]])
    await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)

async def cb_appeal(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not q: return
    await q.answer("Appeal sent!")
    user = q.from_user
    try:
        from handlers.relay import _ensure_topic
        topic_id = await _ensure_topic(ctx.bot, user)
        alert = (f"⚖️ <b>BAN APPEAL</b>\n\n"
                 f"User {_user_link(user.id, user.first_name)} has requested an unban.\n"
                 f"Use <code>/unban</code> inside this topic to restore access.")
        await ctx.bot.send_message(chat_id=ADMIN_GROUP_ID, message_thread_id=topic_id, text=alert, parse_mode=ParseMode.HTML)
        await q.edit_message_text("✅ Your appeal has been sent to the admin team. Please wait.")
    except Exception as e:
        log.error("Appeal failed: %s", e)

async def cb_back_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not q: return
    await q.answer()
    log.info("Back to menu button clicked by %s", q.from_user.id)
    try:
        user = q.from_user
        db_upsert_user(user)
        welcome = db_get_setting("welcome_message", get_text("user_commands.welcome_default"))
        wallets = db_get_user_wallets(user.id)
        raw_wallet_text = get_text("user_commands.cmd_wallet_desc")
        wallet_label = "💳 My Wallets" if wallets else raw_wallet_text.split(" — ")[0] if " — " in raw_wallet_text else "💳 Wallet"
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(wallet_label, callback_data="wallet_view" if wallets else "wallet_add")],
            [InlineKeyboardButton(get_text("user_commands.settings_btn", "⚙️ Settings"), callback_data="settings"), InlineKeyboardButton(get_text("user_commands.help_btn", "📖 Help"), callback_data="help")]
        ])
        await q.edit_message_text(welcome, reply_markup=keyboard)
    except Exception as e:
        log.error("Error in cb_back_start: %s", e, exc_info=True)
