"""Minimal HTTP health endpoint (stdlib only). Enabled when HEALTH_PORT is set.

GET anything → 200 with JSON: status, user count, schema version, uptime,
heartbeat age. 503 if the heartbeat is stale (event loop stuck/frozen — the
DB-only check this used to be would report "ok" even then, since a stuck
loop never gets a chance to run a query in the first place; M11,
docs/AUDIT-2026-07-10.md). For systemd watchdogs, uptime monitors, or a
future dashboard."""
import asyncio
import json
import logging
import time

log = logging.getLogger("nopmsbot")
_start_time = time.monotonic()
_last_heartbeat = time.monotonic()

# bot.py's heartbeat loop calls mark_heartbeat() every HEARTBEAT_INTERVAL
# seconds; if the age exceeds this, the event loop has stalled (e.g. a
# blocking call that got past the C3-style async guards) even though the
# process itself is still alive and would otherwise look "ok".
STALE_AFTER_SECONDS = 45

def mark_heartbeat() -> None:
    global _last_heartbeat
    _last_heartbeat = time.monotonic()

def _health_payload() -> dict:
    from database.users import db_user_count
    from database.settings import db_get_setting
    heartbeat_age = time.monotonic() - _last_heartbeat
    return {
        "status": "ok" if heartbeat_age <= STALE_AFTER_SECONDS else "stale",
        "users": db_user_count(),
        "schema_version": int(db_get_setting("schema_version", "0")),
        "uptime_seconds": int(time.monotonic() - _start_time),
        "heartbeat_age_seconds": int(heartbeat_age),
    }

async def _handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        await asyncio.wait_for(reader.read(1024), timeout=5)
        try:
            payload = _health_payload()
            body = json.dumps(payload)
            status = "200 OK" if payload["status"] == "ok" else "503 Service Unavailable"
        except Exception as e:
            body = json.dumps({"status": "error", "detail": str(e)})
            status = "500 Internal Server Error"
        writer.write(
            (f"HTTP/1.1 {status}\r\n"
             f"Content-Type: application/json\r\n"
             f"Content-Length: {len(body)}\r\n"
             f"Connection: close\r\n\r\n{body}").encode()
        )
        await writer.drain()
    except Exception:
        pass
    finally:
        try:
            writer.close()
        except Exception:
            pass

async def start_health_server(port: int, host: str = "127.0.0.1") -> asyncio.AbstractServer:
    server = await asyncio.start_server(_handle, host, port)
    log.info("Health endpoint listening on %s:%s", host, port)
    return server
