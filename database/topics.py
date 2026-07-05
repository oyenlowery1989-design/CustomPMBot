import sqlite3
import logging
from typing import Optional, List
from database.connection import get_db
from utils.helpers import _now_iso

log = logging.getLogger("nopmsbot")

def db_create_custom_topic(name: str, topic_id: int, description: str = "") -> None:
    get_db().execute(
        "INSERT OR REPLACE INTO custom_topics (name, topic_id, description, created_at) VALUES (?,?,?,?)",
        (name.lower(), topic_id, description, _now_iso()),
    )
    get_db().commit()

def db_delete_custom_topic(name: str) -> bool:
    db = get_db()
    db.execute("DELETE FROM topic_bindings WHERE topic_name=?", (name.lower(),))
    cur = db.execute("DELETE FROM custom_topics WHERE name=?", (name.lower(),))
    db.commit()
    return cur.rowcount > 0

def db_get_custom_topic(name: str) -> Optional[sqlite3.Row]:
    return get_db().execute("SELECT * FROM custom_topics WHERE name=?", (name.lower(),)).fetchone()

def db_list_custom_topics() -> List[sqlite3.Row]:
    return get_db().execute("SELECT * FROM custom_topics ORDER BY name").fetchall()

def db_bind_topic(bind_type: str, bind_key: str, topic_name: str) -> None:
    get_db().execute(
        "INSERT OR REPLACE INTO topic_bindings (bind_type, bind_key, topic_name) VALUES (?,?,?)",
        (bind_type, bind_key.lower(), topic_name.lower()),
    )
    get_db().commit()

def db_unbind_topic(bind_type: str, bind_key: str) -> bool:
    cur = get_db().execute(
        "DELETE FROM topic_bindings WHERE bind_type=? AND bind_key=?",
        (bind_type, bind_key.lower()),
    )
    get_db().commit()
    return cur.rowcount > 0

def db_get_binding(bind_type: str, bind_key: str) -> Optional[int]:
    row = get_db().execute(
        "SELECT ct.topic_id FROM topic_bindings tb "
        "JOIN custom_topics ct ON tb.topic_name = ct.name "
        "WHERE tb.bind_type=? AND tb.bind_key=?",
        (bind_type, bind_key.lower()),
    ).fetchone()
    return row["topic_id"] if row else None

def db_list_bindings() -> List[sqlite3.Row]:
    return get_db().execute(
        "SELECT tb.*, ct.topic_id FROM topic_bindings tb "
        "JOIN custom_topics ct ON tb.topic_name = ct.name "
        "ORDER BY tb.bind_type, tb.bind_key"
    ).fetchall()
