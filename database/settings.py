import sqlite3
import logging
from database.connection import get_db

log = logging.getLogger("nopmsbot")

def db_set_setting(key: str, value: str) -> None:
    get_db().execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)", (key, value))
    get_db().commit()

def db_get_setting(key: str, default: str = "") -> str:
    row = get_db().execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default
