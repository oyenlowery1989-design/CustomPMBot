# Deploying to the VPS

Upgrades the production install at `/opt/nopmsbot-v2` (Ubuntu 24.04) from the
legacy code to the current repository. The deploy script is idempotent — the
same command performs first-time migration and every later update.

---

## 0. Rotate the bot token — DO THIS FIRST

The old token was shared in a chat (flagged in TODO.md since v2.1).

1. [@BotFather](https://t.me/BotFather) → `/mybots` → your bot → **API Token → Revoke current token**.
2. Put the new token in `/etc/nopmsbot-v2.env` (`BOT_TOKEN=...`).

The old token dies instantly; the running old bot stops working — deploy right
after.

## 1. Check the env file

Compare `/etc/nopmsbot-v2.env` against `env.example`. Everything required
should already exist from v2. New since then:

```bash
# optional — HTTP health endpoint
HEALTH_PORT=8080
```

Required: `BOT_TOKEN`, `OWNER_ID`, `ADMIN_IDS`, `ADMIN_GROUP_ID`.
For wallet verification: `VERIFY_WALLET_PUBLIC`, `WALLET_ENCRYPTION_KEY`.
Set `DB_PATH=/opt/nopmsbot-v2/state.db`.

## 2. Deploy

```bash
ssh root@<vps>
curl -fsSL https://raw.githubusercontent.com/oyenlowery1989-design/CustomPMBot/main/deploy/deploy.sh -o /tmp/deploy.sh
sudo bash /tmp/deploy.sh
```

What it does:
1. creates the `nopmsbot` system user (first run),
2. stops the service, **backs up the DB** (`state.db.bak-<timestamp>`),
3. first run: moves the legacy dir to `/opt/nopmsbot-v2.old-<timestamp>`,
   clones the repo, carries `state.db` over — later runs: `git pull`,
4. installs the venv + dependencies,
5. installs/refreshes the hardened systemd unit (`nopmsbot-v2`),
6. restarts and prints the log tail.

DB migrations run automatically on start — the old schema upgrades to v10
incrementally; existing data is preserved (covered by tests).

## 3. Verify

```bash
systemctl status nopmsbot-v2
journalctl -u nopmsbot-v2 -f          # watch for "Polling..."
curl -s localhost:8080/health          # if HEALTH_PORT set
```

In Telegram: `/stats` in the admin group, then message the bot from a
non-admin account and check the topic relay.

## 4. Post-deploy (one-time)

- `/manual publish` — creates the Instant View manual page.
- **Behavior change:** broadcasts now show a preview with a Send button
  before going out. Old instant behavior: `/setmsg broadcast_confirm off`.
- Old service name: if the legacy unit wasn't called `nopmsbot-v2`, disable
  it: `systemctl disable --now <oldname>`.

## 5. Rollback

```bash
systemctl stop nopmsbot-v2
mv /opt/nopmsbot-v2 /opt/nopmsbot-v2.failed
mv /opt/nopmsbot-v2.old-<timestamp> /opt/nopmsbot-v2
cp /opt/nopmsbot-v2.failed/state.db.bak-<timestamp> /opt/nopmsbot-v2/state.db  # if needed
systemctl start <old-service-name>
```

Note: the new schema (v10) is backward-incompatible with old code only in the
sense that old code ignores new tables/columns — restoring the pre-deploy
`.bak` DB alongside old code is the clean rollback.

## Updating later

Same command:

```bash
sudo bash /opt/nopmsbot-v2/deploy/deploy.sh
```
