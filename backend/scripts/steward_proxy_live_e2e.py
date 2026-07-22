#!/usr/bin/env python3
"""Live n8n -> revision-pinned API Hub proxy -> AI HOT acceptance test.

The test creates a temporary API Hub database, ephemeral proxy bearer token and
localtunnel endpoint.  It then creates and activates a uniquely named n8n
workflow, verifies two cursor pages traversed the proxy, and deletes all remote
and local test state in ``finally``.  Long-lived credentials are never written
to workflow JSON or disk.
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import queue
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import requests
import uvicorn
from fastapi import FastAPI


AIHOT_URL = "https://aihot.virxact.com/api/public/items"
AIHOT_USER_AGENT = "aihot-skill/0.3.4 (+https://aihot.virxact.com/aihot-skill/)"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _read_tunnel_url(process: subprocess.Popen[str], timeout: int = 90) -> str:
    lines: queue.Queue[str] = queue.Queue()

    def reader() -> None:
        if process.stdout:
            for line in process.stdout:
                lines.put(line.strip())

    threading.Thread(target=reader, daemon=True).start()
    deadline = time.time() + timeout
    seen: list[str] = []
    while time.time() < deadline:
        if process.poll() is not None:
            raise RuntimeError("localtunnel exited before publishing a URL: " + " | ".join(seen[-5:]))
        try:
            line = lines.get(timeout=1)
        except queue.Empty:
            continue
        if line:
            seen.append(line)
        marker = "your url is:"
        if marker in line.lower():
            return line.split(":", 1)[1].strip().rstrip("/")
    raise RuntimeError("localtunnel did not publish a URL within 90 seconds")


def _workflow(proxy_url: str, proxy_token: str, name: str, webhook_path: str) -> dict:
    headers = {
        "parameters": [
            {"name": "Authorization", "value": f"Bearer {proxy_token}"},
            {"name": "Bypass-Tunnel-Reminder", "value": "true"},
        ]
    }
    webhook = {
        "id": str(uuid.uuid4()),
        "name": "Webhook",
        "type": "n8n-nodes-base.webhook",
        "typeVersion": 2,
        "position": [0, 300],
        "parameters": {"httpMethod": "POST", "path": webhook_path, "options": {}},
        "webhookId": str(uuid.uuid4()),
    }
    page1 = {
        "id": str(uuid.uuid4()),
        "name": "代理取第1页",
        "type": "n8n-nodes-base.httpRequest",
        "typeVersion": 4.2,
        "position": [280, 300],
        "parameters": {
            "method": "POST",
            "url": proxy_url,
            "sendHeaders": True,
            "headerParameters": headers,
            "sendBody": True,
            "contentType": "json",
            "specifyBody": "keypair",
            "bodyParameters": {"parameters": [
                {"name": "interface_revision", "value": 1},
                {"name": "query.mode", "value": "all"},
                {"name": "query.take", "value": "2"},
            ]},
            "options": {},
        },
    }
    page2 = {
        "id": str(uuid.uuid4()),
        "name": "代理取第2页",
        "type": "n8n-nodes-base.httpRequest",
        "typeVersion": 4.2,
        "position": [560, 300],
        "parameters": {
            "method": "POST",
            "url": proxy_url,
            "sendHeaders": True,
            "headerParameters": headers,
            "sendBody": True,
            "contentType": "json",
            "specifyBody": "keypair",
            "bodyParameters": {"parameters": [
                {"name": "interface_revision", "value": 1},
                {"name": "query.mode", "value": "all"},
                {"name": "query.take", "value": "2"},
                {"name": "query.cursor", "value": "={{ $json.nextCursor }}"},
            ]},
            "options": {},
        },
    }
    output = {
        "id": str(uuid.uuid4()),
        "name": "合并代理分页结果",
        "type": "n8n-nodes-base.code",
        "typeVersion": 2,
        "position": [840, 300],
        "parameters": {"jsCode": (
            "const firstPage = $('代理取第1页').first().json;\n"
            "const secondPage = $input.first().json;\n"
            "const rows = [...(firstPage.items || []), ...(secondPage.items || [])];\n"
            "return rows.map(row => ({ json: { id: row.id, title: row.title, "
            "source: row.source, publishedAt: row.publishedAt } }));"
        )},
    }
    return {
        "name": name,
        "nodes": [webhook, page1, page2, output],
        "connections": {
            "Webhook": {"main": [[{"node": "代理取第1页", "type": "main", "index": 0}]]},
            "代理取第1页": {"main": [[{"node": "代理取第2页", "type": "main", "index": 0}]]},
            "代理取第2页": {"main": [[{"node": "合并代理分页结果", "type": "main", "index": 0}]]},
        },
        "settings": {"executionOrder": "v1"},
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--n8n-api-url", default=os.getenv("N8N_API_URL", ""))
    args = parser.parse_args()
    n8n_api_url = args.n8n_api_url.strip()
    if not n8n_api_url:
        raise SystemExit("--n8n-api-url or N8N_API_URL is required")
    n8n_key = os.getenv("N8N_API_KEY", "").strip() or getpass.getpass("N8N_API_KEY: ").strip()
    if not n8n_key:
        raise SystemExit("N8N_API_KEY is required")

    temp_root = Path(tempfile.mkdtemp(prefix="steward-proxy-live-e2e-"))
    proxy_token = secrets.token_urlsafe(36)
    os.environ["API_HUB_DATA_DIR"] = str(temp_root / "api-hub")
    os.environ["API_HUB_SYSTEM_MCP_TOKEN"] = proxy_token
    os.environ["API_HUB_INTERNAL_PROXY_TOKEN"] = proxy_token

    backend_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(backend_root))
    from app.api_hub import db as api_hub_db
    from app.api_hub.routers.proxy import internal_router as proxy_router
    from app.data_channel.steward.runner import trigger_and_collect
    from app.settings.workflows.n8n_client import N8nClient

    api_hub_db.init_db()
    now = datetime.now(timezone.utc).isoformat()
    with api_hub_db.get_conn() as conn:
        cursor = conn.execute(
            "INSERT INTO interfaces(name, description, group_name, method, url, query_params, headers, "
            "body_type, body_content, use_w3, mcp_enabled, open_enabled, parameter_schema, "
            "created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "AI HOT live proxy E2E", "Temporary legal public API acceptance test",
                "数据管家测试", "GET", AIHOT_URL,
                json.dumps([{"key": "mode", "value": "all"}, {"key": "take", "value": "2"}]),
                json.dumps([{"key": "User-Agent", "value": AIHOT_USER_AGENT}]),
                "none", "", 0, 0, 0,
                json.dumps([
                    {"name": "mode", "location": "query", "value_type": "string", "dynamic": True},
                    {"name": "take", "location": "query", "value_type": "integer", "dynamic": True},
                    {"name": "cursor", "location": "query", "value_type": "string", "dynamic": True},
                ]),
                now, now,
            ),
        )
        interface_id = int(cursor.lastrowid)

    port = _free_port()
    app = FastAPI()
    app.include_router(proxy_router)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    server_thread = threading.Thread(target=server.run, daemon=True)
    tunnel: subprocess.Popen[str] | None = None
    client = N8nClient(n8n_api_url, n8n_key, timeout_seconds=30)
    workflow_id: str | None = None
    report = {"ok": False, "proxy": {}, "n8n": {}, "cleanup": {"workflowDeleted": False}}

    try:
        server_thread.start()
        deadline = time.time() + 15
        while not server.started and time.time() < deadline:
            time.sleep(0.1)
        if not server.started:
            raise RuntimeError("temporary proxy server did not start")

        tunnel = subprocess.Popen(
            ["npx", "--yes", "localtunnel", "--port", str(port)],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        public_root = _read_tunnel_url(tunnel)
        proxy_url = f"{public_root}/api-hub/internal/interfaces/{interface_id}/invoke"
        public_response = requests.post(
            proxy_url,
            json={
                "interface_revision": 1,
                "query": {"mode": "all", "take": "2"},
            },
            headers={
                "Authorization": f"Bearer {proxy_token}",
                "Bypass-Tunnel-Reminder": "true",
            },
            timeout=30,
        )
        public_response.raise_for_status()
        public_payload = public_response.json()
        if len(public_payload.get("items") or []) != 2 or not public_payload.get("nextCursor"):
            raise RuntimeError("public proxy did not return the expected first AI HOT page")
        report["proxy"] = {
            "publicReachable": True,
            "status": public_response.status_code,
            "firstPageRows": len(public_payload["items"]),
            "hasNextCursor": True,
        }

        suffix = uuid.uuid4().hex[:8]
        webhook_path = f"steward-proxy-live-e2e-{suffix}"
        created = client.create_workflow(_workflow(
            proxy_url, proxy_token, f"OB-PROXY-LIVE-E2E-{suffix}", webhook_path))
        workflow_id = str(created["id"])
        client.activate_workflow(workflow_id)
        rows, execution = trigger_and_collect(
            client, workflow_id, webhook_path, payload={"e2e": True}, wait_seconds=60,
            expected_output_node="合并代理分页结果",
        )
        expected_columns = {"id", "title", "source", "publishedAt"}
        if len(rows) != 4 or any(set(row) != expected_columns for row in rows):
            raise RuntimeError(f"unexpected proxy pagination output: {len(rows)} rows")
        with api_hub_db.get_conn() as conn:
            run_rows = conn.execute(
                "SELECT ok, status_code FROM runs WHERE interface_id = ? ORDER BY id", (interface_id,)
            ).fetchall()
        if len(run_rows) < 3 or any(not row["ok"] or row["status_code"] != 200 for row in run_rows):
            raise RuntimeError("API Hub run audit does not prove all proxy calls succeeded")
        report["n8n"] = {
            "workflowCreated": True,
            "activationConfirmed": True,
            "executionId": execution.get("execution_id"),
            "status": execution.get("execution_status"),
            "rowCount": len(rows),
            "columns": sorted(expected_columns),
            "proxyAuditRuns": len(run_rows),
        }
        report["ok"] = True
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        if client is not None and workflow_id:
            try:
                executions = client.list_executions(
                    workflow_id=workflow_id, limit=3, include_data=True
                )
                if executions:
                    detail = client.get_execution(
                        str(executions[0]["id"]), include_data=True
                    )
                    result_data = (detail.get("data") or {}).get("resultData") or {}
                    error = result_data.get("error") or {}
                    report["n8nDebug"] = {
                        "status": detail.get("status"),
                        "lastNodeExecuted": result_data.get("lastNodeExecuted"),
                        "errorMessage": error.get("message"),
                        "errorDescription": error.get("description"),
                        "errorNode": (error.get("node") or {}).get("name"),
                    }
            except Exception as debug_exc:
                report["n8nDebug"] = {"error": str(debug_exc)}
    finally:
        if workflow_id:
            try:
                current = client.get_workflow(workflow_id)
                if current.get("active"):
                    client.deactivate_workflow(workflow_id)
                client.delete_workflow(workflow_id)
                report["cleanup"]["workflowDeleted"] = True
            except Exception as exc:
                report["cleanup"]["error"] = str(exc)
        if tunnel:
            tunnel.terminate()
            try:
                tunnel.wait(timeout=5)
            except subprocess.TimeoutExpired:
                tunnel.kill()
        server.should_exit = True
        server_thread.join(timeout=10)
        shutil.rmtree(temp_root, ignore_errors=True)

    report["cleanup"]["tempRemoved"] = not temp_root.exists()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] and report["cleanup"].get("workflowDeleted") else 1


if __name__ == "__main__":
    raise SystemExit(main())
