from typing import Dict, List
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

