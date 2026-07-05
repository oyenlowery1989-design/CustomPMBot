#!/usr/bin/env python3
"""
NoPMsBot v2 — Telegram PM-to-forum-topic relay bot.

Features:
  • User DMs → dedicated forum topic per user in admin group
  • Admin replies in topic → relayed back to user
  • Broadcast topic → message sent to all/tagged subscribers
  • Ban with optional expiry, spam filter, media relay, topic management
  • User tags/labels, canned responses, message logging, conversation export

Single-file, SQLite-backed, python-telegram-bot 20.7.
"""

import asyncio
import html
import io
import json
import logging
import os
import re
import sqlite3
import time
from datetime import datetime, timezone
from typing import Optional

from telegram import (
    Bot,
    Chat,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    Update,
    User,
)
from telegram.constants import ChatType, MessageLimit, ParseMode
from telegram.error import BadRequest, Forbidden, TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# ═══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════════

BOT_TOKEN: str = os.environ["BOT_TOKEN"]
OWNER_ID: int = int(os.environ["OWNER_ID"])
ADMIN_IDS: set[int] = {int(x.strip()) for x in os.environ.get("ADMIN_IDS", str(OWNER_ID)).split(",") if x.strip()}
ADMIN_GROUP_ID: int = int(os.environ["ADMIN_GROUP_ID"])
BROADCAST_TOPIC_NAME: str = os.environ.get("BROADCAST_TOPIC_NAME", "📢 Broadcast")
DB_PATH: str = os.environ.get("DB_PATH", "state.db")
MAX_CONCURRENT: int = int(os.environ.get("MAX_CONCURRENT", "15"))

# Spam filter settings
SPAM_WINDOW: int = 10        # seconds
SPAM_MAX_MSGS: int = 5       # max messages in window
SPAM_WARN_BEFORE_BAN: int = 2  # warnings before auto-ban
SPAM_BAN_DURATION: int = 86400  # 1 day auto-ban in seconds

# ═══════════════════════════════════════════════════════════════════════════════
# LOGGING
# ═══════════════════════════════════════════════════════════════════════════════

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("nopmsbot")

# ═══════════════════════════════════════════════════════════════════════════════
# DATABASE
# ═══════════════════════════════════════════════════════════════════════════════

_db: sqlite3.Connection | None = None


def get_db() -> sqlite3.Connection:
    """Return (and lazily create) the module-level SQLite connection."""
    global _db
    if _db is None:
        _db = sqlite3.connect(DB_PATH, check_same_thread=False)
        _db.row_factory = sqlite3.Row
        _db.execute("PRAGMA journal_mode=WAL")
        _db.execute("PRAGMA foreign_keys=ON")
        _init_schema(_db)
    return _db


SCHEMA_VERSION = 5  # Bump this when adding migrations


def _init_schema(db: sqlite3.Connection) -> None:
    """Create tables if they don't exist, then run migrations."""

    # ── Base schema (v1) ──────────────────────────────────────────────────
    db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            user_id       INTEGER PRIMARY KEY,
            username      TEXT,
            first_name    TEXT,
            last_name     TEXT,
            topic_id      INTEGER,
            broadcast_opt INTEGER DEFAULT 1,   -- 1=subscribed, 0=opted out
            first_seen    TEXT,
            last_seen     TEXT
        );

        CREATE TABLE IF NOT EXISTS bans (
            user_id       INTEGER PRIMARY KEY,
            reason        TEXT,
            banned_at     TEXT,
            expires_at    TEXT   -- NULL = permanent
        );

        CREATE TABLE IF NOT EXISTS tags (
            user_id       INTEGER NOT NULL,
            tag           TEXT NOT NULL,
            PRIMARY KEY (user_id, tag)
        );

        CREATE TABLE IF NOT EXISTS canned (
            name          TEXT PRIMARY KEY,
            body          TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS messages (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id       INTEGER NOT NULL,
            direction     TEXT NOT NULL,  -- 'in' or 'out'
            content_type  TEXT,           -- text, photo, video, etc.
            text          TEXT,
            timestamp     TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS settings (
            key           TEXT PRIMARY KEY,
            value         TEXT
        );
    """)
    db.commit()

    # ── Migrations ────────────────────────────────────────────────────────
    _run_migrations(db)


def _get_schema_version(db: sqlite3.Connection) -> int:
    """Read current schema version from settings table."""
    try:
        row = db.execute("SELECT value FROM settings WHERE key='schema_version'").fetchone()
        return int(row["value"]) if row else 0
    except Exception:
        return 0


def _set_schema_version(db: sqlite3.Connection, version: int) -> None:
    db.execute(
        "INSERT OR REPLACE INTO settings (key, value) VALUES ('schema_version', ?)",
        (str(version),)
    )
    db.commit()


def _run_migrations(db: sqlite3.Connection) -> None:
    """
    Run incremental migrations based on schema_version.
    Each migration bumps the version by 1.
    To add a migration for v3:
      1. Bump SCHEMA_VERSION at top
      2. Add `if current < N:` block below
      3. ALTER TABLE / CREATE TABLE as needed
    """
    current = _get_schema_version(db)
    if current >= SCHEMA_VERSION:
        return

    log.info("DB migration: current v%d → target v%d", current, SCHEMA_VERSION)

    # ── Migration v0 → v1: initial schema (already created above) ────────
    if current < 1:
        log.info("Migration v0→v1: base schema created")
        current = 1

    # ── Migration v1 → v2: (placeholder for future columns) ─────────────
    if current < 2:
        # Example: add indexes for performance
        db.executescript("""
            CREATE INDEX IF NOT EXISTS idx_messages_user ON messages(user_id);
            CREATE INDEX IF NOT EXISTS idx_messages_ts ON messages(timestamp);
            CREATE INDEX IF NOT EXISTS idx_tags_tag ON tags(tag);
            CREATE INDEX IF NOT EXISTS idx_users_topic ON users(topic_id);
            CREATE INDEX IF NOT EXISTS idx_bans_expires ON bans(expires_at);
        """)
        log.info("Migration v1→v2: added performance indexes")
        current = 2

    # ── Future migration template ─────────────────────────────────────────
    # if current < 4:
    #     db.execute("ALTER TABLE users ADD COLUMN language TEXT DEFAULT 'en'")
    #     log.info("Migration v3→v4: ...")
    #     current = 4

    if current < 3:
        # Add blocked column: tracks users who blocked/deleted the bot
        try:
            db.execute("ALTER TABLE users ADD COLUMN blocked INTEGER DEFAULT 0")
        except Exception:
            pass  # column may already exist
        log.info("Migration v2→v3: added 'blocked' column to users")
        current = 3

    if current < 4:
        # Custom topics system: named topics + command/event bindings
        db.executescript("""
            CREATE TABLE IF NOT EXISTS custom_topics (
                name          TEXT PRIMARY KEY,   -- e.g. 'stats', 'logs', 'test'
                topic_id      INTEGER NOT NULL,   -- Telegram forum topic ID
                description   TEXT,
                created_at    TEXT
            );
            CREATE TABLE IF NOT EXISTS topic_bindings (
                bind_type     TEXT NOT NULL,       -- 'command' or 'event'
                bind_key      TEXT NOT NULL,       -- command name or event name
                topic_name    TEXT NOT NULL,       -- references custom_topics.name
                PRIMARY KEY (bind_type, bind_key)
            );
        """)
        log.info("Migration v3→v4: added custom_topics + topic_bindings tables")
        current = 4

    if current < 5:
        # Stellar wallet storage
        db.executescript("""
            CREATE TABLE IF NOT EXISTS wallets (
                user_id       INTEGER PRIMARY KEY,
                address       TEXT NOT NULL,
                added_at      TEXT
            );
        """)
        log.info("Migration v4→v5: added wallets table")
        current = 5

    _set_schema_version(db, current)
    db.commit()
    log.info("DB migration complete: now at v%d", current)


# ── DB helpers ────────────────────────────────────────────────────────────────

def db_get_user(user_id: int) -> Optional[sqlite3.Row]:
    return get_db().execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()


def db_upsert_user(u: User, topic_id: Optional[int] = None) -> None:
    """Insert or update a user record. If topic_id is given, set it."""
    now = _now_iso()
    db = get_db()
    existing = db_get_user(u.id)
    if existing is None:
        db.execute(
            "INSERT INTO users (user_id, username, first_name, last_name, topic_id, first_seen, last_seen) "
            "VALUES (?,?,?,?,?,?,?)",
            (u.id, u.username, u.first_name, u.last_name, topic_id, now, now),
        )
    else:
        if topic_id is not None:
            db.execute(
                "UPDATE users SET username=?, first_name=?, last_name=?, topic_id=?, last_seen=? WHERE user_id=?",
                (u.username, u.first_name, u.last_name, topic_id, now, u.id),
            )
        else:
            db.execute(
                "UPDATE users SET username=?, first_name=?, last_name=?, last_seen=? WHERE user_id=?",
                (u.username, u.first_name, u.last_name, now, u.id),
            )
    db.commit()


def db_set_topic(user_id: int, topic_id: int) -> None:
    get_db().execute("UPDATE users SET topic_id=? WHERE user_id=?", (topic_id, user_id))
    get_db().commit()


def db_get_user_by_topic(topic_id: int) -> Optional[sqlite3.Row]:
    return get_db().execute("SELECT * FROM users WHERE topic_id=?", (topic_id,)).fetchone()


def db_get_all_subscribers() -> list[sqlite3.Row]:
    """Users who opted into broadcasts, are not banned, and haven't blocked the bot."""
    return get_db().execute(
        "SELECT u.* FROM users u WHERE u.broadcast_opt=1 AND u.blocked=0 "
        "AND u.user_id NOT IN (SELECT user_id FROM bans)"
    ).fetchall()


def db_get_subscribers_by_tag(tag: str) -> list[sqlite3.Row]:
    """Subscribers with a specific tag (not banned, not blocked)."""
    return get_db().execute(
        "SELECT u.* FROM users u "
        "JOIN tags t ON u.user_id = t.user_id "
        "WHERE u.broadcast_opt=1 AND u.blocked=0 AND t.tag=? "
        "AND u.user_id NOT IN (SELECT user_id FROM bans)",
        (tag,),
    ).fetchall()


def db_is_banned(user_id: int) -> bool:
    row = get_db().execute("SELECT * FROM bans WHERE user_id=?", (user_id,)).fetchone()
    if row is None:
        return False
    if row["expires_at"] is not None:
        if datetime.fromisoformat(row["expires_at"]) <= datetime.now(timezone.utc):
            # Expired — remove ban
            get_db().execute("DELETE FROM bans WHERE user_id=?", (user_id,))
            get_db().commit()
            return False
    return True


def db_ban(user_id: int, reason: str = "", expires_at: Optional[str] = None) -> None:
    db = get_db()
    db.execute(
        "INSERT OR REPLACE INTO bans (user_id, reason, banned_at, expires_at) VALUES (?,?,?,?)",
        (user_id, reason, _now_iso(), expires_at),
    )
    db.commit()


def db_unban(user_id: int) -> bool:
    db = get_db()
    cur = db.execute("DELETE FROM bans WHERE user_id=?", (user_id,))
    db.commit()
    return cur.rowcount > 0


def db_get_banned() -> list[sqlite3.Row]:
    return get_db().execute("SELECT b.*, u.first_name, u.username FROM bans b LEFT JOIN users u ON b.user_id=u.user_id").fetchall()


def db_add_tag(user_id: int, tag: str) -> None:
    get_db().execute("INSERT OR IGNORE INTO tags (user_id, tag) VALUES (?,?)", (user_id, tag.upper()))
    get_db().commit()


def db_remove_tag(user_id: int, tag: str) -> bool:
    cur = get_db().execute("DELETE FROM tags WHERE user_id=? AND tag=?", (user_id, tag.upper()))
    get_db().commit()
    return cur.rowcount > 0


def db_get_tags(user_id: int) -> list[str]:
    rows = get_db().execute("SELECT tag FROM tags WHERE user_id=? ORDER BY tag", (user_id,)).fetchall()
    return [r["tag"] for r in rows]


def db_set_setting(key: str, value: str) -> None:
    get_db().execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)", (key, value))
    get_db().commit()


def db_get_setting(key: str, default: str = "") -> str:
    row = get_db().execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def db_set_broadcast_opt(user_id: int, opt: bool) -> None:
    get_db().execute("UPDATE users SET broadcast_opt=? WHERE user_id=?", (1 if opt else 0, user_id))
    get_db().commit()


def db_log_message(user_id: int, direction: str, content_type: str, text: str = "") -> None:
    get_db().execute(
        "INSERT INTO messages (user_id, direction, content_type, text, timestamp) VALUES (?,?,?,?,?)",
        (user_id, direction, content_type, text or "", _now_iso()),
    )
    get_db().commit()


def db_export_messages(user_id: int, limit: int = 200) -> list[sqlite3.Row]:
    return get_db().execute(
        "SELECT * FROM messages WHERE user_id=? ORDER BY id DESC LIMIT ?",
        (user_id, limit),
    ).fetchall()


def db_canned_set(name: str, body: str) -> None:
    get_db().execute("INSERT OR REPLACE INTO canned (name, body) VALUES (?,?)", (name.lower(), body))
    get_db().commit()


def db_canned_get(name: str) -> Optional[str]:
    row = get_db().execute("SELECT body FROM canned WHERE name=?", (name.lower(),)).fetchone()
    return row["body"] if row else None


def db_canned_delete(name: str) -> bool:
    cur = get_db().execute("DELETE FROM canned WHERE name=?", (name.lower(),))
    get_db().commit()
    return cur.rowcount > 0


def db_canned_list() -> list[sqlite3.Row]:
    return get_db().execute("SELECT name, body FROM canned ORDER BY name").fetchall()


def db_user_count() -> int:
    row = get_db().execute("SELECT COUNT(*) as c FROM users").fetchone()
    return row["c"]


def db_mark_blocked(user_id: int) -> None:
    """Mark a user as having blocked/deleted the bot."""
    get_db().execute("UPDATE users SET blocked=1 WHERE user_id=?", (user_id,))
    get_db().commit()


def db_mark_unblocked(user_id: int) -> None:
    """Mark user as reachable again (they messaged us)."""
    get_db().execute("UPDATE users SET blocked=0 WHERE user_id=?", (user_id,))
    get_db().commit()


def db_force_broadcast_all(on: bool = True) -> int:
    """Force-set broadcast opt for ALL users. Returns count affected."""
    cur = get_db().execute("UPDATE users SET broadcast_opt=?", (1 if on else 0,))
    get_db().commit()
    return cur.rowcount


def db_full_stats() -> dict:
    """Comprehensive stats for /stats command."""
    db = get_db()
    total = db.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]
    active = db.execute("SELECT COUNT(*) c FROM users WHERE blocked=0").fetchone()["c"]
    blocked = db.execute("SELECT COUNT(*) c FROM users WHERE blocked=1").fetchone()["c"]
    banned = len(db_get_banned())
    subs_on = db.execute(
        "SELECT COUNT(*) c FROM users WHERE broadcast_opt=1 AND blocked=0 "
        "AND user_id NOT IN (SELECT user_id FROM bans)"
    ).fetchone()["c"]
    subs_off = db.execute(
        "SELECT COUNT(*) c FROM users WHERE broadcast_opt=0 AND blocked=0 "
        "AND user_id NOT IN (SELECT user_id FROM bans)"
    ).fetchone()["c"]
    msg_count = db.execute("SELECT COUNT(*) c FROM messages").fetchone()["c"]
    msg_in = db.execute("SELECT COUNT(*) c FROM messages WHERE direction='in'").fetchone()["c"]
    msg_out = db.execute("SELECT COUNT(*) c FROM messages WHERE direction='out'").fetchone()["c"]
    return {
        "total": total, "active": active, "blocked": blocked,
        "banned": banned, "subs_on": subs_on, "subs_off": subs_off,
        "msg_total": msg_count, "msg_in": msg_in, "msg_out": msg_out,
    }


def db_get_expired_bans() -> list[sqlite3.Row]:
    now = _now_iso()
    return get_db().execute(
        "SELECT * FROM bans WHERE expires_at IS NOT NULL AND expires_at <= ?", (now,)
    ).fetchall()


# ── Wallet DB helpers ────────────────────────────────────────────────────────

def db_set_wallet(user_id: int, address: str) -> None:
    get_db().execute(
        "INSERT OR REPLACE INTO wallets (user_id, address, added_at) VALUES (?,?,?)",
        (user_id, address, _now_iso()),
    )
    get_db().commit()


def db_get_wallet(user_id: int) -> Optional[str]:
    row = get_db().execute("SELECT address FROM wallets WHERE user_id=?", (user_id,)).fetchone()
    return row["address"] if row else None


def db_delete_wallet(user_id: int) -> bool:
    cur = get_db().execute("DELETE FROM wallets WHERE user_id=?", (user_id,))
    get_db().commit()
    return cur.rowcount > 0


def db_get_wallet_by_address(address: str) -> Optional[sqlite3.Row]:
    return get_db().execute(
        "SELECT w.*, u.first_name, u.username FROM wallets w "
        "LEFT JOIN users u ON w.user_id = u.user_id WHERE w.address=?",
        (address,),
    ).fetchone()


def db_all_wallets() -> list[sqlite3.Row]:
    return get_db().execute(
        "SELECT w.*, u.first_name, u.username FROM wallets w "
        "LEFT JOIN users u ON w.user_id = u.user_id ORDER BY w.added_at DESC"
    ).fetchall()


# ── Custom Topics DB helpers ─────────────────────────────────────────────────

def db_create_custom_topic(name: str, topic_id: int, description: str = "") -> None:
    """Register a custom topic in the DB."""
    get_db().execute(
        "INSERT OR REPLACE INTO custom_topics (name, topic_id, description, created_at) VALUES (?,?,?,?)",
        (name.lower(), topic_id, description, _now_iso()),
    )
    get_db().commit()


def db_delete_custom_topic(name: str) -> bool:
    """Delete a custom topic and its bindings."""
    db = get_db()
    db.execute("DELETE FROM topic_bindings WHERE topic_name=?", (name.lower(),))
    cur = db.execute("DELETE FROM custom_topics WHERE name=?", (name.lower(),))
    db.commit()
    return cur.rowcount > 0


def db_get_custom_topic(name: str) -> Optional[sqlite3.Row]:
    return get_db().execute("SELECT * FROM custom_topics WHERE name=?", (name.lower(),)).fetchone()


def db_list_custom_topics() -> list[sqlite3.Row]:
    return get_db().execute("SELECT * FROM custom_topics ORDER BY name").fetchall()


def db_bind_topic(bind_type: str, bind_key: str, topic_name: str) -> None:
    """Bind a command or event to a custom topic.

    bind_type: 'command' or 'event'
    bind_key:  command name (e.g. 'stats') or event name (e.g. 'new_user', 'ban', 'unban', 'spam')
    """
    get_db().execute(
        "INSERT OR REPLACE INTO topic_bindings (bind_type, bind_key, topic_name) VALUES (?,?,?)",
        (bind_type, bind_key.lower(), topic_name.lower()),
    )
    get_db().commit()


def db_unbind_topic(bind_type: str, bind_key: str) -> bool:
    cur = get_db().execute(
        "DELETE FROM topic_bindings WHERE bind_type=? AND bind_key=?",
        (bind_type, bind_key.lower()),
    )
    get_db().commit()
    return cur.rowcount > 0


def db_get_binding(bind_type: str, bind_key: str) -> Optional[int]:
    """Get the topic_id for a bound command/event. Returns None if unbound."""
    row = get_db().execute(
        "SELECT ct.topic_id FROM topic_bindings tb "
        "JOIN custom_topics ct ON tb.topic_name = ct.name "
        "WHERE tb.bind_type=? AND tb.bind_key=?",
        (bind_type, bind_key.lower()),
    ).fetchone()
    return row["topic_id"] if row else None


def db_list_bindings() -> list[sqlite3.Row]:
    return get_db().execute(
        "SELECT tb.*, ct.topic_id FROM topic_bindings tb "
        "JOIN custom_topics ct ON tb.topic_name = ct.name "
        "ORDER BY tb.bind_type, tb.bind_key"
    ).fetchall()


async def _get_output_topic(bot, command_name: str, fallback_thread_id: int = None) -> Optional[int]:
    """Get the topic_id where a command should output.

    If bound to a custom topic, returns that topic_id.
    Otherwise returns fallback_thread_id (usually the current thread).
    """
    bound = db_get_binding("command", command_name)
    return bound if bound else fallback_thread_id


async def _send_event(bot, event_name: str, text: str, parse_mode=ParseMode.HTML) -> None:
    """Send an event message to its bound topic (if any).

    Events: new_user, ban, unban, spam, blocked
    """
    topic_id = db_get_binding("event", event_name)
    if topic_id:
        try:
            await bot.send_message(
                chat_id=ADMIN_GROUP_ID,
                message_thread_id=topic_id,
                text=text,
                parse_mode=parse_mode,
            )
        except TelegramError as e:
            log.warning("Failed to send event '%s' to topic: %s", event_name, e)


# ── Utilities ─────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_duration(s: str) -> Optional[int]:
    """Parse duration string like '1h', '7d', '30m' into seconds. Returns None on failure."""
    m = re.fullmatch(r"(\d+)\s*([mhdw])", s.strip().lower())
    if not m:
        return None
    val = int(m.group(1))
    unit = m.group(2)
    multipliers = {"m": 60, "h": 3600, "d": 86400, "w": 604800}
    return val * multipliers[unit]


def _format_duration(seconds: int) -> str:
    if seconds >= 604800 and seconds % 604800 == 0:
        return f"{seconds // 604800}w"
    if seconds >= 86400 and seconds % 86400 == 0:
        return f"{seconds // 86400}d"
    if seconds >= 3600 and seconds % 3600 == 0:
        return f"{seconds // 3600}h"
    return f"{seconds // 60}m"


def _user_link(user_id: int, name: str) -> str:
    return f'<a href="tg://user?id={user_id}">{html.escape(name)}</a>'


def _user_display(row: sqlite3.Row) -> str:
    name = row["first_name"] or "Unknown"
    if row["last_name"]:
        name += f" {row['last_name']}"
    return name


def _content_type_of(msg: Message) -> str:
    """Determine the content type of an incoming message."""
    if msg.photo:
        return "photo"
    if msg.video:
        return "video"
    if msg.document:
        return "document"
    if msg.sticker:
        return "sticker"
    if msg.voice:
        return "voice"
    if msg.video_note:
        return "video_note"
    if msg.animation:
        return "animation"
    if msg.audio:
        return "audio"
    if msg.contact:
        return "contact"
    if msg.location:
        return "location"
    if msg.text:
        return "text"
    return "other"


def _is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


# ═══════════════════════════════════════════════════════════════════════════════
# SPAM FILTER
# ═══════════════════════════════════════════════════════════════════════════════

# In-memory rate tracking: user_id → list of timestamps
_spam_timestamps: dict[int, list[float]] = {}
_spam_warnings: dict[int, int] = {}


def _check_spam(user_id: int) -> str:
    """
    Check if user is spamming.
    Returns: 'ok', 'warn', or 'ban'.
    """
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


# ═══════════════════════════════════════════════════════════════════════════════
# TOPIC MANAGEMENT
# ═══════════════════════════════════════════════════════════════════════════════

async def _ensure_topic(bot: Bot, user: User) -> int:
    """
    Get or create a forum topic for this user in the admin group.
    Returns the topic (message_thread_id).
    """
    row = db_get_user(user.id)
    if row and row["topic_id"]:
        return row["topic_id"]

    # Create new topic
    name = user.first_name or "User"
    if user.last_name:
        name += f" {user.last_name}"
    name = name[:128]  # Telegram limit

    try:
        topic = await bot.create_forum_topic(
            chat_id=ADMIN_GROUP_ID,
            name=name,
        )
        topic_id = topic.message_thread_id
    except TelegramError as e:
        log.error("Failed to create topic for user %s: %s", user.id, e)
        raise

    # Save topic
    db_upsert_user(user, topic_id=topic_id)

    # Send info card
    tags = db_get_tags(user.id)
    tag_str = ", ".join(tags) if tags else "none"
    info_lines = [
        f"👤 <b>New conversation</b>",
        f"Name: {_user_link(user.id, name)}",
        f"ID: <code>{user.id}</code>",
    ]
    if user.username:
        info_lines.append(f"Username: @{user.username}")
    info_lines.append(f"Tags: {tag_str}")
    info_lines.append(f"First seen: {_now_iso()[:10]}")
    info = "\n".join(info_lines)

    try:
        await bot.send_message(
            chat_id=ADMIN_GROUP_ID,
            message_thread_id=topic_id,
            text=info,
            parse_mode=ParseMode.HTML,
        )
    except TelegramError as e:
        log.warning("Failed to send info card: %s", e)

    log.info("Created topic %s for user %s (%s)", topic_id, user.id, name)

    # Send new_user event to bound topic
    uname = f" (@{user.username})" if user.username else ""
    await _send_event(bot, "new_user",
        f"🆕 <b>New user</b>: {_user_link(user.id, name)}{uname} — ID: <code>{user.id}</code>")

    return topic_id


async def _send_user_info_card(bot: Bot, topic_id: int, user: User) -> None:
    """Send an updated user info card in their topic."""
    row = db_get_user(user.id)
    name = user.first_name or "User"
    if user.last_name:
        name += f" {user.last_name}"

    tags = db_get_tags(user.id)
    tag_str = ", ".join(tags) if tags else "none"

    lines = [
        f"👤 <b>User Info</b>",
        f"Name: {_user_link(user.id, name)}",
        f"ID: <code>{user.id}</code>",
    ]
    if user.username:
        lines.append(f"Username: @{user.username}")
    lines.append(f"Tags: {tag_str}")
    if row:
        lines.append(f"First seen: {row['first_seen'][:10] if row['first_seen'] else 'unknown'}")
        lines.append(f"Last seen: {row['last_seen'][:10] if row['last_seen'] else 'unknown'}")
        lines.append(f"Broadcasts: {'✅ subscribed' if row['broadcast_opt'] else '❌ opted out'}")

    # Check ban status
    ban = get_db().execute("SELECT * FROM bans WHERE user_id=?", (user.id,)).fetchone()
    if ban:
        ban_info = "🚫 BANNED"
        if ban["reason"]:
            ban_info += f" — {ban['reason']}"
        if ban["expires_at"]:
            ban_info += f" (until {ban['expires_at'][:16]})"
        lines.append(ban_info)

    await bot.send_message(
        chat_id=ADMIN_GROUP_ID,
        message_thread_id=topic_id,
        text="\n".join(lines),
        parse_mode=ParseMode.HTML,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# MEDIA FORWARDING HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

async def _forward_to_topic(bot: Bot, msg: Message, topic_id: int) -> None:
    """Forward any message type from user to admin topic."""
    try:
        await msg.forward(chat_id=ADMIN_GROUP_ID, message_thread_id=topic_id)
    except TelegramError as e:
        log.error("Failed to forward message to topic %s: %s", topic_id, e)


async def _relay_to_user(bot: Bot, msg: Message, user_id: int, raise_on_block: bool = False) -> None:
    """Relay an admin's reply to the user, supporting all media types."""
    try:
        if msg.text:
            await bot.send_message(chat_id=user_id, text=msg.text, entities=msg.entities)
        elif msg.photo:
            await bot.send_photo(
                chat_id=user_id,
                photo=msg.photo[-1].file_id,
                caption=msg.caption,
                caption_entities=msg.caption_entities,
            )
        elif msg.video:
            await bot.send_video(
                chat_id=user_id,
                video=msg.video.file_id,
                caption=msg.caption,
                caption_entities=msg.caption_entities,
            )
        elif msg.document:
            await bot.send_document(
                chat_id=user_id,
                document=msg.document.file_id,
                caption=msg.caption,
                caption_entities=msg.caption_entities,
            )
        elif msg.sticker:
            await bot.send_sticker(chat_id=user_id, sticker=msg.sticker.file_id)
        elif msg.voice:
            await bot.send_voice(
                chat_id=user_id,
                voice=msg.voice.file_id,
                caption=msg.caption,
                caption_entities=msg.caption_entities,
            )
        elif msg.video_note:
            await bot.send_video_note(chat_id=user_id, video_note=msg.video_note.file_id)
        elif msg.animation:
            await bot.send_animation(
                chat_id=user_id,
                animation=msg.animation.file_id,
                caption=msg.caption,
                caption_entities=msg.caption_entities,
            )
        elif msg.audio:
            await bot.send_audio(
                chat_id=user_id,
                audio=msg.audio.file_id,
                caption=msg.caption,
                caption_entities=msg.caption_entities,
            )
        elif msg.contact:
            await bot.send_contact(
                chat_id=user_id,
                phone_number=msg.contact.phone_number,
                first_name=msg.contact.first_name,
                last_name=msg.contact.last_name,
            )
        elif msg.location:
            await bot.send_location(
                chat_id=user_id,
                latitude=msg.location.latitude,
                longitude=msg.location.longitude,
            )
        else:
            # Fallback: try to forward
            await msg.forward(chat_id=user_id)
    except Forbidden:
        log.warning("User %s has blocked the bot", user_id)
        db_mark_blocked(user_id)
        if raise_on_block:
            raise
    except TelegramError as e:
        log.error("Failed to relay to user %s: %s", user_id, e)
        if raise_on_block:
            raise


# ═══════════════════════════════════════════════════════════════════════════════
# BROADCAST
# ═══════════════════════════════════════════════════════════════════════════════

_broadcast_topic_id: Optional[int] = None


async def _find_broadcast_topic(bot: Bot) -> Optional[int]:
    """We store the broadcast topic ID in settings once found/created."""
    global _broadcast_topic_id
    if _broadcast_topic_id is not None:
        return _broadcast_topic_id

    stored = db_get_setting("broadcast_topic_id")
    if stored:
        _broadcast_topic_id = int(stored)
        return _broadcast_topic_id

    # Create broadcast topic
    try:
        topic = await bot.create_forum_topic(
            chat_id=ADMIN_GROUP_ID,
            name=BROADCAST_TOPIC_NAME,
        )
        _broadcast_topic_id = topic.message_thread_id
        db_set_setting("broadcast_topic_id", str(_broadcast_topic_id))
        log.info("Created broadcast topic: %s", _broadcast_topic_id)
        return _broadcast_topic_id
    except TelegramError as e:
        log.error("Failed to create broadcast topic: %s", e)
        return None


async def _do_broadcast(bot: Bot, msg: Message, recipients: list[sqlite3.Row], label: str) -> None:
    """Send a broadcast message to recipients with live progress."""
    total = len(recipients)
    if total == 0:
        await bot.send_message(
            chat_id=ADMIN_GROUP_ID,
            message_thread_id=msg.message_thread_id,
            text=f"📢 No recipients for broadcast ({label}).",
        )
        return

    # Determine broadcast content (strip optional @TAG first line)
    broadcast_msg = msg

    progress = await bot.send_message(
        chat_id=ADMIN_GROUP_ID,
        message_thread_id=msg.message_thread_id,
        text=f"📢 Broadcasting to {total} users ({label})… 0/{total}",
    )

    sem = asyncio.Semaphore(MAX_CONCURRENT)
    sent, failed, blocked = 0, 0, 0
    lock = asyncio.Lock()

    async def _send_one(user_row: sqlite3.Row) -> None:
        nonlocal sent, failed, blocked
        async with sem:
            try:
                await _relay_to_user(bot, broadcast_msg, user_row["user_id"], raise_on_block=True)
                async with lock:
                    sent += 1
            except Forbidden:
                async with lock:
                    blocked += 1
            except TelegramError:
                async with lock:
                    failed += 1

    tasks = [asyncio.create_task(_send_one(r)) for r in recipients]

    # Update progress periodically
    update_interval = max(1, total // 20)
    done_count = 0
    for coro in asyncio.as_completed(tasks):
        await coro
        done_count += 1
        if done_count % update_interval == 0 or done_count == total:
            try:
                await progress.edit_text(
                    f"📢 Broadcasting ({label})… {done_count}/{total}\n"
                    f"✅ {sent}  ❌ {failed}  🚫 {blocked}"
                )
            except TelegramError:
                pass

    # Final report
    try:
        await progress.edit_text(
            f"📢 Broadcast complete ({label})\n"
            f"Total: {total} | ✅ Sent: {sent} | ❌ Failed: {failed} | 🚫 Blocked: {blocked}"
        )
    except TelegramError:
        pass

    log.info("Broadcast (%s): %d sent, %d failed, %d blocked out of %d", label, sent, failed, blocked, total)


# ═══════════════════════════════════════════════════════════════════════════════
# USER-FACING COMMANDS (in DM)
# ═══════════════════════════════════════════════════════════════════════════════

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start in DM — welcome message with buttons."""
    if update.effective_chat.type != ChatType.PRIVATE:
        return

    user = update.effective_user
    db_upsert_user(user)

    welcome = db_get_setting("welcome_message",
        "👋 Welcome! Send me a message and I'll forward it to the admin.\n\n"
        "Commands:\n"
        "/help — Show help\n"
        "/settings — Broadcast preferences"
    )

    # Check if wallet already exists
    wallet = db_get_wallet(user.id)
    wallet_label = "✅ Wallet Connected" if wallet else "💳 Add Stellar Wallet"
    wallet_data = "wallet_view" if wallet else "wallet_add"

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(wallet_label, callback_data=wallet_data)],
        [
            InlineKeyboardButton("⚙️ Settings", callback_data="settings"),
            InlineKeyboardButton("📖 Help", callback_data="help"),
        ],
    ])

    await update.message.reply_text(welcome, reply_markup=keyboard)
    log.info("User %s (%s) started the bot", user.id, user.first_name)


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /help — shows all commands. Admin commands only shown to admins."""
    user = update.effective_user
    if not user or not update.message:
        return

    is_admin = _is_admin(user.id)

    text = (
        "📬 <b>NoPMsBot — Command Reference</b>\n\n"
        "Just send me any message (text, photo, video, etc.) "
        "and it will be forwarded to the admin.\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "👤 <b>User Commands</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "/start — Start the bot / welcome message\n"
        "/help — This command reference\n"
        "/settings — Show broadcast subscription status\n"
        "/settings on — Subscribe to broadcasts\n"
        "/settings off — Unsubscribe from broadcasts\n"
    )

    if is_admin:
        text += (
            "\n━━━━━━━━━━━━━━━━━━━━\n"
            "📊 <b>Admin — Statistics</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "/stats — Full bot statistics (users, broadcasts, messages)\n"

            "\n━━━━━━━━━━━━━━━━━━━━\n"
            "🚫 <b>Admin — User Management</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "/ban &lt;user_id&gt; [duration] [reason] — Ban a user\n"
            "  <i>Duration: 30m, 1h, 7d, 4w (omit = permanent)</i>\n"
            "  <i>Can also use inside a user's topic without user_id</i>\n"
            "/unban &lt;user_id&gt; — Unban a user\n"
            "/banned — List all banned users\n"

            "\n━━━━━━━━━━━━━━━━━━━━\n"
            "📢 <b>Admin — Broadcasting</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "Post in 📢 Broadcast topic → sent to all subscribers\n"
            "First line <code>@TAG</code> → sends only to tagged users\n"
            "/forcebroadcast on — Re-enable broadcasts for ALL users\n"
            "/forcebroadcast off — Disable broadcasts for ALL users\n"

            "\n━━━━━━━━━━━━━━━━━━━━\n"
            "🗂 <b>Admin — Topic Management</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "/close — Close/archive current user topic\n"
            "/reopen — Reopen a closed topic\n"
            "/note &lt;text&gt; — Pin a note in current topic\n"
            "/topic create &lt;name&gt; [desc] — Create custom topic\n"
            "/topic delete &lt;name&gt; — Delete custom topic\n"
            "/topic list — List all custom topics + bindings\n"
            "/topic info &lt;name&gt; — Show topic details\n"
            "/topic bind &lt;key&gt; &lt;topic&gt; — Bind command/event to topic\n"
            "/topic unbind &lt;key&gt; — Remove binding\n"
            "  <i>Bindable commands: stats, banned, export</i>\n"
            "  <i>Bindable events: new_user, ban, spam, blocked</i>\n"

            "\n━━━━━━━━━━━━━━━━━━━━\n"
            "🏷 <b>Admin — Tags &amp; Labels</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "/tag &lt;label&gt; — Tag user (in their topic)\n"
            "/tag &lt;user_id&gt; &lt;label&gt; — Tag user by ID\n"
            "/tag remove &lt;label&gt; — Remove tag (in topic)\n"
            "/tag remove &lt;user_id&gt; &lt;label&gt; — Remove tag by ID\n"
            "/tag — Show tags for current topic user\n"

            "\n━━━━━━━━━━━━━━━━━━━━\n"
            "💬 <b>Admin — Canned Responses</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "/canned add &lt;name&gt; &lt;text&gt; — Save a reusable response\n"
            "/canned list — List all saved responses\n"
            "/canned del &lt;name&gt; — Delete a response\n"
            "/canned &lt;name&gt; — Send saved response to user (in topic)\n"

            "\n━━━━━━━━━━━━━━━━━━━━\n"
            "📋 <b>Admin — Messages &amp; Export</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "/export — Export conversation log (in topic)\n"
            "/export &lt;user_id&gt; — Export by user ID\n"

            "\n━━━━━━━━━━━━━━━━━━━━\n"
            "⚙️ <b>Admin — Settings</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "/setmsg &lt;text&gt; — Change welcome message\n"
            "/setmsg — Show current welcome message\n"
        )

    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


async def cmd_settings(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /settings in DM — toggle broadcast opt-in."""
    if update.effective_chat.type != ChatType.PRIVATE:
        return

    user = update.effective_user
    db_upsert_user(user)
    row = db_get_user(user.id)

    current = bool(row["broadcast_opt"]) if row else True

    args = ctx.args
    if args and args[0].lower() in ("on", "off"):
        new_val = args[0].lower() == "on"
        db_set_broadcast_opt(user.id, new_val)
        status = "subscribed ✅" if new_val else "unsubscribed ❌"
        await update.message.reply_text(f"Broadcasts: {status}")
    else:
        status = "subscribed ✅" if current else "unsubscribed ❌"
        await update.message.reply_text(
            f"📢 Broadcast status: {status}\n\n"
            f"Use /settings on or /settings off to change."
        )


# ═══════════════════════════════════════════════════════════════════════════════
# ADMIN COMMANDS (in admin group)
# ═══════════════════════════════════════════════════════════════════════════════

async def cmd_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Show bot stats. Works in admin group or DM for admins."""
    if not update.effective_user or not _is_admin(update.effective_user.id):
        return
    if not update.message:
        return

    s = db_full_stats()

    text = (
        f"📊 <b>Bot Statistics</b>\n"
        f"<i>{_now_iso()[:16]}</i>\n\n"
        f"<b>👥 Users</b>\n"
        f"  Total: <b>{s['total']}</b>\n"
        f"  Active: <b>{s['active']}</b>\n"
        f"  Blocked/Deleted us: <b>{s['blocked']}</b>\n"
        f"  Banned: <b>{s['banned']}</b>\n\n"
        f"<b>📢 Broadcasts</b>\n"
        f"  Subscribed: <b>{s['subs_on']}</b>\n"
        f"  Opted out: <b>{s['subs_off']}</b>\n"
        f"  Unreachable (blocked): <b>{s['blocked']}</b>\n\n"
        f"<b>💬 Messages</b>\n"
        f"  Total: <b>{s['msg_total']}</b>\n"
        f"  Received: <b>{s['msg_in']}</b>\n"
        f"  Sent: <b>{s['msg_out']}</b>"
    )

    # If stats is bound to a custom topic, also send there
    bound_topic = db_get_binding("command", "stats")
    if bound_topic:
        try:
            await ctx.bot.send_message(
                chat_id=ADMIN_GROUP_ID,
                message_thread_id=bound_topic,
                text=text,
                parse_mode=ParseMode.HTML,
            )
        except TelegramError as e:
            log.warning("Failed to send stats to bound topic: %s", e)

    # Always reply to the caller too
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


async def cmd_ban(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /ban <user_id> [duration] [reason]
    Duration: 1h, 7d, 30d, etc. Omit for permanent.
    Can also be used in a topic without user_id.
    """
    if not update.effective_user or not _is_admin(update.effective_user.id):
        return
    if not update.message:
        return

    args = ctx.args or []
    user_id = None
    duration_str = None
    reason = ""

    # If in a topic, we can infer user_id
    if update.effective_message.message_thread_id and not args:
        row = db_get_user_by_topic(update.effective_message.message_thread_id)
        if row:
            user_id = row["user_id"]
    elif args:
        try:
            user_id = int(args[0])
            args = args[1:]
        except ValueError:
            await update.message.reply_text("Usage: /ban <user_id> [duration] [reason]")
            return

        # Check for duration
        if args:
            dur = _parse_duration(args[0])
            if dur is not None:
                duration_str = args[0]
                args = args[1:]

        reason = " ".join(args)

    if user_id is None:
        await update.message.reply_text("Usage: /ban <user_id> [duration] [reason]\nOr use in a user's topic.")
        return

    expires_at = None
    if duration_str:
        dur_seconds = _parse_duration(duration_str)
        if dur_seconds:
            expires_at = (datetime.now(timezone.utc).timestamp() + dur_seconds)
            expires_at = datetime.fromtimestamp(expires_at, tz=timezone.utc).isoformat()

    db_ban(user_id, reason=reason, expires_at=expires_at)
    _reset_spam(user_id)

    parts = [f"🚫 Banned user <code>{user_id}</code>"]
    if duration_str:
        parts.append(f"Duration: {duration_str}")
    if reason:
        parts.append(f"Reason: {reason}")
    if expires_at:
        parts.append(f"Expires: {expires_at[:16]}")

    await update.message.reply_text("\n".join(parts), parse_mode=ParseMode.HTML)
    await _send_event(ctx.bot, "ban", "\n".join(parts))
    log.info("Banned user %s (duration=%s, reason=%s)", user_id, duration_str, reason)


async def cmd_unban(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """/unban <user_id> or use in topic."""
    if not update.effective_user or not _is_admin(update.effective_user.id):
        return
    if not update.message:
        return

    args = ctx.args or []
    user_id = None

    if update.effective_message.message_thread_id and not args:
        row = db_get_user_by_topic(update.effective_message.message_thread_id)
        if row:
            user_id = row["user_id"]
    elif args:
        try:
            user_id = int(args[0])
        except ValueError:
            await update.message.reply_text("Usage: /unban <user_id>")
            return

    if user_id is None:
        await update.message.reply_text("Usage: /unban <user_id>")
        return

    if db_unban(user_id):
        _reset_spam(user_id)
        await update.message.reply_text(f"✅ Unbanned user <code>{user_id}</code>", parse_mode=ParseMode.HTML)
        await _send_event(ctx.bot, "ban", f"✅ Unbanned user <code>{user_id}</code>")
        log.info("Unbanned user %s", user_id)
    else:
        await update.message.reply_text("User is not banned.")


async def cmd_banned(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """/banned — list all banned users."""
    if not update.effective_user or not _is_admin(update.effective_user.id):
        return
    if not update.message:
        return

    bans = db_get_banned()
    if not bans:
        await update.message.reply_text("No banned users.")
        return

    lines = ["🚫 <b>Banned Users</b>\n"]
    for b in bans:
        name = b["first_name"] or "Unknown"
        uname = f" (@{b['username']})" if b["username"] else ""
        line = f"• <code>{b['user_id']}</code> — {html.escape(name)}{uname}"
        if b["reason"]:
            line += f" — {html.escape(b['reason'])}"
        if b["expires_at"]:
            line += f"\n  ⏰ expires {b['expires_at'][:16]}"
        lines.append(line)

    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


async def cmd_setmsg(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """/setmsg <text> — set the welcome message."""
    if not update.effective_user or not _is_admin(update.effective_user.id):
        return
    if not update.message:
        return

    text = " ".join(ctx.args) if ctx.args else ""
    if not text:
        current = db_get_setting("welcome_message", "(default)")
        await update.message.reply_text(f"Current welcome message:\n\n{current}\n\nUsage: /setmsg <text>")
        return

    db_set_setting("welcome_message", text)
    await update.message.reply_text("✅ Welcome message updated.")


async def cmd_forcebroadcast(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """/forcebroadcast on — force-enable broadcasts for ALL users (even those who opted out)."""
    if not update.effective_user or not _is_admin(update.effective_user.id):
        return
    if not update.message:
        return

    args = ctx.args or []
    if not args or args[0].lower() not in ("on", "off"):
        s = db_full_stats()
        await update.message.reply_text(
            f"📢 <b>Force Broadcast Control</b>\n\n"
            f"Currently: <b>{s['subs_on']}</b> subscribed, <b>{s['subs_off']}</b> opted out\n\n"
            f"Usage:\n"
            f"/forcebroadcast on — re-enable broadcasts for ALL users\n"
            f"/forcebroadcast off — disable broadcasts for ALL users",
            parse_mode=ParseMode.HTML,
        )
        return

    on = args[0].lower() == "on"
    count = db_force_broadcast_all(on)
    status = "enabled ✅" if on else "disabled ❌"
    await update.message.reply_text(
        f"📢 Broadcasts force-{status} for <b>{count}</b> users.",
        parse_mode=ParseMode.HTML,
    )
    log.info("Force broadcast %s for %d users by admin %s", "on" if on else "off", count, update.effective_user.id)


async def cmd_topic(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /topic create <name> [description] — Create a custom topic in the admin group
    /topic delete <name>               — Delete a custom topic and its bindings
    /topic list                        — List all custom topics and their bindings
    /topic bind <command|event> <name> — Bind a command/event output to a topic
    /topic unbind <command|event>      — Remove a binding
    /topic info <name>                 — Show topic details and bindings

    Bindable commands: stats, banned, export
    Bindable events:   new_user, ban, unban, spam, blocked
    """
    if not update.effective_user or not _is_admin(update.effective_user.id):
        return
    if not update.message:
        return

    args = ctx.args or []
    if not args:
        await update.message.reply_text(
            "🗂 <b>Custom Topics Manager</b>\n\n"
            "<b>Commands:</b>\n"
            "/topic create &lt;name&gt; [description]\n"
            "  → Create a named topic in admin group\n\n"
            "/topic delete &lt;name&gt;\n"
            "  → Delete a topic + its bindings\n\n"
            "/topic list\n"
            "  → Show all topics and bindings\n\n"
            "/topic bind &lt;command|event&gt; &lt;topic_name&gt;\n"
            "  → Route command output or events to a topic\n\n"
            "/topic unbind &lt;command|event&gt;\n"
            "  → Remove a routing\n\n"
            "/topic info &lt;name&gt;\n"
            "  → Details about a topic\n\n"
            "<b>Bindable commands:</b>\n"
            "  stats, banned, export\n\n"
            "<b>Bindable events:</b>\n"
            "  new_user — new user notifications\n"
            "  ban — ban/unban actions\n"
            "  spam — spam filter auto-bans\n"
            "  blocked — user blocked/unblocked bot\n",
            parse_mode=ParseMode.HTML,
        )
        return

    subcmd = args[0].lower()

    # ── /topic create <name> [description] ────────────────────────────────
    if subcmd == "create":
        if len(args) < 2:
            await update.message.reply_text("Usage: /topic create <name> [description]")
            return

        name = args[1].lower()
        description = " ".join(args[2:]) if len(args) > 2 else ""

        # Check if already exists
        existing = db_get_custom_topic(name)
        if existing:
            await update.message.reply_text(
                f"Topic <b>{name}</b> already exists (ID: {existing['topic_id']})",
                parse_mode=ParseMode.HTML,
            )
            return

        # Create topic in Telegram
        # Use emoji prefixes for common names
        emoji_map = {
            "stats": "📊", "logs": "📋", "test": "🧪", "bans": "🚫",
            "events": "📣", "spam": "🛡️", "debug": "🐛", "notes": "📝",
        }
        emoji = emoji_map.get(name, "📁")
        display_name = f"{emoji} {name.title()}"

        try:
            topic = await ctx.bot.create_forum_topic(
                chat_id=ADMIN_GROUP_ID,
                name=display_name,
            )
            topic_id = topic.message_thread_id
        except TelegramError as e:
            await update.message.reply_text(f"Failed to create topic: {e}")
            return

        db_create_custom_topic(name, topic_id, description)

        # Send welcome message in the new topic
        await ctx.bot.send_message(
            chat_id=ADMIN_GROUP_ID,
            message_thread_id=topic_id,
            text=(
                f"🗂 <b>Custom Topic: {name}</b>\n"
                f"{description or 'No description'}\n\n"
                f"Bind commands/events here with:\n"
                f"<code>/topic bind stats {name}</code>\n"
                f"<code>/topic bind new_user {name}</code>"
            ),
            parse_mode=ParseMode.HTML,
        )

        await update.message.reply_text(
            f"✅ Created topic <b>{display_name}</b> (ID: {topic_id})\n"
            f"Now bind commands with: /topic bind &lt;command&gt; {name}",
            parse_mode=ParseMode.HTML,
        )
        log.info("Created custom topic '%s' (ID: %s)", name, topic_id)
        return

    # ── /topic delete <name> ──────────────────────────────────────────────
    if subcmd in ("delete", "del", "remove"):
        if len(args) < 2:
            await update.message.reply_text("Usage: /topic delete <name>")
            return

        name = args[1].lower()
        topic = db_get_custom_topic(name)
        if not topic:
            await update.message.reply_text(f"Topic '{name}' not found.")
            return

        # Try to close the Telegram topic
        try:
            await ctx.bot.close_forum_topic(
                chat_id=ADMIN_GROUP_ID,
                message_thread_id=topic["topic_id"],
            )
        except TelegramError:
            pass

        db_delete_custom_topic(name)
        await update.message.reply_text(f"🗑 Deleted topic <b>{name}</b> and all its bindings.", parse_mode=ParseMode.HTML)
        log.info("Deleted custom topic '%s'", name)
        return

    # ── /topic list ───────────────────────────────────────────────────────
    if subcmd == "list":
        topics = db_list_custom_topics()
        bindings = db_list_bindings()

        if not topics:
            await update.message.reply_text(
                "No custom topics yet.\n\nCreate one with: /topic create <name> [description]"
            )
            return

        lines = ["🗂 <b>Custom Topics</b>\n"]
        for t in topics:
            # Find bindings for this topic
            topic_binds = [b for b in bindings if b["topic_name"] == t["name"]]
            bind_str = ", ".join(f"{b['bind_key']}" for b in topic_binds) if topic_binds else "none"
            desc = f" — {t['description']}" if t["description"] else ""
            lines.append(f"• <b>{t['name']}</b> (ID: {t['topic_id']}){desc}")
            lines.append(f"  Bindings: {bind_str}")

        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)
        return

    # ── /topic bind <command|event> <topic_name> ──────────────────────────
    if subcmd == "bind":
        if len(args) < 3:
            await update.message.reply_text("Usage: /topic bind <command|event> <topic_name>")
            return

        key = args[1].lower()
        topic_name = args[2].lower()

        # Validate bindable keys
        valid_commands = {"stats", "banned", "export"}
        valid_events = {"new_user", "ban", "unban", "spam", "blocked"}

        if key in valid_commands:
            bind_type = "command"
        elif key in valid_events:
            bind_type = "event"
        else:
            await update.message.reply_text(
                f"Unknown bindable key: <b>{key}</b>\n\n"
                f"Commands: {', '.join(sorted(valid_commands))}\n"
                f"Events: {', '.join(sorted(valid_events))}",
                parse_mode=ParseMode.HTML,
            )
            return

        # Check topic exists
        topic = db_get_custom_topic(topic_name)
        if not topic:
            await update.message.reply_text(f"Topic '{topic_name}' not found. Create it first with /topic create {topic_name}")
            return

        db_bind_topic(bind_type, key, topic_name)
        await update.message.reply_text(
            f"✅ Bound <b>{key}</b> ({bind_type}) → topic <b>{topic_name}</b>",
            parse_mode=ParseMode.HTML,
        )
        log.info("Bound %s '%s' to topic '%s'", bind_type, key, topic_name)
        return

    # ── /topic unbind <command|event> ─────────────────────────────────────
    if subcmd == "unbind":
        if len(args) < 2:
            await update.message.reply_text("Usage: /topic unbind <command|event>")
            return

        key = args[1].lower()
        # Try both types
        removed = db_unbind_topic("command", key) or db_unbind_topic("event", key)
        if removed:
            await update.message.reply_text(f"✅ Unbound <b>{key}</b>", parse_mode=ParseMode.HTML)
        else:
            await update.message.reply_text(f"No binding found for '{key}'.")
        return

    # ── /topic info <name> ────────────────────────────────────────────────
    if subcmd == "info":
        if len(args) < 2:
            await update.message.reply_text("Usage: /topic info <name>")
            return

        name = args[1].lower()
        topic = db_get_custom_topic(name)
        if not topic:
            await update.message.reply_text(f"Topic '{name}' not found.")
            return

        bindings = db_list_bindings()
        topic_binds = [b for b in bindings if b["topic_name"] == name]

        lines = [
            f"🗂 <b>Topic: {name}</b>",
            f"ID: {topic['topic_id']}",
            f"Description: {topic['description'] or '—'}",
            f"Created: {topic['created_at'][:16] if topic['created_at'] else '—'}",
            "",
            "<b>Bindings:</b>",
        ]
        if topic_binds:
            for b in topic_binds:
                lines.append(f"  • {b['bind_type']}: {b['bind_key']}")
        else:
            lines.append("  none — use /topic bind <key> " + name)

        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)
        return

    await update.message.reply_text("Unknown subcommand. Use /topic for help.")


async def cmd_close(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """/close — close/archive the current topic."""
    if not update.effective_user or not _is_admin(update.effective_user.id):
        return
    if not update.message:
        return

    thread_id = update.effective_message.message_thread_id
    if not thread_id:
        await update.message.reply_text("Use this command inside a user's topic.")
        return

    try:
        await ctx.bot.close_forum_topic(chat_id=ADMIN_GROUP_ID, message_thread_id=thread_id)
        await update.message.reply_text("📁 Topic closed.")
        log.info("Closed topic %s", thread_id)
    except TelegramError as e:
        await update.message.reply_text(f"Failed to close topic: {e}")


async def cmd_reopen(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """/reopen — reopen a closed topic."""
    if not update.effective_user or not _is_admin(update.effective_user.id):
        return
    if not update.message:
        return

    thread_id = update.effective_message.message_thread_id
    if not thread_id:
        await update.message.reply_text("Use this command inside a user's topic.")
        return

    try:
        await ctx.bot.reopen_forum_topic(chat_id=ADMIN_GROUP_ID, message_thread_id=thread_id)
        await update.message.reply_text("🔓 Topic reopened.")
        log.info("Reopened topic %s", thread_id)
    except TelegramError as e:
        await update.message.reply_text(f"Failed to reopen topic: {e}")


async def cmd_note(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """/note <text> — send and pin a note in the current topic."""
    if not update.effective_user or not _is_admin(update.effective_user.id):
        return
    if not update.message:
        return

    thread_id = update.effective_message.message_thread_id
    if not thread_id:
        await update.message.reply_text("Use this command inside a user's topic.")
        return

    text = " ".join(ctx.args) if ctx.args else ""
    if not text:
        await update.message.reply_text("Usage: /note <text>")
        return

    try:
        note_msg = await ctx.bot.send_message(
            chat_id=ADMIN_GROUP_ID,
            message_thread_id=thread_id,
            text=f"📌 <b>Note:</b> {html.escape(text)}",
            parse_mode=ParseMode.HTML,
        )
        await ctx.bot.pin_chat_message(
            chat_id=ADMIN_GROUP_ID,
            message_id=note_msg.message_id,
        )
        log.info("Pinned note in topic %s", thread_id)
    except TelegramError as e:
        await update.message.reply_text(f"Failed: {e}")


async def cmd_tag(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /tag <user_id> <label> — add a tag to a user
    /tag remove <user_id> <label> — remove a tag
    Or use in topic: /tag <label>
    """
    if not update.effective_user or not _is_admin(update.effective_user.id):
        return
    if not update.message:
        return

    args = ctx.args or []
    thread_id = update.effective_message.message_thread_id

    # /tag remove ...
    if args and args[0].lower() == "remove":
        args = args[1:]
        if thread_id and len(args) == 1:
            row = db_get_user_by_topic(thread_id)
            if row:
                tag = args[0]
                if db_remove_tag(row["user_id"], tag):
                    await update.message.reply_text(f"🏷 Removed tag {tag.upper()} from user {row['user_id']}")
                else:
                    await update.message.reply_text("Tag not found.")
                return
        elif len(args) >= 2:
            try:
                uid = int(args[0])
                tag = args[1]
                if db_remove_tag(uid, tag):
                    await update.message.reply_text(f"🏷 Removed tag {tag.upper()} from user {uid}")
                else:
                    await update.message.reply_text("Tag not found.")
            except ValueError:
                await update.message.reply_text("Usage: /tag remove <user_id> <label>")
            return
        await update.message.reply_text("Usage: /tag remove <user_id> <label>")
        return

    # /tag in topic with just label
    if thread_id and len(args) == 1:
        row = db_get_user_by_topic(thread_id)
        if row:
            tag = args[0]
            db_add_tag(row["user_id"], tag)
            all_tags = db_get_tags(row["user_id"])
            await update.message.reply_text(
                f"🏷 Tagged user {row['user_id']} as <b>{tag.upper()}</b>\nAll tags: {', '.join(all_tags)}",
                parse_mode=ParseMode.HTML,
            )
            return

    # /tag <user_id> <label>
    if len(args) >= 2:
        try:
            uid = int(args[0])
            tag = args[1]
            db_add_tag(uid, tag)
            all_tags = db_get_tags(uid)
            await update.message.reply_text(
                f"🏷 Tagged user {uid} as <b>{tag.upper()}</b>\nAll tags: {', '.join(all_tags)}",
                parse_mode=ParseMode.HTML,
            )
        except ValueError:
            await update.message.reply_text("Usage: /tag <user_id> <label>")
        return

    # No args — show tags for topic user
    if thread_id:
        row = db_get_user_by_topic(thread_id)
        if row:
            tags = db_get_tags(row["user_id"])
            if tags:
                await update.message.reply_text(f"🏷 Tags for {row['user_id']}: {', '.join(tags)}")
            else:
                await update.message.reply_text("No tags. Usage: /tag <label>")
            return

    await update.message.reply_text("Usage: /tag <user_id> <label> or /tag <label> in a topic")


async def cmd_export(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """/export [user_id] — export conversation log."""
    if not update.effective_user or not _is_admin(update.effective_user.id):
        return
    if not update.message:
        return

    args = ctx.args or []
    user_id = None
    thread_id = update.effective_message.message_thread_id

    if args:
        try:
            user_id = int(args[0])
        except ValueError:
            await update.message.reply_text("Usage: /export <user_id>")
            return
    elif thread_id:
        row = db_get_user_by_topic(thread_id)
        if row:
            user_id = row["user_id"]

    if user_id is None:
        await update.message.reply_text("Usage: /export <user_id> or use in a topic")
        return

    messages = db_export_messages(user_id, limit=500)
    if not messages:
        await update.message.reply_text("No messages logged for this user.")
        return

    user_row = db_get_user(user_id)
    name = _user_display(user_row) if user_row else str(user_id)

    lines = [f"📋 Conversation log for {name} (ID: {user_id})\n"]
    for m in reversed(messages):  # chronological order
        direction = "→" if m["direction"] == "in" else "←"
        ts = m["timestamp"][:16].replace("T", " ")
        content = m["text"][:100] if m["text"] else f"[{m['content_type']}]"
        lines.append(f"{ts} {direction} {content}")

    text = "\n".join(lines)
    if len(text) > 4000:
        # Send as file
        buf = io.BytesIO(text.encode())
        buf.name = f"export_{user_id}.txt"
        await update.message.reply_document(document=buf)
    else:
        await update.message.reply_text(text)


async def cmd_canned(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /canned list — list canned responses
    /canned add <name> <text> — add a canned response
    /canned del <name> — delete a canned response
    /canned <name> — send canned response to topic user
    """
    if not update.effective_user or not _is_admin(update.effective_user.id):
        return
    if not update.message:
        return

    args = ctx.args or []
    if not args:
        await update.message.reply_text(
            "Usage:\n"
            "/canned list\n"
            "/canned add <name> <text>\n"
            "/canned del <name>\n"
            "/canned <name> — send to user in topic"
        )
        return

    subcmd = args[0].lower()

    if subcmd == "list":
        items = db_canned_list()
        if not items:
            await update.message.reply_text("No canned responses saved.")
            return
        lines = ["📝 <b>Canned Responses</b>\n"]
        for item in items:
            preview = item["body"][:50] + ("…" if len(item["body"]) > 50 else "")
            lines.append(f"• <b>{html.escape(item['name'])}</b>: {html.escape(preview)}")
        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)
        return

    if subcmd == "add":
        if len(args) < 3:
            await update.message.reply_text("Usage: /canned add <name> <text>")
            return
        name = args[1]
        body = " ".join(args[2:])
        db_canned_set(name, body)
        await update.message.reply_text(f"✅ Saved canned response: <b>{html.escape(name)}</b>", parse_mode=ParseMode.HTML)
        return

    if subcmd == "del":
        if len(args) < 2:
            await update.message.reply_text("Usage: /canned del <name>")
            return
        name = args[1]
        if db_canned_delete(name):
            await update.message.reply_text(f"🗑 Deleted canned response: {name}")
        else:
            await update.message.reply_text("Not found.")
        return

    # /canned <name> — send to user
    name = subcmd
    body = db_canned_get(name)
    if body is None:
        await update.message.reply_text(f"Canned response '{name}' not found. Use /canned list")
        return

    thread_id = update.effective_message.message_thread_id
    if not thread_id:
        await update.message.reply_text("Use /canned <name> inside a user's topic to send it.")
        return

    row = db_get_user_by_topic(thread_id)
    if not row:
        await update.message.reply_text("Can't determine user for this topic.")
        return

    try:
        await ctx.bot.send_message(chat_id=row["user_id"], text=body)
        db_log_message(row["user_id"], "out", "text", body)
        await update.message.reply_text(f"✅ Sent canned response '{name}' to user.")
    except TelegramError as e:
        await update.message.reply_text(f"Failed to send: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# MESSAGE HANDLERS
# ═══════════════════════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════════════════════
# CALLBACK QUERY HANDLERS (inline buttons)
# ═══════════════════════════════════════════════════════════════════════════════

# In-memory state: tracks users who are in "wallet input" mode
_awaiting_wallet: set[int] = set()


async def cb_wallet_add(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """User pressed '💳 Add Stellar Wallet' button."""
    q = update.callback_query
    await q.answer()
    user = q.from_user

    _awaiting_wallet.add(user.id)
    await q.edit_message_text(
        "💳 <b>Add Stellar Wallet</b>\n\n"
        "Please send your Stellar public address.\n"
        "It should start with <code>G</code> and be 56 characters long.\n\n"
        "Example: <code>GABCDE...XYZ</code>\n\n"
        "Send /cancel to abort.",
        parse_mode=ParseMode.HTML,
    )


async def cb_wallet_view(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """User pressed '✅ Wallet Connected' button — show wallet info."""
    q = update.callback_query
    await q.answer()
    user = q.from_user

    address = db_get_wallet(user.id)
    if not address:
        await q.edit_message_text("No wallet connected. Use /start to add one.")
        return

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Change Wallet", callback_data="wallet_add")],
        [InlineKeyboardButton("🗑 Remove Wallet", callback_data="wallet_remove")],
        [InlineKeyboardButton("« Back", callback_data="back_start")],
    ])

    await q.edit_message_text(
        f"💳 <b>Your Stellar Wallet</b>\n\n"
        f"Address: <code>{address}</code>\n\n"
        f"Use the buttons below to manage.",
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard,
    )


async def cb_wallet_remove(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """User pressed 'Remove Wallet' button."""
    q = update.callback_query
    await q.answer()
    user = q.from_user

    db_delete_wallet(user.id)
    await q.edit_message_text(
        "🗑 Wallet removed.\n\nUse /start to add a new one.",
    )


async def cb_settings(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Settings button from /start."""
    q = update.callback_query
    await q.answer()
    user = q.from_user

    row = db_get_user(user.id)
    current = bool(row["broadcast_opt"]) if row else True
    status = "🔔 ON" if current else "🔕 OFF"

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(
            f"Broadcasts: {status} — tap to toggle",
            callback_data="toggle_broadcast",
        )],
        [InlineKeyboardButton("« Back", callback_data="back_start")],
    ])

    await q.edit_message_text(
        "⚙️ <b>Settings</b>\n\n"
        f"Broadcast subscription: <b>{status}</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard,
    )


async def cb_toggle_broadcast(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Toggle broadcast opt-in/out."""
    q = update.callback_query
    await q.answer()
    user = q.from_user

    row = db_get_user(user.id)
    new_val = not bool(row["broadcast_opt"]) if row else False
    db_set_broadcast_opt(user.id, new_val)

    status = "🔔 ON" if new_val else "🔕 OFF"
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(
            f"Broadcasts: {status} — tap to toggle",
            callback_data="toggle_broadcast",
        )],
        [InlineKeyboardButton("« Back", callback_data="back_start")],
    ])

    await q.edit_message_text(
        "⚙️ <b>Settings</b>\n\n"
        f"Broadcast subscription: <b>{status}</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard,
    )


async def cb_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Help button from /start."""
    q = update.callback_query
    await q.answer()

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("« Back", callback_data="back_start")],
    ])

    await q.edit_message_text(
        "📬 <b>Help</b>\n\n"
        "Just send me any message (text, photo, video, etc.) "
        "and it will be forwarded to the admin.\n\n"
        "/start — Main menu\n"
        "/help — Full command list\n"
        "/settings — Broadcast preferences\n"
        "/wallet — Manage Stellar wallet",
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard,
    )


async def cb_back_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Back to /start menu."""
    q = update.callback_query
    await q.answer()
    user = q.from_user

    welcome = db_get_setting("welcome_message",
        "👋 Welcome! Send me a message and I'll forward it to the admin.\n\n"
        "Commands:\n"
        "/help — Show help\n"
        "/settings — Broadcast preferences"
    )

    wallet = db_get_wallet(user.id)
    wallet_label = "✅ Wallet Connected" if wallet else "💳 Add Stellar Wallet"
    wallet_data = "wallet_view" if wallet else "wallet_add"

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(wallet_label, callback_data=wallet_data)],
        [
            InlineKeyboardButton("⚙️ Settings", callback_data="settings"),
            InlineKeyboardButton("📖 Help", callback_data="help"),
        ],
    ])

    await q.edit_message_text(welcome, reply_markup=keyboard)


# ═══════════════════════════════════════════════════════════════════════════════
# WALLET COMMANDS
# ═══════════════════════════════════════════════════════════════════════════════

async def cmd_wallet(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """/wallet — show or manage Stellar wallet."""
    if update.effective_chat.type != ChatType.PRIVATE:
        return
    if not update.message:
        return

    user = update.effective_user
    address = db_get_wallet(user.id)

    if address:
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Change Wallet", callback_data="wallet_add")],
            [InlineKeyboardButton("🗑 Remove Wallet", callback_data="wallet_remove")],
        ])
        await update.message.reply_text(
            f"💳 <b>Your Stellar Wallet</b>\n\n"
            f"Address: <code>{address}</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard,
        )
    else:
        _awaiting_wallet.add(user.id)
        await update.message.reply_text(
            "💳 <b>Add Stellar Wallet</b>\n\n"
            "Please send your Stellar public address.\n"
            "It should start with <code>G</code> and be 56 characters long.\n\n"
            "Send /cancel to abort.",
            parse_mode=ParseMode.HTML,
        )


async def cmd_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """/cancel — cancel wallet input or any pending action."""
    if update.effective_chat.type != ChatType.PRIVATE:
        return
    if not update.message:
        return

    user = update.effective_user
    if user.id in _awaiting_wallet:
        _awaiting_wallet.discard(user.id)
        await update.message.reply_text("Cancelled.")
    else:
        await update.message.reply_text("Nothing to cancel.")


# Admin command: /wallets — list all wallets
async def cmd_wallets(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """/wallets — list all user wallets (admin only)."""
    if not update.effective_user or not _is_admin(update.effective_user.id):
        return
    if not update.message:
        return

    wallets = db_all_wallets()
    if not wallets:
        await update.message.reply_text("No wallets registered.")
        return

    lines = [f"💳 <b>Registered Wallets ({len(wallets)})</b>\n"]
    for w in wallets:
        name = w["first_name"] or "Unknown"
        uname = f" (@{w['username']})" if w["username"] else ""
        addr = w["address"][:8] + "..." + w["address"][-4:]
        lines.append(f"• <code>{w['user_id']}</code> — {html.escape(name)}{uname}")
        lines.append(f"  <code>{w['address']}</code>")

    text = "\n".join(lines)
    if len(text) > 4000:
        buf = io.BytesIO(text.encode())
        buf.name = "wallets.txt"
        await update.message.reply_document(document=buf)
    else:
        await update.message.reply_text(text, parse_mode=ParseMode.HTML)


# ═══════════════════════════════════════════════════════════════════════════════
# MESSAGE HANDLERS
# ═══════════════════════════════════════════════════════════════════════════════

async def handle_private_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle all incoming DMs from users → forward to admin topic."""
    msg = update.effective_message
    user = update.effective_user
    if not msg or not user:
        return

    # ── Wallet input mode ─────────────────────────────────────────────────
    if user.id in _awaiting_wallet and msg.text:
        address = msg.text.strip()

        # Validate Stellar address: starts with G, 56 chars, alphanumeric
        if len(address) == 56 and address.startswith("G") and address.isalnum():
            db_set_wallet(user.id, address)
            _awaiting_wallet.discard(user.id)

            await msg.reply_text(
                f"✅ <b>Wallet saved!</b>\n\n"
                f"Address: <code>{address}</code>\n\n"
                f"Use /wallet to view or change it.",
                parse_mode=ParseMode.HTML,
            )

            # Notify admin topic
            row = db_get_user(user.id)
            if row and row["topic_id"]:
                try:
                    await ctx.bot.send_message(
                        chat_id=ADMIN_GROUP_ID,
                        message_thread_id=row["topic_id"],
                        text=f"💳 User set Stellar wallet:\n<code>{address}</code>",
                        parse_mode=ParseMode.HTML,
                    )
                except TelegramError:
                    pass

            log.info("User %s set wallet: %s", user.id, address)
            return
        else:
            await msg.reply_text(
                "❌ Invalid Stellar address.\n\n"
                "It should start with <b>G</b> and be exactly 56 characters.\n"
                "Please try again or send /cancel to abort.",
                parse_mode=ParseMode.HTML,
            )
            return

    # Update user record
    db_upsert_user(user)

    # If user was marked blocked, they're back — unblock
    u = db_get_user(user.id)
    if u and u["blocked"]:
        db_mark_unblocked(user.id)
        log.info("User %s unblocked (messaged us again)", user.id)

    # Check ban
    if db_is_banned(user.id):
        log.info("Ignoring message from banned user %s", user.id)
        return

    # Spam check
    spam_result = _check_spam(user.id)
    if spam_result == "ban":
        db_ban(user.id, reason="Auto-ban: spam", expires_at=(
            datetime.fromtimestamp(
                datetime.now(timezone.utc).timestamp() + SPAM_BAN_DURATION,
                tz=timezone.utc
            ).isoformat()
        ))
        await msg.reply_text("🚫 You have been temporarily banned for spamming.")
        log.warning("Auto-banned user %s for spamming", user.id)
        # Notify admin topic
        row = db_get_user(user.id)
        if row and row["topic_id"]:
            try:
                await ctx.bot.send_message(
                    chat_id=ADMIN_GROUP_ID,
                    message_thread_id=row["topic_id"],
                    text=f"⚠️ User auto-banned for spamming (24h).",
                )
            except TelegramError:
                pass
        await _send_event(ctx.bot, "spam",
            f"🛡️ Auto-banned user <code>{user.id}</code> ({html.escape(user.first_name or 'Unknown')}) for spamming (24h)")
        return
    elif spam_result == "warn":
        await msg.reply_text("⚠️ Slow down! You're sending messages too fast.")
        return

    # Ensure topic exists
    try:
        topic_id = await _ensure_topic(ctx.bot, user)
    except TelegramError:
        await msg.reply_text("⚠️ Something went wrong. Please try again later.")
        return

    # Forward to topic
    await _forward_to_topic(ctx.bot, msg, topic_id)

    # Log message
    ct = _content_type_of(msg)
    text_content = msg.text or msg.caption or ""
    db_log_message(user.id, "in", ct, text_content)

    log.info("Forwarded %s from user %s to topic %s", ct, user.id, topic_id)


async def handle_admin_group_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle messages in the admin group → relay to users or handle broadcasts."""
    msg = update.effective_message
    if not msg or not msg.message_thread_id:
        return

    sender = update.effective_user
    if not sender or sender.is_bot:
        return

    # Only admins can relay
    if not _is_admin(sender.id):
        return

    thread_id = msg.message_thread_id

    # Check if this is the broadcast topic
    broadcast_tid = await _find_broadcast_topic(ctx.bot)
    if broadcast_tid and thread_id == broadcast_tid:
        # Broadcast message
        text = msg.text or msg.caption or ""
        lines = text.split("\n", 1)
        tag_match = re.match(r"^@(\w+)$", lines[0].strip()) if lines else None

        if tag_match:
            tag = tag_match.group(1)
            # Strip the tag line from the message text for broadcast
            if msg.text and len(lines) > 1:
                # We need to create a modified message — but we can't modify msg.
                # Instead, we'll send the remaining text to each user directly.
                broadcast_text = lines[1] if len(lines) > 1 else ""
                recipients = db_get_subscribers_by_tag(tag)

                if not recipients:
                    await ctx.bot.send_message(
                        chat_id=ADMIN_GROUP_ID,
                        message_thread_id=thread_id,
                        text=f"No subscribers with tag @{tag}.",
                    )
                    return

                # For tagged broadcasts with modified text, send manually
                total = len(recipients)
                progress = await ctx.bot.send_message(
                    chat_id=ADMIN_GROUP_ID,
                    message_thread_id=thread_id,
                    text=f"📢 Broadcasting to {total} @{tag} users… 0/{total}",
                )
                sem = asyncio.Semaphore(MAX_CONCURRENT)
                _lock = asyncio.Lock()
                sent, failed, blocked = 0, 0, 0

                async def _send_tagged(uid: int) -> None:
                    nonlocal sent, failed, blocked
                    async with sem:
                        try:
                            await ctx.bot.send_message(chat_id=uid, text=broadcast_text)
                            async with _lock:
                                sent += 1
                        except Forbidden:
                            async with _lock:
                                blocked += 1
                        except TelegramError:
                            async with _lock:
                                failed += 1

                tasks = [asyncio.create_task(_send_tagged(r["user_id"])) for r in recipients]
                for coro in asyncio.as_completed(tasks):
                    await coro

                await progress.edit_text(
                    f"📢 Broadcast to @{tag} complete\n"
                    f"Total: {total} | ✅ {sent} | ❌ {failed} | 🚫 {blocked}"
                )
                return
            else:
                recipients = db_get_subscribers_by_tag(tag)
                await _do_broadcast(ctx.bot, msg, recipients, f"@{tag}")
                return
        else:
            # Broadcast to all subscribers
            recipients = db_get_all_subscribers()
            await _do_broadcast(ctx.bot, msg, recipients, "all")
            return

    # Regular topic → relay to user
    row = db_get_user_by_topic(thread_id)
    if not row:
        return

    user_id = row["user_id"]
    await _relay_to_user(ctx.bot, msg, user_id)

    # Log outgoing message
    ct = _content_type_of(msg)
    text_content = msg.text or msg.caption or ""
    db_log_message(user_id, "out", ct, text_content)

    log.info("Relayed %s from admin to user %s", ct, user_id)


# ═══════════════════════════════════════════════════════════════════════════════
# BACKGROUND JOBS
# ═══════════════════════════════════════════════════════════════════════════════

async def job_expire_bans(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Periodic job to remove expired bans."""
    expired = db_get_expired_bans()
    for ban in expired:
        user_id = ban["user_id"]
        get_db().execute("DELETE FROM bans WHERE user_id=?", (user_id,))
        get_db().commit()
        _reset_spam(user_id)
        log.info("Auto-unbanned user %s (ban expired)", user_id)

        # Notify in user's topic if it exists
        row = db_get_user(user_id)
        if row and row["topic_id"]:
            try:
                await ctx.bot.send_message(
                    chat_id=ADMIN_GROUP_ID,
                    message_thread_id=row["topic_id"],
                    text=f"🔓 Ban expired — user {user_id} auto-unbanned.",
                )
            except TelegramError:
                pass


# ═══════════════════════════════════════════════════════════════════════════════
# APPLICATION SETUP
# ═══════════════════════════════════════════════════════════════════════════════

async def post_init(app: Application) -> None:
    """Runs after the application is initialized."""
    # Ensure broadcast topic exists
    await _find_broadcast_topic(app.bot)
    log.info("Bot initialized. Admin group: %s, Admins: %s", ADMIN_GROUP_ID, ADMIN_IDS)


def main() -> None:
    """Entry point."""
    log.info("Starting NoPMsBot v2…")

    # Initialize DB early
    get_db()

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    # ── User DM commands ──────────────────────────────────────────────────
    dm_filter = filters.ChatType.PRIVATE

    app.add_handler(CommandHandler("start", cmd_start, filters=dm_filter))
    app.add_handler(CommandHandler("help", cmd_help))  # works in DM + admin group
    app.add_handler(CommandHandler("settings", cmd_settings, filters=dm_filter))

    # ── Admin commands (work in admin group, some also in DM) ─────────────
    admin_filter = filters.Chat(chat_id=ADMIN_GROUP_ID)

    app.add_handler(CommandHandler("stats", cmd_stats))  # works anywhere for admins
    app.add_handler(CommandHandler("ban", cmd_ban))
    app.add_handler(CommandHandler("unban", cmd_unban))
    app.add_handler(CommandHandler("banned", cmd_banned))
    app.add_handler(CommandHandler("setmsg", cmd_setmsg))
    app.add_handler(CommandHandler("forcebroadcast", cmd_forcebroadcast))
    app.add_handler(CommandHandler("topic", cmd_topic))
    app.add_handler(CommandHandler("close", cmd_close, filters=admin_filter))
    app.add_handler(CommandHandler("reopen", cmd_reopen, filters=admin_filter))
    app.add_handler(CommandHandler("note", cmd_note, filters=admin_filter))
    app.add_handler(CommandHandler("tag", cmd_tag))
    app.add_handler(CommandHandler("export", cmd_export))
    app.add_handler(CommandHandler("canned", cmd_canned))
    app.add_handler(CommandHandler("wallet", cmd_wallet))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(CommandHandler("wallets", cmd_wallets))

    # ── Callback query handlers (inline buttons) ─────────────────────────
    app.add_handler(CallbackQueryHandler(cb_wallet_add, pattern="^wallet_add$"))
    app.add_handler(CallbackQueryHandler(cb_wallet_view, pattern="^wallet_view$"))
    app.add_handler(CallbackQueryHandler(cb_wallet_remove, pattern="^wallet_remove$"))
    app.add_handler(CallbackQueryHandler(cb_settings, pattern="^settings$"))
    app.add_handler(CallbackQueryHandler(cb_toggle_broadcast, pattern="^toggle_broadcast$"))
    app.add_handler(CallbackQueryHandler(cb_help, pattern="^help$"))
    app.add_handler(CallbackQueryHandler(cb_back_start, pattern="^back_start$"))

    # ── Message handlers ──────────────────────────────────────────────────
    # Private messages from users (non-command)
    app.add_handler(MessageHandler(
        dm_filter & ~filters.COMMAND & ~filters.StatusUpdate.ALL,
        handle_private_message,
    ))

    # Admin group messages (non-command, in topics)
    app.add_handler(MessageHandler(
        admin_filter & ~filters.COMMAND & filters.IS_TOPIC_MESSAGE & ~filters.StatusUpdate.ALL,
        handle_admin_group_message,
    ))

    # ── Background jobs ───────────────────────────────────────────────────
    job_queue = app.job_queue
    if job_queue:
        job_queue.run_repeating(job_expire_bans, interval=60, first=10)
        log.info("Scheduled ban expiry job (every 60s)")

    # ── Error handler ─────────────────────────────────────────────────────
    async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        log.error("Unhandled exception: %s", context.error, exc_info=context.error)

    app.add_error_handler(error_handler)

    # ── Run ────────────────────────────────────────────────────────────────
    log.info("Polling…")
    app.run_polling(
        drop_pending_updates=True,
        allowed_updates=Update.ALL_TYPES,
    )


if __name__ == "__main__":
    main()
