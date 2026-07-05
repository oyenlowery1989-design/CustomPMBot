import asyncio
import logging
from datetime import datetime, timezone

import httpx
from stellar_sdk import Server

from config import VERIFY_WALLET_PUBLIC, log
from database.wallets import (
    db_get_pending_verifications,
    db_set_wallet_verified,
    db_delete_verification,
)
from utils.strings import get_text


class StellarWatcher:
    def __init__(self, bot):
        self.bot = bot
        self.server = Server("https://horizon.stellar.org")
        self.is_running = False

    async def start(self):
        if not VERIFY_WALLET_PUBLIC:
            log.warning("VERIFY_WALLET_PUBLIC not set. Memo watcher disabled.")
            return

        self.is_running = True
        log.info("Stellar Payment Watcher started for: %s", VERIFY_WALLET_PUBLIC)

        while self.is_running:
            try:
                await self._check_payments()
            except Exception as e:
                log.error("Watcher error: %s", e)

            await asyncio.sleep(10)

    async def _check_payments(self):
        # Skip work if there's nothing pending
        pending = db_get_pending_verifications()
        if not pending:
            return

        # Filter to non-expired, build challenge → record map
        now = datetime.now(timezone.utc)
        active = {}
        for p in pending:
            expires = datetime.fromisoformat(p["expires_at"])
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
            if now < expires:
                active[p["challenge"]] = p

        if not active:
            return

        # Fetch recent payments via sync SDK — run in thread to avoid blocking the event loop
        loop = asyncio.get_running_loop()
        try:
            payments = await loop.run_in_executor(
                None,
                lambda: self.server.payments()
                    .for_account(VERIFY_WALLET_PUBLIC)
                    .order(desc=True)
                    .limit(20)
                    .call(),
            )
        except Exception as e:
            log.error("Horizon payments fetch failed: %s", e)
            return

        records = payments.get("_embedded", {}).get("records", [])

        # For each payment, fetch its transaction to read the memo field
        async with httpx.AsyncClient(timeout=10) as client:
            for r in records:
                if r.get("type") != "payment":
                    continue

                tx_href = r.get("_links", {}).get("transaction", {}).get("href")
                if not tx_href:
                    continue

                try:
                    resp = await client.get(tx_href)
                    resp.raise_for_status()
                    tx = resp.json()
                except Exception as e:
                    log.warning("Failed to fetch tx details from %s: %s", tx_href, e)
                    continue

                memo = tx.get("memo", "").strip()
                if memo not in active:
                    continue

                v = active[memo]
                db_set_wallet_verified(v["user_id"], v["address"], 1)
                db_delete_verification(v["user_id"], v["address"])
                log.info(
                    "Memo verification confirmed — user %s wallet %s",
                    v["user_id"], v["address"],
                )

                try:
                    await self.bot.send_message(
                        v["user_id"],
                        get_text("wallet.verified_memo"),
                        parse_mode="HTML",
                    )
                except Exception as e:
                    log.warning(
                        "Could not notify user %s of memo verification: %s",
                        v["user_id"], e,
                    )

                # Remove from active map so we don't process the same code twice
                # if multiple matching payments somehow appear
                del active[memo]

    def stop(self):
        self.is_running = False
