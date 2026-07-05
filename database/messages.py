import sqlite3
import logging
from typing import List
from database.connection import get_db
from utils.helpers import _now_iso

log = logging.getLogger("nopmsbot")

def db_log_message(user_id: int, direction: str, content_type: str, text: str = "") -> None:
    get_db().execute(
        "INSERT INTO messages (user_id, direction, content_type, text, timestamp) VALUES (?,?,?,?,?)",
        (user_id, direction, content_type, text or "", _now_iso()),
    )
    get_db().commit()

def db_export_messages(user_id: int, limit: int = 200) -> List[sqlite3.Row]:
    return get_db().execute(
        "SELECT * FROM messages WHERE user_id=? ORDER BY id DESC LIMIT ?",
        (user_id, limit),
    ).fetchall()
