import logging
from typing import Dict, List, Optional
from stellar_sdk import Server, Keypair
from config import log

# Mainnet Horizon Server
HORIZON_URL = "https://horizon.stellar.org"

def get_balances(address: str) -> List[Dict]:
    """Fetch XLM and Asset balances for a wallet."""
    try:
        server = Server(HORIZON_URL)
        account = server.accounts().account_id(address).call()
        return account.get("balances", [])
    except Exception as e:
        log.error("Failed to fetch balances for %s: %s", address, e)
        return []

def verify_secret_key_match(public_address: str, secret_key: str) -> bool:
    """Verify if a secret key belongs to the given public address."""
    try:
        kp = Keypair.from_secret(secret_key)
        return kp.public_key == public_address
    except Exception:
        return False

async def check_recent_payments(verification_address: str, memo_code: str) -> bool:
    """
    Check if a specific payment with memo exists on the Stellar network.
    Usually used by the background watcher.
    """
    try:
        server = Server(HORIZON_URL)
        # We check the last 10 payments to the verification wallet
        payments = server.payments().for_account(verification_address).order(desc=True).limit(10).call()
        for p in payments.get("_embedded", {}).get("records", []):
            # Check if it's a payment with the correct memo
            # (Requires additional tx detail call usually, or streaming)
            pass
        return False
    except Exception as e:
        log.error("Stellar payment check failed: %s", e)
        return False
