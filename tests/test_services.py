"""Tests for services: spam throttle, encryption, stellar helpers, payment watcher."""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from stellar_sdk import Keypair

import services.encryption as encryption
import services.spam as spam
from services.spam import _check_spam, _reset_spam
from services.stellar import verify_secret_key_match, get_balances
from services.watcher import StellarWatcher
from config import SPAM_MAX_MSGS, SPAM_WARN_BEFORE_BAN
from database.wallets import (
    db_add_wallet, db_create_verification, db_get_pending_verifications,
    db_get_user_wallets,
)
from tests.conftest import make_bot


class TestSpam:
    def test_under_limit_ok(self):
        for _ in range(SPAM_MAX_MSGS):
            assert _check_spam(1) == "ok"

    def test_overflow_warns_then_bans(self):
        for _ in range(SPAM_MAX_MSGS):
            _check_spam(1)
        assert _check_spam(1) == "warn"   # 6th message in window
        assert _check_spam(1) == "ban"    # 7th → second warning → ban

    def test_state_reset_after_ban(self):
        for _ in range(SPAM_MAX_MSGS + SPAM_WARN_BEFORE_BAN):
            _check_spam(1)
        # After a ban the counters are wiped — user starts clean
        assert _check_spam(1) == "ok"

    def test_users_tracked_independently(self):
        for _ in range(SPAM_MAX_MSGS + 1):
            _check_spam(1)
        assert _check_spam(2) == "ok"

    def test_old_timestamps_pruned(self, monkeypatch):
        t = [0.0]
        monkeypatch.setattr(spam.time, "monotonic", lambda: t[0])
        for _ in range(SPAM_MAX_MSGS):
            assert _check_spam(1) == "ok"
        t[0] += 11  # jump past the 10s window
        assert _check_spam(1) == "ok"

    def test_reset_spam(self):
        for _ in range(SPAM_MAX_MSGS + 1):
            _check_spam(1)
        _reset_spam(1)
        assert _check_spam(1) == "ok"


class TestEncryption:
    def test_roundtrip(self):
        secret = "SB6EXAMPLESECRETKEY123"
        blob = encryption.encrypt_key(secret)
        assert blob != secret
        assert encryption.decrypt_key(blob) == secret

    def test_ciphertext_differs_from_plaintext(self):
        blob = encryption.encrypt_key("SABC")
        assert "SABC" not in blob

    def test_decrypt_garbage_raises(self):
        with pytest.raises(Exception):
            encryption.decrypt_key("not-a-fernet-token")

    def test_missing_key_raises(self, monkeypatch):
        monkeypatch.setattr(encryption, "_cipher", None)
        monkeypatch.setattr(encryption, "WALLET_ENCRYPTION_KEY", None)
        with pytest.raises(ValueError):
            encryption.get_cipher()
        monkeypatch.setattr(encryption, "WALLET_ENCRYPTION_KEY",
                            "FlOtZfckJ2iKvP5o1HFQz8XwtA6EYp5cvKCqj-yrg18=")
        monkeypatch.setattr(encryption, "_cipher", None)
        assert encryption.get_cipher() is not None


class TestStellar:
    def test_secret_key_match(self):
        kp = Keypair.random()
        assert verify_secret_key_match(kp.public_key, kp.secret) is True

    def test_secret_key_mismatch(self):
        assert verify_secret_key_match(Keypair.random().public_key,
                                       Keypair.random().secret) is False

    def test_invalid_secret_returns_false(self):
        assert verify_secret_key_match("GABC", "not-a-key") is False
        assert verify_secret_key_match("GABC", "") is False

    def test_get_balances_error_returns_empty(self, monkeypatch):
        import services.stellar as stellar
        monkeypatch.setattr(stellar, "Server",
                            MagicMock(side_effect=Exception("network down")))
        assert get_balances("GABC") == []

    def test_get_balances_success(self, monkeypatch):
        import services.stellar as stellar
        fake_server = MagicMock()
        fake_server.accounts.return_value.account_id.return_value.call.return_value = {
            "balances": [{"asset_type": "native", "balance": "12.5"}]
        }
        monkeypatch.setattr(stellar, "Server", MagicMock(return_value=fake_server))
        assert get_balances("GABC") == [{"asset_type": "native", "balance": "12.5"}]


ADDR = "G" + "A" * 55


def _make_watcher(payment_records, tx_memo):
    """Watcher with Horizon + httpx stubbed out."""
    watcher = StellarWatcher(make_bot())
    watcher.server = MagicMock()
    (watcher.server.payments.return_value
        .for_account.return_value
        .order.return_value
        .limit.return_value
        .call.return_value) = {"_embedded": {"records": payment_records}}
    return watcher


def _payment_record():
    return {"type": "payment",
            "_links": {"transaction": {"href": "https://horizon/tx/1"}}}


class _FakeAsyncClient:
    """Stands in for httpx.AsyncClient; returns a fixed tx JSON."""
    def __init__(self, memo, **kwargs):
        self._memo = memo

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json = MagicMock(return_value={"memo": self._memo})
        return resp


class TestWatcher:
    async def test_no_pending_short_circuits(self):
        watcher = _make_watcher([], "")
        await watcher._check_payments()
        watcher.server.payments.assert_not_called()

    async def test_memo_match_verifies_and_notifies(self, monkeypatch):
        import services.watcher as watcher_mod
        db_add_wallet(1, ADDR)
        db_create_verification(1, ADDR, "123456")

        watcher = _make_watcher([_payment_record()], "123456")
        monkeypatch.setattr(watcher_mod.httpx, "AsyncClient",
                            lambda **kw: _FakeAsyncClient("123456"))
        await watcher._check_payments()

        assert db_get_user_wallets(1)[0]["verified"] == 1  # method 1 = memo
        assert db_get_pending_verifications() == []
        watcher.bot.send_message.assert_awaited_once()
        assert watcher.bot.send_message.await_args.args[0] == 1

    async def test_wrong_memo_ignored(self, monkeypatch):
        import services.watcher as watcher_mod
        db_add_wallet(1, ADDR)
        db_create_verification(1, ADDR, "123456")

        watcher = _make_watcher([_payment_record()], "999999")
        monkeypatch.setattr(watcher_mod.httpx, "AsyncClient",
                            lambda **kw: _FakeAsyncClient("999999"))
        await watcher._check_payments()

        assert db_get_user_wallets(1)[0]["verified"] == 0
        assert len(db_get_pending_verifications()) == 1

    async def test_expired_verification_skipped(self, fresh_db, monkeypatch):
        db_add_wallet(1, ADDR)
        past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        fresh_db.execute(
            "INSERT INTO wallet_verifications (user_id, address, challenge, expires_at) "
            "VALUES (?,?,?,?)", (1, ADDR, "123456", past))
        fresh_db.commit()

        watcher = _make_watcher([_payment_record()], "123456")
        await watcher._check_payments()
        # Expired → no Horizon call at all, wallet stays unverified
        watcher.server.payments.assert_not_called()
        assert db_get_user_wallets(1)[0]["verified"] == 0

    async def test_naive_expiry_treated_as_utc(self, fresh_db, monkeypatch):
        import services.watcher as watcher_mod
        db_add_wallet(1, ADDR)
        naive_future = (datetime.now(timezone.utc) + timedelta(minutes=10)) \
            .replace(tzinfo=None).isoformat()
        fresh_db.execute(
            "INSERT INTO wallet_verifications (user_id, address, challenge, expires_at) "
            "VALUES (?,?,?,?)", (1, ADDR, "123456", naive_future))
        fresh_db.commit()

        watcher = _make_watcher([_payment_record()], "123456")
        monkeypatch.setattr(watcher_mod.httpx, "AsyncClient",
                            lambda **kw: _FakeAsyncClient("123456"))
        await watcher._check_payments()
        assert db_get_user_wallets(1)[0]["verified"] == 1

    async def test_non_payment_records_skipped(self, monkeypatch):
        import services.watcher as watcher_mod
        db_add_wallet(1, ADDR)
        db_create_verification(1, ADDR, "123456")

        watcher = _make_watcher([{"type": "create_account"}], "123456")
        monkeypatch.setattr(watcher_mod.httpx, "AsyncClient",
                            lambda **kw: _FakeAsyncClient("123456"))
        await watcher._check_payments()
        assert db_get_user_wallets(1)[0]["verified"] == 0

    async def test_horizon_failure_is_contained(self):
        db_add_wallet(1, ADDR)
        db_create_verification(1, ADDR, "123456")
        watcher = StellarWatcher(make_bot())
        watcher.server = MagicMock()
        watcher.server.payments.side_effect = Exception("horizon down")
        await watcher._check_payments()  # must not raise
        assert len(db_get_pending_verifications()) == 1

    def test_stop_flag(self):
        watcher = StellarWatcher(make_bot())
        watcher.is_running = True
        watcher.stop()
        assert watcher.is_running is False
