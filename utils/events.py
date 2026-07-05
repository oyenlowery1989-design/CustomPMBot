from telegram.constants import ParseMode
from telegram.error import TelegramError
from config import ADMIN_GROUP_ID, log
from database.topics import db_get_binding

async def _get_output_topic(bot, command_name: str, fallback_thread_id: int = None) -> int:
    bound = db_get_binding("command", command_name)
    return bound if bound else fallback_thread_id

async def _send_event(bot, event_name: str, text: str, parse_mode=ParseMode.HTML) -> None:
    topic_id = db_get_binding("event", event_name)
    if topic_id:
        try:
            await bot.send_message(
                chat_id=ADMIN_GROUP_ID,
                message_thread_id=topic_id,
                text=text,
                parse_mode=parse_mode,
            )
        except TelegramError as e:
            log.warning("Failed to send event '%s' to topic: %s", event_name, e)
