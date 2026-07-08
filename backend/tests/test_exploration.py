"""业务探索（exploration）模块测试：

  1. 会话 CRUD 与归属隔离
  2. 回合编排：假 LLM 驱动工具调用沉淀画布，轨迹/画布版本/标题持久化；
     非法元素被 pydantic 拒绝并把错误回填给 LLM（对话期修复回路）
  3. 需求文档：确定性章节永远生成，LLM 缺席时叙述节降级占位；版本递增
  4. 转化管线（纯确定性，不依赖 LLM）：类型映射 / 主键回退 / 悬空关系剔除 /
     规则挂载(disabled) / 审批传播 / 主体合并 / 场景覆盖检查 /
     derivation→激活函数草稿(enabled=false) / alert+事件→哨兵草稿(muted 影子) /
     未命中行为的 approval 规则必须产生警告（不得静默丢失）
  5. 落地：新建本体后 fo_* 表可查；函数/哨兵带三重闸门落地（enabled=false/muted/draft）；
     五类元素写 source 血缘（session/document/draft/draftKey/sourceRefs）；
     草稿可重复应用（同名跳过幂等，部分勾选后剩余元素可二次落地）；
     废弃后不可应用；保守合并——同名对象跳过、链接端点可绑定到目标本体既有类型
"""
import uuid

import pytest

from app.exploration import canvas as C
from app.exploration import converter as CV
from app.exploration.models import ExplorationSession

BASE = "/api/v2/exploration"


@pytest.fixture
def session(client, auth_headers):
    r = client.post(f"{BASE}/sessions", headers=auth_headers, json={})
    assert r.status_code == 201, r.text
    return r.json()["data"]


def _seed_canvas(db, session_id: str, canvas: dict) -> None:
    s = db.query(ExplorationSession).filter(ExplorationSession.id == session_id).first()
    s.canvas = canvas
    s.canvas_version = (s.canvas_version or 0) + 1
    db.commit()


def _demo_canvas() -> dict:
    cv = C.empty_canvas()
    cv, _, errs = C.upsert_elements(cv, "object", [
        {"name": "Order", "displayName": "订单", "keyAttribute": "order_no",
         "attributes": [
             {"name": "order_no", "displayName": "订单号", "typeHint": "文本", "required": True},
             {"name": "amount", "displayName": "金额", "typeHint": "金额"},
             {"name": "paid", "displayName": "是否已支付", "typeHint": "是否"},
         ],
         "relations": [{"target": "Supplier", "displayName": "归属供应商",
                        "cardinality": "many-to-one"},
                       {"target": "Ghost", "displayName": "悬空关系"}]},
        {"name": "Supplier", "displayName": "供应商",
         "attributes": [{"name": "sname", "displayName": "名称", "required": True}]},
    ])
    assert not errs
    cv, _, _ = C.upsert_elements(cv, "actor", [
        {"name": "Finance", "displayName": "财务", "kind": "role",
         "responsibilities": ["审核付款"]},
        {"name": "Supplier", "displayName": "供应商（主体）", "kind": "org"},   # 与对象同名 → 合并
        {"name": "ERP", "kind": "system"},                                    # system 不建对象
    ])
    cv, _, _ = C.upsert_elements(cv, "behavior", [
        {"name": "mark_paid", "displayName": "标记支付", "actor": "Finance",
         "object": "Order", "trigger": "收到银行回单",
         "inputs": [{"name": "note", "displayName": "备注", "typeHint": "文本"}],
         "constraints": ["金额必须大于 0"], "needsApproval": False},
    ])
    cv, _, _ = C.upsert_elements(cv, "rule", [
        {"name": "big_amount_approval", "displayName": "大额审批", "kind": "approval",
         "appliesTo": "mark_paid", "statement": "金额超过 1 万需要审批"},
        {"name": "amount_positive", "kind": "validation", "appliesTo": "mark_paid",
         "statement": "金额必须为正数", "errorMessage": "金额必须大于 0"},
        {"name": "orphan_rule", "kind": "alert", "appliesTo": "Warehouse",
         "statement": "库存低于安全线告警"},          # 目标未定义 → 哨兵草稿不绑对象
        {"name": "total_calc", "displayName": "订单总额计算", "kind": "derivation",
         "appliesTo": "Order", "statement": "总额 = 明细金额之和"},
    ])
    cv, _, _ = C.upsert_elements(cv, "event", [
        {"name": "order_paid", "displayName": "订单已支付", "source": "mark_paid",
         "payload": ["order_no", "amount"], "consequences": ["通知供应商发货"]},
        {"name": "daily_check", "displayName": "每日对账", "source": "time",
         "consequences": ["生成对账单"]},              # time 来源 → 定期扫描哨兵
    ])
    cv, _, _ = C.upsert_elements(cv, "scenario", [
        {"name": "pay_flow", "displayName": "支付流程", "goal": "完成订单支付",
         "objects": ["Order", "Invoice"], "behaviors": ["mark_paid", "refund"]},
    ])
    return cv


# ---------------------------------------------------------------- 会话


def test_session_crud(client, auth_headers, session):
    r = client.get(f"{BASE}/sessions", headers=auth_headers)
    assert any(s["id"] == session["id"] for s in r.json()["data"])

    r = client.get(f"{BASE}/sessions/{session['id']}", headers=auth_headers)
    data = r.json()["data"]
    assert data["messages"] == []
    assert set(data["canvas"].keys()) >= {"objects", "actors", "behaviors",
                                          "events", "rules", "scenarios"}
    assert data["completeness"]["counts"]["objects"] == 0

    r = client.delete(f"{BASE}/sessions/{session['id']}", headers=auth_headers)
    assert r.status_code == 204
    assert client.get(f"{BASE}/sessions/{session['id']}",
                      headers=auth_headers).status_code == 404


# ---------------------------------------------------------------- 回合编排


def _fake_model_config(db, admin_user):
    from app.models.model_config import ModelConfig
    db.add(ModelConfig(id=str(uuid.uuid4()), name="fake", provider="openai",
                       config_type="llm", models=["fake-model"],
                       created_by=admin_user.id))
    db.commit()


def test_chat_turn_persists_canvas(client, auth_headers, session, db, admin_user, monkeypatch):
    _fake_model_config(db, admin_user)
    calls = {"n": 0}

    def fake_chat(call_kwargs, messages, tools):
        calls["n"] += 1
        if calls["n"] == 1:
            assert any(t["name"] == "upsert_elements" for t in tools)
            assert "业务探索" in messages[0]["content"]
            return {"content": None, "usage": {"inputTokens": 5, "outputTokens": 5},
                    "tool_calls": [{"id": "tc1", "name": "upsert_elements",
                                    "arguments": {"kind": "object", "elements": [
                                        {"name": "Order", "displayName": "订单",
                                         "attributes": [{"name": "order_no", "required": True}]}]}}]}
        assert messages[-1]["role"] == "tool" and '"applied": 1' in messages[-1]["content"]
        return {"content": "已记录订单对象。它的业务主键是哪个属性？", "tool_calls": [],
                "usage": {"inputTokens": 5, "outputTokens": 5}}

    from app.ontologies.agent_runtime import llm_bridge
    monkeypatch.setattr(llm_bridge, "chat", fake_chat)

    r = client.post(f"{BASE}/sessions/{session['id']}/chat", headers=auth_headers,
                    json={"message": "我们要做订单管理", "stream": False})
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["error"] is None
    assert data["steps"][0]["tool"] == "upsert_elements"
    assert data["canvas"]["objects"][0]["name"] == "Order"
    assert data["completeness"]["counts"]["objects"] == 1

    r = client.get(f"{BASE}/sessions/{session['id']}", headers=auth_headers)
    detail = r.json()["data"]
    assert detail["title"] == "我们要做订单管理"          # 首条消息成为标题
    assert detail["canvasVersion"] == 1
    assert [m["role"] for m in detail["messages"]] == ["user", "assistant"]
    assert detail["messages"][1]["steps"][0]["tool"] == "upsert_elements"


def test_chat_invalid_elements_rejected(client, auth_headers, session, db, admin_user, monkeypatch):
    _fake_model_config(db, admin_user)
    calls = {"n": 0}

    def fake_chat(call_kwargs, messages, tools):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"content": None, "usage": None,
                    "tool_calls": [{"id": "tc1", "name": "upsert_elements",
                                    "arguments": {"kind": "object",
                                                  "elements": [{"displayName": "没有name字段"}]}}]}
        # 校验错误应回填，供 LLM 修正（对话期修复回路）
        assert "不合法" in messages[-1]["content"]
        return {"content": "收到，我会补全 name。", "tool_calls": [], "usage": None}

    from app.ontologies.agent_runtime import llm_bridge
    monkeypatch.setattr(llm_bridge, "chat", fake_chat)

    r = client.post(f"{BASE}/sessions/{session['id']}/chat", headers=auth_headers,
                    json={"message": "记录一个对象", "stream": False})
    data = r.json()["data"]
    assert data["error"] is None
    assert data["canvas"] is None            # 画布没有被非法元素污染
    r = client.get(f"{BASE}/sessions/{session['id']}/canvas", headers=auth_headers)
    assert r.json()["data"]["completeness"]["counts"]["objects"] == 0


# ---------------------------------------------------------------- 需求文档


def test_document_generation_without_llm(client, auth_headers, session, db):
    r = client.post(f"{BASE}/sessions/{session['id']}/documents",
                    headers=auth_headers, json={})
    assert r.status_code == 422                      # 空画布拒绝生成

    _seed_canvas(db, session["id"], _demo_canvas())
    r = client.post(f"{BASE}/sessions/{session['id']}/documents",
                    headers=auth_headers, json={})
    assert r.status_code == 201, r.text
    doc = r.json()["data"]
    md = doc["contentMd"]
    # 确定性章节忠实于画布；LLM 缺席时叙述节降级为占位而非失败
    assert "## 4. 对象模型" in md and "订单" in md and "order_no" in md
    assert "标记支付" in md and "大额审批" in md and "支付流程" in md
    assert "待澄清" in md

    r = client.post(f"{BASE}/sessions/{session['id']}/documents",
                    headers=auth_headers, json={})
    assert r.json()["data"]["version"] == 2          # 版本递增

    r = client.get(f"{BASE}/sessions/{session['id']}/documents", headers=auth_headers)
    assert [d["version"] for d in r.json()["data"]] == [2, 1]


# ---------------------------------------------------------------- 转化管线（确定性单测）


def test_converter_deterministic_mapping():
    draft, report = CV.build_draft(_demo_canvas())

    ot = {o["name"]: o for o in draft["objectTypes"]}
    # 对象 + 非 system 主体（同名 Supplier 合并、ERP 跳过）
    assert set(ot) == {"Order", "Supplier", "Finance"}
    order = ot["Order"]
    assert order["primaryKey"] == "order_no"
    types = {p["name"]: p["type"] for p in order["properties"]}
    assert types["amount"] == "number" and types["paid"] == "boolean" \
        and types["order_no"] == "string"
    # Supplier 未指定主键 → 自动补 id 并回退
    assert ot["Supplier"]["primaryKey"] == "id"
    assert any("Supplier" in w and "主键" in w for w in report["warnings"])

    # 悬空关系剔除、合法关系保留基数
    links = draft["linkTypes"]
    assert len(links) == 1 and links[0]["cardinality"] == "many-to-one"
    assert any("Ghost" in w for w in report["warnings"])

    act = draft["actions"][0]
    assert act["name"] == "mark_paid"
    assert act["requiresApproval"] is True           # approval 规则传播
    # 约束 + validation 规则 → disabled 待形式化规则
    assert len(act["rules"]) == 2
    assert all(r["enabled"] is False and r["config"]["type"] == "validation"
               for r in act["rules"])
    assert any("金额必须大于 0" == r["config"]["errorMessage"] for r in act["rules"])
    assert act["parameters"][0]["name"] == "note"
    assert "订单已支付" in act["description"]         # 事件并入动作描述

    # derivation 规则 → 激活函数草稿（enabled=false，绑定作用对象）
    fns = draft["functions"]
    assert len(fns) == 1
    fn = fns[0]
    assert fn["name"] == "total_calc" and fn["enabled"] is False
    assert fn["displayName"].startswith("待形式化")
    assert fn["functionType"] == "object" and fn["targetObjectTypeKey"] == "obj:order"
    assert fn["language"] == "expression" and fn["body"] == ""
    assert "总额" in fn["description"]

    # alert 规则 + 事件 → 哨兵草稿（muted 影子 + enabled=false + status=draft）
    sens = {s["name"]: s for s in draft["sentinels"]}
    assert set(sens) == {"orphan_rule", "order_paid", "daily_check"}
    assert all(s["muted"] is True and s["enabled"] is False and s["status"] == "draft"
               and s["displayName"].startswith("待形式化") for s in sens.values())
    # 事件来源=行为 → 绑定行为的作用对象，变化驱动
    assert sens["order_paid"]["bindingObjectKey"] == "obj:order"
    assert sens["order_paid"]["onChange"] is True and sens["order_paid"]["onSchedule"] is False
    assert "order_no" in sens["order_paid"]["description"]     # 事件载荷不再丢弃
    # 事件来源=time → 定期扫描
    assert sens["daily_check"]["onSchedule"] is True and sens["daily_check"]["onChange"] is False
    # 告警目标未定义 → 不绑对象 + 警告提示补绑定
    assert sens["orphan_rule"]["bindingObjectKey"] is None
    assert any("orphan_rule" in w and "绑定" in w for w in report["warnings"])

    # 场景覆盖检查：Invoice / refund 缺失
    cov = report["scenarioCoverage"]
    assert cov and cov[0]["missingObjects"] == ["Invoice"] \
        and cov[0]["missingBehaviors"] == ["refund"]
    assert report["llmRefined"] is False


def test_converter_approval_unmatched_not_silent():
    """approval 规则未命中任何行为时必须产生警告（修复此前的静默丢失）。"""
    cv = C.empty_canvas()
    cv, _, _ = C.upsert_elements(cv, "rule", [
        {"name": "ghost_approval", "displayName": "幽灵审批", "kind": "approval",
         "appliesTo": "not_exist_behavior", "statement": "需要审批"},
    ])
    _, report = CV.build_draft(cv)
    assert any("ghost_approval" in w and "审批" in w for w in report["warnings"])


def test_converter_llm_refine_discarded_on_bad_json(monkeypatch):
    """LLM 补缺输出垃圾 → 重试后丢弃补丁，确定性结果不受影响。"""
    from app.ontologies.agent_runtime import llm_bridge
    monkeypatch.setattr(llm_bridge, "chat",
                        lambda kw, msgs, tools: {"content": "这不是 JSON", "tool_calls": [],
                                                 "usage": None})
    draft, report = CV.build_draft(_demo_canvas(), call_kwargs={"model": "fake"})
    assert report["llmRefined"] is False
    assert any("补缺" in w for w in report["warnings"])
    assert {o["name"] for o in draft["objectTypes"]} == {"Order", "Supplier", "Finance"}


def test_converter_llm_refine_whitelist(monkeypatch):
    """LLM 补缺只允许白名单字段生效，非法类型/基数被忽略。"""
    import json as _json
    from app.ontologies.agent_runtime import llm_bridge
    patch = {"objectTypes": [{"key": "obj:order", "description": "客户订单",
                              "properties": [{"name": "amount", "type": "decimal128"},
                                             {"name": "paid", "type": "boolean",
                                              "displayName": "已支付"}]}],
             "linkTypes": [{"key": "link:order_supplier", "cardinality": "many-to-many-badly"}],
             "actions": [{"key": "act:mark_paid", "description": "财务确认收款后标记"}]}
    monkeypatch.setattr(llm_bridge, "chat",
                        lambda kw, msgs, tools: {"content": _json.dumps(patch),
                                                 "tool_calls": [], "usage": None})
    draft, report = CV.build_draft(_demo_canvas(), call_kwargs={"model": "fake"})
    assert report["llmRefined"] is True
    order = next(o for o in draft["objectTypes"] if o["key"] == "obj:order")
    types = {p["name"]: p for p in order["properties"]}
    assert order["description"] == "客户订单"
    assert types["amount"]["type"] == "number"        # 非法类型被忽略，保留确定性结果
    assert types["paid"]["displayName"] == "已支付"
    link = draft["linkTypes"][0]
    assert link["cardinality"] == "many-to-one"       # 非法基数被忽略
    assert any("不合法" in w for w in report["warnings"])


# ---------------------------------------------------------------- 草稿生成与落地


def _make_draft(client, auth_headers, session_id, db, target_ontology_id=None):
    _seed_canvas(db, session_id, _demo_canvas())
    r = client.post(f"{BASE}/sessions/{session_id}/documents", headers=auth_headers, json={})
    doc_id = r.json()["data"]["id"]
    body = {"targetOntologyId": target_ontology_id} if target_ontology_id else {}
    r = client.post(f"{BASE}/documents/{doc_id}/drafts", headers=auth_headers, json=body)
    assert r.status_code == 201, r.text
    return r.json()["data"]


def test_draft_apply_to_new_ontology(client, auth_headers, session, db):
    draft = _make_draft(client, auth_headers, session["id"], db)
    assert draft["status"] == "draft"

    r = client.post(f"{BASE}/drafts/{draft['id']}/apply", headers=auth_headers,
                    json={"newOntology": {"name": "订单管理本体", "domain": "供应链"}})
    assert r.status_code == 200, r.text
    result = r.json()["data"]
    oid = result["ontologyId"]
    assert result["created"] == {"objectTypes": 3, "linkTypes": 1, "actions": 1,
                                 "functions": 1, "sentinels": 3}

    # 图谱编辑器数据通道可见（与编辑器同一 /full 端点）
    r = client.get(f"/api/v2/formal/ontologies/{oid}/full", headers=auth_headers)
    full = r.json()["data"]
    names = {o["name"] for o in full["objectTypes"]}
    assert names == {"Order", "Supplier", "Finance"}
    order = next(o for o in full["objectTypes"] if o["name"] == "Order")
    assert order["primaryKey"] == "order_no"
    assert full["linkTypes"][0]["cardinality"] == "many-to-one"
    action = full["actions"][0]
    assert action["requiresApproval"] is True
    assert action["objectTypeId"] == order["id"]      # 名称引用被解析成真实 id

    # 激活函数落地：enabled=false 休眠、绑定解析成真实对象 id、待形式化标记
    fns = full["functions"]
    assert len(fns) == 1
    assert fns[0]["enabled"] is False and fns[0]["functionType"] == "object"
    assert fns[0]["targetObjectTypeId"] == order["id"]
    assert fns[0]["displayName"].startswith("待形式化")

    # 哨兵落地：三重闸门（muted + enabled=false + status=draft），不进执行链路
    from app.ontologies.sentinels.models import Sentinel
    sens = {s.name: s for s in db.query(Sentinel).filter(Sentinel.ontology_id == oid)}
    assert set(sens) == {"orphan_rule", "order_paid", "daily_check"}
    assert all(s.muted and not s.enabled and s.status == "draft" for s in sens.values())
    assert sens["order_paid"].bindings[0]["objectTypeId"] == order["id"]
    assert sens["order_paid"].primary_alias == "a" and sens["order_paid"].on_change
    assert sens["daily_check"].on_schedule and not sens["daily_check"].on_change
    assert sens["orphan_rule"].bindings == [] and sens["orphan_rule"].primary_alias is None

    # 血缘：五类元素都带 source（session/document/draft/draftKey/sourceRefs）
    from app.ontologies.formal_modeling.models import (ActionType, LinkType,
                                                       ObjectType, OntologyFunction)
    for model in (ObjectType, LinkType, ActionType, OntologyFunction, Sentinel):
        rows = db.query(model).filter(model.ontology_id == oid).all()
        assert rows and all(
            (x.source or {}).get("kind") == "business_exploration"
            and x.source.get("draftId") == draft["id"]
            and x.source.get("sessionId") == session["id"]
            and x.source.get("draftKey") and x.source.get("sourceRefs")
            for x in rows), f"{model.__name__} 血缘缺失"


def test_draft_reapply_partial_then_rest(client, auth_headers, session, db):
    """部分勾选落地后，剩余元素可二次落地到同一本体；重复应用同名跳过（幂等）。"""
    draft = _make_draft(client, auth_headers, session["id"], db)

    r = client.post(f"{BASE}/drafts/{draft['id']}/apply", headers=auth_headers,
                    json={"selectedKeys": ["obj:order"],
                          "newOntology": {"name": "分批落地本体"}})
    assert r.status_code == 200, r.text
    first = r.json()["data"]
    assert first["created"]["objectTypes"] == 1 and first["created"]["sentinels"] == 0

    # 二次应用（全选）：不再 409，固定合并进首次的本体；已落地的 Order 同名跳过
    r = client.post(f"{BASE}/drafts/{draft['id']}/apply", headers=auth_headers, json={})
    assert r.status_code == 200, r.text
    second = r.json()["data"]
    assert second["ontologyId"] == first["ontologyId"]
    assert second["created"]["objectTypes"] == 2          # Supplier + Finance
    assert second["created"]["linkTypes"] == 1            # 端点绑到首批落地的 Order
    assert second["created"]["functions"] == 1 and second["created"]["sentinels"] == 3
    assert any(s["key"] == "obj:order" for s in second["skipped"])

    # 三次应用：全部同名跳过，零新建（幂等收敛）
    r = client.post(f"{BASE}/drafts/{draft['id']}/apply", headers=auth_headers, json={})
    third = r.json()["data"]
    assert third["ontologyId"] == first["ontologyId"]
    assert all(v == 0 for v in third["created"].values())


def test_draft_discard(client, auth_headers, session, db):
    """废弃草稿：幂等；废弃后不可应用。"""
    draft = _make_draft(client, auth_headers, session["id"], db)

    r = client.post(f"{BASE}/drafts/{draft['id']}/discard", headers=auth_headers)
    assert r.status_code == 200 and r.json()["data"]["status"] == "discarded"
    # 幂等
    r = client.post(f"{BASE}/drafts/{draft['id']}/discard", headers=auth_headers)
    assert r.status_code == 200 and r.json()["data"]["status"] == "discarded"

    r = client.post(f"{BASE}/drafts/{draft['id']}/apply", headers=auth_headers,
                    json={"newOntology": {"name": "不该被创建"}})
    assert r.status_code == 409


def test_draft_conservative_merge(client, auth_headers, session, db, ontology):
    """合并进已有本体：同名跳过并在报告/结果中体现，链接端点绑定到既有类型。"""
    oid = ontology["id"]
    r = client.put(f"/api/v2/formal/ontologies/{oid}/full", headers=auth_headers, json={
        "objectTypes": [{"id": "ot-order", "name": "Order", "displayName": "已有订单",
                         "primaryKey": "order_no",
                         "properties": [{"id": "p1", "name": "order_no", "displayName": "订单号",
                                         "type": "string", "required": True}],
                         "positionX": 0, "positionY": 0}],
        "linkTypes": [], "actions": [], "functions": [], "instances": [], "linkInstances": [],
    })
    assert r.status_code == 200, r.text

    draft = _make_draft(client, auth_headers, session["id"], db, target_ontology_id=oid)
    conflicted = {o["name"]: o["conflict"] for o in draft["draft"]["objectTypes"]}
    assert conflicted["Order"] is True and conflicted["Supplier"] is False
    assert any("Order" in c for c in draft["report"]["conflicts"])

    r = client.post(f"{BASE}/drafts/{draft['id']}/apply", headers=auth_headers, json={})
    result = r.json()["data"]
    assert result["ontologyId"] == oid
    assert result["created"]["objectTypes"] == 2      # Order 被跳过
    assert any("Order" in s["reason"] for s in result["skipped"])
    assert result["created"]["linkTypes"] == 1        # Order→Supplier 绑到既有 ot-order

    r = client.get(f"/api/v2/formal/ontologies/{oid}/full", headers=auth_headers)
    full = r.json()["data"]
    assert {o["name"] for o in full["objectTypes"]} == {"Order", "Supplier", "Finance"}
    link = full["linkTypes"][0]
    assert link["sourceObjectTypeId"] == "ot-order"   # 端点解析到已有对象类型


def test_apply_requires_new_ontology_name(client, auth_headers, session, db):
    draft = _make_draft(client, auth_headers, session["id"], db)
    r = client.post(f"{BASE}/drafts/{draft['id']}/apply", headers=auth_headers, json={})
    assert r.status_code == 422
    r = client.post(f"{BASE}/drafts/{draft['id']}/apply", headers=auth_headers,
                    json={"selectedKeys": [], "newOntology": {"name": "x"}})
    assert r.status_code == 422


# ---------------------------------------------------------------- 技能（use_skill 渐进披露）


def test_chat_with_skill_catalog_and_use_skill(client, auth_headers, session, db,
                                               admin_user, monkeypatch):
    """技能目录注入系统提示；use_skill 取全文；无技能时不挂 use_skill 工具。"""
    from app.capabilities.builtin import seed_builtin_skills
    seed_builtin_skills(db)
    _fake_model_config(db, admin_user)
    calls = {"n": 0}

    def fake_chat(call_kwargs, messages, tools):
        calls["n"] += 1
        if calls["n"] == 1:
            sysmsg = messages[0]["content"]
            assert "可用技能" in sysmsg and "er_diagram" in sysmsg
            # 目录只有一句话描述，不含全文指令（渐进披露）
            assert "输出契约" not in sysmsg
            assert any(t["name"] == "use_skill" for t in tools)
            return {"content": None, "usage": None,
                    "tool_calls": [{"id": "t1", "name": "use_skill",
                                    "arguments": {"name": "er_diagram"}}]}
        # 第二轮：全文指令已回填
        assert "erDiagram" in messages[-1]["content"]
        return {"content": "```mermaid\nerDiagram\n```", "tool_calls": [], "usage": None}

    from app.ontologies.agent_runtime import llm_bridge
    monkeypatch.setattr(llm_bridge, "chat", fake_chat)

    r = client.post(f"{BASE}/sessions/{session['id']}/chat", headers=auth_headers,
                    json={"message": "画个ER图", "stream": False})
    data = r.json()["data"]
    assert data["error"] is None
    assert data["steps"][0]["tool"] == "use_skill"
    assert "激活技能" in data["steps"][0]["summary"]
    assert "mermaid" in data["content"]


def test_chat_without_skills_has_no_use_skill_tool(client, auth_headers, session, db,
                                                   admin_user, monkeypatch):
    _fake_model_config(db, admin_user)

    def fake_chat(call_kwargs, messages, tools):
        assert not any(t["name"] == "use_skill" for t in tools)
        assert "可用技能" not in messages[0]["content"]
        return {"content": "好的。", "tool_calls": [], "usage": None}

    from app.ontologies.agent_runtime import llm_bridge
    monkeypatch.setattr(llm_bridge, "chat", fake_chat)
    r = client.post(f"{BASE}/sessions/{session['id']}/chat", headers=auth_headers,
                    json={"message": "你好", "stream": False})
    assert r.json()["data"]["error"] is None


def test_use_skill_unknown_name(client, auth_headers, session, db, admin_user, monkeypatch):
    from app.capabilities.builtin import seed_builtin_skills
    seed_builtin_skills(db)
    _fake_model_config(db, admin_user)
    calls = {"n": 0}

    def fake_chat(call_kwargs, messages, tools):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"content": None, "usage": None,
                    "tool_calls": [{"id": "t1", "name": "use_skill",
                                    "arguments": {"name": "no_such"}}]}
        assert "不存在或未启用" in messages[-1]["content"]
        return {"content": "抱歉。", "tool_calls": [], "usage": None}

    from app.ontologies.agent_runtime import llm_bridge
    monkeypatch.setattr(llm_bridge, "chat", fake_chat)
    r = client.post(f"{BASE}/sessions/{session['id']}/chat", headers=auth_headers,
                    json={"message": "画图", "stream": False})
    assert r.json()["data"]["steps"][0].get("error")


# ---------------------------------------------------------------- 确定性 ER 图


def test_er_mermaid_deterministic():
    from app.exploration.diagram import er_mermaid
    text = er_mermaid(_demo_canvas())
    assert text.startswith("erDiagram")
    assert "string order_no PK" in text          # 主键标记
    assert "number amount" in text               # 类型映射
    assert 'Order }o--|| Supplier : "归属供应商"' in text
    assert "Ghost" not in text                   # 悬空关系不进图
    # 无属性的主体对象兜底 id PK — Finance 来自画布? demo canvas 的 actor 不进 er（er 只投影 objects）
    assert "Finance" not in text
    assert er_mermaid({}) == ""                  # 空画布


def test_er_diagram_endpoint(client, auth_headers, session, db):
    r = client.get(f"{BASE}/sessions/{session['id']}/diagrams/er", headers=auth_headers)
    assert r.status_code == 422                  # 空画布拒绝

    _seed_canvas(db, session["id"], _demo_canvas())
    r = client.get(f"{BASE}/sessions/{session['id']}/diagrams/er", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["data"]["mermaid"].startswith("erDiagram")


# ---------------------------------------------------------------- 会话附件


def _upload(client, headers, sid, filename, content, content_type="text/plain"):
    return client.post(f"{BASE}/sessions/{sid}/attachments", headers=headers,
                       files={"file": (filename, content, content_type)})


def test_attachment_upload_list_inject_delete(client, auth_headers, session, db,
                                              tmp_path, monkeypatch):
    """上传→列表→注入对话上下文→删除 全链路。"""
    from app.config import settings
    from app.exploration.orchestrator import _attachments_block
    monkeypatch.setattr(settings, "uploads_dir", str(tmp_path))
    sid = session["id"]

    r = _upload(client, auth_headers, sid, "spec.md",
                "# 采购单\n采购单包含供应商、金额、下单日期。".encode("utf-8"))
    assert r.status_code == 201, r.text
    a = r.json()["data"]
    assert a["filename"] == "spec.md" and a["status"] == "ready" and a["charCount"] > 0
    aid = a["id"]

    r = client.get(f"{BASE}/sessions/{sid}/attachments", headers=auth_headers)
    assert r.status_code == 200 and [x["id"] for x in r.json()["data"]] == [aid]

    # 附件内容注入引导师上下文
    block = _attachments_block(db, sid)
    assert "spec.md" in block and "采购单" in block

    r = client.delete(f"{BASE}/sessions/{sid}/attachments/{aid}", headers=auth_headers)
    assert r.status_code == 204
    assert client.get(f"{BASE}/sessions/{sid}/attachments", headers=auth_headers).json()["data"] == []
    assert _attachments_block(db, sid) == ""


def test_attachment_rejects_unsupported_type(client, auth_headers, session, tmp_path, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "uploads_dir", str(tmp_path))
    r = _upload(client, auth_headers, session["id"], "malware.exe", b"MZ\x90\x00")
    assert r.status_code == 400


def test_attachment_cross_session_isolation(client, auth_headers, tmp_path, monkeypatch):
    """附件严格绑定会话：B 会话看不到 A 会话的附件。"""
    from app.config import settings
    monkeypatch.setattr(settings, "uploads_dir", str(tmp_path))
    a = client.post(f"{BASE}/sessions", headers=auth_headers, json={}).json()["data"]["id"]
    b = client.post(f"{BASE}/sessions", headers=auth_headers, json={}).json()["data"]["id"]

    assert _upload(client, auth_headers, a, "note.txt", b"hello world").status_code == 201
    assert client.get(f"{BASE}/sessions/{b}/attachments", headers=auth_headers).json()["data"] == []
    assert len(client.get(f"{BASE}/sessions/{a}/attachments", headers=auth_headers).json()["data"]) == 1


def test_attachment_cascade_on_session_delete(client, auth_headers, db, tmp_path, monkeypatch):
    from app.config import settings
    from app.exploration.models import ExplorationAttachment
    monkeypatch.setattr(settings, "uploads_dir", str(tmp_path))
    sid = client.post(f"{BASE}/sessions", headers=auth_headers, json={}).json()["data"]["id"]

    assert _upload(client, auth_headers, sid, "a.txt", b"data").status_code == 201
    assert db.query(ExplorationAttachment).filter_by(session_id=sid).count() == 1

    assert client.delete(f"{BASE}/sessions/{sid}", headers=auth_headers).status_code == 204
    assert db.query(ExplorationAttachment).filter_by(session_id=sid).count() == 0
