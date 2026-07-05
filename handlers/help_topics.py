"""Single source of truth for command help. /help renders the overview from
this dict; /help <command> shows the detail. Admin entries are hidden from
non-admins."""

HELP_TOPICS = {
    # --- User commands ---
    "start": dict(admin=False, cat="User", summary="Main menu & buttons",
                  detail="/start — open the main menu with wallet, settings and help buttons."),
    "help": dict(admin=False, cat="User", summary="This help",
                 detail="/help — command overview.\n/help <command> — detailed usage, e.g. /help schedule"),
    "settings": dict(admin=False, cat="User", summary="Broadcast preferences",
                     detail="/settings — toggle broadcast subscription with buttons.\n"
                            "/settings on|off — set it directly."),
    "wallet": dict(admin=False, cat="User", summary="Manage Stellar wallets",
                   detail="/wallet — list your wallets (max 5), add, remove, verify.\n"
                          "Verify by sending a memo payment or by proving your secret key "
                          "(checked locally, stored encrypted, your message is deleted immediately)."),
    "cancel": dict(admin=False, cat="User", summary="Abort wallet input",
                   detail="/cancel — abort a pending wallet address/label/key input."),

    # --- Moderation ---
    "ban": dict(admin=True, cat="Moderation", summary="Ban a user",
                detail="/ban <id> [reason] — ban by user id.\n"
                       "/ban [reason] — inside a user topic bans that user.\n"
                       "Banned users get an Appeal button; appeals land in their topic."),
    "unban": dict(admin=True, cat="Moderation", summary="Lift a ban",
                  detail="/unban <id> — or just /unban inside the user's topic."),
    "banned": dict(admin=True, cat="Moderation", summary="List active bans",
                   detail="/banned — every active ban with reason.\n"
                          "Spam auto-bans expire on their own (checked every 5 min)."),
    "close": dict(admin=True, cat="Moderation", summary="Archive a topic",
                  detail="/close — inside a user topic: archives it and pauses relay. "
                         "The user gets a 'conversation closed' notice if they write."),
    "reopen": dict(admin=True, cat="Moderation", summary="Reopen a topic",
                   detail="/reopen — inside a closed user topic: reopens and resumes relay."),
    "note": dict(admin=True, cat="Moderation", summary="Pin an admin note",
                 detail="/note <text> — posts and pins a note inside the current user topic."),

    # --- Broadcasts ---
    "broadcast": dict(admin=True, cat="Broadcasts", summary="How broadcasting works",
                      detail="Post any message in the 📢 Broadcast topic → goes to all subscribers.\n"
                             "A preview with Send/Cancel buttons appears first "
                             "(disable: /setmsg broadcast_confirm off).\n"
                             "First line '@VIP' alone targets only that tag's subscribers."),
    "schedule": dict(admin=True, cat="Broadcasts", summary="Delayed broadcasts",
                     detail="/schedule <duration> <text> — e.g. /schedule 2h Big news!\n"
                            "Durations: 10m, 2h, 1d, 1w. '@TAG' first line targets a tag.\n"
                            "/schedule list — pending + recently sent.\n"
                            "/schedule cancel <id> — cancel a pending one."),
    "forcebroadcast": dict(admin=True, cat="Broadcasts", summary="Override all opt-outs",
                           detail="/forcebroadcast on|off — set broadcast_opt for ALL users. "
                                  "Use sparingly; overrides user choices."),

    # --- Organization ---
    "stats": dict(admin=True, cat="Organization", summary="Bot statistics",
                  detail="/stats — totals: users, active, banned, subscriptions, messages in/out."),
    "analytics": dict(admin=True, cat="Organization", summary="Activity report",
                      detail="/analytics [days] — messages/day, new users/day, top users, "
                             "busiest hours. Default 7 days, max 90."),
    "users": dict(admin=True, cat="Organization", summary="List users",
                  detail="/users — all users, newest activity first (max 50).\n"
                         "/users active|blocked|banned|paused — filtered.\n"
                         "/users tag <TAG> — by tag."),
    "search": dict(admin=True, cat="Organization", summary="Search message logs",
                   detail="/search <query> — case-insensitive search over all logged messages.\n"
                          "Inside a user topic it searches only that user's conversation."),
    "tag": dict(admin=True, cat="Organization", summary="Label users",
                detail="/tag <label> — inside a topic tags that user.\n"
                       "/tag <id> <label> — tag by id.\n"
                       "/tag remove <id> <label> — remove.\n"
                       "Tags target broadcasts: '@VIP' first line."),
    "export": dict(admin=True, cat="Organization", summary="Export a conversation",
                   detail="/export <id> — or /export inside a topic. "
                          "Long logs arrive as a .txt file."),
    "canned": dict(admin=True, cat="Organization", summary="Saved responses",
                   detail="/canned add <name> <text> — save a text response.\n"
                          "Reply to a photo/video/file with /canned add <name> [caption] — save media.\n"
                          "/canned <name> — send it to the user (inside their topic).\n"
                          "/canned list, /canned del <name>."),
    "autoreply": dict(admin=True, cat="Organization", summary="Keyword auto-replies",
                      detail="/autoreply add <keyword> <response> — auto-answer when a user's "
                             "message contains the keyword (whole word, case-insensitive).\n"
                             "The message still reaches admins, with a 🤖 note.\n"
                             "/autoreply list, /autoreply del <keyword>."),
    "topic": dict(admin=True, cat="Organization", summary="Custom topics",
                  detail="/topic create <name> — new forum topic (random colored icon).\n"
                         "/topic list — all custom topics.\n"
                         "/topic bind <event|command> <key> <topic name> — route an event "
                         "(e.g. new_user) or command's output into a custom topic.\n"
                         "/topic unbind <event|command> <key> — remove a binding.\n"
                         "/topic bindings — list all active bindings."),
    "wallets": dict(admin=True, cat="Organization", summary="All registered wallets",
                    detail="/wallets — every user wallet with label and owner id."),

    # --- Setup ---
    "setmsg": dict(admin=True, cat="Setup", summary="Change bot settings",
                   detail="/setmsg <key> <value> — set any setting. Useful keys:\n"
                          "• welcome_message — the /start greeting\n"
                          "• broadcast_confirm on|off — preview before broadcasting (default on)"),
    "manual": dict(admin=True, cat="Setup", summary="Full manual (file + Instant View)",
                   detail="/manual — sends the complete manual as a document, with an "
                          "⚡ Instant View button once published.\n"
                          "/manual publish — publish/update the Telegraph (Instant View) "
                          "version; the URL stays stable across updates."),
    "setup": dict(admin=True, cat="Setup", summary="Environment configuration",
                  detail="Env vars (see env.example):\n"
                         "• BOT_TOKEN, OWNER_ID, ADMIN_IDS, ADMIN_GROUP_ID — required\n"
                         "• DB_PATH — SQLite location\n"
                         "• VERIFY_WALLET_PUBLIC/SECRET — Stellar wallet for memo verification\n"
                         "• WALLET_ENCRYPTION_KEY — Fernet key for stored secret keys\n"
                         "• HEALTH_PORT — optional HTTP health endpoint\n"
                         "• MAX_CONCURRENT — broadcast parallelism (default 15)\n"
                         "The admin group must be a forum with the bot as admin + Manage Topics."),
}

CATEGORY_ORDER = ["User", "Moderation", "Broadcasts", "Organization", "Setup"]

def admin_overview() -> str:
    """Grouped one-liner list of every admin topic."""
    lines = []
    for cat in CATEGORY_ORDER:
        if cat == "User":
            continue
        lines.append(f"\n<b>{cat}</b>")
        for name, t in HELP_TOPICS.items():
            if t["admin"] and t["cat"] == cat:
                lines.append(f"/{name} — {t['summary']}")
    lines.append("\n💡 /help <command> for details, e.g. /help schedule")
    return "\n".join(lines)
