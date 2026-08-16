"""确定性结论校验在编排器中的集成测试：编造数字 → 一次修正回环 → 通过。"""
import uuid

from app.ontologies.agent_runtime import llm_bridge


def _fo(oid: str) -> str:
    return f"/api/v2/formal/ontologies/{oid}"


def test_answer_verification_corrects_fabricated_number(
        client, auth_headers, modeled_ontology, db, admin_user, monkeypatch):
    """假 LLM 终答编造 999 → 校验失败 → 修正回环后给出与工具结果一致的数字。"""
    oid = modeled_ontology["id"]

    from app.models.model_config import ModelConfig
    db.add(ModelConfig(id=str(uuid.uuid4()), name="verify-fake", provider="openai",
                       config_type="llm", models=["verify-fake-model"],
                       created_by=admin_user.id))
    db.commit()

    calls = {"n": 0}

    def fake_chat(call_kwargs, messages, tools):
        calls["n"] += 1
        if calls["n"] == 1:
            assert any(t["name"] == "search_objects" for t in tools)
            return {"content": None,
                    "usage": {"inputTokens": 10, "outputTokens": 5},
                    "tool_calls": [{"id": "tc1", "name": "search_objects",
                                    "arguments": {"object_type": "Order",
                                                  "filters": [{"property": "status",
                                                               "op": "eq",
                                                               "value": "pending"}]}}]}
        if calls["n"] == 2:
            # 终答编造了一个工具结果里没有的数字
            return {"content": "待支付订单共 999 个。", "tool_calls": [],
                    "usage": {"inputTokens": 20, "outputTokens": 8}}
        # 修正回环（tools=[]）：给出可对应工具结果的数字
        assert tools == []
        assert "999" in messages[-1]["content"] and "结论校验失败" in messages[-1]["content"]
        return {"content": "待支付订单共 1 个，即 SO-001。", "tool_calls": [],
                "usage": {"inputTokens": 15, "outputTokens": 6}}

    monkeypatch.setattr(llm_bridge, "chat", fake_chat)

    r = client.post(
        f"{_fo(oid)}/agent/chat", headers=auth_headers,
        json={"message": "待支付订单有几个？", "stream": False})
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["error"] is None
    assert "999" not in data["content"]
    assert "SO-001" in data["content"]
    verification = data["verification"]
    assert verification["passed"] is True
    assert verification["retried"] is True
    assert calls["n"] == 3


def test_answer_verification_warns_when_model_wont_fix(
        client, auth_headers, modeled_ontology, db, admin_user, monkeypatch):
    """修正回环后模型仍编造 → 不摧毁回答，但显式标注未验证数字。"""
    oid = modeled_ontology["id"]

    from app.models.model_config import ModelConfig
    db.add(ModelConfig(id=str(uuid.uuid4()), name="verify-stubborn", provider="openai",
                       config_type="llm", models=["verify-stubborn-model"],
                       created_by=admin_user.id))
    db.commit()

    calls = {"n": 0}

    def fake_chat(call_kwargs, messages, tools):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"content": None,
                    "usage": {"inputTokens": 10, "outputTokens": 5},
                    "tool_calls": [{"id": "tc1", "name": "search_objects",
                                    "arguments": {"object_type": "Order"}}]}
        # 终答与修正回环都坚持编造 999
        return {"content": "待支付订单共 999 个。", "tool_calls": [],
                "usage": {"inputTokens": 20, "outputTokens": 8}}

    monkeypatch.setattr(llm_bridge, "chat", fake_chat)

    r = client.post(
        f"{_fo(oid)}/agent/chat", headers=auth_headers,
        json={"message": "待支付订单有几个？", "stream": False})
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["error"] is None
    assert "999" in data["content"]
    assert "结论校验" in data["content"]
    verification = data["verification"]
    assert verification["passed"] is False
    assert 999.0 in verification["unverified"]
    assert verification["retried"] is True
    assert calls["n"] == 3  # 首轮工具调用 + 终答 + 一次修正回环


import pytest


@pytest.fixture
def modeled_ontology(client, auth_headers, ontology):
    """两个对象类型（订单/供应商）+ 链接 + 动作 + 若干实例（与 test_agent_runtime 同构）。"""
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
