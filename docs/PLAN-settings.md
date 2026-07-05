# NoPMsBot — Admin Settings Panel Plan

**Last updated:** 2026-02-16

> **Superseded — not implemented.** Describes a settings dashboard that was
> never built; the live settings surface is the `/setmsg` command plus the
> single broadcast toggle in `handlers/user.py`. See `docs/PLAN-v2.2.md` and
> `docs/TODO.md` for the current roadmap. Kept for historical reference. Note
> it also still references `VERIFY_WALLET_SECRET`, which was removed as
> dead config (never read anywhere in the codebase).

---

## Command: `/settings` (in admin DM or group)

Shows a full settings dashboard with inline buttons to change each setting.

---

## Settings Layout

```
⚙️ NoPMsBot Settings

━━━━━━━━━━━━━━━━━━━━
💬 General
━━━━━━━━━━━━━━━━━━━━

Welcome Message:
"👋 Welcome! Send me a message..."
[✏️ Change Welcome Message]

Broadcast Default: 🔔 ON (new users get broadcasts)
[Toggle]

━━━━━━━━━━━━━━━━━━━━
💳 Stellar / Wallets
━━━━━━━━━━━━━━━━━━━━

Verification Wallet: GBEK2...H6LCM
[✏️ Change Verification Wallet]

Max Wallets per User: 5
[◀️ 4] [5] [▶️ 6]

Verification Methods Shown to Users:
☑️ Memo (send 0.0000001 XLM)
☐ Signature (sign a challenge)
☑️ Secret Key (admin-only advanced)
[Toggle Memo] [Toggle Signature] [Toggle Secret Key]

Encryption Key: ✅ Configured
(Cannot be changed via bot — edit /etc/nopmsbot-v2.env)

━━━━━━━━━━━━━━━━━━━━
🛡 Anti-Spam
━━━━━━━━━━━━━━━━━━━━

Spam Threshold: 5 messages / 10 seconds
[◀️ Lower] [▶️ Higher]

Warnings Before Ban: 2
[◀️ 1] [2] [▶️ 3]

Spam Ban Duration: 24 hours
[1h] [6h] [24h] [48h] [7d]

━━━━━━━━━━━━━━━━━━━━
👥 Admin
━━━━━━━━━━━━━━━━━━━━

Owner: <owner-id>
Admins: <owner-id>
[➕ Add Admin] [➖ Remove Admin]

Admin Group: <admin-group-id>
(Cannot be changed via bot — edit /etc/nopmsbot-v2.env)

━━━━━━━━━━━━━━━━━━━━
📊 Database
━━━━━━━━━━━━━━━━━━━━

Schema Version: v6
DB Size: 80 KB
Total Users: 2
Total Wallets: 0
Total Messages: 11

[📋 Export Full DB Backup]
```

---

## All Configurable Settings

### Via Bot (stored in `settings` table)

| Setting | Key | Default | Type | Description |
|---|---|---|---|---|
| Welcome Message | `welcome_message` | "👋 Welcome!..." | text | Message shown on /start |
| Broadcast Default | `broadcast_default` | `on` | on/off | New users get broadcasts? |
| Max Wallets | `max_wallets` | `5` | 1-20 | Max wallets per user |
| Verify Methods | `verify_methods` | `memo` | memo/signature/key/all | Which verification options users see |
| Verification Wallet | `verify_wallet_public` | from env | address | Public address for memo verification |
| Spam Threshold | `spam_msg_count` | `5` | 1-20 | Messages before spam warning |
| Spam Window | `spam_window_sec` | `10` | 5-60 | Time window for spam detection |
| Spam Warnings | `spam_max_warnings` | `2` | 1-5 | Warnings before auto-ban |
| Spam Ban Duration | `spam_ban_hours` | `24` | 1-168 | Auto-ban duration in hours |

### Via Environment Only (requires restart)

| Setting | Env Var | Description |
|---|---|---|
| Bot Token | `BOT_TOKEN` | Telegram bot token |
| Owner ID | `OWNER_ID` | Primary owner Telegram ID |
| Admin IDs | `ADMIN_IDS` | Comma-separated admin IDs |
| Admin Group ID | `ADMIN_GROUP_ID` | Forum group for topics |
| Verification Secret | `VERIFY_WALLET_SECRET` | Secret key for verification wallet |
| Encryption Key | `WALLET_ENCRYPTION_KEY` | AES key for stored secret keys |
| DB Path | `DB_PATH` | SQLite database location |
| Max Concurrent | `MAX_CONCURRENT` | Concurrent broadcast sends |

---

## Changing Verification Wallet

```
Admin taps [✏️ Change Verification Wallet]

Bot: "Current verification wallet:"
Bot: "GBEK2OCX4JL7CKXW3L6EAK6R3PA7OQFOIUWY25IZWD5RHMTW5BLH6LCM"
Bot: ""
Bot: "Send the new public address (starts with G, 56 chars)"
Bot: "Or /cancel"

Admin sends: GNEWADDRESSHERE...

Bot: "✅ Verification wallet updated."
Bot: "⚠️ Make sure to also update VERIFY_WALLET_SECRET in"
Bot: "/etc/nopmsbot-v2.env and restart the bot."
Bot: ""
Bot: "Pending verifications using the old address will fail."
```

---

## Quick Commands (alternative to button UI)

For admins who prefer typing:

```
/settings                     — show full settings dashboard
/settings welcome <text>      — set welcome message
/settings broadcast on|off    — set broadcast default
/settings maxwallets <n>      — set max wallets
/settings verify memo|sig|key|all — set verification methods
/settings spam <count> <sec>  — set spam threshold
/settings spamban <hours>     — set spam ban duration
/settings spamwarn <count>    — set spam warnings
```

---

## Implementation Notes

- All settings stored in existing `settings` table (key-value)
- Bot reads settings on startup + caches in memory
- Changes take effect immediately (no restart needed)
- Environment-only settings clearly marked — cannot be changed via bot
- Settings panel works in DM and admin group
- Only OWNER can change settings (not regular admins)
