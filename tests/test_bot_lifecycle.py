"""Tests for bot.py's shutdown drain behavior — post_shutdown must let an
in-flight scheduled broadcast finish sending before cancelling its task,
since the broadcast is already marked "sent" in the DB (at-most-once) before
delivery starts (M9, docs/AUDIT-2026-07-10.md)."""
import asyncio
from types import SimpleNamespace

import bot


async def _dummy_task():
    await asyncio.sleep(100)


def _make_app(broadcast_idle, bg_task):
    return SimpleNamespace(bot_data={
        "watcher": None,
        "broadcast_idle": broadcast_idle,
        "bg_tasks": [bg_task],
        "health_server": None,
    })


class TestPostShutdown:
    async def test_shuts_down_immediately_when_broadcast_idle(self):
        idle = asyncio.Event()
        idle.set()
        task = asyncio.create_task(_dummy_task())
        app = _make_app(idle, task)

        await asyncio.wait_for(bot.post_shutdown(app), timeout=1)

        assert task.cancelled()

    async def test_waits_for_in_flight_broadcast_before_cancelling(self):
        idle = asyncio.Event()  # starts clear — simulates a send in flight
        task = asyncio.create_task(_dummy_task())
        app = _make_app(idle, task)

        async def _finish_soon():
            await asyncio.sleep(0.05)
            idle.set()
        asyncio.create_task(_finish_soon())

        await asyncio.wait_for(bot.post_shutdown(app), timeout=1)

        assert idle.is_set()
        assert task.cancelled()  # still gets cancelled once the drain completes

    async def test_drain_timeout_still_cancels(self, monkeypatch):
        monkeypatch.setattr(bot, "BROADCAST_DRAIN_TIMEOUT", 0.05)
        idle = asyncio.Event()  # never set — simulates a stuck send
        task = asyncio.create_task(_dummy_task())
        app = _make_app(idle, task)

        await asyncio.wait_for(bot.post_shutdown(app), timeout=1)

        assert task.cancelled()  # timeout must not leave the task running forever
