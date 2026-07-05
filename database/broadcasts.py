import sqlite3
import logging
from typing import List, Optional
from database.connection import get_db
from utils.helpers import _now_iso

log = logging.getLogger("nopmsbot")

def db_schedule_broadcast(text: str, run_at: str, created_by: int) -> int:
    cur = get_db().execute(
        "INSERT INTO scheduled_broadcasts (text, run_at, created_by, created_at) VALUES (?,?,?,?)",
        (text, run_at, created_by, _now_iso()),
    )
    get_db().commit()
    return cur.lastrowid

def db_get_due_broadcasts() -> List[sqlite3.Row]:
    return get_db().execute(
        "SELECT * FROM scheduled_broadcasts WHERE sent=0 AND run_at <= ? ORDER BY run_at",
        (_now_iso(),),
    ).fetchall()

def db_list_pending_broadcasts() -> List[sqlite3.Row]:
    return get_db().execute(
        "SELECT * FROM scheduled_broadcasts WHERE sent=0 ORDER BY run_at"
    ).fetchall()

def db_get_sent_broadcasts(limit: int = 10) -> List[sqlite3.Row]:
    return get_db().execute(
        "SELECT * FROM scheduled_broadcasts WHERE sent=1 ORDER BY sent_at DESC LIMIT ?",
        (limit,),
    ).fetchall()

def db_cancel_scheduled(broadcast_id: int) -> bool:
    """Cancel a pending scheduled broadcast. Already-sent ones can't be cancelled."""
    cur = get_db().execute(
        "DELETE FROM scheduled_broadcasts WHERE id=? AND sent=0", (broadcast_id,)
    )
    get_db().commit()
    return cur.rowcount > 0

def db_mark_broadcast_sent(broadcast_id: int) -> None:
    get_db().execute(
        "UPDATE scheduled_broadcasts SET sent=1, sent_at=? WHERE id=?",
        (_now_iso(), broadcast_id),
    )
    get_db().commit()
