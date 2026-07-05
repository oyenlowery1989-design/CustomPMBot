import logging
import sqlite3

log = logging.getLogger("nopmsbot")
SCHEMA_VERSION = 11

def _get_schema_version(db: sqlite3.Connection) -> int:
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
    current = _get_schema_version(db)
    if current >= SCHEMA_VERSION:
        return

    log.info("DB migration: current v%d → target v%d", current, SCHEMA_VERSION)

    if current < 1:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value TEXT
            );
            CREATE TABLE IF NOT EXISTS users (
                user_id       INTEGER PRIMARY KEY,
                first_name    TEXT,
                last_name     TEXT,
                username      TEXT,
                topic_id      INTEGER,
                broadcast_opt INTEGER DEFAULT 1,
                blocked       INTEGER DEFAULT 0,
                first_seen    TEXT,
                last_seen     TEXT
            );
            CREATE TABLE IF NOT EXISTS bans (
                user_id    INTEGER PRIMARY KEY,
                reason     TEXT,
                banned_at  TEXT,
                expires_at TEXT
            );
            CREATE TABLE IF NOT EXISTS messages (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id      INTEGER NOT NULL,
                direction    TEXT NOT NULL,
                content_type TEXT,
                text         TEXT,
                timestamp    TEXT
            );
            CREATE TABLE IF NOT EXISTS tags (
                user_id INTEGER NOT NULL,
                tag     TEXT NOT NULL,
                PRIMARY KEY (user_id, tag)
            );
            CREATE TABLE IF NOT EXISTS canned (
                name TEXT PRIMARY KEY,
                body TEXT NOT NULL
            );
        """)
        log.info("Migration v0→v1: Base schema created")
        current = 1

    if current < 2:
        db.executescript("""
            CREATE INDEX IF NOT EXISTS idx_messages_user ON messages(user_id);
            CREATE INDEX IF NOT EXISTS idx_messages_ts ON messages(timestamp);
            CREATE INDEX IF NOT EXISTS idx_tags_tag ON tags(tag);
            CREATE INDEX IF NOT EXISTS idx_users_topic ON users(topic_id);
        """)
        log.info("Migration v1→v2: Performance indexes added")
        current = 2

    if current < 3:
        try:
            db.execute("ALTER TABLE users ADD COLUMN blocked INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass
        current = 3

    if current < 4:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS custom_topics (
                name TEXT PRIMARY KEY,
                topic_id INTEGER NOT NULL,
                description TEXT,
                created_at TEXT
            );
            CREATE TABLE IF NOT EXISTS topic_bindings (
                bind_type TEXT NOT NULL,
                bind_key TEXT NOT NULL,
                topic_name TEXT NOT NULL,
                PRIMARY KEY (bind_type, bind_key)
            );
        """)
        current = 4

    if current < 5:
        # Placeholder for old v5 wallets (we'll replace them in v6)
        current = 5

    if current < 6:
        # THE BIG UPDATE: Multiple Wallets & Encrypted Storage
        db.executescript("""
            DROP TABLE IF EXISTS wallets;
            
            CREATE TABLE IF NOT EXISTS wallets (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id       INTEGER NOT NULL,
                address       TEXT NOT NULL,
                label         TEXT DEFAULT 'Main',
                verified      INTEGER DEFAULT 0,     -- 0=none, 1=memo, 2=secret_key
                verified_at   TEXT,
                added_at      TEXT,
                UNIQUE(user_id, address)
            );

            CREATE TABLE IF NOT EXISTS wallet_keys (
                address       TEXT PRIMARY KEY,
                encrypted_key TEXT NOT NULL,
                stored_at     TEXT
            );

            CREATE TABLE IF NOT EXISTS wallet_verifications (
                user_id       INTEGER NOT NULL,
                address       TEXT NOT NULL,
                challenge     TEXT NOT NULL,
                expires_at    TEXT NOT NULL,
                PRIMARY KEY (user_id, address)
            );
        """)
        log.info("Migration v5→v6: Multi-wallet and Encrypted Storage system added.")
        current = 6

    if current < 7:
        # relay_paused: /close pauses relay without touching the `blocked` flag,
        # which is reserved for Forbidden tracking (user blocked the bot)
        try:
            db.execute("ALTER TABLE users ADD COLUMN relay_paused INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass
        log.info("Migration v6→v7: relay_paused column added (topic close/reopen state).")
        current = 7

    if current < 8:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS scheduled_broadcasts (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                text       TEXT NOT NULL,
                run_at     TEXT NOT NULL,
                created_by INTEGER,
                created_at TEXT,
                sent       INTEGER DEFAULT 0,
                sent_at    TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_sched_due ON scheduled_broadcasts(sent, run_at);
        """)
        log.info("Migration v7→v8: scheduled_broadcasts table added.")
        current = 8

    if current < 9:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS message_map (
                topic_msg_id INTEGER PRIMARY KEY,
                user_msg_id  INTEGER NOT NULL,
                user_id      INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS auto_replies (
                keyword    TEXT PRIMARY KEY,
                response   TEXT NOT NULL,
                created_at TEXT
            );
        """)
        log.info("Migration v8→v9: message_map (reply threading) and auto_replies added.")
        current = 9

    if current < 10:
        # ISSUE-007: canned responses can carry media (file_id replay)
        for stmt in (
            "ALTER TABLE canned ADD COLUMN content_type TEXT DEFAULT 'text'",
            "ALTER TABLE canned ADD COLUMN file_id TEXT",
        ):
            try:
                db.execute(stmt)
            except sqlite3.OperationalError:
                pass
        log.info("Migration v9→v10: canned media columns added.")
        current = 10

    if current < 11:
        # SEC-FIX: wallet_keys was keyed by address only, so two users who
        # add/verify the same address could silently overwrite or delete
        # each other's encrypted secret key. Rebuild keyed by (user_id, address).
        # pending_key_verifications persists the "awaiting secret key" flow to
        # the DB so a bot restart mid-flow can't drop the user into the
        # standard relay path with a live secret key still in their message.
        #
        # The old table has no user_id, so before dropping it we recover the
        # owning user_id(s) from wallets.verified=2 (verified by secret key)
        # and carry the encrypted key forward — otherwise every previously
        # stored key would be silently deleted on upgrade with no way back.
        try:
            old_keys = db.execute("SELECT address, encrypted_key, stored_at FROM wallet_keys").fetchall()
        except sqlite3.OperationalError:
            old_keys = []  # fresh install: wallet_keys doesn't exist yet

        db.executescript("""
            DROP TABLE IF EXISTS wallet_keys;

            CREATE TABLE IF NOT EXISTS wallet_keys (
                user_id       INTEGER NOT NULL,
                address       TEXT NOT NULL,
                encrypted_key TEXT NOT NULL,
                stored_at     TEXT,
                PRIMARY KEY (user_id, address)
            );

            CREATE TABLE IF NOT EXISTS pending_key_verifications (
                user_id    INTEGER PRIMARY KEY,
                address    TEXT NOT NULL,
                expires_at TEXT NOT NULL
            );
        """)

        backfilled = 0
        for row in old_keys:
            owners = db.execute(
                "SELECT user_id FROM wallets WHERE address=? AND verified=2",
                (row["address"],),
            ).fetchall()
            if len(owners) > 1:
                log.warning(
                    "wallet_keys backfill: address %s has %d verified-by-key owners sharing "
                    "one legacy stored key — copying it to all of them; verify manually.",
                    row["address"], len(owners),
                )
            for owner in owners:
                db.execute(
                    "INSERT OR REPLACE INTO wallet_keys (user_id, address, encrypted_key, stored_at) "
                    "VALUES (?,?,?,?)",
                    (owner["user_id"], row["address"], row["encrypted_key"], row["stored_at"]),
                )
                backfilled += 1

        log.info(
            "Migration v10→v11: wallet_keys keyed by (user_id, address); pending_key_verifications "
            "added; backfilled %d of %d legacy key(s).", backfilled, len(old_keys),
        )
        current = 11

    _set_schema_version(db, current)
    db.commit()
    log.info("DB migration complete: now at v%d", current)
