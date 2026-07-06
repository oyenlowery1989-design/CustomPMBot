# Running CustomPMBot — Step by Step

From zero to a running bot, plus AI provider setup for the upcoming
AI-drafted-replies feature. Companion docs: [MANUAL.md](MANUAL.md) (features),
[DEPLOY.md](DEPLOY.md) (production VPS).

---

## 1. Prerequisites

- **Python 3.9+** (`python3 --version`)
- **A Telegram bot token** — [@BotFather](https://t.me/BotFather) → `/newbot`
- **A forum group** — new Telegram group → Settings → Topics → enable;
  add the bot as admin with *Manage Topics*, *Pin Messages*, *Delete Messages*
- **Your Telegram user id** — message [@userinfobot](https://t.me/userinfobot)
- **The group id** — forward a group message to @userinfobot (forum ids look like `-100…`)

## 2. Install

```bash
git clone https://github.com/oyenlowery1989-design/CustomPMBot.git
cd CustomPMBot
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## 3. Configure

The bot reads plain environment variables (see `env.example` for the full list).

```bash
export BOT_TOKEN="123456789:AAF...your-token"
export OWNER_ID="111111111"              # your user id
export ADMIN_IDS="111111111,222222222"   # comma-separated, include yourself
export ADMIN_GROUP_ID="-1001234567890"   # the forum group
export DB_PATH="state.db"
```

Optional — Stellar wallet verification:

```bash
# generate the encryption key:
.venv/bin/python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

export WALLET_ENCRYPTION_KEY="<generated key>"
export VERIFY_WALLET_PUBLIC="G...your verification wallet"
```

Optional — ops:

```bash
export HEALTH_PORT=8080   # GET /health returns JSON status
```

## 4. Run

```bash
.venv/bin/python bot.py
```

Expected log lines: `DB migration complete` → `Bot initialized` → `Polling...`.

## 5. Verify (2 minutes)

1. From a **non-admin** account, DM the bot `/start` → welcome menu appears.
2. Send it any text → a topic named after that user appears in your forum group.
3. Reply inside the topic → the user receives it.
4. In the group: `/stats` → numbers; `/help` → command overview.
5. Optional: `curl localhost:8080/health` → `{"status":"ok",...}`.

If step 2 fails, check: bot is group **admin**, Topics enabled, `ADMIN_GROUP_ID`
starts with `-100`. Full table: [MANUAL.md §12](MANUAL.md).

## 6. Tests

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest        # 346 tests, offline, ~3s
```

## 7. Production

One command on the VPS — backs up the DB, migrates, installs systemd:

```bash
sudo bash deploy/deploy.sh
```

Full guide incl. token rotation and rollback: [DEPLOY.md](DEPLOY.md).

---

## 8. AI-drafted replies — provider setup

> **Status: v1 shipped 2026-07-05, Anthropic only** (see TODO.md → v3.0 and
> `docs/superpowers/specs/2026-07-05-ai-drafted-replies-design.md`). Admin taps
> "🤖 Draft reply" on a forwarded message; the AI never messages users
> directly — it posts a draft in the topic with ✅ Send / ✏️ Edit / ❌ Dismiss
> buttons. Set `AI_API_KEY` (and optionally `AI_MODEL`, default
> `claude-haiku-4-5`) and use `/ai on` to enable it.
>
> The multi-provider `AI_PROVIDER` switch below is the **future** design —
> only Option A (Anthropic) is actually implemented right now.

The feature is provider-agnostic in the long-term design — one env-var switch:

```bash
export AI_PROVIDER="anthropic"        # anthropic | openai | gemini
export AI_API_KEY="sk-..."
export AI_MODEL="claude-haiku-4-5"    # optional; sensible default per provider
```

### Option A — Anthropic (Claude)

1. Create a key: [console.anthropic.com](https://console.anthropic.com) → API Keys.
2. `AI_PROVIDER=anthropic`, `AI_API_KEY=sk-ant-...`

| Model | Input / Output per 1M tokens | Fit |
|---|---|---|
| `claude-haiku-4-5` | $1 / $5 | Default — fast, cheap, plenty for support drafts |
| `claude-sonnet-5` | $3 / $15 (intro $2 / $10 through 2026-08-31) | Noticeably better drafts, still cheap |

Cost per draft: a support reply is roughly 1,500 input + 500 output tokens →
**≈ $0.004 with Haiku** (about 250 drafts per dollar), ≈ $0.012 with Sonnet.

### Option B — OpenAI (ChatGPT models)

1. Create a key: [platform.openai.com](https://platform.openai.com) → API Keys.
2. `AI_PROVIDER=openai`, `AI_API_KEY=sk-...`, `AI_MODEL` = a small chat model
   (e.g. `gpt-4o-mini` or the current cheapest chat model — check their pricing
   page, small models are in the same sub-cent-per-draft range).

### Option C — Google (Gemini)

1. Create a key: [aistudio.google.com](https://aistudio.google.com) → Get API key.
2. `AI_PROVIDER=gemini`, `AI_API_KEY=AIza...`, `AI_MODEL` = a Flash-class model
   (e.g. `gemini-2.5-flash`) — Flash models are also sub-cent per draft; Google
   additionally has a free tier with daily request limits, fine for low volume.

### How the integration will work (implementation notes)

- **Anthropic** — official `anthropic` Python SDK, Messages API.
- **OpenAI and Gemini** — both speak the OpenAI chat-completions wire format
  (Gemini via its OpenAI-compatible endpoint), so one `httpx` adapter covers
  both; no extra SDK dependencies.
- The prompt is assembled from: your guidelines (`/ai guidelines`), the user's
  conversation history (already logged in the `messages` table), and canned
  responses as a knowledge base.
- API failure or an `ESCALATE` verdict silently degrades to today's manual
  workflow — a provider outage never blocks support.
- Keys live only in env vars, same as `BOT_TOKEN`; never in the DB or repo.

### Which to pick?

All three produce good support drafts. Deciding factors:

- **Cheapest ready-to-go:** Anthropic Haiku or Gemini Flash (Gemini's free tier wins at very low volume).
- **Best draft quality per dollar:** `claude-sonnet-5` at intro pricing.
- **Already have an account somewhere:** use that provider — switching later is a 2-line env change.
