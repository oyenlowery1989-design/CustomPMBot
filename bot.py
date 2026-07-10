import asyncio
import atexit
from telegram import Update, BotCommand, BotCommandScopeDefault, BotCommandScopeChat
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
from config import BOT_TOKEN, ADMIN_GROUP_ID, HEALTH_PORT, HEALTH_HOST, WALLET_ENCRYPTION_KEY, DATA_RETENTION_DAYS, log
from database.connection import get_db, close_db
from database.bans import cleanup_expired_bans
from database.wallets import cleanup_expired_verifications
from database.messages import prune_old_messages, prune_old_message_map
from database.ai_drafts import prune_old_drafts
from database.migrations import _run_migrations
from services.spam import prune_stale_spam_state
from services.health import start_health_server, mark_heartbeat
from services.watcher import StellarWatcher
from handlers.user import cmd_start, cmd_help, cmd_settings
from handlers.admin import cmd_stats, cmd_ban, cmd_unban, cmd_banned, cmd_setmsg, cmd_forcebroadcast, cmd_users, cmd_search, cmd_analytics, cmd_manual
from handlers.relay import handle_private_message, handle_admin_group_message
from handlers.broadcast import (
    _find_broadcast_topic, cmd_schedule, process_due_broadcasts,
    cb_broadcast_confirm, cb_broadcast_cancel,
)
from handlers.topics import cmd_topic, cmd_close, cmd_reopen, cmd_note
from handlers.ai_reply import cmd_ai, cb_ai_draft, cb_ai_send, cb_ai_edit, cb_ai_dismiss
from handlers.autoreply import cmd_autoreply
from handlers.tags import cmd_tag
from handlers.export import cmd_export
from handlers.canned import cmd_canned
from handlers.wallet import (
    cmd_wallet, cmd_wallets, cmd_cancel,
    cb_wallet_add, cb_wallet_view, cb_wallet_remove, cb_wallet_verify,
    cb_verify_memo, cb_verify_key,
    cb_settings, cb_toggle_broadcast, cb_help, cb_back_start, cb_appeal
)
from utils.strings import load_texts

BAN_CLEANUP_INTERVAL = 300  # seconds
SCHEDULE_INTERVAL = 30  # seconds
HEARTBEAT_INTERVAL = 15  # seconds
SPAM_STATE_CLEANUP_INTERVAL = 300  # seconds
DATA_RETENTION_CLEANUP_INTERVAL = 86400  # seconds (once a day)
BROADCAST_DRAIN_TIMEOUT = 20  # seconds — bounded grace period on shutdown for
                              # an in-flight scheduled broadcast to finish
                              # sending before its task is force-cancelled.

# Excludes edited_message updates. CommandHandler/MessageHandler dispatch on
# effective_message (which includes edits), but every handler body reads
# update.message directly — that's None for an edited command/message, so
# without this every handler would crash with AttributeError on an edit.
NOT_EDITED = filters.UpdateType.MESSAGE

async def _scheduled_broadcast_loop(app: Application, idle: asyncio.Event) -> None:
    """Fire scheduled broadcasts when their run_at time arrives. `idle` is
    clear exactly while a send is in flight, so post_shutdown can wait for it
    instead of cancelling mid-broadcast (M9, docs/AUDIT-2026-07-10.md)."""
    while True:
        idle.clear()
        try:
            await process_due_broadcasts(app.bot)
        except Exception as e:
            log.error("Scheduled broadcast loop failed: %s", e)
        finally:
            idle.set()
        await asyncio.sleep(SCHEDULE_INTERVAL)

async def _heartbeat_loop() -> None:
    """Marks the event loop as alive for the health endpoint, independent of
    Telegram traffic (an idle bot with no messages is not the same as a
    frozen one) — a stuck/blocked event loop stops this loop from ever
    running, which is exactly the failure the plain DB-only health check
    couldn't detect (M11, docs/AUDIT-2026-07-10.md)."""
    while True:
        mark_heartbeat()
        await asyncio.sleep(HEARTBEAT_INTERVAL)

async def _ban_cleanup_loop() -> None:
    """Periodically purge expired bans so auto-unban doesn't wait for the
    user's next message (db_is_banned only cleans lazily)."""
    while True:
        try:
            removed = cleanup_expired_bans()
            if removed:
                log.info("Auto-unban: removed %d expired ban(s)", removed)
        except Exception as e:
            log.error("Expired-ban cleanup failed: %s", e)
        await asyncio.sleep(BAN_CLEANUP_INTERVAL)

async def _wallet_verification_cleanup_loop() -> None:
    """Periodically purge expired wallet_verifications rows — they otherwise
    accumulate forever since nothing else deletes them."""
    while True:
        try:
            removed = cleanup_expired_verifications()
            if removed:
                log.info("Removed %d expired wallet verification(s)", removed)
        except Exception as e:
            log.error("Expired-verification cleanup failed: %s", e)
        await asyncio.sleep(BAN_CLEANUP_INTERVAL)

async def _spam_state_cleanup_loop() -> None:
    """Periodically drop in-memory spam-tracking entries for users with no
    recent activity — otherwise every distinct user who has ever messaged
    the bot keeps an entry forever, for the life of the process (L4,
    docs/AUDIT-2026-07-10.md)."""
    while True:
        try:
            removed = prune_stale_spam_state()
            if removed:
                log.info("Pruned %d stale spam-tracking entr(ies)", removed)
        except Exception as e:
            log.error("Spam-state cleanup failed: %s", e)
        await asyncio.sleep(SPAM_STATE_CLEANUP_INTERVAL)

async def _data_retention_loop() -> None:
    """Periodically prune old messages, reply-thread mappings, and
    terminal-status AI drafts — these tables otherwise grow forever (L5/L6,
    docs/AUDIT-2026-07-10.md). DATA_RETENTION_DAYS <= 0 disables this."""
    if DATA_RETENTION_DAYS <= 0:
        return
    while True:
        try:
            msgs = prune_old_messages(DATA_RETENTION_DAYS)
            mapped = prune_old_message_map(DATA_RETENTION_DAYS)
            drafts = prune_old_drafts(DATA_RETENTION_DAYS)
            if msgs or mapped or drafts:
                log.info(
                    "Data retention: pruned %d message(s), %d message_map row(s), %d ai_draft(s) older than %d day(s)",
                    msgs, mapped, drafts, DATA_RETENTION_DAYS,
                )
        except Exception as e:
            log.error("Data retention cleanup failed: %s", e)
        await asyncio.sleep(DATA_RETENTION_CLEANUP_INTERVAL)

async def post_init(app: Application) -> None:
    load_texts()
    await _find_broadcast_topic(app.bot)

    # Background services: Stellar memo-verification watcher + ban expiry.
    watcher = StellarWatcher(app.bot)
    app.bot_data["watcher"] = watcher
    broadcast_idle = asyncio.Event()
    broadcast_idle.set()
    app.bot_data["broadcast_idle"] = broadcast_idle
    app.bot_data["bg_tasks"] = [
        asyncio.create_task(watcher.start(), name="stellar-watcher"),
        asyncio.create_task(_heartbeat_loop(), name="heartbeat"),
        asyncio.create_task(_ban_cleanup_loop(), name="ban-cleanup"),
        asyncio.create_task(_wallet_verification_cleanup_loop(), name="wallet-verification-cleanup"),
        asyncio.create_task(_scheduled_broadcast_loop(app, broadcast_idle), name="scheduled-broadcasts"),
        asyncio.create_task(_spam_state_cleanup_loop(), name="spam-state-cleanup"),
        asyncio.create_task(_data_retention_loop(), name="data-retention"),
    ]

    if HEALTH_PORT:
        try:
            app.bot_data["health_server"] = await start_health_server(HEALTH_PORT, HEALTH_HOST)
        except OSError as e:
            log.error("Could not start health endpoint on %s:%s: %s", HEALTH_HOST, HEALTH_PORT, e)
    
    # 1. Set Default Commands (for everyone)
    user_cmds = [
        BotCommand("start", "Main menu & buttons"),
        BotCommand("help", "How to use the bot"),
        BotCommand("settings", "Broadcast preferences"),
        BotCommand("wallet", "Manage Stellar wallet"),
    ]
    await app.bot.set_my_commands(user_cmds, scope=BotCommandScopeDefault())

    # 2. Set Admin Commands (only for the Admin Group)
    admin_cmds = user_cmds + [
        BotCommand("stats", "Bot statistics"),
        BotCommand("topic", "Manage custom topics"),
        BotCommand("ban", "Ban a user"),
        BotCommand("unban", "Unban a user"),
        BotCommand("banned", "List banned users"),
        BotCommand("export", "Export conversation log"),
        BotCommand("canned", "Canned responses"),
        BotCommand("forcebroadcast", "Global broadcast override"),
        BotCommand("schedule", "Schedule a broadcast"),
        BotCommand("autoreply", "Keyword auto-replies"),
        BotCommand("users", "List users with filters"),
        BotCommand("search", "Search message logs"),
        BotCommand("analytics", "Activity report"),
        BotCommand("manual", "Full manual as a file"),
        BotCommand("wallets", "List all user wallets"),
        BotCommand("close", "Archive topic"),
        BotCommand("reopen", "Reopen topic"),
        BotCommand("note", "Pin a note"),
        BotCommand("ai", "AI-drafted reply settings"),
    ]
    try:
        await app.bot.set_my_commands(admin_cmds, scope=BotCommandScopeChat(chat_id=ADMIN_GROUP_ID))
    except Exception as e:
        log.warning("Could not set admin command scope: %s", e)
    
    log.info("Bot initialized. Command scopes set. Admin group: %s", ADMIN_GROUP_ID)

async def post_shutdown(app: Application) -> None:
    watcher = app.bot_data.get("watcher")
    if watcher:
        watcher.stop()

    broadcast_idle = app.bot_data.get("broadcast_idle")
    if broadcast_idle is not None and not broadcast_idle.is_set():
        # A scheduled broadcast is mid-send. It's already marked "sent" in the
        # DB (at-most-once) before delivery starts, so cancelling now means
        # some recipients never get it and it's never retried — wait, bounded,
        # instead (M9, docs/AUDIT-2026-07-10.md).
        log.info("Waiting up to %ss for in-flight scheduled broadcast to finish...", BROADCAST_DRAIN_TIMEOUT)
        try:
            await asyncio.wait_for(broadcast_idle.wait(), timeout=BROADCAST_DRAIN_TIMEOUT)
        except asyncio.TimeoutError:
            log.warning("Broadcast drain timed out after %ss — cancelling anyway.", BROADCAST_DRAIN_TIMEOUT)

    tasks = app.bot_data.get("bg_tasks", [])
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    health = app.bot_data.get("health_server")
    if health:
        health.close()
        await health.wait_closed()

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.error("Unhandled exception: %s", context.error, exc_info=context.error)

def build_application() -> Application:
    """Construct the Application and register every handler. Split out from
    main() so tests can inspect registered handlers/filters without touching
    run_polling()."""
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).post_shutdown(post_shutdown).build()

    # User Commands
    dm_filter = filters.ChatType.PRIVATE
    app.add_handler(CommandHandler("start", cmd_start, filters=dm_filter & NOT_EDITED))
    app.add_handler(CommandHandler("help", cmd_help, filters=NOT_EDITED))
    app.add_handler(CommandHandler("settings", cmd_settings, filters=dm_filter & NOT_EDITED))
    app.add_handler(CommandHandler("wallet", cmd_wallet, filters=dm_filter & NOT_EDITED))
    app.add_handler(CommandHandler("cancel", cmd_cancel, filters=dm_filter & NOT_EDITED))

    # Admin Commands
    admin_filter = filters.Chat(chat_id=ADMIN_GROUP_ID)
    app.add_handler(CommandHandler("stats", cmd_stats, filters=NOT_EDITED))
    app.add_handler(CommandHandler("topic", cmd_topic, filters=NOT_EDITED))
    app.add_handler(CommandHandler("wallets", cmd_wallets, filters=NOT_EDITED))
    app.add_handler(CommandHandler("ban", cmd_ban, filters=NOT_EDITED))
    app.add_handler(CommandHandler("unban", cmd_unban, filters=NOT_EDITED))
    app.add_handler(CommandHandler("banned", cmd_banned, filters=NOT_EDITED))
    app.add_handler(CommandHandler("setmsg", cmd_setmsg, filters=NOT_EDITED))
    app.add_handler(CommandHandler("forcebroadcast", cmd_forcebroadcast, filters=NOT_EDITED))
    app.add_handler(CommandHandler("schedule", cmd_schedule, filters=NOT_EDITED))
    app.add_handler(CommandHandler("autoreply", cmd_autoreply, filters=NOT_EDITED))
    app.add_handler(CommandHandler("users", cmd_users, filters=NOT_EDITED))
    app.add_handler(CommandHandler("search", cmd_search, filters=NOT_EDITED))
    app.add_handler(CommandHandler("analytics", cmd_analytics, filters=NOT_EDITED))
    app.add_handler(CommandHandler("manual", cmd_manual, filters=NOT_EDITED))
    app.add_handler(CommandHandler("tag", cmd_tag, filters=NOT_EDITED))
    app.add_handler(CommandHandler("export", cmd_export, filters=NOT_EDITED))
    app.add_handler(CommandHandler("canned", cmd_canned, filters=NOT_EDITED))
    app.add_handler(CommandHandler("close", cmd_close, filters=admin_filter & NOT_EDITED))
    app.add_handler(CommandHandler("reopen", cmd_reopen, filters=admin_filter & NOT_EDITED))
    app.add_handler(CommandHandler("note", cmd_note, filters=admin_filter & NOT_EDITED))
    app.add_handler(CommandHandler("ai", cmd_ai, filters=admin_filter & NOT_EDITED))

    # Callbacks
    app.add_handler(CallbackQueryHandler(cb_wallet_add, pattern="^wallet_add$"))
    app.add_handler(CallbackQueryHandler(cb_wallet_view, pattern="^wallet_view$"))
    app.add_handler(CallbackQueryHandler(cb_wallet_remove, pattern="^w_r_"))
    app.add_handler(CallbackQueryHandler(cb_wallet_verify, pattern="^w_v_"))
    app.add_handler(CallbackQueryHandler(cb_verify_memo, pattern="^v_m_"))
    app.add_handler(CallbackQueryHandler(cb_verify_key, pattern="^v_k_"))
    app.add_handler(CallbackQueryHandler(cb_settings, pattern="^settings$"))
    app.add_handler(CallbackQueryHandler(cb_toggle_broadcast, pattern="^toggle_broadcast$"))
    app.add_handler(CallbackQueryHandler(cb_help, pattern="^help$"))
    app.add_handler(CallbackQueryHandler(cb_back_start, pattern="^back_start$"))
    app.add_handler(CallbackQueryHandler(cb_appeal, pattern="^appeal_"))
    app.add_handler(CallbackQueryHandler(cb_broadcast_confirm, pattern="^bc_go_"))
    app.add_handler(CallbackQueryHandler(cb_broadcast_cancel, pattern="^bc_no_"))
    app.add_handler(CallbackQueryHandler(cb_ai_draft, pattern="^ai_draft_"))
    app.add_handler(CallbackQueryHandler(cb_ai_send, pattern="^ad_s_"))
    app.add_handler(CallbackQueryHandler(cb_ai_edit, pattern="^ad_e_"))
    app.add_handler(CallbackQueryHandler(cb_ai_dismiss, pattern="^ad_d_"))

    # Relay Handlers
    app.add_handler(MessageHandler(dm_filter & ~filters.COMMAND & NOT_EDITED, handle_private_message))
    app.add_handler(MessageHandler(admin_filter & ~filters.COMMAND & filters.IS_TOPIC_MESSAGE & NOT_EDITED, handle_admin_group_message))

    app.add_error_handler(error_handler)
    return app

def main() -> None:
    log.info("Starting CustomPMBot (v2.3 Next Gen)...")

    # Fail fast on a malformed WALLET_ENCRYPTION_KEY — otherwise the error
    # only surfaces the first time a user tries to verify a wallet by key.
    if WALLET_ENCRYPTION_KEY:
        from services.encryption import get_cipher
        get_cipher()

    # Init DB
    db = get_db()
    _run_migrations(db)
    atexit.register(close_db)

    app = build_application()

    log.info("Polling...")
    app.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
