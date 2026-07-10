"""本体智能体（agent_runtime）的边界与回合测试：

  1. 授权边界：白名单外的对象类型/动作不可见、不可用；链接两端有隐藏类型时链接也隐藏
  2. 工具行为：search 过滤 + 配额截断；aggregate 统计；history 溯源
  3. 动作治理：propose_action 永远 dry-run 不落数据；execute-proposal 才真实执行，
     requires_approval 的动作进 HITL pending 队列
  4. 回合编排：假 LLM 驱动 orchestrator 走「查询→回答」全链路，轨迹/引用被持久化
"""
import uuid

import pytest


def _fo(ontology_id: str) -> str:
    return f"/api/v2/formal/ontologies/{ontology_id}"


@pytest.fixture
def modeled_ontology(client, auth_headers, ontology):
    """两个对象类型（订单/供应商）+ 链接 + 一个改状态动作 + 若干实例。"""
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
        "actions": [
            {"id": "act-1", "name": "mark_paid", "displayName": "标记已支付",
             "objectTypeId": "ot-order", "requiresApproval": False,
             "parameters": [{"name": "note", "displayName": "备注", "type": "string"}],
             "rules": [{"type": "update_property", "name": "set-status", "enabled": True,
                        "order": 0, "config": {"targetProperty": "status",
                                               "valueSource": "constant", "value": "\"paid\""}}]},
            {"id": "act-2", "name": "cancel_order", "displayName": "取消订单",
             "objectTypeId": "ot-order", "requiresApproval": True,
             "parameters": [],
             "rules": [{"type": "update_property", "name": "set-status", "enabled": True,
                        "order": 0, "config": {"targetProperty": "status",
                                               "valueSource": "constant", "value": "\"cancelled\""}}]},
        ],
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
    # Real actions are a production runtime capability and therefore execute
    # only against an immutable published ontology release.
    r = client.post(
        f"/api/v2/ontologies/{oid}/versions", headers=auth_headers,
        json={"version_label": "agent-runtime-test"},
    )
    assert r.status_code == 201, r.text
    return ontology


# ---------------------------------------------------------------- 边界


def test_profile_defaults_deny_actions(client, auth_headers, modeled_ontology):
    oid = modeled_ontology["id"]
    r = client.get(f"{_fo(oid)}/agent/profile", headers=auth_headers)
    assert r.status_code == 200
    p = r.json()["data"]
    assert p["allowedActionIds"] == []            # 动作默认拒绝
    assert p["allowedObjectTypeIds"] is None      # 读默认全开

    caps = client.get(f"{_fo(oid)}/agent/capabilities", headers=auth_headers).json()["data"]
    assert len(caps["objectTypes"]) == 2
    assert caps["actions"] == []                  # 未授权 → 不可见
    assert "订单" in caps["skillCard"]


def test_scope_hides_unauthorized_types_and_links(client, auth_headers, modeled_ontology, db):
    oid = modeled_ontology["id"]
    # 只授权订单，不授权供应商 → 供应商隐藏，且链接因一端隐藏而隐藏
    r = client.put(f"{_fo(oid)}/agent/profile", headers=auth_headers,
                   json={"allowedObjectTypeIds": ["ot-order"]})
    assert r.status_code == 200

    caps = client.get(f"{_fo(oid)}/agent/capabilities", headers=auth_headers).json()["data"]
    assert [t["id"] for t in caps["objectTypes"]] == ["ot-order"]
    assert caps["linkTypes"] == []
    assert "供应商" not in caps["skillCard"]

    from app.ontologies.agent_runtime.boundary import build_scope, ToolError
    from app.ontologies.agent_runtime.toolkit import ToolRunner
    _, _, scope = build_scope(db, oid)
    runner = ToolRunner(db, scope)
    with pytest.raises(ToolError):
        runner.run("search_objects", {"object_type": "Supplier"})
    # 隐藏类型的实例连 get_object 也不行
    with pytest.raises(ToolError):
        runner.run("get_object", {"instance_id": "inst-s1"})


def test_reset_to_all(client, auth_headers, modeled_ontology):
    oid = modeled_ontology["id"]
    client.put(f"{_fo(oid)}/agent/profile", headers=auth_headers,
               json={"allowedObjectTypeIds": ["ot-order"]})
    r = client.put(f"{_fo(oid)}/agent/profile", headers=auth_headers,
                   json={"resetToAll": ["allowed_object_type_ids"]})
    assert r.json()["data"]["allowedObjectTypeIds"] is None


# ---------------------------------------------------------------- 工具


def test_search_filter_and_cap(client, auth_headers, modeled_ontology, db):
    oid = modeled_ontology["id"]
    from app.ontologies.agent_runtime.boundary import build_scope
    from app.ontologies.agent_runtime.toolkit import ToolRunner
    _, profile, scope = build_scope(db, oid)

    runner = ToolRunner(db, scope)
    r = runner.run("search_objects", {
        "object_type": "订单",
        "filters": [{"property": "amount", "op": "gt", "value": 150}],
    })
    assert r["total"] == 1
    assert r["items"][0]["properties"]["order_no"] == "SO-002"
    assert runner.citations and runner.citations[0]["label"] == "SO-002"

    # 配额：limit 请求 999 也被 max_rows_per_query 压回
    profile.max_rows_per_query = 1
    db.commit()
    _, _, scope2 = build_scope(db, oid)
    r = ToolRunner(db, scope2).run("search_objects", {"object_type": "Order", "limit": 999})
    assert r["returned"] == 1 and r["truncated"] is True


def test_aggregate_and_history_and_traverse(client, auth_headers, modeled_ontology, db):
    oid = modeled_ontology["id"]
    from app.ontologies.agent_runtime.boundary import build_scope
    from app.ontologies.agent_runtime.toolkit import ToolRunner
    _, _, scope = build_scope(db, oid)
    runner = ToolRunner(db, scope)

    agg = runner.run("aggregate_objects", {"object_type": "Order", "metric": "sum",
                                           "metric_property": "amount"})
    assert agg["value"] == 350

    grouped = runner.run("aggregate_objects", {"object_type": "Order", "metric": "count",
                                               "group_by": "status"})
    assert {g["group"]: g["value"] for g in grouped["groups"]} == {"pending": 1, "paid": 1}

    trav = runner.run("traverse_links", {"instance_id": "inst-o1",
                                         "link_type": "order_supplier"})
    assert trav["returned"] == 1
    assert trav["items"][0]["properties"]["sname"] == "华南电子"

    hist = runner.run("get_object_history", {"instance_id": "inst-o1"})
    assert any(f["property"] == "status" for f in hist["facts"])   # 保存时追加过事实


# ---------------------------------------------------------------- 动作治理


def _grant_actions(client, auth_headers, oid, ids):
    r = client.put(f"{_fo(oid)}/agent/profile", headers=auth_headers,
                   json={"allowedActionIds": ids})
    assert r.status_code == 200


def test_propose_is_dry_run_only(client, auth_headers, modeled_ontology, db):
    oid = modeled_ontology["id"]
    _grant_actions(client, auth_headers, oid, ["act-1"])

    from app.ontologies.agent_runtime.boundary import build_scope, ToolError
    from app.ontologies.agent_runtime.toolkit import ToolRunner
    _, _, scope = build_scope(db, oid)
    runner = ToolRunner(db, scope)

    r = runner.run("propose_action", {"action": "mark_paid",
                                      "target_instance_id": "inst-o1",
                                      "parameters": {"note": "ok"}})
    assert r["proposal"]["status"] == "success"
    assert runner.proposals and runner.proposals[0]["actionId"] == "act-1"

    # dry-run 不落变更：状态仍是 pending
    from app.models.ontology_formal import ObjectInstance
    inst = db.query(ObjectInstance).filter(ObjectInstance.id == "inst-o1").first()
    db.refresh(inst)
    assert inst.properties["status"] == "pending"

    # 白名单外的动作提案被拒
    with pytest.raises(ToolError):
        runner.run("propose_action", {"action": "cancel_order",
                                      "target_instance_id": "inst-o1"})


def test_execute_proposal_respects_boundary_and_hitl(client, auth_headers, modeled_ontology, db):
    oid = modeled_ontology["id"]
    _grant_actions(client, auth_headers, oid, ["act-1", "act-2"])

    # 普通动作：真实执行 → 属性变更 + 事实追加
    r = client.post(f"{_fo(oid)}/agent/execute-proposal", headers=auth_headers,
                    json={"actionId": "act-1", "targetInstanceId": "inst-o1",
                          "parameters": {"note": "confirmed"}})
    assert r.status_code == 200
    log = r.json()["data"]
    assert log["status"] == "success"
    assert log["actorId"]                      # 执行者 = 确认的用户

    from app.models.ontology_formal import ObjectInstance
    inst = db.query(ObjectInstance).filter(ObjectInstance.id == "inst-o1").first()
    db.refresh(inst)
    assert inst.properties["status"] == "paid"

    # 需审批动作 → pending，进 HITL 队列，属性不变
    r = client.post(f"{_fo(oid)}/agent/execute-proposal", headers=auth_headers,
                    json={"actionId": "act-2", "targetInstanceId": "inst-o1"})
    assert r.json()["data"]["status"] == "pending"
    db.refresh(inst)
    assert inst.properties["status"] == "paid"     # 没被取消

    # 边界外动作 → 403
    _grant_actions(client, auth_headers, oid, ["act-1"])
    r = client.post(f"{_fo(oid)}/agent/execute-proposal", headers=auth_headers,
                    json={"actionId": "act-2", "targetInstanceId": "inst-o1"})
    assert r.status_code == 403


# ---------------------------------------------------------------- 回合编排


def test_chat_turn_with_fake_llm(client, auth_headers, modeled_ontology, db,
                                 admin_user, monkeypatch):
    """假 LLM：第一轮要求查订单，第二轮给出答案 → 验证轨迹、引用、持久化。"""
    oid = modeled_ontology["id"]

    # 造一个可用的模型配置（不会真的被调用）
    from app.models.model_config import ModelConfig
    db.add(ModelConfig(id=str(uuid.uuid4()), name="fake", provider="openai",
                       config_type="llm", models=["fake-model"],
                       created_by=admin_user.id))
    db.commit()

    calls = {"n": 0}

    def fake_chat(call_kwargs, messages, tools):
        calls["n"] += 1
        if calls["n"] == 1:
            assert any(t["name"] == "search_objects" for t in tools)
            assert messages[0]["role"] == "system" and "订单" in messages[0]["content"]
            return {"content": None, "usage": {"inputTokens": 10, "outputTokens": 5},
                    "tool_calls": [{"id": "tc1", "name": "search_objects",
                                    "arguments": {"object_type": "Order",
                                                  "filters": [{"property": "status", "op": "eq",
                                                               "value": "pending"}]}}]}
        # 第二轮能看到工具结果
        assert messages[-1]["role"] == "tool" and "SO-001" in messages[-1]["content"]
        return {"content": "待支付订单只有 SO-001。", "tool_calls": [],
                "usage": {"inputTokens": 20, "outputTokens": 8}}

    from app.ontologies.agent_runtime import llm_bridge
    monkeypatch.setattr(llm_bridge, "chat", fake_chat)

    r = client.post(f"{_fo(oid)}/agent/chat", headers=auth_headers,
                    json={"message": "哪些订单还没支付？", "stream": False})
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["error"] is None
    assert data["content"] == "待支付订单只有 SO-001。"
    assert len(data["steps"]) == 1 and data["steps"][0]["tool"] == "search_objects"
    assert data["citations"][0]["label"] == "SO-001"
    assert data["usage"]["outputTokens"] == 13

    # 会话与消息持久化，轨迹可审计
    conv_id = data["conversationId"]
    r = client.get(f"{_fo(oid)}/agent/conversations/{conv_id}", headers=auth_headers)
    msgs = r.json()["data"]["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[1]["steps"][0]["tool"] == "search_objects"

    r = client.get(f"{_fo(oid)}/agent/conversations", headers=auth_headers)
    assert any(c["id"] == conv_id for c in r.json()["data"])


def test_chat_without_model_config(client, auth_headers, modeled_ontology):
    oid = modeled_ontology["id"]
    r = client.post(f"{_fo(oid)}/agent/chat", headers=auth_headers,
                    json={"message": "你好", "stream": False})
    assert r.status_code == 200
    assert "模型配置" in (r.json()["data"]["error"] or "")


def test_disabled_agent(client, auth_headers, modeled_ontology):
    oid = modeled_ontology["id"]
    client.put(f"{_fo(oid)}/agent/profile", headers=auth_headers, json={"enabled": False})
    r = client.post(f"{_fo(oid)}/agent/chat", headers=auth_headers,
                    json={"message": "你好", "stream": False})
    assert "停用" in (r.json()["data"]["error"] or "")


# ---------------------------------------------------------------- 多跳遍历 & 护栏


def test_traverse_path_multi_hop(client, auth_headers, modeled_ontology, db):
    """2 跳：订单 →(order_supplier)→ 供应商 →(supplier_customer)→ 客户。"""
    oid = modeled_ontology["id"]
    from app.models.ontology_formal import ObjectType, LinkType, ObjectInstance, LinkInstance
    db.add(ObjectType(id="ot-customer", ontology_id=oid, name="Customer", display_name="客户",
                      primary_key="cname",
                      properties=[{"id": "pc", "name": "cname", "displayName": "客户名", "type": "string"}]))
    db.add(LinkType(id="lt-2", ontology_id=oid, name="supplier_customer", display_name="供应商-客户",
                    source_object_type_id="ot-supplier", target_object_type_id="ot-customer",
                    cardinality="one-to-many"))
    db.add(ObjectInstance(id="inst-c1", ontology_id=oid, object_type_id="ot-customer",
                          properties={"cname": "张三"}, computed={}))
    db.add(LinkInstance(id="li-2", ontology_id=oid, link_type_id="lt-2",
                        source_object_id="inst-s1", target_object_id="inst-c1"))
    db.commit()

    from app.ontologies.agent_runtime.boundary import build_scope, ToolError
    from app.ontologies.agent_runtime.toolkit import ToolRunner
    _, _, scope = build_scope(db, oid)
    runner = ToolRunner(db, scope)

    res = runner.run("traverse_path", {"instance_id": "inst-o1", "path": [
        {"link_type": "order_supplier", "direction": "out"},
        {"link_type": "supplier_customer", "direction": "out"},
    ]})
    assert res["returned"] == 1
    assert res["items"][0]["properties"]["cname"] == "张三"
    assert len(res["hops"]) == 2 and res["hops"][-1]["reached"] == 1
    assert runner.citations[-1]["label"] == "张三"

    # 缰绳：超过最大跳数 → 硬失败
    with pytest.raises(ToolError):
        runner.run("traverse_path", {"instance_id": "inst-o1",
                                     "path": [{"link_type": "order_supplier"}] * 6})

    # 边界：隐藏供应商 → 经其的链接不可见，多跳越界即失败（防经链接泄露隐藏类型）
    client.put(f"{_fo(oid)}/agent/profile", headers=auth_headers,
               json={"allowedObjectTypeIds": ["ot-order"]})
    _, _, scope2 = build_scope(db, oid)
    with pytest.raises(ToolError):
        ToolRunner(db, scope2).run("traverse_path", {"instance_id": "inst-o1",
                                   "path": [{"link_type": "order_supplier"}]})


def test_property_scope_guard(client, auth_headers, modeled_ontology, db):
    """越界属性硬失败：拼错/臆造的属性名当场报错，而非静默算错。"""
    oid = modeled_ontology["id"]
    from app.ontologies.agent_runtime.boundary import build_scope, ToolError
    from app.ontologies.agent_runtime.toolkit import ToolRunner
    _, _, scope = build_scope(db, oid)
    runner = ToolRunner(db, scope)

    with pytest.raises(ToolError):
        runner.run("aggregate_objects", {"object_type": "Order", "metric": "count",
                                         "group_by": "bogus_prop"})
    with pytest.raises(ToolError):
        runner.run("search_objects", {"object_type": "Order",
                                      "filters": [{"property": "nope", "op": "eq", "value": 1}]})

    # displayName 也能解析（状态 → status），并正常分组 + 附确定性图表
    grouped = runner.run("aggregate_objects", {"object_type": "订单", "metric": "count",
                                               "group_by": "状态"})
    assert {g["group"]: g["value"] for g in grouped["groups"]} == {"pending": 1, "paid": 1}
    assert grouped["partial"] is False and grouped["scanned"] == 2
