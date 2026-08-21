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


def test_projects_pagination(client, auth_headers):
    """列表走服务端分页：total 为筛选后总数，page/size 生效。"""
    for index in range(3):
        r = client.post(
            f"{BASE}/projects",
            json={"name": f"模型-{index}", "description": "",
                  "engine_type": "statistical"},
            headers=auth_headers,
        )
        assert r.status_code == 201, r.text

    r = client.get(
        f"{BASE}/projects", params={"page": 1, "size": 2}, headers=auth_headers)
    data = r.json()["data"]
    assert data["total"] == 3
    assert len(data["items"]) == 2

    r = client.get(
        f"{BASE}/projects", params={"page": 2, "size": 2}, headers=auth_headers)
    data = r.json()["data"]
    assert data["total"] == 3
    assert len(data["items"]) == 1

    # keyword 收窄后 total 同步收窄（服务端全量筛选，不受单页 500 条上限影响）
    r = client.get(
        f"{BASE}/projects",
        params={"keyword": "模型-2", "page": 1, "size": 2},
        headers=auth_headers,
    )
    data = r.json()["data"]
    assert data["total"] == 1
    assert data["items"][0]["name"] == "模型-2"


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
    assert "world_model" in normalize_menu_keys(["world_model.services"])
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


# ──────────────────────────── 推演服务：发布 / 状态 / 调用 ────────────────────────────

_PUBLISH_BODY = {
    "name": "负荷推演服务",
    "description": "对外提供负荷推演",
    "applicable_ontology_id": "ontology-1",
    "applicable_object_type_ids": ["ot-line"],
    "preconditions": [{"object_type_id": "ot-line", "min_count": 1}],
}


def _save_version(client, auth_headers, project_id, monkeypatch):
    monkeypatch.setattr(service, "execute_code", _fake_execute_ok)
    r = client.post(
        f"{BASE}/projects/{project_id}/save",
        json={"script": "def simulate(context, actions, horizon):\n"
                        "    return {'trajectory': [1, 2]}",
              "test_input": {}},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"]["ok"] is True
    return r.json()["data"]["version_no"]


def test_publish_requires_saved_version(client, auth_headers, project):
    r = client.post(
        f"{BASE}/projects/{project['id']}/publish",
        json=_PUBLISH_BODY,
        headers=auth_headers,
    )
    assert r.status_code == 400
    assert "保存" in str(r.json()["detail"])


def test_publish_creates_online_service_and_marks_project(
    client, auth_headers, project, monkeypatch,
):
    version_no = _save_version(client, auth_headers, project["id"], monkeypatch)
    r = client.post(
        f"{BASE}/projects/{project['id']}/publish",
        json=_PUBLISH_BODY,
        headers=auth_headers,
    )
    assert r.status_code == 201, r.text
    svc = r.json()["data"]
    assert svc["status"] == "online"
    assert svc["version_no"] == version_no
    assert svc["endpoint_path"].endswith(f"/services/{svc['id']}/invoke")
    assert svc["applicable_object_types"] == {
        "ontology_id": "ontology-1", "object_type_ids": ["ot-line"],
    }
    assert svc["preconditions"] == [{"object_type_id": "ot-line", "min_count": 1}]

    r = client.get(f"{BASE}/projects/{project['id']}", headers=auth_headers)
    detail = r.json()["data"]
    assert detail["status"] == "published"
    assert detail["service_status"] == "online"
    assert detail["version_count"] == 1

    # 列表接口同样携带 service_status（回归：曾因 schema 缺字段被静默丢弃，
    # 列表徽标永远显示「草稿」）
    r = client.get(f"{BASE}/projects", headers=auth_headers)
    listed = [i for i in r.json()["data"]["items"] if i["id"] == project["id"]]
    assert listed[0]["service_status"] == "online"

    r = client.get(f"{BASE}/projects/{project['id']}/service", headers=auth_headers)
    assert r.json()["data"]["id"] == svc["id"]


def test_republish_overwrites_same_service(
    client, auth_headers, project, monkeypatch,
):
    _save_version(client, auth_headers, project["id"], monkeypatch)
    first = client.post(
        f"{BASE}/projects/{project['id']}/publish",
        json=_PUBLISH_BODY, headers=auth_headers,
    ).json()["data"]
    version_no_2 = _save_version(client, auth_headers, project["id"], monkeypatch)
    r = client.post(
        f"{BASE}/projects/{project['id']}/publish",
        json={**_PUBLISH_BODY, "name": "负荷推演服务 v2"},
        headers=auth_headers,
    )
    assert r.status_code == 201
    second = r.json()["data"]
    assert second["id"] == first["id"]  # 同一项目覆盖更新，不产生第二个服务
    assert second["name"] == "负荷推演服务 v2"
    assert second["version_no"] == version_no_2


def test_project_list_includes_service_summary(
    client, auth_headers, project, monkeypatch,
):
    """列表条目携带已发布服务的名称/端点/冻结版本号（卡片服务快捷入口数据源）。"""
    version_no = _save_version(client, auth_headers, project["id"], monkeypatch)
    svc = client.post(
        f"{BASE}/projects/{project['id']}/publish",
        json=_PUBLISH_BODY, headers=auth_headers,
    ).json()["data"]

    r = client.get(f"{BASE}/projects", headers=auth_headers)
    listed = [i for i in r.json()["data"]["items"] if i["id"] == project["id"]]
    item = listed[0]
    assert item["service_status"] == "online"
    assert item["service_name"] == "负荷推演服务"
    assert item["service_version_no"] == version_no
    assert item["service_endpoint"].endswith(f"/services/{svc['id']}/invoke")


def test_delete_project_blocked_while_service_online(
    client, auth_headers, project, db, monkeypatch,
):
    """在线服务保护：在线时拒绝删除（409）；下线后可删，服务随项目清理。"""
    from app.world_model.models import WorldModelService

    _save_version(client, auth_headers, project["id"], monkeypatch)
    client.post(
        f"{BASE}/projects/{project['id']}/publish",
        json=_PUBLISH_BODY, headers=auth_headers,
    )

    r = client.delete(f"{BASE}/projects/{project['id']}", headers=auth_headers)
    assert r.status_code == 409
    assert "下线" in str(r.json()["detail"])
    assert db.query(WorldModelService).count() == 1

    r = client.post(
        f"{BASE}/projects/{project['id']}/service/status",
        json={"status": "offline"}, headers=auth_headers,
    )
    assert r.status_code == 200

    r = client.delete(f"{BASE}/projects/{project['id']}", headers=auth_headers)
    assert r.status_code == 200
    db.expire_all()
    # 服务显式随项目删除（不依赖 PG 外键级联，SQLite 行为一致）
    assert db.query(WorldModelService).count() == 0
    r = client.get(f"{BASE}/projects/{project['id']}", headers=auth_headers)
    assert r.status_code == 404


def test_delete_project_unlinks_call_records(
    client, auth_headers, project, db, monkeypatch,
):
    """删除项目后调用记录保留审计但解除项目/服务关联。"""
    _save_version(client, auth_headers, project["id"], monkeypatch)
    svc = client.post(
        f"{BASE}/projects/{project['id']}/publish",
        json=_PUBLISH_BODY, headers=auth_headers,
    ).json()["data"]
    r = client.post(
        f"{BASE}/services/{svc['id']}/invoke",
        json={"context": {}, "actions": [], "horizon": 1},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    client.post(
        f"{BASE}/projects/{project['id']}/service/status",
        json={"status": "offline"}, headers=auth_headers,
    )

    r = client.delete(f"{BASE}/projects/{project['id']}", headers=auth_headers)
    assert r.status_code == 200
    db.expire_all()
    record = db.query(WorldModelCallRecord).one()
    assert record.project_id is None
    assert record.service_id is None
    assert record.service_name == "负荷推演服务"  # 审计快照保留


def test_service_offline_blocks_invoke(
    client, auth_headers, project, monkeypatch,
):
    _save_version(client, auth_headers, project["id"], monkeypatch)
    svc = client.post(
        f"{BASE}/projects/{project['id']}/publish",
        json=_PUBLISH_BODY, headers=auth_headers,
    ).json()["data"]

    r = client.post(
        f"{BASE}/projects/{project['id']}/service/status",
        json={"status": "offline"}, headers=auth_headers,
    )
    assert r.json()["data"]["status"] == "offline"

    r = client.post(
        f"{BASE}/services/{svc['id']}/invoke",
        json={"context": {}, "actions": [], "horizon": 1},
        headers=auth_headers,
    )
    assert r.status_code == 409


def test_invoke_writes_call_record(
    client, auth_headers, project, monkeypatch,
):
    _save_version(client, auth_headers, project["id"], monkeypatch)
    svc = client.post(
        f"{BASE}/projects/{project['id']}/publish",
        json=_PUBLISH_BODY, headers=auth_headers,
    ).json()["data"]

    r = client.post(
        f"{BASE}/services/{svc['id']}/invoke",
        json={"context": {"current_value": 7}, "actions": [], "horizon": 2},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["ok"] is True
    assert data["payload"] == {"trajectory": [1, 2]}
    assert data["call_id"]

    r = client.get(f"{BASE}/calls", headers=auth_headers)
    items = r.json()["data"]["items"]
    assert len(items) == 1
    assert items[0]["service_name"] == "负荷推演服务"
    assert items[0]["caller"] == "wm_admin" or items[0]["caller"]

    r = client.get(f"{BASE}/calls/{data['call_id']}", headers=auth_headers)
    detail = r.json()["data"]
    assert detail["request_payload"]["context"] == {"current_value": 7}
    assert detail["response_payload"] == {"result": {"trajectory": [1, 2]}}


def test_invoke_records_script_failure(
    client, auth_headers, project, monkeypatch,
):
    _save_version(client, auth_headers, project["id"], monkeypatch)
    svc = client.post(
        f"{BASE}/projects/{project['id']}/publish",
        json=_PUBLISH_BODY, headers=auth_headers,
    ).json()["data"]

    monkeypatch.setattr(service, "execute_code", _fake_execute_fail)
    r = client.post(
        f"{BASE}/services/{svc['id']}/invoke",
        json={"context": {}, "actions": [], "horizon": 1},
        headers=auth_headers,
    )
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["ok"] is False
    assert "ValueError" in data["error"]

    r = client.get(f"{BASE}/calls/overview", headers=auth_headers)
    assert r.json()["data"]["failed"] == 1


def test_invoke_gateway_down_returns_502_and_records(
    client, auth_headers, project, monkeypatch,
):
    from app.data_channel.pipelines.python_engine.client import (
        PythonEngineError,
    )

    _save_version(client, auth_headers, project["id"], monkeypatch)
    svc = client.post(
        f"{BASE}/projects/{project['id']}/publish",
        json=_PUBLISH_BODY, headers=auth_headers,
    ).json()["data"]

    def _boom(code, **kwargs):
        raise PythonEngineError("Python 执行网关未配置")

    monkeypatch.setattr(service, "execute_code", _boom)
    r = client.post(
        f"{BASE}/services/{svc['id']}/invoke",
        json={"context": {}, "actions": [], "horizon": 1},
        headers=auth_headers,
    )
    assert r.status_code == 502
    r = client.get(f"{BASE}/calls/overview", headers=auth_headers)
    assert r.json()["data"] == {"total": 1, "failed": 1, "avg_duration_ms": 0}


def test_publish_and_invoke_require_menu(client, custom_headers, project):
    r = client.post(
        f"{BASE}/projects/{project['id']}/publish",
        json=_PUBLISH_BODY, headers=custom_headers,
    )
    assert r.status_code == 403
    r = client.post(
        f"{BASE}/services/any/invoke",
        json={"context": {}}, headers=custom_headers,
    )
    assert r.status_code == 403


# ──────────────────────────── 推演服务注册表（跨项目） ────────────────────────────


def test_service_registry_lists_published_service_with_stats(
    client, auth_headers, project, monkeypatch,
):
    version_no = _save_version(client, auth_headers, project["id"], monkeypatch)
    svc = client.post(
        f"{BASE}/projects/{project['id']}/publish",
        json=_PUBLISH_BODY, headers=auth_headers,
    ).json()["data"]
    assert svc["status"] == "online"

    r = client.get(f"{BASE}/services", headers=auth_headers)
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["total"] == 1
    item = data["items"][0]
    assert item["id"] == svc["id"]
    assert item["project_name"] == "负荷推演"
    assert item["version_no"] == version_no
    assert item["name"] == "负荷推演服务"
    assert item["endpoint_path"].endswith(f"/services/{svc['id']}/invoke")
    assert item["applicable_object_types"]["ontology_id"] == "ontology-1"
    assert item["call_count"] == 0
    assert item["failed_count"] == 0

    # 调用一次后统计随之更新
    client.post(
        f"{BASE}/services/{svc['id']}/invoke",
        json={"context": {}, "actions": [], "horizon": 1},
        headers=auth_headers,
    )
    r = client.get(f"{BASE}/services", headers=auth_headers)
    item = r.json()["data"]["items"][0]
    assert item["call_count"] == 1
    assert item["failed_count"] == 0


def test_service_registry_filters_and_pagination(
    client, auth_headers, project, monkeypatch,
):
    _save_version(client, auth_headers, project["id"], monkeypatch)
    client.post(
        f"{BASE}/projects/{project['id']}/publish",
        json=_PUBLISH_BODY, headers=auth_headers,
    )

    r = client.get(
        f"{BASE}/services", params={"keyword": "不存在"}, headers=auth_headers)
    assert r.json()["data"]["total"] == 0
    r = client.get(
        f"{BASE}/services", params={"status": "offline"}, headers=auth_headers)
    assert r.json()["data"]["total"] == 0
    r = client.get(
        f"{BASE}/services", params={"status": "online"}, headers=auth_headers)
    assert r.json()["data"]["total"] == 1
    r = client.get(
        f"{BASE}/services", params={"status": "bogus"}, headers=auth_headers)
    assert r.status_code == 400
    r = client.get(
        f"{BASE}/services", params={"page": 2, "size": 1}, headers=auth_headers)
    assert r.json()["data"]["total"] == 1
    assert r.json()["data"]["items"] == []


def test_service_registry_detail_and_status_by_id(
    client, auth_headers, project, monkeypatch,
):
    _save_version(client, auth_headers, project["id"], monkeypatch)
    svc = client.post(
        f"{BASE}/projects/{project['id']}/publish",
        json=_PUBLISH_BODY, headers=auth_headers,
    ).json()["data"]

    r = client.get(f"{BASE}/services/{svc['id']}", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["data"]["id"] == svc["id"]
    assert r.json()["data"]["preconditions"] == [
        {"object_type_id": "ot-line", "min_count": 1}]

    r = client.get(f"{BASE}/services/nonexistent", headers=auth_headers)
    assert r.status_code == 404

    r = client.post(
        f"{BASE}/services/{svc['id']}/status",
        json={"status": "offline"}, headers=auth_headers,
    )
    assert r.status_code == 200
    assert r.json()["data"]["status"] == "offline"

    # 下线后调用被拒绝（服务侧状态入口与项目侧入口语义一致）
    r = client.post(
        f"{BASE}/services/{svc['id']}/invoke",
        json={"context": {}, "actions": [], "horizon": 1},
        headers=auth_headers,
    )
    assert r.status_code == 409

    r = client.post(
        f"{BASE}/services/{svc['id']}/status",
        json={"status": "online"}, headers=auth_headers,
    )
    assert r.json()["data"]["status"] == "online"


def test_calls_list_filters_by_service_id(
    client, auth_headers, project, monkeypatch,
):
    _save_version(client, auth_headers, project["id"], monkeypatch)
    svc = client.post(
        f"{BASE}/projects/{project['id']}/publish",
        json=_PUBLISH_BODY, headers=auth_headers,
    ).json()["data"]
    client.post(
        f"{BASE}/services/{svc['id']}/invoke",
        json={"context": {}, "actions": [], "horizon": 1},
        headers=auth_headers,
    )

    r = client.get(f"{BASE}/calls", headers=auth_headers)
    assert r.json()["data"]["total"] == 1
    r = client.get(
        f"{BASE}/calls", params={"service_id": svc["id"]}, headers=auth_headers)
    assert r.json()["data"]["total"] == 1
    r = client.get(
        f"{BASE}/calls", params={"service_id": "other"}, headers=auth_headers)
    assert r.json()["data"]["total"] == 0


def test_service_registry_requires_menu(client, custom_headers):
    assert client.get(f"{BASE}/services", headers=custom_headers).status_code == 403
    assert client.get(
        f"{BASE}/services/any", headers=custom_headers).status_code == 403
    r = client.post(
        f"{BASE}/services/any/status",
        json={"status": "online"}, headers=custom_headers,
    )
    assert r.status_code == 403
