import asyncio
import logging
import re
import sqlite3
from typing import Optional, List
from telegram import Update, Bot, Message
from telegram.constants import ParseMode, ChatAction
from telegram.error import Forbidden, TelegramError
from telegram.ext import ContextTypes
from config import ADMIN_GROUP_ID, BROADCAST_TOPIC_NAME, MAX_CONCURRENT, log
from database.settings import db_get_setting, db_set_setting
from database.users import db_get_all_subscribers, db_get_subscribers_by_tag
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
