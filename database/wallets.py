import sqlite3
import logging
from typing import Optional, List
from database.connection import get_db
from services.encryption import encrypt_key, decrypt_key
from utils.helpers import _now_iso

log = logging.getLogger("nopmsbot")

def db_add_wallet(user_id: int, address: str, label: str = "Main") -> bool:
    """Add a new wallet for a user. UNIQUE(user_id, address) ensures no duplicates."""
    try:
        get_db().execute(
            "INSERT INTO wallets (user_id, address, label, added_at) VALUES (?,?,?,?)",
            (user_id, address, label, _now_iso()),
        )
        get_db().commit()
        return True
    except sqlite3.IntegrityError:
        return False

def db_get_user_wallets(user_id: int) -> List[sqlite3.Row]:
    """Get all wallets for a specific user."""
    return get_db().execute(
        "SELECT * FROM wallets WHERE user_id=? ORDER BY added_at ASC", 
        (user_id,)
    ).fetchall()

def db_delete_wallet(user_id: int, address: str) -> bool:
    """Delete a specific wallet for a user."""
    db = get_db()
    cur = db.execute("DELETE FROM wallets WHERE user_id=? AND address=?", (user_id, address))
    # Also delete the key if it exists
    db.execute("DELETE FROM wallet_keys WHERE address=?", (address,))
    db.commit()
    return cur.rowcount > 0

def db_set_wallet_verified(user_id: int, address: str, method: int) -> None:
    """
    Mark a wallet as verified.
    method: 1=memo, 2=secret_key
    """
    get_db().execute(
        "UPDATE wallets SET verified=?, verified_at=? WHERE user_id=? AND address=?",
        (method, _now_iso(), user_id, address),
    )
    get_db().commit()

def db_store_key(address: str, secret_key: str) -> None:
    """Encrypt and store a wallet secret key."""
    encrypted = encrypt_key(secret_key)
    get_db().execute(
        "INSERT OR REPLACE INTO wallet_keys (address, encrypted_key, stored_at) VALUES (?,?,?)",
        (address, encrypted, _now_iso()),
    )
    get_db().commit()

def db_get_key(address: str) -> Optional[str]:
    """Retrieve and decrypt a wallet secret key."""
    row = get_db().execute("SELECT encrypted_key FROM wallet_keys WHERE address=?", (address,)).fetchone()
    if row:
        return decrypt_key(row["encrypted_key"])
    return None

def db_get_wallet_count(user_id: int) -> int:
    row = get_db().execute("SELECT COUNT(*) c FROM wallets WHERE user_id=?", (user_id,)).fetchone()
    return row["c"]

def db_get_wallet_by_id(wallet_id: int) -> Optional[sqlite3.Row]:
    return get_db().execute("SELECT * FROM wallets WHERE id=?", (wallet_id,)).fetchone()

def db_create_verification(user_id: int, address: str, challenge: str) -> None:
    """Create a pending memo verification entry (15 min expiry)."""
    from datetime import datetime, timezone, timedelta
    expires = (datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat()
    get_db().execute(
        "INSERT OR REPLACE INTO wallet_verifications (user_id, address, challenge, expires_at) VALUES (?,?,?,?)",
        (user_id, address, challenge, expires),
    )
    get_db().commit()

def db_get_pending_verifications() -> List[sqlite3.Row]:
    return get_db().execute("SELECT * FROM wallet_verifications").fetchall()

def db_delete_verification(user_id: int, address: str) -> None:
    get_db().execute("DELETE FROM wallet_verifications WHERE user_id=? AND address=?", (user_id, address))
    get_db().commit()

def db_all_wallets() -> List[sqlite3.Row]:
    return get_db().execute("SELECT * FROM wallets ORDER BY added_at DESC").fetchall()
