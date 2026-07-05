# NoPMsBot — Architecture Plan: Modular Split

**Last updated:** 2026-02-16

---

## Problem

`bot.py` is currently ~2500 lines in a single file. With planned features (Stellar wallet, sales, tiers, invoices, settings panel), it would grow to 6000-8000+ lines.

Risks of monolith:
- One bug in Stellar code can crash message relay
- Hard to find anything
- Can't test features independently
- Two people can't work on different features at once
- One syntax error = entire bot down

---

## Solution: Modular Architecture

Split into focused modules. Each file handles ONE responsibility.

```
/opt/nopmsbot-v2/
│
├── bot.py                  # Entry point — startup, handler registration ONLY (~200 lines)
├── config.py               # All settings, env loading, constants (~100 lines)
├── database.py             # DB connection, migrations, all SQL helpers (~500 lines)
│
├── handlers/
│   ├── __init__.py
│   ├── user.py             # /start, /help, /settings, /cancel — user-facing commands
│   ├── admin.py            # /stats, /ban, /unban, /banned, /setmsg, /forcebroadcast
│   ├── relay.py            # Message forwarding: user→topic, topic→user, media relay
│   ├── broadcast.py        # Broadcast topic detection, sending, tagged broadcasts
│   ├── topics.py           # /close, /reopen, /note, /topic create/delete/bind
│   ├── tags.py             # /tag, tag management
│   ├── canned.py           # /canned add/list/del/send
│   ├── export.py           # /export conversation dump
│   ├── wallet.py           # /wallet, add/remove/list wallets, inline buttons
│   ├── verification.py     # Memo verification, signature verification, secret key
│   ├── stellar.py          # /balance, /assets, /transactions, Horizon API queries
│   ├── sales.py            # Asset store, buy flow, order management, LOBSTR links
│   ├── tiers.py            # Tier system, tier checks, tier commands
│   ├── invoices.py         # Invoice creation, payment detection, auto-confirm
│   └── settings_panel.py   # Admin /settings dashboard, all config commands
│
├── services/
│   ├── __init__.py
│   ├── horizon.py          # Stellar Horizon API client (balance, payments, trustlines)
│   ├── encryption.py       # AES-256 encrypt/decrypt for secret keys
│   ├── spam.py             # Spam detection, rate limiting
│   └── watcher.py          # Background payment watcher (deposit detection, order fulfillment)
│
├── models/
│   ├── __init__.py
│   └── types.py            # Shared data types, enums (VerifyMethod, Tier, OrderStatus)
│
├── utils/
│   ├── __init__.py
│   ├── formatting.py       # HTML formatting helpers, address truncation
│   └── keyboards.py        # Reusable InlineKeyboardMarkup builders
│
├── state.db                # SQLite database
├── requirements.txt
├── env.example
└── README.md
```

---

## What Each File Does

### Core (3 files)

| File | Lines | Purpose |
|---|---|---|
| `bot.py` | ~200 | Entry point. Loads config, registers handlers, starts polling. Nothing else. |
| `config.py` | ~100 | Reads env vars, defines constants (OWNER_ID, ADMIN_IDS, etc.), loads settings from DB |
| `database.py` | ~500 | DB connection, `get_db()`, all migrations (v1→v6+), every SQL helper function |

### Handlers (15 files) — one per feature group

| File | Handles | Key Functions |
|---|---|---|
| `user.py` | /start, /help, /settings, /cancel | User-facing DM commands |
| `admin.py` | /stats, /ban, /unban, /banned, /setmsg, /forcebroadcast | Admin management |
| `relay.py` | Private messages → topic, topic replies → user | The core PM relay |
| `broadcast.py` | Broadcast topic → all users, tagged broadcasts | Broadcasting engine |
| `topics.py` | /close, /reopen, /note, /topic CRUD | Forum topic management |
| `tags.py` | /tag add/remove, tag queries | User tagging |
| `canned.py` | /canned add/list/del/send | Canned responses |
| `export.py` | /export | Conversation export |
| `wallet.py` | /wallet, add/remove, inline buttons, wallet menu | Wallet management UI |
| `verification.py` | Memo flow, signature flow, secret key flow | All verification logic |
| `stellar.py` | /balance, /assets, /transactions | Read-only Stellar queries |
| `sales.py` | /buy, /asset, order flow, LOBSTR links | Asset store |
| `tiers.py` | /tier, tier checks, tier display | Tier system |
| `invoices.py` | /invoice, payment matching | Invoice system |
| `settings_panel.py` | /settings (admin), all bot config | Settings dashboard |

### Services (4 files) — background logic, no Telegram dependency

| File | Purpose |
|---|---|
| `horizon.py` | Talks to Stellar Horizon API. Get balance, check payments, submit transactions. |
| `encryption.py` | Encrypt/decrypt secret keys. Fernet wrapper. |
| `spam.py` | Rate limiter, warning counter, auto-ban logic |
| `watcher.py` | Background task: polls Horizon for payments. Handles order fulfillment, deposit alerts, verification confirmation. |

### Utils (2 files)

| File | Purpose |
|---|---|
| `formatting.py` | `truncate_address("GABCD...WXYZ")`, HTML escaping, message builders |
| `keyboards.py` | Reusable button layouts (wallet menu, settings menu, etc.) |

---

## How They Connect

```
bot.py (entry point)
  │
  ├── config.py ← loads env + settings
  ├── database.py ← runs migrations, provides db helpers
  │
  ├── Registers handlers from:
  │   ├── handlers/user.py
  │   ├── handlers/admin.py
  │   ├── handlers/relay.py        ← uses database.py
  │   ├── handlers/broadcast.py    ← uses database.py
  │   ├── handlers/wallet.py       ← uses services/horizon.py
  │   ├── handlers/verification.py ← uses services/encryption.py
  │   ├── handlers/sales.py        ← uses services/horizon.py
  │   ├── handlers/tiers.py        ← uses services/horizon.py
  │   └── ...
  │
  └── Starts background tasks:
      └── services/watcher.py ← polls Horizon, fulfills orders
```

**Key rule:** Handlers import from database, services, utils. Handlers NEVER import from each other. If two handlers need the same logic, it goes in a service or util.

---

## Migration Plan (v2.1 → v2.2)

**Step 1:** Split current bot.py into modules (no new features, just reorganize)
- Verify everything still works identically
- Run side by side, test each command
- This is a refactor, not a rewrite

**Step 2:** Add new features in their own handler files
- Each feature is isolated
- Breaking `sales.py` can't affect `relay.py`
- Can test features independently

**Step 3:** For each new version:
```
/root/Desktop/NoPMsBot/
├── v2.1-current/         ← single file (current)
│   └── bot.py (2500 lines)
│
├── v2.2-current/         ← modular (next)
│   ├── bot.py (200 lines)
│   ├── config.py
│   ├── database.py
│   ├── handlers/
│   ├── services/
│   └── utils/
│
└── backups/
```

---

## Benefits

| Before (monolith) | After (modular) |
|---|---|
| 1 file, 2500+ lines | 20+ files, ~200 lines each |
| One bug = everything breaks | Bug in tiers ≠ broken relay |
| Hard to find code | Open the right file |
| Can't test independently | Test each module alone |
| Scary to change anything | Change confidently |
| One person at a time | Multiple features in parallel |

---

## What Stays the Same

- Still SQLite (one database file)
- Still python-telegram-bot framework
- Still systemd service
- Still same env file
- Database migrations still automatic
- All existing features preserved exactly
