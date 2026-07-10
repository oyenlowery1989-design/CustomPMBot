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
            # Explicit rather than relying on sqlite3's implicit default —
            # without this, a writer under contention gets "database is
            # locked" immediately instead of waiting briefly for the other
            # writer to finish (L8, docs/AUDIT-2026-07-10.md).
            _db.execute("PRAGMA busy_timeout=5000")
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
