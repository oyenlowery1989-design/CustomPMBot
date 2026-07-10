import sqlite3
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional
from database.connection import get_db
from utils.helpers import _now_iso

log = logging.getLogger("nopmsbot")

_TERMINAL_STATUSES = ("sent", "dismissed", "edited", "failed")

def db_create_draft(user_id: int, topic_id: int, draft_text: str) -> int:
    """Insert a new pending draft. Returns the new row id."""
    cur = get_db().execute(
        "INSERT INTO ai_drafts (user_id, topic_id, draft_text, status, created_at) VALUES (?,?,?,?,?)",
        (user_id, topic_id, draft_text, "pending", _now_iso()),
    )
    get_db().commit()
    return cur.lastrowid

def db_get_draft(draft_id: int) -> Optional[sqlite3.Row]:
    return get_db().execute("SELECT * FROM ai_drafts WHERE id=?", (draft_id,)).fetchone()

def db_set_draft_topic_msg_id(draft_id: int, topic_msg_id: int) -> None:
    """Called right after the draft message is actually sent, to record its own message_id."""
    get_db().execute("UPDATE ai_drafts SET topic_msg_id=? WHERE id=?", (topic_msg_id, draft_id))
    get_db().commit()

def db_set_draft_status(draft_id: int, status: str) -> None:
    get_db().execute("UPDATE ai_drafts SET status=? WHERE id=?", (status, draft_id))
    get_db().commit()

def db_update_draft_text(draft_id: int, new_text: str) -> None:
    """Used on edit."""
    get_db().execute("UPDATE ai_drafts SET draft_text=? WHERE id=?", (new_text, draft_id))
    get_db().commit()

def db_get_awaiting_edit_draft(topic_id: int) -> Optional[sqlite3.Row]:
    """There should only ever be one at a time per topic; if somehow more than
    one row matches, use the most recent."""
    return get_db().execute(
        "SELECT * FROM ai_drafts WHERE topic_id=? AND status='awaiting_edit' ORDER BY id DESC LIMIT 1",
        (topic_id,),
    ).fetchone()

def prune_old_drafts(retention_days: int) -> int:
    """Delete terminal-status rows (sent/dismissed/edited/failed) older than
    retention_days. Rows still pending or awaiting_edit are left alone
    regardless of age — those are still actionable. Without this, ai_drafts
    grows forever (L6, docs/AUDIT-2026-07-10.md)."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).isoformat()
    placeholders = ",".join("?" * len(_TERMINAL_STATUSES))
    db = get_db()
    cur = db.execute(
        f"DELETE FROM ai_drafts WHERE status IN ({placeholders}) AND created_at < ?",
        (*_TERMINAL_STATUSES, cutoff),
    )
    db.commit()
    return cur.rowcount
