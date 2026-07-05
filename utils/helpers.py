import html
import re
import sqlite3
from datetime import datetime, timezone
from typing import Optional
from telegram import Message

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def _parse_duration(s: str) -> Optional[int]:
    m = re.fullmatch(r"(\d+)\s*([mhdw])", s.strip().lower())
    if not m:
        return None
    val = int(m.group(1))
    unit = m.group(2)
    multipliers = {"m": 60, "h": 3600, "d": 86400, "w": 604800}
    return val * multipliers[unit]

def _format_duration(seconds: int) -> str:
    if seconds >= 604800 and seconds % 604800 == 0:
        return f"{seconds // 604800}w"
    if seconds >= 86400 and seconds % 86400 == 0:
        return f"{seconds // 86400}d"
    if seconds >= 3600 and seconds % 3600 == 0:
        return f"{seconds // 3600}h"
    return f"{seconds // 60}m"

def _user_link(user_id: int, name: str) -> str:
    return f'<a href="tg://user?id={user_id}">{html.escape(name)}</a>'

def _user_display(row: sqlite3.Row) -> str:
    name = row["first_name"] or "Unknown"
    if row["last_name"]:
        name += f" {row['last_name']}"
    return name

def _content_type_of(msg: Message) -> str:
    if msg.photo: return "photo"
    if msg.video: return "video"
    if msg.document: return "document"
    if msg.sticker: return "sticker"
    if msg.voice: return "voice"
    if msg.video_note: return "video_note"
    if msg.animation: return "animation"
    if msg.audio: return "audio"
    if msg.contact: return "contact"
    if msg.location: return "location"
    if msg.text: return "text"
    return "other"

def _is_admin(user_id: int, admin_ids: set[int]) -> bool:
    return user_id in admin_ids

def _media_of(msg: Message):
    """(content_type, file_id) of a message's replayable media, else None.
    Only types that can be re-sent by file_id with an optional caption."""
    if msg.photo: return ("photo", msg.photo[-1].file_id)
    if msg.video: return ("video", msg.video.file_id)
    if msg.document: return ("document", msg.document.file_id)
    if msg.animation: return ("animation", msg.animation.file_id)
    if msg.audio: return ("audio", msg.audio.file_id)
    if msg.voice: return ("voice", msg.voice.file_id)
    if msg.sticker: return ("sticker", msg.sticker.file_id)
    return None
