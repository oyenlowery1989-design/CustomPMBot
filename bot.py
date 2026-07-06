import asyncio
import atexit
from telegram import Update, BotCommand, BotCommandScopeDefault, BotCommandScopeChat
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
from config import BOT_TOKEN, ADMIN_GROUP_ID, HEALTH_PORT, HEALTH_HOST, WALLET_ENCRYPTION_KEY, log
from database.connection import get_db, close_db
from database.bans import cleanup_expired_bans
from database.wallets import cleanup_expired_verifications
from database.migrations import _run_migrations
from services.health import start_health_server
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

async def _scheduled_broadcast_loop(app: Application) -> None:
    """Fire scheduled broadcasts when their run_at time arrives."""
    while True:
        try:
            await process_due_broadcasts(app.bot)
        except Exception as e:
            log.error("Scheduled broadcast loop failed: %s", e)
        await asyncio.sleep(SCHEDULE_INTERVAL)

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

async def post_init(app: Application) -> None:
    load_texts()
    await _find_broadcast_topic(app.bot)

    # Background services: Stellar memo-verification watcher + ban expiry.
    watcher = StellarWatcher(app.bot)
    app.bot_data["watcher"] = watcher
    app.bot_data["bg_tasks"] = [
        asyncio.create_task(watcher.start(), name="stellar-watcher"),
        asyncio.create_task(_ban_cleanup_loop(), name="ban-cleanup"),
        asyncio.create_task(_wallet_verification_cleanup_loop(), name="wallet-verification-cleanup"),
        asyncio.create_task(_scheduled_broadcast_loop(app), name="scheduled-broadcasts"),
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

    app = Application.builder().token(BOT_TOKEN).post_init(post_init).post_shutdown(post_shutdown).build()

    # User Commands
    dm_filter = filters.ChatType.PRIVATE
    app.add_handler(CommandHandler("start", cmd_start, filters=dm_filter))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("settings", cmd_settings, filters=dm_filter))
    app.add_handler(CommandHandler("wallet", cmd_wallet, filters=dm_filter))
    app.add_handler(CommandHandler("cancel", cmd_cancel, filters=dm_filter))

    # Admin Commands
    admin_filter = filters.Chat(chat_id=ADMIN_GROUP_ID)
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("topic", cmd_topic))
    app.add_handler(CommandHandler("wallets", cmd_wallets))
    app.add_handler(CommandHandler("ban", cmd_ban))
    app.add_handler(CommandHandler("unban", cmd_unban))
    app.add_handler(CommandHandler("banned", cmd_banned))
    app.add_handler(CommandHandler("setmsg", cmd_setmsg))
    app.add_handler(CommandHandler("forcebroadcast", cmd_forcebroadcast))
    app.add_handler(CommandHandler("schedule", cmd_schedule))
    app.add_handler(CommandHandler("autoreply", cmd_autoreply))
    app.add_handler(CommandHandler("users", cmd_users))
    app.add_handler(CommandHandler("search", cmd_search))
    app.add_handler(CommandHandler("analytics", cmd_analytics))
    app.add_handler(CommandHandler("manual", cmd_manual))
    app.add_handler(CommandHandler("tag", cmd_tag))
    app.add_handler(CommandHandler("export", cmd_export))
    app.add_handler(CommandHandler("canned", cmd_canned))
    app.add_handler(CommandHandler("close", cmd_close, filters=admin_filter))
    app.add_handler(CommandHandler("reopen", cmd_reopen, filters=admin_filter))
    app.add_handler(CommandHandler("note", cmd_note, filters=admin_filter))
    app.add_handler(CommandHandler("ai", cmd_ai, filters=admin_filter))

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
    app.add_handler(MessageHandler(dm_filter & ~filters.COMMAND, handle_private_message))
    app.add_handler(MessageHandler(admin_filter & ~filters.COMMAND & filters.IS_TOPIC_MESSAGE, handle_admin_group_message))

    app.add_error_handler(error_handler)

    log.info("Polling...")
    app.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
