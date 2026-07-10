#!/usr/bin/env bash
# Deploy/upgrade NoPMsBot on the VPS. Idempotent — safe to re-run.
# First run migrates a non-git install (the old /opt/nopmsbot-v2) to a git
# clone, preserving the database. DB schema migrations run automatically on
# bot start.
#
# Usage: sudo bash deploy.sh
set -euo pipefail

APP_DIR=/opt/nopmsbot-v2
REPO=https://github.com/oyenlowery1989-design/CustomPMBot.git
SERVICE=nopmsbot-v2
ENV_FILE=/etc/nopmsbot-v2.env
RUN_USER=nopmsbot
TS=$(date +%Y%m%d-%H%M%S)

say() { echo -e "\033[1;32m==>\033[0m $*"; }

[ "$(id -u)" -eq 0 ] || { echo "Run as root (sudo)."; exit 1; }
[ -f "$ENV_FILE" ] || { echo "Missing $ENV_FILE — create it from env.example first."; exit 1; }

# 0. Dedicated user
if ! id "$RUN_USER" &>/dev/null; then
    say "Creating user $RUN_USER"
    useradd --system --home-dir "$APP_DIR" --shell /usr/sbin/nologin "$RUN_USER"
fi

# 1. Get the code: pull if already a clone, otherwise move the legacy install
#    aside, clone fresh, and carry the database over. Deliberately BEFORE
#    stopping the service: the bot keeps running on the old code/venv while
#    we fetch and install the new ones, so a network blip or a bad dependency
#    resolution here aborts (set -euo pipefail) with the service still up,
#    instead of leaving the bot down with nothing to restart it (H8,
#    docs/AUDIT-2026-07-10.md).
if [ -d "$APP_DIR/.git" ]; then
    say "Updating existing clone"
    git -C "$APP_DIR" fetch origin
    git -C "$APP_DIR" reset --hard origin/main
else
    if [ -d "$APP_DIR" ]; then
        say "Legacy (non-git) install found — moving to $APP_DIR.old-$TS"
        mv "$APP_DIR" "$APP_DIR.old-$TS"
    fi
    say "Cloning $REPO"
    git clone "$REPO" "$APP_DIR"
    if compgen -G "$APP_DIR.old-$TS/state.db*" > /dev/null; then
        say "Carrying database over from the legacy install"
        cp "$APP_DIR.old-$TS"/state.db* "$APP_DIR/" 2>/dev/null || true
    fi
fi

# 2. Virtualenv + dependencies — still before stopping the service, for the
#    same reason as step 1.
say "Installing dependencies"
[ -d "$APP_DIR/.venv" ] || python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install -q --upgrade pip
"$APP_DIR/.venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"

# 3. Only now stop the service (whatever it's currently called) — code and
#    dependencies are already staged, so the downtime window is just the
#    backup + permissions + restart below, not the slow/network-dependent part.
for svc in "$SERVICE" nopmsbot; do
    systemctl stop "$svc" 2>/dev/null || true
done

# 4. Back up the database. The service is already stopped (step 3), but the
# DB runs in WAL mode — recent writes can still be sitting in state.db-wal
# rather than state.db itself. A plain `cp state.db` alone can silently miss
# them (L12, docs/AUDIT-2026-07-10.md), so checkpoint (fold the WAL back into
# the main file) before copying.
if [ -f "$APP_DIR/state.db" ]; then
    if command -v sqlite3 &>/dev/null; then
        sqlite3 "$APP_DIR/state.db" "PRAGMA wal_checkpoint(TRUNCATE);" >/dev/null
    else
        say "WARNING: sqlite3 CLI not found — backing up without a WAL checkpoint first."
    fi
    say "Backing up database to $APP_DIR/state.db.bak-$TS"
    cp "$APP_DIR/state.db" "$APP_DIR/state.db.bak-$TS"
fi

# 5. Permissions
chown -R "$RUN_USER":"$RUN_USER" "$APP_DIR"
chmod 640 "$ENV_FILE"
chown root:"$RUN_USER" "$ENV_FILE"

# 6. systemd unit
say "Installing systemd unit $SERVICE"
cp "$APP_DIR/deploy/nopmsbot-v2.service" "/etc/systemd/system/$SERVICE.service"
systemctl daemon-reload
systemctl enable "$SERVICE"

# 7. Start + verify
say "Starting $SERVICE"
systemctl restart "$SERVICE"
sleep 3
if systemctl is-active --quiet "$SERVICE"; then
    say "Service is running. Recent log:"
    journalctl -u "$SERVICE" -n 10 --no-pager

    # If a health endpoint is configured, actually check it instead of just
    # trusting systemd's "active" status — that only means the process
    # started, not that the bot/event-loop is actually healthy (M11,
    # docs/AUDIT-2026-07-10.md).
    HEALTH_PORT_VAL=$(grep -E '^HEALTH_PORT=' "$ENV_FILE" | cut -d= -f2- | tr -d '[:space:]')
    if [ -n "$HEALTH_PORT_VAL" ]; then
        say "Checking health endpoint on port $HEALTH_PORT_VAL..."
        HEALTH_BODY=$(curl -fsS "http://127.0.0.1:$HEALTH_PORT_VAL/" 2>&1) && HEALTH_OK=1 || HEALTH_OK=0
        if [ "$HEALTH_OK" = "1" ] && echo "$HEALTH_BODY" | grep -q '"status": *"ok"'; then
            say "Health check OK: $HEALTH_BODY"
        else
            echo "Health check FAILED or reported non-ok status: $HEALTH_BODY"
            echo "Service is running but may not be healthy — check logs above."
        fi
    fi

    say "Done. Verify in Telegram: /stats in the admin group."
else
    echo "Service FAILED to start. Log:"
    journalctl -u "$SERVICE" -n 30 --no-pager
    exit 1
fi
