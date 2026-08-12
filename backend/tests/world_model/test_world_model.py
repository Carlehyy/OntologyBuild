"""世界模型域测试 — JKG 边界（execute_code）一律 mock，测试不起真实内核。"""
from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException

from app.auth.models import RoleMenuPermission, User
from app.services.auth_service import hash_password
from app.world_model import service
from app.world_model.models import WorldModelCallRecord

BASE = "/api/v2/world-model"


# ──────────────────────────── 工具与夹具 ────────────────────────────


def _make_user(db, username: str, role: str) -> User:
    user = User(
        id=str(uuid.uuid4()),
        username=username,
        email=f"{username}@test.com",
        password_hash=hash_password("test123"),
        role=role,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _login(client, username: str) -> dict:
    r = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": "test123"},
    )
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['data']['access_token']}"}


@pytest.fixture
def viewer_headers(client, db):
    _make_user(db, "wm_viewer", "viewer")
    return _login(client, "wm_viewer")


@pytest.fixture
def custom_headers(client, db):
    """custom 角色默认只有 overview 菜单，用于验证 403。"""
    _make_user(db, "wm_custom", "custom")
    return _login(client, "wm_custom")


@pytest.fixture
def project(client, auth_headers):
    r = client.post(
        f"{BASE}/projects",
        json={"name": "负荷推演", "description": "台区负荷短期推演",
              "engine_type": "statistical"},
        headers=auth_headers,
    )
    assert r.status_code == 201, r.text
    return r.json()["data"]


class _FakeExecution:
    """execute_code 的最小替身：error 为空即成功。"""

    def __init__(self, *, stdout="", error=None, traceback="", duration_ms=12):
        self.rows = []
        self.stdout = stdout
        self.error = error
        self.traceback = traceback
        self.duration_ms = duration_ms
        self.kernel_id = "fake-kernel"


def _fake_execute_ok(code, **kwargs):
    assert "simulate" in code  # 收尾代码应注入 simulate 调用
    return _FakeExecution(
        stdout='log\n__OB_RESULT_BEGIN__\n{"trajectory": [1, 2]}\n'
               "__OB_RESULT_END__\n")


def _fake_execute_fail(code, **kwargs):
    return _FakeExecution(
        stdout="boom", error="脚本执行失败（ValueError）：bad",
        traceback="ValueError: bad")


# ──────────────────────────── 项目 CRUD ────────────────────────────


def test_project_crud_flow(client, auth_headers, project):
    pid = project["id"]
    assert project["status"] == "draft"
    assert "def simulate(" in project["script"]  # 初始化为契约模板

    r = client.get(f"{BASE}/projects", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["data"]["total"] == 1

    r = client.get(
        f"{BASE}/projects", params={"keyword": "负荷"}, headers=auth_headers)
    assert r.json()["data"]["total"] == 1
    r = client.get(
        f"{BASE}/projects", params={"keyword": "不存在"}, headers=auth_headers)
    assert r.json()["data"]["total"] == 0
    r = client.get(
        f"{BASE}/projects", params={"engine_type": "mechanistic"},
        headers=auth_headers)
    assert r.json()["data"]["total"] == 0
    r = client.get(
        f"{BASE}/projects", params={"engine_type": "bogus"},
        headers=auth_headers)
    assert r.status_code == 400

    r = client.patch(
        f"{BASE}/projects/{pid}",
        json={"name": "负荷推演-v2", "engine_type": "mechanistic"},
        headers=auth_headers,
    )
    assert r.status_code == 200
    assert r.json()["data"]["name"] == "负荷推演-v2"
    assert r.json()["data"]["engine_type"] == "mechanistic"

    r = client.delete(f"{BASE}/projects/{pid}", headers=auth_headers)
    assert r.status_code == 200
    r = client.get(f"{BASE}/projects/{pid}", headers=auth_headers)
    assert r.status_code == 404


def test_projects_require_world_model_menu(client, custom_headers):
    r = client.get(f"{BASE}/projects", headers=custom_headers)
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "MENU_ACCESS_DENIED"


def test_editor_role_sees_world_model_by_default(client, viewer_headers):
    """无显式授权记录的角色走默认全集（除 api_hub），世界模型默认可见。"""
    r = client.get(f"{BASE}/projects", headers=viewer_headers)
    assert r.status_code == 200


def test_world_model_group_requires_child_after_normalize(db):
    """GROUP_MENU_KEYS 归一化：world_model 父 key 需至少一个子 key 才能保留。

    世界模型提升为一级导航分组（models/calls 子 key）；本体管理恢复单项，
    不再受分组归一化约束；旧 ontologies.* 子 key 已失效，归一化直接滤除。
    """
    from app.auth.permissions import normalize_menu_keys

    assert "world_model" not in normalize_menu_keys(["world_model"])
    assert "world_model" in normalize_menu_keys(
        ["world_model", "world_model.models"])
    # 只有子 key 时自动补父 key
    assert "world_model" in normalize_menu_keys(["world_model.calls"])
    assert "world_model.models" in normalize_menu_keys(["world_model.models"])
    # 本体管理恢复单项：独立保留
    assert "ontologies" in normalize_menu_keys(["ontologies"])
    # 旧 key 已不在 ALL_MENU_KEYS，归一化滤除
    assert "ontologies.library" not in normalize_menu_keys(["ontologies.library"])
    assert "ontologies.world_model" not in normalize_menu_keys(
        ["ontologies.world_model"])


def test_role_record_with_world_model_keys_grants_access(db):
    """存量授权记录持有一级 world_model 组 key 时，访问世界模型域恢复。"""
    db.add(RoleMenuPermission(
        role="editor",
        menu_keys=["ontologies", "world_model", "world_model.models",
                   "world_model.calls"],
        updated_by="test",
    ))
    db.commit()
    from app.auth.permissions import get_role_menu_keys

    keys = get_role_menu_keys(db, "editor")
    assert "ontologies" in keys
    assert "world_model" in keys
    assert "world_model.models" in keys
    assert "world_model.calls" in keys


# ──────────────────────────── 调试执行与保存 ────────────────────────────


def test_execute_returns_simulate_payload(
    client, auth_headers, project, monkeypatch,
):
    monkeypatch.setattr(service, "execute_code", _fake_execute_ok)
    r = client.post(
        f"{BASE}/projects/{project['id']}/execute",
        json={"script": "def simulate(context, actions, horizon):\n"
                        "    return {'trajectory': [1, 2]}",
              "test_input": {"context": {"current_value": 1}, "actions": [],
                             "horizon": 2}},
        headers=auth_headers,
    )
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["ok"] is True
    assert data["payload"] == {"trajectory": [1, 2]}
    assert data["error"] is None


def test_execute_surfaces_script_error(
    client, auth_headers, project, monkeypatch,
):
    monkeypatch.setattr(service, "execute_code", _fake_execute_fail)
    r = client.post(
        f"{BASE}/projects/{project['id']}/execute",
        json={"script": "x = 1", "test_input": {}},
        headers=auth_headers,
    )
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["ok"] is False
    assert "ValueError" in data["error"]


def test_execute_gateway_unreachable_returns_502(
    client, auth_headers, project, monkeypatch,
):
    from app.data_channel.pipelines.python_engine.client import (
        PythonEngineError,
    )

    def _boom(code, **kwargs):
        raise PythonEngineError("Python 执行网关未配置")

    monkeypatch.setattr(service, "execute_code", _boom)
    r = client.post(
        f"{BASE}/projects/{project['id']}/execute",
        json={"script": "x = 1", "test_input": {}},
        headers=auth_headers,
    )
    assert r.status_code == 502
    assert "网关" in str(r.json()["detail"])


def test_save_requires_successful_execution(
    client, auth_headers, project, monkeypatch,
):
    monkeypatch.setattr(service, "execute_code", _fake_execute_fail)
    r = client.post(
        f"{BASE}/projects/{project['id']}/save",
        json={"script": "def simulate(context, actions, horizon):\n"
                        "    return {}",
              "test_input": {}},
        headers=auth_headers,
    )
    assert r.status_code == 200
    assert r.json()["data"]["ok"] is False

    # 保存失败不落版本
    r = client.get(
        f"{BASE}/projects/{project['id']}/versions", headers=auth_headers)
    assert r.json()["data"] == []


def test_save_freezes_version_and_prunes(
    client, auth_headers, project, db, monkeypatch,
):
    monkeypatch.setattr(service, "execute_code", _fake_execute_ok)
    pid = project["id"]
    script = "def simulate(context, actions, horizon):\n    return {}"
    for _ in range(3):
        r = client.post(
            f"{BASE}/projects/{pid}/save",
            json={"script": script, "test_input": {"horizon": 3}},
            headers=auth_headers,
        )
        assert r.json()["data"]["ok"] is True

    r = client.get(f"{BASE}/projects/{pid}", headers=auth_headers)
    assert r.json()["data"]["script"] == script

    r = client.get(f"{BASE}/projects/{pid}/versions", headers=auth_headers)
    versions = r.json()["data"]
    assert [v["version_no"] for v in versions] == [3, 2, 1]
    assert versions[0]["test_input"]["horizon"] == 3

    r = client.get(
        f"{BASE}/projects/{pid}/versions/{versions[1]['id']}",
        headers=auth_headers)
    assert r.json()["data"]["script"] == script

    r = client.get(
        f"{BASE}/projects/{pid}/versions/nonexistent", headers=auth_headers)
    assert r.status_code == 404


def test_versions_of_deleted_project_are_cascaded(
    client, auth_headers, project, db, monkeypatch,
):
    from app.world_model.models import WorldModelScriptVersion

    monkeypatch.setattr(service, "execute_code", _fake_execute_ok)
    pid = project["id"]
    client.post(
        f"{BASE}/projects/{pid}/save",
        json={"script": "def simulate(context, actions, horizon):\n"
                        "    return {}",
              "test_input": {}},
        headers=auth_headers,
    )
    assert db.query(WorldModelScriptVersion).count() == 1

    client.delete(f"{BASE}/projects/{pid}", headers=auth_headers)
    db.expire_all()
    assert db.query(WorldModelScriptVersion).count() == 0


# ──────────────────────────── 调用记录（只读） ────────────────────────────


def _seed_calls(db):
    db.add_all([
        WorldModelCallRecord(
            service_name="负荷推演", caller="agent-session-1", ok=True,
            duration_ms=120, request_payload={"horizon": 6},
            response_payload={"trajectory": [1, 2]}),
        WorldModelCallRecord(
            service_name="负荷推演", caller="agent-session-2", ok=False,
            duration_ms=40, error="超时", request_payload={}),
        WorldModelCallRecord(
            service_name="潮流仿真", caller="manual", ok=True,
            duration_ms=350),
    ])
    db.commit()


def test_call_records_list_filter_and_overview(client, auth_headers, db):
    _seed_calls(db)

    r = client.get(f"{BASE}/calls", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["data"]["total"] == 3

    r = client.get(
        f"{BASE}/calls", params={"result": "failed"}, headers=auth_headers)
    assert r.json()["data"]["total"] == 1
    assert r.json()["data"]["items"][0]["ok"] is False

    r = client.get(
        f"{BASE}/calls", params={"keyword": "潮流"}, headers=auth_headers)
    assert r.json()["data"]["total"] == 1

    r = client.get(f"{BASE}/calls/overview", headers=auth_headers)
    overview = r.json()["data"]
    assert overview == {"total": 3, "failed": 1, "avg_duration_ms": 170}

    r = client.get(f"{BASE}/calls", headers=auth_headers)
    first = r.json()["data"]["items"][0]
    r = client.get(f"{BASE}/calls/{first['id']}", headers=auth_headers)
    detail = r.json()["data"]
    assert "request_payload" in detail

    r = client.get(f"{BASE}/calls/nonexistent", headers=auth_headers)
    assert r.status_code == 404


def test_call_records_require_menu(client, custom_headers):
    r = client.get(f"{BASE}/calls", headers=custom_headers)
    assert r.status_code == 403


# ──────────────────────────── 调试执行收尾代码回归 ────────────────────────────


def test_debug_epilogue_handles_json_literals():
    """test_input 含 JSON 布尔/null 时，注入代码在内核中必须可执行。

    回归：曾把 JSON 文本直接拼进 Python 表达式（true/false/null 不是合法
    Python 标识符），此类测试入参在内核里必报 NameError。这里在进程内真实
    执行生成代码（不起内核），锁定拼接正确性。
    """
    import contextlib
    import io

    script = (
        "def simulate(context, actions, horizon):\n"
        "    return {\n"
        "        'flag': context.get('flag'),\n"
        "        'missing': context.get('nothing'),\n"
        "        'tags': context.get('tags'),\n"
        "        'horizon': horizon,\n"
        "    }\n"
    )
    code = service._build_debug_code(script, {
        "context": {"flag": True, "nothing": None, "tags": ["a", False]},
        "actions": [],
        "horizon": 2,
    })

    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        exec(compile(code, "<world-model-debug>", "exec"), {})
    stdout = buffer.getvalue()

    assert "__OB_RESULT_BEGIN__" in stdout
    from app.data_channel.pipelines.python_engine.client import extract_payload
    payload = extract_payload(stdout)
    assert payload == {
        "flag": True, "missing": None, "tags": ["a", False], "horizon": 2,
    }
