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

def prune_stale_spam_state() -> int:
    """Remove per-user tracking entries with no recent activity. Without
    this, every distinct user who has ever messaged the bot keeps an entry
    in these dicts forever, for the life of the process (L4,
    docs/AUDIT-2026-07-10.md). Returns the number of entries removed."""
    now = time.monotonic()
    stale = [
        user_id for user_id, timestamps in _spam_timestamps.items()
        if not timestamps or now - timestamps[-1] >= SPAM_WINDOW
    ]
    for user_id in stale:
        _spam_timestamps.pop(user_id, None)
        _spam_warnings.pop(user_id, None)
    return len(stale)
