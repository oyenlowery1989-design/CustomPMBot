import sqlite3
import logging
from typing import Optional, List
from database.connection import get_db

log = logging.getLogger("nopmsbot")

def db_canned_set(name: str, body: str) -> None:
    get_db().execute("INSERT OR REPLACE INTO canned (name, body) VALUES (?,?)", (name.lower(), body))
    get_db().commit()

def db_canned_get(name: str) -> Optional[str]:
    row = get_db().execute("SELECT body FROM canned WHERE name=?", (name.lower(),)).fetchone()
    return row["body"] if row else None

def db_canned_delete(name: str) -> bool:
    cur = get_db().execute("DELETE FROM canned WHERE name=?", (name.lower(),))
    get_db().commit()
    return cur.rowcount > 0

def db_canned_list() -> List[sqlite3.Row]:
    return get_db().execute("SELECT name, body FROM canned ORDER BY name").fetchall()
