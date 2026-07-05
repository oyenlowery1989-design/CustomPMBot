# NoPMsBot — Asset Sales & Tier System Plan

**Last updated:** 2026-02-16

> **Superseded — not implemented.** Scope was cut down; see `docs/PLAN-v2.2.md`
> and `docs/TODO.md` for the current roadmap. Kept for historical reference.

---

## Overview

The bot becomes a **storefront** for selling custom Stellar assets, with a tier system based on holdings, and two purchase methods.

---

## 🛒 Asset Sales System

### Two Ways to Buy

#### Option 1: Buy via LOBSTR (External Link)
```
User taps [🛒 Buy BNVG]

Bot: "Buy BNVG on LOBSTR:"
Bot: "https://lobstr.co/trade/BNVG:ISSUER..."
Bot: ""
Bot: "Current price: ~0.05 XLM per BNVG"
```
- User buys on LOBSTR DEX
- No bot involvement in the transaction
- Bot detects new balance on next check
- Simple, trustless

#### Option 2: Buy Direct Through Bot (+10% Bonus)
```
User taps [⚡ Buy Direct (+10% bonus)]

Bot: "⚡ Direct Purchase"
Bot: ""
Bot: "Price: 1 BNVG = 0.05 XLM"
Bot: "🎁 BONUS: +10% extra tokens!"
Bot: ""
Bot: "How many BNVG do you want?"
Bot: "[100] [500] [1000] [5000] [Custom]"

User taps [1000]:

Bot: "🧾 Order Summary:"
Bot: ""
Bot: "You buy:    1,000 BNVG"
Bot: "Bonus:      +100 BNVG (10%)"
Bot: "Total:      1,100 BNVG"
Bot: "Cost:       50 XLM"
Bot: ""
Bot: "Send exactly 50 XLM to:"
Bot: "GBOTWALLETADDRESS..."
Bot: "Memo (TEXT): BUY-847291"
Bot: ""
Bot: "⏰ This order expires in 30 minutes."
Bot: "[❌ Cancel Order]"

--- 30 seconds later ---

Bot: "✅ Payment received!"
Bot: "Sending 1,100 BNVG to your wallet GABCD...WXYZ"
Bot: ""
Bot: "✅ Done! Transaction: https://stellar.expert/tx/..."
Bot: "Your new balance: 2,100 BNVG"
```

**How it works technically:**
1. Bot generates unique memo: `BUY-<random>`
2. Bot watches its own wallet for incoming XLM with that memo
3. Payment arrives → bot uses USER's secret key? No...
   
   Actually TWO approaches:
   
   **Approach A — Bot sends from pool wallet:**
   - Bot's wallet holds the asset supply
   - User sends XLM to bot wallet
   - Bot sends asset FROM bot wallet TO user's address
   - User just needs a trustline, no secret key needed
   - ✅ RECOMMENDED — simpler, safer
   
   **Approach B — Admin-only, using user's secret key:**
   - For special cases where admin needs to operate user's wallet
   - E.g., creating trustlines, claiming balances
   - Requires stored secret key (verified method 3)

### Admin: Asset Configuration

```
/asset add BNVG ISSUERADDRESS 0.05 10
         ^code  ^issuer       ^price ^bonus%

/asset list
  📦 Assets for Sale:
  1. BNVG — 0.05 XLM — 10% bonus — Active
  2. VNGD — 0.10 XLM — 5% bonus — Paused

/asset price BNVG 0.06      — update price
/asset bonus BNVG 15        — change bonus to 15%
/asset pause BNVG           — stop selling
/asset resume BNVG          — resume selling
/asset remove BNVG          — remove from store
/asset stock                — check bot wallet balances
```

### Database: Orders Table
```sql
CREATE TABLE orders (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       INTEGER NOT NULL,
    asset_code    TEXT NOT NULL,
    quantity      REAL NOT NULL,          -- amount ordered
    bonus         REAL NOT NULL,          -- bonus amount
    total         REAL NOT NULL,          -- quantity + bonus
    price_xlm     REAL NOT NULL,          -- XLM cost
    memo          TEXT UNIQUE NOT NULL,   -- unique payment memo
    status        TEXT DEFAULT 'pending', -- pending, paid, sent, completed, expired, cancelled
    user_address  TEXT,                   -- where to send the asset
    tx_hash_in    TEXT,                   -- incoming XLM tx hash
    tx_hash_out   TEXT,                   -- outgoing asset tx hash
    created_at    TEXT,
    paid_at       TEXT,
    completed_at  TEXT,
    expires_at    TEXT                    -- 30 min from creation
);

CREATE TABLE assets_for_sale (
    asset_code    TEXT PRIMARY KEY,
    issuer        TEXT NOT NULL,
    price_xlm     REAL NOT NULL,         -- price per 1 token in XLM
    bonus_pct     REAL DEFAULT 0,        -- bonus percentage
    active        INTEGER DEFAULT 1,
    lobstr_url    TEXT,                   -- LOBSTR trade link
    created_at    TEXT
);
```

---

## 🏷 Tier System

### Tiers Based on Holdings

| Tier | Requirement | Badge | Perks |
|---|---|---|---|
| 🥉 Bronze | 100+ BNVG | 🥉 | Basic access |
| 🥈 Silver | 1,000+ BNVG | 🥈 | +5% bonus on purchases |
| 🥇 Gold | 5,000+ BNVG | 🥇 | +10% bonus, priority support |
| 💎 Diamond | 25,000+ BNVG | 💎 | +15% bonus, exclusive airdrops |
| 👑 Platinum | 100,000+ BNVG | 👑 | +20% bonus, direct admin line |

### How Tiers Work

1. Bot checks user's wallet balance (verified wallets only)
2. Tier assigned based on highest qualifying balance
3. Tier badge shown in:
   - User's `/start` menu
   - Admin topic view
   - Broadcast messages (optional)
4. Tier checked on every balance query or purchase
5. **Bonus stacks with direct purchase bonus:**
   - Base direct bonus: 10%
   - Gold tier bonus: +10%
   - Total: 20% bonus

### User Sees:
```
👑 Welcome back, Stuart!
Tier: 🥇 Gold (5,234 BNVG)
Next tier: 💎 Diamond (need 19,766 more BNVG)

━━━━━━━━━━━━━━━━━━━━
[📊 My Balance]
[🛒 Buy BNVG]
[💳 My Wallets (2)]
[⚙️ Settings] [📖 Help]
```

### Admin Configuration:
```
/tier list
  🏷 Tier Configuration:
  1. 🥉 Bronze — 100+ BNVG — +0% bonus
  2. 🥈 Silver — 1,000+ BNVG — +5% bonus
  3. 🥇 Gold — 5,000+ BNVG — +10% bonus
  4. 💎 Diamond — 25,000+ BNVG — +15% bonus
  5. 👑 Platinum — 100,000+ BNVG — +20% bonus

/tier set gold 5000 10
         ^name ^min  ^bonus%

/tier add vip 500000 25
         ^new ^min   ^bonus%

/tier remove vip

/tier asset BNVG     — which asset determines tiers (default)
```

### Database:
```sql
CREATE TABLE tiers (
    name          TEXT PRIMARY KEY,
    display       TEXT NOT NULL,          -- emoji + name
    min_balance   REAL NOT NULL,
    bonus_pct     REAL DEFAULT 0,
    sort_order    INTEGER DEFAULT 0,
    perks         TEXT                    -- JSON list of perks
);
```

---

## 🧾 Invoice System

### What It Does
Admin creates a payment request → user pays → auto-confirmed.

### Use Cases
- Sell services ("Pay 50 XLM for VIP membership")
- Collect payments ("Monthly fee: 10 XLM")
- One-time charges ("Custom work: 200 XLM")
- Donations ("Support the project")

### Flow
```
Admin (in user topic): /invoice 50 XLM VIP Membership

Bot (to admin): "🧾 Invoice created for Example User"
Bot (to admin): "Amount: 50 XLM"
Bot (to admin): "Description: VIP Membership"
Bot (to admin): "Memo: INV-394827"

Bot (to user): "🧾 Invoice from Admin"
Bot (to user): ""
Bot (to user): "VIP Membership"
Bot (to user): "Amount: 50 XLM"
Bot (to user): ""
Bot (to user): "Send 50 XLM to:"
Bot (to user): "GBOTWALLETADDRESS..."
Bot (to user): "Memo: INV-394827"
Bot (to user): ""
Bot (to user): "⏰ Valid for 24 hours"
Bot (to user): "[💳 Pay Now (LOBSTR)] [❌ Decline]"

--- user pays ---

Bot (to user): "✅ Payment confirmed! Thank you."
Bot (to admin): "✅ Invoice INV-394827 paid by Example User (50 XLM)"
```

### Database:
```sql
CREATE TABLE invoices (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       INTEGER NOT NULL,
    amount        REAL NOT NULL,
    asset_code    TEXT DEFAULT 'XLM',
    description   TEXT,
    memo          TEXT UNIQUE NOT NULL,
    status        TEXT DEFAULT 'pending', -- pending, paid, expired, cancelled
    tx_hash       TEXT,
    created_at    TEXT,
    paid_at       TEXT,
    expires_at    TEXT
);
```

---

## 💰 Deposit Detection (Balance Watcher)

### How It Works
- Bot periodically checks verified wallets via Horizon API
- Detects new incoming payments
- Notifies user + admin

### Notifications:
```
User gets: "💰 You received 500 BNVG!"
           "From: GSEND...ADDR"
           "New balance: 2,500 BNVG"
           "Tier: 🥈 Silver → 🥇 Gold 🎉"

Admin gets (in user topic):
           "💰 Stuart received 500 BNVG"
           "New tier: 🥇 Gold"
```

### Settings:
```
/alerts on   — user enables balance alerts
/alerts off  — user disables
/alerts min 10 — only notify for amounts > 10
```

---

## 🤝 Auto-Trustline

### Problem
Users can't receive a custom asset unless their wallet has a trustline for it.

### Solution
When user buys an asset, bot checks trustline:
```
Bot: "⚠️ Your wallet doesn't have a trustline for BNVG yet."
Bot: ""

If user has stored secret key:
  Bot: "Want me to add it automatically?"
  [✅ Yes, add trustline] [No, I'll do it manually]
  
  User taps yes → bot signs & submits changeTrust operation
  Bot: "✅ Trustline added! Now receiving your BNVG..."

If no secret key:
  Bot: "Please add a trustline for BNVG in LOBSTR:"
  Bot: "1. Open LOBSTR → Assets → Search BNVG"
  Bot: "2. Tap 'Add' or 'Trust'"
  Bot: "3. Come back here and tap 'I've added it'"
  [✅ I've added it] [❌ Cancel]
```

---

## 📦 Batch Distribution

### Admin uploads a list, bot sends to all:
```
/distribute BNVG 100

Bot: "📦 Batch Distribution"
Bot: "Asset: BNVG"
Bot: "Amount: 100 per user"
Bot: "Target: all verified wallets (38)"
Bot: "Total: 3,800 BNVG"
Bot: ""
Bot: "Bot wallet balance: 50,000 BNVG ✅"
Bot: ""
Bot: "[✅ Send to All] [🏷 Send to Tag] [❌ Cancel]"

Admin taps [🏷 Send to Tag]:
Bot: "Which tag?"
Bot: "[VIP] [TIER1] [EARLY] [Custom]"

Admin taps [VIP]:
Bot: "Sending 100 BNVG to 12 VIP users..."
Bot: "████████░░ 8/12"
Bot: "..."
Bot: "✅ Done! 12 sent, 0 failed"
Bot: "TX hashes saved to distribution log"
```

---

## 📊 Full Feature Priority List

| # | Feature | Status | Dependencies |
|---|---|---|---|
| 1 | Multi-wallet + labels | 🔜 Next | DB migration v6 |
| 2 | Wallet verification (memo) | 🔜 Next | Verification wallet funded |
| 3 | Wallet verification (secret key) | 🔜 Next | Encryption key |
| 4 | Settings panel (admin) | 🔜 Next | None |
| 5 | Balance check | Planned | Stellar SDK |
| 6 | Balance alerts / deposit detection | Planned | #5 |
| 7 | Auto-trustline | Planned | Secret key stored |
| 8 | Asset sales (LOBSTR + direct) | Planned | Pool wallet, #7 |
| 9 | Tier system | Planned | #5 |
| 10 | Invoice system | Planned | Payment detection |
| 11 | Airdrop / batch distribution | Planned | Pool wallet, #7 |
| 12 | Transaction history | Planned | Stellar SDK |
| 13 | Telegram Mini App (wallet) | Future | Web development |

---

## Questions Still Open

1. **Pool wallet** — which wallet holds the assets for sale? Same as verification wallet or different?
2. **Which assets to sell?** — BNVG? Others? Need asset codes + issuer addresses
3. **Tier asset** — which asset determines tier? BNVG?
4. **Pricing** — fixed price or pull from DEX?
5. **Order expiry** — 30 minutes for direct buys?
6. **Auto-send** — immediately on payment detection, or queue for admin approval?
7. **Fund the verification wallet** — needs ~1.5 XLM to activate on mainnet
