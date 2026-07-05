from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes
from config import ADMIN_IDS
from database.users import db_get_user_by_topic
from database.tags import db_add_tag, db_remove_tag, db_get_tags
from utils.helpers import _is_admin

async def cmd_tag(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not _is_admin(update.effective_user.id, ADMIN_IDS):
        return
    if not update.message:
        return

    args = ctx.args or []
    thread_id = update.effective_message.message_thread_id

    if args and args[0].lower() == "remove":
        args = args[1:]
        if thread_id and len(args) == 1:
            row = db_get_user_by_topic(thread_id)
            if row:
                tag = args[0]
                if db_remove_tag(row["user_id"], tag):
                    await update.message.reply_text(f"🏷 Removed tag {tag.upper()} from user {row['user_id']}")
                else: await update.message.reply_text("Tag not found.")
                return
        elif len(args) >= 2:
            try:
                uid = int(args[0])
                tag = args[1]
                if db_remove_tag(uid, tag):
                    await update.message.reply_text(f"🏷 Removed tag {tag.upper()} from user {uid}")
                else: await update.message.reply_text("Tag not found.")
            except ValueError: await update.message.reply_text("Usage: /tag remove <user_id> <label>")
            return
        await update.message.reply_text("Usage: /tag remove <user_id> <label>")
        return

    if thread_id and len(args) == 1:
        row = db_get_user_by_topic(thread_id)
        if row:
            tag = args[0]
            db_add_tag(row["user_id"], tag)
            all_tags = db_get_tags(row["user_id"])
            await update.message.reply_text(
                f"🏷 Tagged user {row['user_id']} as <b>{tag.upper()}</b>\nAll tags: {', '.join(all_tags)}",
                parse_mode=ParseMode.HTML,
            )
            return

    if len(args) >= 2:
        try:
            uid = int(args[0])
            tag = args[1]
            db_add_tag(uid, tag)
            all_tags = db_get_tags(uid)
            await update.message.reply_text(
                f"🏷 Tagged user {uid} as <b>{tag.upper()}</b>\nAll tags: {', '.join(all_tags)}",
                parse_mode=ParseMode.HTML,
            )
        except ValueError: await update.message.reply_text("Usage: /tag <user_id> <label>")
        return

    if thread_id:
        row = db_get_user_by_topic(thread_id)
        if row:
            tags = db_get_tags(row["user_id"])
            if tags: await update.message.reply_text(f"🏷 Tags for {row['user_id']}: {', '.join(tags)}")
            else: await update.message.reply_text("No tags. Usage: /tag <label>")
            return

    await update.message.reply_text("Usage: /tag <user_id> <label> or /tag <label> in a topic")
