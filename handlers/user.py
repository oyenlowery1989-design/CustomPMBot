import html
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ChatType, ParseMode
from telegram.ext import ContextTypes
from config import ADMIN_IDS
from database.users import db_upsert_user, db_get_user, db_set_broadcast_opt
from database.settings import db_get_setting
from database.wallets import db_get_user_wallets
from handlers.help_topics import HELP_TOPICS, admin_overview
from utils.helpers import _is_admin
from utils.strings import get_text

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat or update.effective_chat.type != ChatType.PRIVATE: return
    user = update.effective_user
    if not user: return
    db_upsert_user(user)

    welcome = db_get_setting("welcome_message", get_text("user_commands.welcome_default"))
    
    wallets = db_get_user_wallets(user.id)
    wallet_label = "💳 My Wallets" if wallets else get_text("user_commands.cmd_wallet_desc").split(" — ")[0]
    
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(wallet_label, callback_data="wallet_view" if wallets else "wallet_add")],
        [
            InlineKeyboardButton("⚙️ Settings", callback_data="settings"),
            InlineKeyboardButton("📖 Help", callback_data="help"),
        ],
    ])
    await update.message.reply_text(welcome, reply_markup=keyboard)

async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user or not update.message: return

    is_admin = _is_admin(user.id, ADMIN_IDS)

    # /help <command> — detailed usage
    if ctx.args:
        topic_name = ctx.args[0].lstrip("/").lower()
        topic = HELP_TOPICS.get(topic_name)
        if topic is None or (topic["admin"] and not is_admin):
            await update.message.reply_text(
                f"No help available for '{html.escape(topic_name)}'. Use /help for the overview.")
            return
        await update.message.reply_text(
            f"📖 <b>/{topic_name}</b> — {topic['summary']}\n\n{topic['detail']}",
            parse_mode=ParseMode.HTML)
        return

    divider = get_text('user_commands.help_divider')

    # Build user section
    text = (
        f"{get_text('user_commands.help_header')}\n"
        f"{divider}\n"
        f"{get_text('user_commands.help_user_section')}\n"
        f"{divider}\n\n"
        f"{get_text('user_commands.cmd_start_desc')}\n"
        f"{get_text('user_commands.cmd_help_desc')}\n"
        f"{get_text('user_commands.cmd_settings_desc')}\n"
        f"{get_text('user_commands.cmd_wallet_desc')}\n"
        f"{get_text('user_commands.cmd_cancel_desc')}\n"
    )

    if is_admin:
        text += (
            f"\n{divider}\n"
            f"{get_text('user_commands.help_admin_section')}\n"
            f"{divider}\n"
            f"{admin_overview()}"
        )

    await update.message.reply_text(text, parse_mode=ParseMode.HTML)

async def cmd_settings(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat or update.effective_chat.type != ChatType.PRIVATE: return
    user = update.effective_user
    if not user: return

    row = db_get_user(user.id)
    current = bool(row["broadcast_opt"]) if row else True
    
    if ctx.args and ctx.args[0].lower() in ("on", "off"):
        new_val = ctx.args[0].lower() == "on"
        db_set_broadcast_opt(user.id, new_val)
        status = "subscribed ✅" if new_val else "unsubscribed ❌"
        await update.message.reply_text(f"Broadcasts: {status}")
        return

    status_text = get_text("settings.status_on") if current else get_text("settings.status_off")
    btn_label = get_text("settings.btn_turn_off") if current else get_text("settings.btn_turn_on")

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(btn_label, callback_data="toggle_broadcast")],
        [InlineKeyboardButton(get_text("settings.btn_back"), callback_data="back_start")],
    ])

    await update.message.reply_text(
        f"{get_text('settings.header')}\n\n{get_text('settings.current_status', status_text=status_text)}",
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard
    )
