import time
from config import SPAM_WINDOW, SPAM_MAX_MSGS, SPAM_WARN_BEFORE_BAN

# In-memory rate tracking
_spam_timestamps = {}
_spam_warnings = {}

def _check_spam(user_id: int) -> str:
    now = time.monotonic()
    timestamps = _spam_timestamps.setdefault(user_id, [])
    
    # Prune old timestamps
    timestamps[:] = [t for t in timestamps if now - t < SPAM_WINDOW]
    timestamps.append(now)

    if len(timestamps) > SPAM_MAX_MSGS:
        warns = _spam_warnings.get(user_id, 0) + 1
        _spam_warnings[user_id] = warns
        if warns >= SPAM_WARN_BEFORE_BAN:
            # Reset state
            _spam_timestamps.pop(user_id, None)
            _spam_warnings.pop(user_id, None)
            return "ban"
        return "warn"

    return "ok"

def _reset_spam(user_id: int) -> None:
    _spam_timestamps.pop(user_id, None)
    _spam_warnings.pop(user_id, None)
