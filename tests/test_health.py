"""Tests for the HTTP health endpoint."""
import json

import httpx

from database.users import db_upsert_user
from services.health import start_health_server
from tests.conftest import make_tg_user


async def _get(url: str) -> httpx.Response:
    async with httpx.AsyncClient() as client:
        return await client.get(url)


class TestHealthEndpoint:
    async def test_returns_ok_json(self):
        db_upsert_user(make_tg_user(1))
        db_upsert_user(make_tg_user(2))
        server = await start_health_server(0, host="127.0.0.1")  # ephemeral port
        try:
            port = server.sockets[0].getsockname()[1]
            resp = await _get(f"http://127.0.0.1:{port}/health")
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "ok"
            assert data["users"] == 2
            assert data["schema_version"] >= 10
            assert data["uptime_seconds"] >= 0
        finally:
            server.close()

    async def test_any_path_answers(self):
        server = await start_health_server(0, host="127.0.0.1")
        try:
            port = server.sockets[0].getsockname()[1]
            resp = await _get(f"http://127.0.0.1:{port}/anything")
            assert resp.status_code == 200
        finally:
            server.close()
