import io
from telegram import Update
from telegram.ext import ContextTypes
from config import ADMIN_IDS
from database.users import db_get_user, db_get_user_by_topic
from database.messages import db_export_messages
from utils.helpers import _is_admin, _user_display

async def cmd_export(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not _is_admin(update.effective_user.id, ADMIN_IDS):
        return
    if not update.message:
        return

    args = ctx.args or []
    user_id = None
    thread_id = update.effective_message.message_thread_id

    if args:
        try: user_id = int(args[0])
        except ValueError:
            await update.message.reply_text("Usage: /export <user_id>")
            return
    elif thread_id:
        row = db_get_user_by_topic(thread_id)
        if row: user_id = row["user_id"]

    if user_id is None:
        await update.message.reply_text("Usage: /export <user_id> or use in a topic")
        return

    messages = db_export_messages(user_id, limit=500)
    if not messages:
        await update.message.reply_text("No messages logged for this user.")
        return

    user_row = db_get_user(user_id)
    name = _user_display(user_row) if user_row else str(user_id)
    lines = [f"📋 Conversation log for {name} (ID: {user_id})\n"]
    for m in reversed(messages):
        direction = "→" if m["direction"] == "in" else "←"
        ts = m["timestamp"][:16].replace("T", " ")
        content = m["text"][:100] if m["text"] else f"[{m['content_type']}]"
        lines.append(f"{ts} {direction} {content}")

    text = "\n".join(lines)
    if len(text) > 4000:
        buf = io.BytesIO(text.encode())
        buf.name = f"export_{user_id}.txt"
        await update.message.reply_document(document=buf)
    else: await update.message.reply_text(text)
