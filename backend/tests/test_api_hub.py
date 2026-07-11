import json

import pytest
import requests
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api_hub import config, db
from app.api_hub.routers import backup, credential, interfaces, mcp


@pytest.fixture
def hub_client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "api_hub.db")
    monkeypatch.setattr(config, "SESSION_PATH", tmp_path / "w3_session.json")
    db.init_db()
    app = FastAPI()
    app.include_router(credential.router)
    app.include_router(interfaces.router)
    app.include_router(interfaces.runs_router)
    app.include_router(backup.router)
    app.include_router(mcp.router)
    return TestClient(app)


def _interface(**overrides):
    payload = {
        "name": "健康检查",
        "description": "检查上游服务是否可用",
        "group_name": "基础服务",
        "method": "GET",
        "url": "https://service.example/health",
        "query_params": [{"key": "verbose", "value": "1"}],
        "headers": [{"key": "X-Trace", "value": "test"}],
        "body_type": "none",
        "body_content": "",
        "use_w3": False,
        "mcp_enabled": True,
        "open_enabled": True,
    }
    payload.update(overrides)
    return payload


def test_interface_crud_group_and_auth_boundary(hub_client):
    created = hub_client.post("/interfaces", json=_interface())
    assert created.status_code == 200
    item = created.json()
    assert item["id"] > 0
    assert item["open_enabled"] is True

    listed = hub_client.get("/interfaces").json()
    assert [row["name"] for row in listed] == ["健康检查"]

    updated = hub_client.put(
        f"/interfaces/{item['id']}",
        json=_interface(name="上游健康检查", method="POST"),
    )
    assert updated.json()["method"] == "POST"

    moved = hub_client.post(
        "/interfaces/groups/delete", json={"group_name": "基础服务"}
    )
    assert moved.json() == {"ok": True, "count": 1}
    assert hub_client.get(f"/interfaces/{item['id']}").json()["group_name"] == ""

    # The host application adds JWT protection to every management route.
    from app.main import app as platform_app

    response = TestClient(platform_app).get("/api/api-hub/interfaces")
    assert response.status_code == 403


def test_run_history_and_response_capture(hub_client, monkeypatch):
    item = hub_client.post("/interfaces", json=_interface()).json()

    def fake_request(session, method, url, **kwargs):
        response = requests.Response()
        response.status_code = 200
        response.url = url
        response.headers["Content-Type"] = "application/json"
        response._content = json.dumps({"ok": True, "method": method}).encode()
        response.encoding = "utf-8"
        return response

    monkeypatch.setattr(requests.Session, "request", fake_request)
    result = hub_client.post(f"/interfaces/{item['id']}/run")
    assert result.status_code == 200
    assert result.json()["ok"] is True
    assert result.json()["status_code"] == 200

    history = hub_client.get("/runs", params={"keyword": "健康"}).json()
    assert history["total"] == 1
    summary = history["items"][0]
    detail = hub_client.get(
        f"/interfaces/{item['id']}/runs/{summary['id']}"
    ).json()
    assert detail["request_snapshot"]["url"] == item["url"]
    assert json.loads(detail["response_body"])["ok"] is True


def test_backup_round_trip_skips_duplicates(hub_client):
    item = hub_client.post("/interfaces", json=_interface()).json()
    exported = hub_client.post(
        "/backup/export",
        json={"name": "review-copy", "mode": "full", "ids": []},
    )
    assert exported.status_code == 200
    payload = exported.json()
    assert payload["app"] == "API-Hub"
    assert payload["interface_count"] == 1

    duplicate = hub_client.post("/backup/import", json=payload).json()
    assert duplicate["imported"] == 0
    assert duplicate["skipped"] == 1

    assert hub_client.delete(f"/interfaces/{item['id']}").status_code == 200
    restored = hub_client.post("/backup/import", json=payload).json()
    assert restored["imported"] == 1
    assert hub_client.get("/interfaces").json()[0]["name"] == "健康检查"
