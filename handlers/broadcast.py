import asyncio
import logging
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Tuple
from telegram import Update, Bot, Message
from telegram.constants import ParseMode, ChatAction
from telegram.error import Forbidden, TelegramError
from telegram.ext import ContextTypes
from config import ADMIN_GROUP_ID, ADMIN_IDS, BROADCAST_TOPIC_NAME, MAX_CONCURRENT, log
from database.broadcasts import (
    db_schedule_broadcast, db_get_due_broadcasts, db_list_pending_broadcasts,
    db_get_sent_broadcasts, db_cancel_scheduled, db_mark_broadcast_sent,
)
from database.settings import db_get_setting, db_set_setting
from database.users import db_get_all_subscribers, db_get_subscribers_by_tag, db_mark_blocked
from utils.helpers import _is_admin, _parse_duration, _format_duration
from utils.media import _relay_to_user

_broadcast_topic_id = None

async def _find_broadcast_topic(bot: Bot) -> Optional[int]:
    global _broadcast_topic_id
    if _broadcast_topic_id: return _broadcast_topic_id
    stored = db_get_setting("broadcast_topic_id")
    if stored:
        tid = int(stored)
        try:
            # Cheap liveness probe — raises if the topic was deleted in Telegram
            await bot.send_chat_action(chat_id=ADMIN_GROUP_ID, action=ChatAction.TYPING, message_thread_id=tid)
            _broadcast_topic_id = tid
            return tid
        except TelegramError as e:
            log.warning("Stored broadcast topic %s is unusable (%s) — creating a new one.", tid, e)
    try:
        topic = await bot.create_forum_topic(chat_id=ADMIN_GROUP_ID, name=BROADCAST_TOPIC_NAME)
        _broadcast_topic_id = topic.message_thread_id
        db_set_setting("broadcast_topic_id", str(_broadcast_topic_id))
        log.info("Broadcast topic created: id=%s name=%r", _broadcast_topic_id, BROADCAST_TOPIC_NAME)
        return _broadcast_topic_id
    except TelegramError as e:
        log.error("Could not create broadcast topic in group %s: %s", ADMIN_GROUP_ID, e)
        return None

async def _do_broadcast(bot: Bot, msg: Message, recipients: List[sqlite3.Row], label: str, opted_out_count: int = 0) -> None:
    """Send a broadcast message to recipients with live progress."""
    total = len(recipients)
    if total == 0:
        text = f"📢 No recipients for broadcast ({label})."
        if opted_out_count > 0:
            text += f"\n(Skipped {opted_out_count} users who opted out)"
        await bot.send_message(chat_id=ADMIN_GROUP_ID, message_thread_id=msg.message_thread_id, text=text)
        return

    progress = await bot.send_message(chat_id=ADMIN_GROUP_ID, message_thread_id=msg.message_thread_id, text=f"📢 Broadcasting ({label})… 0/{total}")
    sem = asyncio.Semaphore(MAX_CONCURRENT)
    sent, failed, blocked = 0, 0, 0
    lock = asyncio.Lock()

    async def _send_one(user_row: sqlite3.Row) -> None:
        nonlocal sent, failed, blocked
        async with sem:
            try:
                await _relay_to_user(bot, msg, user_row["user_id"], raise_on_block=True)
                async with lock: sent += 1
            except Forbidden:
                async with lock: blocked += 1
            except:
                async with lock: failed += 1

    tasks = [asyncio.create_task(_send_one(user_row)) for user_row in recipients]
    done_count = 0
    for coro in asyncio.as_completed(tasks):
        await coro
        done_count += 1
        if done_count % max(1, total // 10) == 0 or done_count == total:
            try: 
                report = f"📢 Broadcasting ({label})… {done_count}/{total}\n✅ {sent}  ❌ {failed}  🚫 {blocked}"
                if opted_out_count > 0:
                    report += f"  💤 {opted_out_count}"
                await progress.edit_text(report)
            except: pass

    try: 
        final_report = (
            f"📢 <b>Broadcast complete ({label})</b>\n\n"
            f"✅ Sent: <b>{sent}</b>\n"
            f"🚫 Blocked: <b>{blocked}</b>\n"
            f"❌ Failed: <b>{failed}</b>\n"
            f"💤 Opted-out: <b>{opted_out_count}</b>\n\n"
            f"Total Reachable: {total + opted_out_count}"
        )
        await progress.edit_text(final_report, parse_mode=ParseMode.HTML)
    except: pass
    log.info("Broadcast (%s): %d sent, %d blocked, %d failed, %d opted-out", label, sent, blocked, failed, opted_out_count)

async def _broadcast_text(bot: Bot, text: str, recipients: List[sqlite3.Row], label: str) -> Tuple[int, int, int]:
    """Broadcast a plain text message (no source Message object — used by the
    scheduler). Reports the result into the broadcast topic. Returns (sent, blocked, failed)."""
    sem = asyncio.Semaphore(MAX_CONCURRENT)
    sent, blocked, failed = 0, 0, 0
    lock = asyncio.Lock()

    async def _send_one(user_row: sqlite3.Row) -> None:
        nonlocal sent, blocked, failed
        async with sem:
            try:
                await bot.send_message(chat_id=user_row["user_id"], text=text)
                async with lock: sent += 1
            except Forbidden:
                db_mark_blocked(user_row["user_id"])
                async with lock: blocked += 1
            except Exception:
                async with lock: failed += 1

    await asyncio.gather(*(_send_one(r) for r in recipients))

    topic_id = await _find_broadcast_topic(bot)
    if topic_id:
        try:
            await bot.send_message(
                chat_id=ADMIN_GROUP_ID, message_thread_id=topic_id,
                text=(f"📢 <b>Broadcast complete ({label})</b>\n\n"
                      f"✅ Sent: <b>{sent}</b>\n🚫 Blocked: <b>{blocked}</b>\n❌ Failed: <b>{failed}</b>"),
                parse_mode=ParseMode.HTML,
            )
        except TelegramError as e:
            log.warning("Could not post broadcast report (%s): %s", label, e)
    log.info("Broadcast (%s): %d sent, %d blocked, %d failed", label, sent, blocked, failed)
    return sent, blocked, failed

async def process_due_broadcasts(bot: Bot) -> int:
    """Send all scheduled broadcasts whose time has come. Returns how many ran.
    Each is marked sent BEFORE sending — at-most-once: a crash mid-send loses
    a broadcast rather than double-spamming every subscriber on restart."""
    due = db_get_due_broadcasts()
    for b in due:
        db_mark_broadcast_sent(b["id"])
        recipients = db_get_all_subscribers()
        await _broadcast_text(bot, b["text"], recipients, label=f"scheduled #{b['id']}")
    return len(due)

async def cmd_schedule(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """/schedule <duration> <text> | /schedule list | /schedule cancel <id>"""
    if not update.effective_user or not _is_admin(update.effective_user.id, ADMIN_IDS): return
    if not update.message: return
    args = ctx.args or []

    if not args:
        await update.message.reply_text(
            "Usage:\n/schedule <duration> <message> — e.g. /schedule 2h Big news!\n"
            "/schedule list\n/schedule cancel <id>\n"
            "Durations: 10m, 2h, 1d, 1w")
        return

    sub = args[0].lower()
    if sub == "list":
        pending = db_list_pending_broadcasts()
        recent = db_get_sent_broadcasts(limit=5)
        lines = ["🗓 <b>Scheduled Broadcasts</b>\n"]
        if pending:
            for b in pending:
                preview = b["text"][:60] + ("…" if len(b["text"]) > 60 else "")
                lines.append(f"• #{b['id']} at {b['run_at'][:16].replace('T', ' ')} UTC — {preview}")
        else:
            lines.append("No pending broadcasts.")
        if recent:
            lines.append("\n<b>Recently sent:</b>")
            for b in recent:
                lines.append(f"• #{b['id']} sent {b['sent_at'][:16].replace('T', ' ')} UTC")
        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)
        return

    if sub == "cancel":
        if len(args) < 2 or not args[1].isdigit():
            await update.message.reply_text("Usage: /schedule cancel <id>")
            return
        if db_cancel_scheduled(int(args[1])):
            await update.message.reply_text(f"🗑 Scheduled broadcast #{args[1]} cancelled.")
        else:
            await update.message.reply_text("Not found (or already sent).")
        return

    seconds = _parse_duration(sub)
    if seconds is None or len(args) < 2:
        await update.message.reply_text(
            "Usage: /schedule <duration> <message> — e.g. /schedule 2h Big news!")
        return

    text = " ".join(args[1:])
    run_at = (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()
    bid = db_schedule_broadcast(text, run_at, update.effective_user.id)
    await update.message.reply_text(
        f"🗓 Broadcast #{bid} scheduled in {_format_duration(seconds)} "
        f"({run_at[:16].replace('T', ' ')} UTC).\nCancel with /schedule cancel {bid}")
