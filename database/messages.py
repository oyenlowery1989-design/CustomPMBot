import sqlite3
import logging
from datetime import datetime, timedelta, timezone
from typing import List, Optional
from database.connection import get_db
from utils.helpers import _now_iso

log = logging.getLogger("nopmsbot")

def _cutoff_iso(retention_days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=retention_days)).isoformat()

def db_map_message(user_id: int, user_msg_id: int, topic_msg_id: int) -> None:
    """Remember which topic message is the forward of which user message,
    so admin replies can quote the user's original (inline reply preview)."""
    get_db().execute(
        "INSERT OR REPLACE INTO message_map (topic_msg_id, user_msg_id, user_id, created_at) VALUES (?,?,?,?)",
        (topic_msg_id, user_msg_id, user_id, _now_iso()),
    )
    get_db().commit()

def db_get_mapped_user_msg(topic_msg_id: int) -> Optional[int]:
    row = get_db().execute(
        "SELECT user_msg_id FROM message_map WHERE topic_msg_id=?", (topic_msg_id,)
    ).fetchone()
    return row["user_msg_id"] if row else None

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

def db_search_messages(query: str, user_id: Optional[int] = None, limit: int = 20) -> List[sqlite3.Row]:
    """Case-insensitive substring search over logged message text, newest first.
    ESCAPE so user-supplied % and _ match literally instead of as wildcards."""
    pattern = "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    if user_id is not None:
        return get_db().execute(
            "SELECT * FROM messages WHERE user_id=? AND text LIKE ? ESCAPE '\\' "
            "ORDER BY id DESC LIMIT ?", (user_id, pattern, limit),
        ).fetchall()
    return get_db().execute(
        "SELECT * FROM messages WHERE text LIKE ? ESCAPE '\\' ORDER BY id DESC LIMIT ?",
        (pattern, limit),
    ).fetchall()

def prune_old_messages(retention_days: int) -> int:
    """Delete logged messages older than retention_days. Without this, the
    messages table grows forever (L5, docs/AUDIT-2026-07-10.md)."""
    db = get_db()
    cur = db.execute("DELETE FROM messages WHERE timestamp < ?", (_cutoff_iso(retention_days),))
    db.commit()
    return cur.rowcount

def prune_old_message_map(retention_days: int) -> int:
    """Delete reply-threading rows older than retention_days — same
    unbounded-growth issue as messages (L5, docs/AUDIT-2026-07-10.md)."""
    db = get_db()
    cur = db.execute("DELETE FROM message_map WHERE created_at < ?", (_cutoff_iso(retention_days),))
    db.commit()
    return cur.rowcount
