import base64
import logging
from cryptography.fernet import Fernet
from config import WALLET_ENCRYPTION_KEY, log

# We use Fernet for AES-256-CBC encryption
_cipher = None

def get_cipher():
    global _cipher
    if _cipher is None:
        if not WALLET_ENCRYPTION_KEY:
            raise ValueError("WALLET_ENCRYPTION_KEY not found in environment!")
        _cipher = Fernet(WALLET_ENCRYPTION_KEY.encode())
    return _cipher

def encrypt_key(secret_key: str) -> str:
    """Encrypt a Stellar secret key to an unreadable string."""
    try:
        return get_cipher().encrypt(secret_key.encode()).decode()
    except Exception as e:
        log.error("Encryption failed: %s", e)
        raise

def decrypt_key(encrypted_blob: str) -> str:
    """Decrypt an unreadable string back to a Stellar secret key."""
    try:
        return get_cipher().decrypt(encrypted_blob.encode()).decode()
    except Exception as e:
        log.error("Decryption failed: %s", e)
        raise
