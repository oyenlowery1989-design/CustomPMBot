import re
import sqlite3
import logging
from typing import List, Optional, Tuple
from database.connection import get_db
from utils.helpers import _now_iso

log = logging.getLogger("nopmsbot")

def db_autoreply_set(keyword: str, response: str) -> None:
    get_db().execute(
        "INSERT OR REPLACE INTO auto_replies (keyword, response, created_at) VALUES (?,?,?)",
        (keyword.lower(), response, _now_iso()),
    )
    get_db().commit()

def db_autoreply_delete(keyword: str) -> bool:
    cur = get_db().execute("DELETE FROM auto_replies WHERE keyword=?", (keyword.lower(),))
    get_db().commit()
    return cur.rowcount > 0

def db_autoreply_list() -> List[sqlite3.Row]:
    return get_db().execute("SELECT * FROM auto_replies ORDER BY keyword").fetchall()

def db_autoreply_match(text: str) -> Optional[Tuple[str, str]]:
    """First keyword that appears as a whole word in text (case-insensitive).
    Word boundaries so 'hi' doesn't fire on 'this'. Returns (keyword, response)."""
    for row in db_autoreply_list():
        if re.search(r"\b" + re.escape(row["keyword"]) + r"\b", text, re.IGNORECASE):
            return row["keyword"], row["response"]
    return None
