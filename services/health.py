"""Minimal HTTP health endpoint (stdlib only). Enabled when HEALTH_PORT is set.

GET anything → 200 with JSON: status, user count, schema version, uptime.
For systemd watchdogs, uptime monitors, or a future dashboard."""
import asyncio
import json
import logging
import time

log = logging.getLogger("nopmsbot")
_start_time = time.monotonic()

def _health_payload() -> dict:
    from database.users import db_user_count
    from database.settings import db_get_setting
    return {
        "status": "ok",
        "users": db_user_count(),
        "schema_version": int(db_get_setting("schema_version", "0")),
        "uptime_seconds": int(time.monotonic() - _start_time),
    }

async def _handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        await asyncio.wait_for(reader.read(1024), timeout=5)
        try:
            body = json.dumps(_health_payload())
            status = "200 OK"
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

async def start_health_server(port: int, host: str = "0.0.0.0") -> asyncio.AbstractServer:
    server = await asyncio.start_server(_handle, host, port)
    log.info("Health endpoint listening on %s:%s", host, port)
    return server
