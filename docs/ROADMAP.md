# NoPMsBot — Feature Roadmap & Planning

**Last updated:** 2026-02-16

---

## ✅ v2.0 — Core (Done)
- DM ↔ topic relay (all media types)
- Broadcasting (all / tagged)
- Ban/unban with expiry + auto-ban spam
- Tags, canned responses, export
- Custom topics with command/event bindings
- Stats, settings, welcome message
- Blocked user tracking

## ✅ v2.1 — Wallet Basics (Done)
- Single wallet per user
- Address validation
- Inline buttons on /start
- Admin /wallets list
- DB migration v5 (wallets table)

---

## 🔜 v2.2 — Multi-Wallet + Verification

### Multi-Wallet
- Up to 5 wallets per user
- Each wallet has a label (main, trading, rewards, etc.)
- User picks which to verify
- DB: PRIMARY KEY changes to (user_id, address)

### Verification Method A: Memo
- Bot generates random 6-digit code
- User sends 0.0000001 XLM to VERIFICATION_WALLET with memo = code
- Bot polls Horizon API `/payments` stream for the verification wallet
- Match → wallet marked verified=1 (memo)
- 15-minute TTL on verification attempts
- Needs: a Stellar keypair the bot controls

**Memo format options:**
- 6-digit numeric: `847291` (simple, easy to type)
- Bot-prefix: `NOPM-847291` (recognizable in transaction history)
- User-specific: `V-<user_id>-<random>` (traceable)

**Recommendation:** 6-digit numeric. Simple, no room for typos.

### Verification Method B: Signature Challenge
- Bot generates challenge: `"NoPMsBot-verify-<random>-<timestamp>"`
- User signs the challenge using:
  - StellarLab (laboratory.stellar.org → Transaction Signer)
  - Stellar CLI tools
  - Any signing tool
- User pastes back the base64 signature
- Bot verifies using stellar-sdk: `Keypair.from_public_key(addr).verify(challenge, sig)`
- Match → wallet marked verified=2 (signature)
- **NO secret key stored or transmitted**

### Admin Verification Settings
- `/setverify memo` — show only memo option to users (default)
- `/setverify both` — show memo + signature options
- `/setverify signature` — show only signature
- Stored in settings table

### User Flow (DM)
```
/start
  → [💳 My Wallets (2/5)]
  → [⚙️ Settings] [📖 Help]

Tap "My Wallets":
  💳 Your Wallets:
  1. ✅ GABCD...WXYZ (main) — verified ✓
  2. ⏳ GEFGH...STUV (trading) — unverified
  
  [+ Add Wallet]
  [🔐 Verify #2]
  [🗑 Remove Wallet]

Tap "Verify #2":
  How would you like to verify?
  [📝 Verify by Memo (send 0.0000001 XLM)]
  [🔐 Verify by Signature (advanced)]     ← only if admin enabled

Tap "Verify by Memo":
  📝 Memo Verification
  
  Send exactly 0.0000001 XLM to:
  GVERIFICATIONADDRESSHERE
  
  Memo (TEXT): 847291
  
  ⏰ Expires in 15 minutes.
  I'll check automatically every 10 seconds.
  
  [❌ Cancel Verification]

  → (10s later, payment found)
  
  ✅ Wallet GEFGH...STUV verified!
```

### Admin Flow (in user topic)
```
/wallet
  💳 Wallets for <example-user> (<example-user-id>):
  
  1. ✅ GABCD...WXYZ (main)
     Verified: memo — 2026-02-16 10:30
  
  2. ⏳ GEFGH...STUV (trading)
     Added: 2026-02-16 10:45
     Not verified
```

### New DB Tables (v6 migration)
```sql
-- Replace wallets table
DROP TABLE IF EXISTS wallets;
CREATE TABLE wallets (
    user_id       INTEGER NOT NULL,
    address       TEXT NOT NULL,
    label         TEXT DEFAULT 'main',
    verified      INTEGER DEFAULT 0,   -- 0=unverified, 1=memo, 2=signature
    verified_at   TEXT,
    added_at      TEXT,
    PRIMARY KEY (user_id, address)
);

CREATE TABLE wallet_verifications (
    user_id       INTEGER NOT NULL,
    address       TEXT NOT NULL,
    method        TEXT NOT NULL,        -- 'memo' or 'signature'
    challenge     TEXT NOT NULL,         -- 6-digit code or challenge string
    created_at    TEXT NOT NULL,
    expires_at    TEXT NOT NULL,
    PRIMARY KEY (user_id, address)
);
```

### Dependencies
- `stellar-sdk` (pip install stellar-sdk) — for signature verification + Horizon API
- A Stellar keypair for receiving memo verifications

---

## 🔮 v2.3 — Stellar Read Features

### /balance
- User command: shows XLM + all custom assets in their verified wallets
- Queries Horizon API: `GET /accounts/{address}`
- Shows:
  ```
  💰 Balance for GABCD...WXYZ (main):
  
  XLM:          1,234.5678
  USDC:         500.00
  BNVG:         15,000.00
  yXLM:         200.00
  ```

### /assets
- Lists all assets held across all wallets
- Grouped by asset code
- Shows issuer (truncated)

### /transactions (or /tx)
- Recent transactions from Horizon
- Shows last 5-10 transactions
- Filter by asset: `/tx XLM`, `/tx BNVG`

### Admin: /balance <user_id>
- Check any user's balance from their topic

---

## 🔮 v2.4 — Stellar Write Features (Requires Custodial Wallet)

### Pending Transactions
- Bot wallet holds assets for distribution
- `/pending` — show queued outgoing transactions
- `/approve <tx_id>` — approve and send
- `/approve all` — approve everything in queue
- `/reject <tx_id>` — cancel a pending tx

### /send <user> <amount> <asset>
- Admin sends assets to a user's verified wallet
- Creates pending tx → admin confirms
- Executed via Stellar SDK
- Logged in database

### /airdrop <tag> <amount> <asset>
- Bulk send to all users with a tag AND verified wallet
- Preview first: "About to send 100 BNVG to 47 users. Proceed?"
- Progress bar during execution
- Summary: sent/failed/skipped

### Distribution Tracking
- `distributions` table: logs every outgoing payment
- `/distributions` — report: total sent, by asset, by user
- Export as CSV

---

## 🔮 v3.0 — Platform Features

### Web Dashboard
- User list with search/filter
- Wallet overview (verified/unverified)
- Broadcast composer + scheduler
- Transaction history
- Analytics (messages/day, new users, etc.)

### Scheduled Broadcasts
- `/schedule 2026-02-20 09:00 UTC <message>`
- Recurring: `/schedule every monday 09:00 <message>`
- Queue view: `/scheduled`

### Notification System
- Price alerts: "BNVG dropped below 0.01 XLM"
- Balance change alerts for users
- Large transaction alerts for admin

### Multi-Bot
- Same codebase, different tokens
- Shared admin group with separate topic sections
- Per-bot settings

---

## Questions to Decide

1. **Verification wallet keypair** — who provides it? Generate fresh?
2. **Memo format** — `847291` (simple) vs `NOPM-847291` (branded)?
3. **Signature method visibility** — admin-only toggle, default hidden?
4. **Max wallets per user** — 5?
5. **Unverified wallet permissions** — can they check balance without verification?
6. **Stellar network** — mainnet or testnet first?
7. **Which Horizon server** — public (horizon.stellar.org) or run our own?

---

## Tech Stack

| Component | Technology |
|---|---|
| Bot framework | python-telegram-bot v21+ |
| Database | SQLite3 |
| Stellar SDK | stellar-sdk (Python) |
| Horizon API | horizon.stellar.org (public) |
| Hosting | Ubuntu 24.04 VPS |
| Service | systemd |
| Backup | /opt/nopmsbot-backups/ |
