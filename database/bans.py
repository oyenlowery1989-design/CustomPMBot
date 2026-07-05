import sqlite3
import logging
from datetime import datetime, timezone
from typing import Optional, List
from database.connection import get_db
from utils.helpers import _now_iso

log = logging.getLogger("nopmsbot")

def db_is_banned(user_id: int) -> bool:
    row = get_db().execute("SELECT * FROM bans WHERE user_id=?", (user_id,)).fetchone()
    if row is None:
        return False
    if row["expires_at"] is not None:
        if datetime.fromisoformat(row["expires_at"]) <= datetime.now(timezone.utc):
            get_db().execute("DELETE FROM bans WHERE user_id=?", (user_id,))
            get_db().commit()
            return False
    return True

def db_ban(user_id: int, reason: str = "", expires_at: Optional[str] = None) -> None:
    db = get_db()
    db.execute(
        "INSERT OR REPLACE INTO bans (user_id, reason, banned_at, expires_at) VALUES (?,?,?,?)",
        (user_id, reason, _now_iso(), expires_at),
    )
    db.commit()

def db_unban(user_id: int) -> bool:
    db = get_db()
    cur = db.execute("DELETE FROM bans WHERE user_id=?", (user_id,))
    db.commit()
    return cur.rowcount > 0

def db_get_banned() -> List[sqlite3.Row]:
    return get_db().execute("SELECT b.*, u.first_name, u.username FROM bans b LEFT JOIN users u ON b.user_id=u.user_id").fetchall()

def db_get_expired_bans() -> List[sqlite3.Row]:
    now = _now_iso()
    return get_db().execute(
        "SELECT * FROM bans WHERE expires_at IS NOT NULL AND expires_at <= ?", (now,)
    ).fetchall()

def cleanup_expired_bans() -> int:
    """Delete all expired bans. Returns number removed. Run periodically —
    db_is_banned only cleans lazily when that user next messages, so expired
    bans would otherwise keep excluding users from broadcasts."""
    db = get_db()
    cur = db.execute(
        "DELETE FROM bans WHERE expires_at IS NOT NULL AND expires_at <= ?", (_now_iso(),)
    )
    db.commit()
    return cur.rowcount
