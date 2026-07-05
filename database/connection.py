from __future__ import annotations
import sqlite3
import logging
import threading
from config import DB_PATH

log = logging.getLogger("nopmsbot")
_db: sqlite3.Connection | None = None
_db_lock = threading.Lock()

def get_db() -> sqlite3.Connection:
    global _db
    with _db_lock:
        if _db is None:
            _db = sqlite3.connect(DB_PATH, check_same_thread=False)
            _db.row_factory = sqlite3.Row
            _db.execute("PRAGMA journal_mode=WAL")
            _db.execute("PRAGMA foreign_keys=ON")
    return _db

def close_db() -> None:
    """Close the database connection cleanly (call on shutdown)."""
    global _db
    with _db_lock:
        if _db is not None:
            try:
                _db.close()
            except Exception:
                pass
            _db = None
            log.info("Database connection closed.")
