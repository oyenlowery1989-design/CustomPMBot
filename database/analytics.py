import sqlite3
import logging
from datetime import datetime, timedelta, timezone
from typing import List
from database.connection import get_db

log = logging.getLogger("nopmsbot")

def _cutoff(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

def db_messages_per_day(days: int = 7) -> List[sqlite3.Row]:
    """Per-day message counts (in/out) for the last N days, newest first."""
    return get_db().execute(
        "SELECT substr(timestamp,1,10) AS day, "
        "SUM(direction='in') AS msgs_in, SUM(direction='out') AS msgs_out "
        "FROM messages WHERE timestamp >= ? "
        "GROUP BY day ORDER BY day DESC",
        (_cutoff(days),),
    ).fetchall()

def db_new_users_per_day(days: int = 7) -> List[sqlite3.Row]:
    return get_db().execute(
        "SELECT substr(first_seen,1,10) AS day, COUNT(*) AS count "
        "FROM users WHERE first_seen >= ? "
        "GROUP BY day ORDER BY day DESC",
        (_cutoff(days),),
    ).fetchall()

def db_top_users(days: int = 7, limit: int = 5) -> List[sqlite3.Row]:
    """Most active users by incoming message count over the last N days."""
    return get_db().execute(
        "SELECT m.user_id, COUNT(*) AS count, u.first_name, u.username "
        "FROM messages m LEFT JOIN users u ON m.user_id = u.user_id "
        "WHERE m.direction='in' AND m.timestamp >= ? "
        "GROUP BY m.user_id ORDER BY count DESC LIMIT ?",
        (_cutoff(days), limit),
    ).fetchall()

def db_busiest_hours(days: int = 7, limit: int = 3) -> List[sqlite3.Row]:
    """Busiest UTC hours by incoming messages over the last N days."""
    return get_db().execute(
        "SELECT substr(timestamp,12,2) AS hour, COUNT(*) AS count "
        "FROM messages WHERE direction='in' AND timestamp >= ? "
        "GROUP BY hour ORDER BY count DESC LIMIT ?",
        (_cutoff(days), limit),
    ).fetchall()
