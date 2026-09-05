#!/usr/bin/env python3
"""Live HTTP publishing acceptance test against the public AI HOT API.

The script uses a temporary API-Hub database, starts the real FastAPI proxy on a
local TCP port, creates an interface and a short-lived caller key through the
management API, then traverses two cursor pages through /proxy/<slug>.
"""
from __future__ import annotations

import json
import socket
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
import uvicorn
from fastapi import FastAPI


AIHOT_URL = "https://aihot.virxact.com/api/public/items"
AIHOT_USER_AGENT = "openontology-api-hub-live-e2e/1.0"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def main() -> int:
    backend_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(backend_root))

    from app.api_hub import config, db
    from app.api_hub.routers import http_proxy, interfaces

    with tempfile.TemporaryDirectory(prefix="api-hub-http-live-e2e-") as temp_root:
        root = Path(temp_root)
        config.DB_PATH = root / "api_hub.db"
        config.SESSION_PATH = root / "w3_session.json"
        config.HTTP_TIMEOUT = 30
        db.init_db()

        app = FastAPI()
        app.include_router(interfaces.router)
        app.include_router(http_proxy.admin_router)
        app.include_router(http_proxy.public_router)

        port = _free_port()
        server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
        )
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        deadline = time.time() + 10
        while not server.started and time.time() < deadline:
            time.sleep(0.05)
        if not server.started:
            raise RuntimeError("temporary API-Hub proxy did not start")

        base = f"http://127.0.0.1:{port}"
        report = {"ok": False, "upstream": AIHOT_URL, "proxy": {}, "audit": {}}
        try:
            created = requests.post(
                base + "/interfaces",
                json={
                    "name": "AI HOT live HTTP proxy E2E",
                    "description": "Temporary public cursor-pagination acceptance test",
                    "group_name": "端到端测试",
                    "method": "GET",
                    "url": AIHOT_URL,
                    "query_params": [
                        {"key": "mode", "value": "all"},
                        {"key": "take", "value": "2"},
                    ],
                    "headers": [{"key": "User-Agent", "value": AIHOT_USER_AGENT}],
                    "http_enabled": True,
                    "proxy_slug": "aihot-live-e2e",
                    "proxy_query_keys": ["mode", "take", "cursor"],
                    "proxy_header_keys": ["X-Trace-ID"],
                    "proxy_body_enabled": False,
                },
                timeout=10,
            )
            created.raise_for_status()
            interface_id = int(created.json()["id"])

            key_response = requests.post(
                base + "/proxy/keys",
                json={
                    "name": "AI HOT live E2E caller",
                    "enabled": True,
                    "expires_at": (
                        datetime.now(timezone.utc) + timedelta(minutes=10)
                    ).isoformat(),
                    "scope_all": False,
                    "interface_ids": [interface_id],
                },
                timeout=10,
            )
            key_response.raise_for_status()
            secret = key_response.json()["secret"]
            headers = {
                config.PROXY_KEY_HEADER: secret,
                "X-Trace-ID": "live-e2e-page-1",
            }

            first = requests.get(
                base + "/proxy/aihot-live-e2e",
                params={"mode": "all", "take": "2"},
                headers=headers,
                timeout=40,
            )
            first.raise_for_status()
            first_payload = first.json()
            first_items = first_payload.get("items") or []
            cursor = first_payload.get("nextCursor")
            if len(first_items) != 2 or not cursor:
                raise RuntimeError("first page did not contain two rows and a nextCursor")

            headers["X-Trace-ID"] = "live-e2e-page-2"
            second = requests.get(
                base + "/proxy/aihot-live-e2e",
                params={"mode": "all", "take": "2", "cursor": cursor},
                headers=headers,
                timeout=40,
            )
            second.raise_for_status()
            second_payload = second.json()
            second_items = second_payload.get("items") or []
            if len(second_items) != 2:
                raise RuntimeError("second page did not contain two rows")

            with db.get_conn() as conn:
                runs = conn.execute(
                    "SELECT ok, status_code, source, proxy_key_name, request_snapshot "
                    "FROM runs WHERE interface_id = ? ORDER BY id",
                    (interface_id,),
                ).fetchall()
                key_row = conn.execute(
                    "SELECT last_used_at FROM proxy_keys ORDER BY id DESC LIMIT 1"
                ).fetchone()
            if len(runs) != 2 or any(
                not row["ok"]
                or row["status_code"] != 200
                or row["source"] != "http_proxy"
                for row in runs
            ):
                raise RuntimeError("proxy audit does not prove both live calls succeeded")
            if any(secret in row["request_snapshot"] for row in runs):
                raise RuntimeError("proxy secret leaked into the request audit")
            if not key_row or not key_row["last_used_at"]:
                raise RuntimeError("proxy key usage timestamp was not updated")

            report["proxy"] = {
                "endpoint": "/proxy/aihot-live-e2e",
                "firstPageRows": len(first_items),
                "secondPageRows": len(second_items),
                "cursorTraversal": True,
                "statusCodes": [first.status_code, second.status_code],
            }
            report["audit"] = {
                "runCount": len(runs),
                "source": "http_proxy",
                "caller": runs[0]["proxy_key_name"],
                "keyLastUsedRecorded": True,
                "secretPersisted": False,
            }
            report["ok"] = True
        except Exception as exc:
            report["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            server.should_exit = True
            thread.join(timeout=5)

        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
