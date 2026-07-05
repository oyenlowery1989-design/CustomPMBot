import os
import logging

# Telegram Config
BOT_TOKEN = os.environ["BOT_TOKEN"]
OWNER_ID = int(os.environ["OWNER_ID"])
ADMIN_IDS = {int(x.strip()) for x in os.environ.get("ADMIN_IDS", str(OWNER_ID)).split(",") if x.strip()}
ADMIN_GROUP_ID = int(os.environ["ADMIN_GROUP_ID"])
BROADCAST_TOPIC_NAME = os.environ.get("BROADCAST_TOPIC_NAME", "📢 Broadcast")

# DB Config
DB_PATH = os.environ.get("DB_PATH", "state.db")
MAX_CONCURRENT = int(os.environ.get("MAX_CONCURRENT", "15"))

# Health endpoint (disabled unless HEALTH_PORT is set). Binds to localhost
# only by default — set HEALTH_HOST=0.0.0.0 to expose it beyond this host.
HEALTH_PORT = int(os.environ["HEALTH_PORT"]) if os.environ.get("HEALTH_PORT") else None
HEALTH_HOST = os.environ.get("HEALTH_HOST", "127.0.0.1")

# Spam Config
SPAM_WINDOW = 10
SPAM_MAX_MSGS = 5
SPAM_WARN_BEFORE_BAN = 2
SPAM_BAN_DURATION = 86400

# Stellar Config
VERIFY_WALLET_PUBLIC = os.environ.get("VERIFY_WALLET_PUBLIC")
WALLET_ENCRYPTION_KEY = os.environ.get("WALLET_ENCRYPTION_KEY")

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("nopmsbot")
