import logging
from typing import List
from database.connection import get_db

log = logging.getLogger("nopmsbot")

def db_add_tag(user_id: int, tag: str) -> None:
    get_db().execute("INSERT OR IGNORE INTO tags (user_id, tag) VALUES (?,?)", (user_id, tag.upper()))
    get_db().commit()

def db_remove_tag(user_id: int, tag: str) -> bool:
    cur = get_db().execute("DELETE FROM tags WHERE user_id=? AND tag=?", (user_id, tag.upper()))
    get_db().commit()
    return cur.rowcount > 0

def db_get_tags(user_id: int) -> List[str]:
    rows = get_db().execute("SELECT tag FROM tags WHERE user_id=? ORDER BY tag", (user_id,)).fetchall()
    return [r["tag"] for r in rows]
