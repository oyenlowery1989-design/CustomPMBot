import sqlite3
import logging
from typing import Optional, List
from telegram import User
from database.connection import get_db
from utils.helpers import _now_iso

log = logging.getLogger("nopmsbot")

def db_get_user(user_id: int) -> Optional[sqlite3.Row]:
    return get_db().execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()

def db_upsert_user(u: User, topic_id: Optional[int] = None) -> None:
    """Single atomic INSERT ... ON CONFLICT instead of SELECT-then-branch —
    the old select-then-insert had a race window where two concurrent first
    messages from the same brand-new user could both see no existing row and
    both attempt an INSERT, raising an uncaught IntegrityError on the second
    one and silently dropping that message (M2, docs/AUDIT-2026-07-10.md).
    first_seen is only ever set on the initial INSERT, never touched by the
    conflict update, matching the old UPDATE branches' behavior."""
    now = _now_iso()
    db = get_db()
    if topic_id is not None:
        db.execute(
            "INSERT INTO users (user_id, username, first_name, last_name, topic_id, first_seen, last_seen) "
            "VALUES (?,?,?,?,?,?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET "
            "username=excluded.username, first_name=excluded.first_name, "
            "last_name=excluded.last_name, topic_id=excluded.topic_id, last_seen=excluded.last_seen",
            (u.id, u.username, u.first_name, u.last_name, topic_id, now, now),
        )
    else:
        db.execute(
            "INSERT INTO users (user_id, username, first_name, last_name, topic_id, first_seen, last_seen) "
            "VALUES (?,?,?,?,NULL,?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET "
            "username=excluded.username, first_name=excluded.first_name, "
            "last_name=excluded.last_name, last_seen=excluded.last_seen",
            (u.id, u.username, u.first_name, u.last_name, now, now),
        )
    db.commit()

def db_set_topic(user_id: int, topic_id: int) -> None:
    get_db().execute("UPDATE users SET topic_id=? WHERE user_id=?", (topic_id, user_id))
    get_db().commit()

def db_get_user_by_topic(topic_id: int) -> Optional[sqlite3.Row]:
    return get_db().execute("SELECT * FROM users WHERE topic_id=?", (topic_id,)).fetchone()

def db_get_all_subscribers() -> List[sqlite3.Row]:
    return get_db().execute(
        "SELECT u.* FROM users u WHERE u.broadcast_opt=1 AND u.blocked=0 "
        "AND u.user_id NOT IN (SELECT user_id FROM bans)"
    ).fetchall()

def db_get_reachable_users() -> List[sqlite3.Row]:
    """All users who are not blocked and not banned (ignoring broadcast_opt)."""
    return get_db().execute(
        "SELECT u.* FROM users u WHERE u.blocked=0 "
        "AND u.user_id NOT IN (SELECT user_id FROM bans)"
    ).fetchall()

def db_set_broadcast_opt(user_id: int, opt: bool) -> None:
    get_db().execute("UPDATE users SET broadcast_opt=? WHERE user_id=?", (1 if opt else 0, user_id))
    get_db().commit()

def db_set_relay_paused(user_id: int, paused: bool) -> None:
    get_db().execute("UPDATE users SET relay_paused=? WHERE user_id=?", (1 if paused else 0, user_id))
    get_db().commit()

def db_mark_blocked(user_id: int) -> None:
    get_db().execute("UPDATE users SET blocked=1 WHERE user_id=?", (user_id,))
    get_db().commit()

def db_mark_unblocked(user_id: int) -> None:
    get_db().execute("UPDATE users SET blocked=0 WHERE user_id=?", (user_id,))
    get_db().commit()

def db_user_count() -> int:
    row = get_db().execute("SELECT COUNT(*) as c FROM users").fetchone()
    return row["c"]

def db_full_stats() -> dict:
    db = get_db()
    total = db.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]
    active = db.execute("SELECT COUNT(*) c FROM users WHERE blocked=0").fetchone()["c"]
    blocked = db.execute("SELECT COUNT(*) c FROM users WHERE blocked=1").fetchone()["c"]
    
    # We'll need to import db_get_banned inside here or handle it simply for now
    banned = db.execute("SELECT COUNT(*) c FROM bans").fetchone()["c"]
    
    subs_on = db.execute(
        "SELECT COUNT(*) c FROM users WHERE broadcast_opt=1 AND blocked=0 "
        "AND user_id NOT IN (SELECT user_id FROM bans)"
    ).fetchone()["c"]
    subs_off = db.execute(
        "SELECT COUNT(*) c FROM users WHERE broadcast_opt=0 AND blocked=0 "
        "AND user_id NOT IN (SELECT user_id FROM bans)"
    ).fetchone()["c"]
    
    msg_count = db.execute("SELECT COUNT(*) c FROM messages").fetchone()["c"]
    msg_in = db.execute("SELECT COUNT(*) c FROM messages WHERE direction='in'").fetchone()["c"]
    msg_out = db.execute("SELECT COUNT(*) c FROM messages WHERE direction='out'").fetchone()["c"]
    
    return {
        "total": total, "active": active, "blocked": blocked,
        "banned": banned, "subs_on": subs_on, "subs_off": subs_off,
        "msg_total": msg_count, "msg_in": msg_in, "msg_out": msg_out,
    }

def db_get_subscribers_by_tag(tag: str) -> List[sqlite3.Row]:
    return get_db().execute(
        "SELECT u.* FROM users u "
        "JOIN tags t ON u.user_id = t.user_id "
        "WHERE u.broadcast_opt=1 AND u.blocked=0 AND t.tag=? "
        "AND u.user_id NOT IN (SELECT user_id FROM bans)",
        (tag.upper(),),
    ).fetchall()

def db_list_users(filter_key: str = "all", tag: str = "", limit: int = 50) -> List[sqlite3.Row]:
    """List users, newest activity first. filter_key: all|active|blocked|banned|paused|tag."""
    base = "SELECT u.* FROM users u"
    where = ""
    params: tuple = ()
    if filter_key == "active":
        where = "WHERE u.blocked=0 AND u.user_id NOT IN (SELECT user_id FROM bans)"
    elif filter_key == "blocked":
        where = "WHERE u.blocked=1"
    elif filter_key == "banned":
        where = "WHERE u.user_id IN (SELECT user_id FROM bans)"
    elif filter_key == "paused":
        where = "WHERE u.relay_paused=1"
    elif filter_key == "tag":
        base += " JOIN tags t ON u.user_id = t.user_id"
        where = "WHERE t.tag=?"
        params = (tag.upper(),)
    return get_db().execute(
        f"{base} {where} ORDER BY u.last_seen DESC LIMIT ?", params + (limit,)
    ).fetchall()

def db_force_broadcast_all(on: bool = True) -> int:
    cur = get_db().execute("UPDATE users SET broadcast_opt=?", (1 if on else 0,))
    get_db().commit()
    return cur.rowcount
