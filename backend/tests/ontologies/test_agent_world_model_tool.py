"""本体助手 × 世界模型推演工具：检索过滤 / 版本漂移 / 前置条件 / RBAC / 审计留痕 / 限额。"""
import uuid

import pytest

from app.ontologies.agent_runtime.boundary import ToolError, build_scope
from app.ontologies.agent_runtime.toolkit import ToolRunner


def _fo(ontology_id: str) -> str:
    return f"/api/v2/formal/ontologies/{ontology_id}"


@pytest.fixture
def modeled_ontology(client, auth_headers, ontology):
    """两个对象类型（订单/供应商）+ 若干实例 —— 与 test_agent_runtime 同构的精简版。"""
    oid = ontology["id"]
    body = {
        "objectTypes": [
            {"id": "ot-order", "name": "Order", "displayName": "订单", "primaryKey": "order_no",
             "properties": [
                 {"id": "p1", "name": "order_no", "displayName": "订单号", "type": "string", "required": True},
                 {"id": "p2", "name": "status", "displayName": "状态", "type": "string"},
                 {"id": "p3", "name": "amount", "displayName": "金额", "type": "number"},
             ], "positionX": 0, "positionY": 0},
            {"id": "ot-supplier", "name": "Supplier", "displayName": "供应商", "primaryKey": "sname",
             "properties": [
                 {"id": "p4", "name": "sname", "displayName": "名称", "type": "string", "required": True},
             ], "positionX": 0, "positionY": 0},
        ],
        "linkTypes": [
            {"id": "lt-1", "name": "order_supplier", "displayName": "订单-供应商",
             "sourceObjectTypeId": "ot-order", "targetObjectTypeId": "ot-supplier",
             "cardinality": "many-to-one"},
        ],
        "actions": [],
        "functions": [],
        "instances": [
            {"id": "inst-o1", "objectTypeId": "ot-order",
             "properties": {"order_no": "SO-001", "status": "pending", "amount": 100}, "computed": {}},
            {"id": "inst-o2", "objectTypeId": "ot-order",
             "properties": {"order_no": "SO-002", "status": "paid", "amount": 250}, "computed": {}},
            {"id": "inst-s1", "objectTypeId": "ot-supplier",
             "properties": {"sname": "华南电子"}, "computed": {}},
        ],
        "linkInstances": [
            {"id": "li-1", "linkTypeId": "lt-1",
             "sourceObjectId": "inst-o1", "targetObjectId": "inst-s1"},
        ],
    }
    r = client.put(f"{_fo(oid)}/full", headers=auth_headers, json=body)
    assert r.status_code == 200, r.text
    return ontology


def _make_service(db, *, ontology_id, object_type_ids, preconditions=None,
                  name="订单态势推演", status="online"):
    from app.world_model.models import WorldModelProject, WorldModelScriptVersion, WorldModelService
    project = WorldModelProject(
        name=f"{name}-项目",
        script="def simulate(context, actions, horizon):\n    return {}",
    )
    db.add(project)
    db.flush()
    version = WorldModelScriptVersion(
        project_id=project.id, version_no=1, script=project.script,
        test_input={"context": {"series": [1, 2, 3]}, "actions": [], "horizon": 3},
    )
    db.add(version)
    db.flush()
    service = WorldModelService(
        project_id=project.id, version_id=version.id, name=name, status=status,
        applicable_object_types={
            "ontology_id": ontology_id, "object_type_ids": object_type_ids},
        preconditions=preconditions or [],
    )
    db.add(service)
    db.commit()
    return service


def _runner(db, oid, user):
    _, _, scope = build_scope(db, oid)
    return ToolRunner(db, scope, world_model_context={"user": user})


# ---------------------------------------------------------------- 检索与资格


def test_list_filters_by_ontology_and_reports_block_reasons(
        db, admin_user, modeled_ontology):
    oid = modeled_ontology["id"]
    _make_service(db, ontology_id=oid, object_type_ids=["ot-order", "ot-supplier"],
                  preconditions=[{"object_type_id": "ot-order", "min_count": 1}],
                  name="可用服务")
    _make_service(db, ontology_id=oid, object_type_ids=["ot-order", "ot-deleted"],
                  name="漂移服务")  # ot-deleted 不存在 → 版本演进漂移
    _make_service(db, ontology_id=oid, object_type_ids=["ot-supplier"],
                  preconditions=[{"object_type_id": "ot-supplier", "min_count": 5}],
                  name="前置不足服务")  # 供应商仅 1 个实例 < 5
    _make_service(db, ontology_id="other-ontology", object_type_ids=["ot-order"],
                  name="他本体服务")
    offline = _make_service(db, ontology_id=oid, object_type_ids=["ot-order"],
                            status="offline", name="下线服务")

    result = _runner(db, oid, admin_user).run("list_world_model_services", {})

    assert result["kind"] == "world_model_services"
    assert [s["name"] for s in result["available"]] == ["可用服务"]
    assert result["available"][0]["exampleInput"]["context"] == {"series": [1, 2, 3]}
    blocked = {s["name"]: s["reasons"] for s in result["blocked"]}
    assert set(blocked) == {"漂移服务", "前置不足服务"}
    assert any("对象类型" in r for r in blocked["漂移服务"])
    assert any("前置条件" in r and "实例数 1 < 要求 5" in r for r in blocked["前置不足服务"])
    # 他本体与已下线服务完全不进入会话视野
    all_names = [s["name"] for s in result["available"] + result["blocked"]]
    assert "他本体服务" not in all_names and offline.name not in all_names


# ---------------------------------------------------------------- RBAC 门控


def test_world_model_tools_require_menu_access(db, admin_user, modeled_ontology):
    from app.models.user import User
    oid = modeled_ontology["id"]
    _make_service(db, ontology_id=oid, object_type_ids=["ot-order"])
    # custom 角色默认菜单只有 overview，不含 world_model
    custom = User(id=str(uuid.uuid4()), username="cu", email="cu@test.com",
                  password_hash="x", role="custom")
    db.add(custom)
    db.commit()

    runner = _runner(db, oid, custom)
    with pytest.raises(ToolError, match="世界模型"):
        runner.run("list_world_model_services", {})

    svc = _make_service(db, ontology_id=oid, object_type_ids=["ot-order"], name="第二服务")
    with pytest.raises(ToolError, match="菜单权限"):
        runner.run("run_world_model_simulation",
                   {"service_id": svc.id, "context": {}})

    # admin 角色放行
    assert _runner(db, oid, admin_user).run("list_world_model_services", {})["available"]


# ---------------------------------------------------------------- 调用与审计


class _FakeExecution:
    def __init__(self, stdout):
        self.stdout = stdout
        self.error = None
        self.traceback = ""
        self.duration_ms = 5
        self.kernel_id = "fake-kernel"


def test_run_invokes_service_and_writes_call_record(
        db, admin_user, modeled_ontology, monkeypatch):
    from app.world_model import service as world_model_service
    from app.world_model.models import WorldModelCallRecord
    oid = modeled_ontology["id"]
    svc = _make_service(db, ontology_id=oid, object_type_ids=["ot-order"],
                        preconditions=[{"object_type_id": "ot-order", "min_count": 1}])

    def fake_execute_code(code, full_stdout=True, **kwargs):
        assert "simulate" in code
        return _FakeExecution(
            '__OB_RESULT_BEGIN__\n'
            '{"trajectory": [{"step": 1, "value": 12.5}], "confidence": 0.8}\n'
            '__OB_RESULT_END__')

    monkeypatch.setattr(world_model_service, "execute_code", fake_execute_code)

    result = _runner(db, oid, admin_user).run("run_world_model_simulation", {
        "service_id": svc.id,
        "context": {"series": [100, 250]},
        "actions": [{"step": 1, "delta": 10}],
        "horizon": 3,
    })

    assert result["kind"] == "world_model_simulation"
    assert result["ok"] is True
    # 成功结果不得带 error 键：_summarize/step.error 按"键存在"判错，
    # error=None 会把步骤摘要渲染成 'None'（线上验收实测回归）
    assert "error" not in result
    from app.ontologies.agent_runtime.orchestrator import _summarize
    assert _summarize("run_world_model_simulation", result) == \
        "世界模型推演「订单态势推演」完成（5ms）"
    assert result["payload"]["trajectory"][0]["value"] == 12.5
    assert result["callId"]
    record = db.query(WorldModelCallRecord).filter(
        WorldModelCallRecord.id == result["callId"]).one()
    assert record.ok is True
    assert record.caller == "admin"  # 会话用户而非系统身份
    assert record.request_payload["context"] == {"series": [100, 250]}


def test_run_blocked_by_preconditions_and_offline(db, admin_user, modeled_ontology):
    oid = modeled_ontology["id"]
    scarce = _make_service(db, ontology_id=oid, object_type_ids=["ot-supplier"],
                           preconditions=[{"object_type_id": "ot-supplier", "min_count": 9}],
                           name="前置不足")
    offline = _make_service(db, ontology_id=oid, object_type_ids=["ot-order"],
                            status="offline", name="已下线")
    runner = _runner(db, oid, admin_user)
    with pytest.raises(ToolError, match="前置条件不满足"):
        runner.run("run_world_model_simulation", {"service_id": scarce.id, "context": {}})
    with pytest.raises(ToolError, match="未在线"):
        runner.run("run_world_model_simulation", {"service_id": offline.id, "context": {}})


def test_run_per_turn_limit(db, admin_user, modeled_ontology, monkeypatch):
    monkeypatch.setenv("AGENT_WORLD_MODEL_INVOKE_PER_TURN", "1")
    from app.world_model import service as world_model_service
    oid = modeled_ontology["id"]
    svc = _make_service(db, ontology_id=oid, object_type_ids=["ot-order"])
    monkeypatch.setattr(
        world_model_service, "execute_code",
        lambda code, full_stdout=True, **kw: _FakeExecution(
            '__OB_RESULT_BEGIN__\n{"trajectory": []}\n__OB_RESULT_END__'))
    runner = _runner(db, oid, admin_user)
    assert runner.run("run_world_model_simulation",
                      {"service_id": svc.id, "context": {}})["ok"]
    with pytest.raises(ToolError, match="上限"):
        runner.run("run_world_model_simulation", {"service_id": svc.id, "context": {}})


def test_run_rejects_oversized_context(db, admin_user, modeled_ontology, monkeypatch):
    monkeypatch.setenv("AGENT_WORLD_MODEL_CONTEXT_CHARS", "1000")
    oid = modeled_ontology["id"]
    svc = _make_service(db, ontology_id=oid, object_type_ids=["ot-order"])
    big = {"blob": "x" * 2000}
    with pytest.raises(ToolError, match="超过上限"):
        _runner(db, oid, admin_user).run("run_world_model_simulation",
                                         {"service_id": svc.id, "context": big})


# ---------------------------------------------------------------- 回合接线


def test_chat_turn_discovers_world_model_service(
        client, auth_headers, modeled_ontology, db, admin_user, monkeypatch):
    """假 LLM 经对话触发 list_world_model_services：验证 orchestrator 把会话用户
    传入工具上下文（RBAC + 审计身份接线），步骤与系统提示词规则生效。"""
    oid = modeled_ontology["id"]
    svc = _make_service(db, ontology_id=oid,
                        object_type_ids=["ot-order", "ot-supplier"], name="订单态势推演")

    from app.models.model_config import ModelConfig
    db.add(ModelConfig(id=str(uuid.uuid4()), name="fake", provider="openai",
                       config_type="llm", models=["fake-model"],
                       created_by=admin_user.id))
    db.commit()

    calls = {"n": 0}

    def fake_chat(call_kwargs, messages, tools):
        calls["n"] += 1
        if calls["n"] == 1:
            assert any(t["name"] == "list_world_model_services" for t in tools)
            assert "世界模型" in messages[0]["content"]
            return {"content": None, "usage": {"inputTokens": 10, "outputTokens": 5},
                    "tool_calls": [{"id": "tc1", "name": "list_world_model_services",
                                    "arguments": {}}]}
        tool_payload = messages[-1]["content"]
        assert svc.name in tool_payload and "exampleInput" in tool_payload
        return {"content": "当前本体有 1 个可用的世界模型推演服务：订单态势推演。",
                "tool_calls": [], "usage": {"inputTokens": 20, "outputTokens": 8}}

    from app.ontologies.agent_runtime import llm_bridge
    monkeypatch.setattr(llm_bridge, "chat", fake_chat)

    r = client.post(f"{_fo(oid)}/agent/chat", headers=auth_headers,
                    json={"message": "未来订单态势会怎样？", "stream": False})
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["error"] is None
    assert data["steps"][0]["tool"] == "list_world_model_services"
    assert "世界模型推演服务：1 个可用" in data["steps"][0]["summary"]
