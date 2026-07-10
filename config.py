import os
import sys
import logging

# A bare os.environ[...] KeyError/ValueError at import time paired with the
# systemd unit's Restart=always/RestartSec=5 meant a bad or missing env var
# crash-looped the service forever with just a raw traceback in the journal
# (M10, docs/AUDIT-2026-07-10.md). These helpers print one clear line and
# exit instead — still a crash-loop under systemd if the env is genuinely
# broken, but StartLimitBurst/StartLimitIntervalSec in the unit now caps
# that loop, and the failure reason is immediately legible in the log.

def _fatal(msg: str) -> None:
    sys.stderr.write(f"FATAL: {msg}\n")
    sys.exit(1)

def _require_str(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        _fatal(f"required environment variable {name} is not set. See env.example.")
    return value

def _require_int(name: str) -> int:
    raw = _require_str(name)
    try:
        return int(raw)
    except ValueError:
        _fatal(f"environment variable {name}={raw!r} must be an integer.")

def _optional_int(name: str, default):
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        _fatal(f"environment variable {name}={raw!r} must be an integer.")

# Telegram Config
BOT_TOKEN = _require_str("BOT_TOKEN")
OWNER_ID = _require_int("OWNER_ID")
try:
    ADMIN_IDS = {int(x.strip()) for x in os.environ.get("ADMIN_IDS", str(OWNER_ID)).split(",") if x.strip()}
except ValueError:
    _fatal(f"environment variable ADMIN_IDS={os.environ.get('ADMIN_IDS')!r} must be a comma-separated list of integers.")
ADMIN_GROUP_ID = _require_int("ADMIN_GROUP_ID")
BROADCAST_TOPIC_NAME = os.environ.get("BROADCAST_TOPIC_NAME", "📢 Broadcast")

# DB Config
DB_PATH = os.environ.get("DB_PATH", "state.db")
MAX_CONCURRENT = _optional_int("MAX_CONCURRENT", 15)
# messages/message_map/ai_drafts otherwise grow forever (L5/L6,
# docs/AUDIT-2026-07-10.md). 0 or negative disables retention pruning.
DATA_RETENTION_DAYS = _optional_int("DATA_RETENTION_DAYS", 180)

# Health endpoint (disabled unless HEALTH_PORT is set). Binds to localhost
# only by default — set HEALTH_HOST=0.0.0.0 to expose it beyond this host.
HEALTH_PORT = _optional_int("HEALTH_PORT", None)
HEALTH_HOST = os.environ.get("HEALTH_HOST", "127.0.0.1")

# Spam Config
SPAM_WINDOW = 10
SPAM_MAX_MSGS = 5
SPAM_WARN_BEFORE_BAN = 2
SPAM_BAN_DURATION = 86400

# Stellar Config
VERIFY_WALLET_PUBLIC = os.environ.get("VERIFY_WALLET_PUBLIC")
WALLET_ENCRYPTION_KEY = os.environ.get("WALLET_ENCRYPTION_KEY")

# AI Config
AI_API_KEY = os.environ.get("AI_API_KEY")
AI_MODEL = os.environ.get("AI_MODEL", "claude-haiku-4-5")

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("nopmsbot")
