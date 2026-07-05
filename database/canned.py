import sqlite3
import logging
from typing import Optional, List
from database.connection import get_db

log = logging.getLogger("nopmsbot")

def db_canned_set(name: str, body: str, content_type: str = "text",
                  file_id: Optional[str] = None) -> None:
    get_db().execute(
        "INSERT OR REPLACE INTO canned (name, body, content_type, file_id) VALUES (?,?,?,?)",
        (name.lower(), body, content_type, file_id),
    )
    get_db().commit()

def db_canned_get(name: str) -> Optional[sqlite3.Row]:
    """Full row: body, content_type ('text' or a media type), file_id."""
    return get_db().execute("SELECT * FROM canned WHERE name=?", (name.lower(),)).fetchone()

def db_canned_delete(name: str) -> bool:
    cur = get_db().execute("DELETE FROM canned WHERE name=?", (name.lower(),))
    get_db().commit()
    return cur.rowcount > 0

def db_canned_list() -> List[sqlite3.Row]:
    return get_db().execute("SELECT * FROM canned ORDER BY name").fetchall()
