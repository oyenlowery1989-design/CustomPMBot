# NoPMsBot v2.2 — Detailed Implementation Plan

**Last updated:** 2026-02-16

---

## Decisions Made

| Question | Answer |
|---|---|
| Max wallets per user | 5 (configurable via admin command) |
| Label flow | Ask AFTER address is submitted, separate step |
| Secret key storage | YES — admin-only option, AES-256 encrypted, with warning |
| Signature verification | Implement but complex for LOBSTR users |
| Memo verification | Primary method for regular users |
| Stellar network | Mainnet (real XLM, real wallets) |
| Unverified wallet features | Balance check only. Airdrop/send/receive show as disabled buttons with "verify to unlock" message |
| Webapp / LOBSTR clone | Future plan — Telegram Mini App |

---

## User Flow — Add Wallet (Detailed)

```
Step 1: User taps [💳 My Wallets] or types /wallet
  │
  ├─ No wallets yet:
  │   "You haven't added any wallets yet."
  │   [+ Add Wallet]
  │
  └─ Has wallets:
      💳 Your Wallets (2/5):
      
      1. ✅ GABCD...WXYZ — "Main" (verified ✓)
      2. ⏳ GEFGH...STUV — "Trading" (unverified)
      
      [+ Add Wallet]
      [🔐 Verify Unverified]
      [✏️ Edit] [🗑 Remove]

Step 2: User taps [+ Add Wallet]
  │
  Bot: "Please send your Stellar public address."
  Bot: "It should start with G and be 56 characters."
  │
  User sends: GABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890ABCDEFGHIJKLMNO
  │
  Bot validates (G + 56 chars + alphanumeric)
  │
  ├─ Invalid:
  │   "❌ That doesn't look like a valid Stellar address."
  │   "It should start with G and be exactly 56 characters."
  │   "Try again or /cancel"
  │
  └─ Valid:
      Bot: "✅ Address accepted!"
      Bot: "What would you like to name this wallet?"
      Bot: "Examples: Main, Trading, Savings, Rewards"
      │
      User sends: "Trading"
      │
      Bot: "💳 Wallet saved!"
      Bot: "Name: Trading"
      Bot: "Address: GABCD...WXYZ"
      Bot: "Status: ⏳ Unverified"
      Bot: ""
      Bot: "Verify your wallet to unlock all features!"
      [🔐 Verify Now] [Later]
```

---

## User Flow — Verify Wallet

```
Step 1: User taps [🔐 Verify] on a wallet
  │
  Bot shows verification options (based on admin setting):
  │
  ├─ Memo only (default):
  │   "Choose verification method:"
  │   [📝 Verify by Memo (send 0.0000001 XLM)]
  │
  ├─ Memo + Signature (if admin enabled):
  │   "Choose verification method:"
  │   [📝 Verify by Memo (send 0.0000001 XLM)]
  │   [🔐 Verify by Signature (advanced)]
  │
  └─ All three (if admin enabled secret key):
      "Choose verification method:"
      [📝 Verify by Memo (send 0.0000001 XLM)]
      [🔐 Verify by Signature (advanced)]
      [🔑 Verify by Secret Key (admin only)]
```

### Memo Verification Flow
```
Step 2a: User taps [📝 Verify by Memo]
  │
  Bot generates 6-digit code: 847291
  │
  Bot: "📝 Memo Verification"
  Bot: ""
  Bot: "Send exactly 0.0000001 XLM (minimum) to:"
  Bot: "GVERIFICATIONADDRESS..."
  Bot: ""
  Bot: "Memo (TEXT): 847291"
  Bot: ""
  Bot: "⏰ You have 15 minutes."
  Bot: "I'll check automatically — just send the payment and wait."
  Bot: ""
  [❌ Cancel]
  │
  │  Bot starts polling Horizon API:
  │  GET horizon.stellar.org/accounts/{verification_address}/payments?order=desc&limit=10
  │  Every 10 seconds for 15 minutes
  │
  ├─ Payment found with matching memo + source = user's address:
  │   "✅ Wallet verified!"
  │   "GABCD...WXYZ is now confirmed as yours."
  │   Wallet marked verified=1
  │
  ├─ Wrong memo:
  │   (ignore, keep waiting)
  │
  └─ 15 minutes expired:
      "⏰ Verification expired."
      "You can try again with /wallet → Verify"
      Pending verification deleted
```

### Secret Key Verification Flow (Admin-Only Option)
```
Step 2c: User taps [🔑 Verify by Secret Key]
  │
  Bot: "⚠️ SECURITY WARNING ⚠️"
  Bot: ""
  Bot: "You are about to share your Stellar secret key."
  Bot: "Your key will be encrypted and stored securely."
  Bot: ""
  Bot: "⚠️ Only do this if you trust this service."
  Bot: "⚠️ Anyone with your secret key controls your wallet."
  Bot: ""
  Bot: "Send your secret key (starts with S, 56 characters)"
  Bot: "Or /cancel to abort."
  │
  User sends: SABCDEFG...
  │
  Bot validates:
  1. Starts with S, 56 characters
  2. Derive public key from secret key using stellar-sdk
  3. Check if derived public key matches the wallet address
  │
  ├─ Match:
  │   "✅ Key verified! The secret key matches wallet GABCD...WXYZ"
  │   Secret key encrypted with AES-256 and stored
  │   Wallet marked verified=3 (secret_key)
  │   Bot DELETES the user's message containing the key (for safety)
  │
  ├─ No match:
  │   "❌ This secret key doesn't match wallet GABCD...WXYZ"
  │   "The key belongs to a different address."
  │   "Try again or /cancel"
  │
  └─ Invalid format:
      "❌ Invalid secret key format."
      "It should start with S and be 56 characters."
```

---

## Unverified vs Verified — Feature Gating

### User Menu (after adding wallet, NOT verified):
```
💳 Wallet: GABCD...WXYZ — "Main"
Status: ⏳ Unverified

Available:
[📊 Check Balance]          ← works (read-only, no harm)

Locked (verify to unlock):
[🔒 Receive 100 XLM Gift]  ← disabled
[🔒 Claim Airdrop]         ← disabled
[🔒 Send Assets]           ← disabled

[🔐 Verify Wallet]
```

When user taps a locked button:
```
"🔒 This feature requires a verified wallet."
"Verify your wallet to unlock:"
[🔐 Verify Now]
```

### User Menu (VERIFIED):
```
💳 Wallet: GABCD...WXYZ — "Main" ✅
Status: Verified (memo) — Feb 16, 2026

[📊 Check Balance]
[🎁 Receive 100 XLM Gift]    ← enabled
[💰 Claim Airdrop]           ← enabled  
[📤 Send Assets]             ← enabled (future)
[📋 Transaction History]     ← enabled (future)
```

---

## Admin View — In User Topic

```
Admin types: /wallet (in <example-user>'s topic)

💳 Wallets for <example-user> (@example_user)
User ID: <example-user-id>

1. ✅ GABCD...WXYZ — "Main"
   Full: GABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890ABCDEFGHIJKLMNO
   Verified: memo — 2026-02-16 10:30
   🔑 Secret key: stored (encrypted)
   
2. ⏳ GEFGH...STUV — "Trading"
   Full: GEFGHIJKLMNOPQRSTUVWXYZ1234567890ABCDEFGHIJKLMNOPQRS
   Added: 2026-02-16 10:45
   Not verified

[📊 Check Balances] [🗑 Remove Wallet]
```

---

## Admin Commands

| Command | Where | Description |
|---|---|---|
| `/wallet` | User topic | Show that user's wallets |
| `/wallets` | DM or group | List all wallets across all users |
| `/wallets verified` | DM or group | List only verified wallets |
| `/wallets unverified` | DM or group | List only unverified |
| `/setverify memo\|signature\|key\|all` | DM | Set which verification methods users see |
| `/setmaxwallets <n>` | DM | Change max wallets per user (default 5) |

---

## Database Schema (v6)

```sql
-- Wallets (replaces v5 single-wallet table)
CREATE TABLE wallets (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       INTEGER NOT NULL,
    address       TEXT NOT NULL,
    label         TEXT DEFAULT 'Unnamed',
    verified      INTEGER DEFAULT 0,     -- 0=none, 1=memo, 2=signature, 3=secret_key
    verified_at   TEXT,
    added_at      TEXT,
    UNIQUE(user_id, address)
);

-- Encrypted secret keys (separate table for isolation)
CREATE TABLE wallet_keys (
    address       TEXT PRIMARY KEY,
    encrypted_key TEXT NOT NULL,          -- AES-256-GCM encrypted
    key_hash      TEXT NOT NULL,          -- SHA-256 hash for quick lookup
    stored_at     TEXT
);

-- Pending verifications (TTL 15 min)
CREATE TABLE wallet_verifications (
    user_id       INTEGER NOT NULL,
    address       TEXT NOT NULL,
    method        TEXT NOT NULL,          -- 'memo' or 'signature'
    challenge     TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    expires_at    TEXT NOT NULL,
    PRIMARY KEY (user_id, address)
);

-- Future: distribution/airdrop log
CREATE TABLE distributions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       INTEGER NOT NULL,
    address       TEXT NOT NULL,
    asset_code    TEXT NOT NULL,
    amount        TEXT NOT NULL,
    tx_hash       TEXT,
    status        TEXT DEFAULT 'pending', -- pending, sent, failed
    created_at    TEXT,
    completed_at  TEXT
);
```

---

## Encryption for Secret Keys

```python
# Key derivation from a master password (stored in env, NOT in DB)
# ENCRYPTION_KEY in /etc/nopmsbot-v2.env

from cryptography.fernet import Fernet
# or AES-256-GCM via cryptography library

# Encrypt: fernet.encrypt(secret_key.encode())
# Decrypt: fernet.decrypt(encrypted).decode()
# Master key never in database
```

Environment variable:
```env
WALLET_ENCRYPTION_KEY=your-32-byte-base64-key-here
```

---

## Dependencies to Add

```
pip install stellar-sdk        # Stellar operations + Horizon API
pip install cryptography       # AES-256 encryption for secret keys
```

---

## Horizon API Endpoints We'll Use

| Endpoint | Purpose |
|---|---|
| `GET /accounts/{address}` | Balance check (XLM + all assets) |
| `GET /accounts/{address}/payments` | Payment history + verification polling |
| `GET /accounts/{address}/transactions` | Transaction history |
| `POST /transactions` | Submit transactions (future: send/airdrop) |

Base URL: `https://horizon.stellar.org` (mainnet)

---

## Future: Telegram Mini App (LOBSTR Clone)

A webapp that opens inside Telegram:
- Login with Stellar keypair OR create new wallet
- See balances, send/receive
- Sign transactions
- Verify wallet ownership seamlessly
- Full wallet management without leaving Telegram

Tech: React/Vue frontend, Python backend, Stellar SDK
Opens via: `InlineKeyboardButton("Open Wallet", web_app=WebAppInfo(url="..."))`

This replaces the need for secret key storage — user manages their own keys in the webapp.

---

## Implementation Order

1. Multi-wallet DB migration (v6)
2. Label flow (ask after address)
3. Wallet list UI with inline buttons
4. Memo verification (need verification wallet keypair)
5. Secret key verification (with encryption)
6. Balance check via Horizon API
7. Disabled/locked buttons for unverified
8. Admin /wallet in topic view
9. Signature verification (if requested)
10. Future: Mini App
