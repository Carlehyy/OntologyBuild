"""业务探索语义保真回归测试。

覆盖此前常规结构测试没有验证的四类风险：
1. display_name 引用通过质量门后，转换器也必须解析到同一个 canonical 元素；
2. actor/object 合并不能丢属性、职责与血缘；
3. 对象级规则必须转为可审计草稿或显式 blocking；
4. 文档必须完整渲染契约，并能识别来源画布已经 stale。
"""
from __future__ import annotations

import copy

from app.exploration import canvas as C
from app.exploration import converter as CV
from app.exploration import readiness as R
from app.exploration.models import (ExplorationDocument, ExplorationSession)
from app.ontologies.formal_modeling.models import ActionType

BASE = "/api/v2/exploration"


def _seed_canvas(db, session_id: str, canvas: dict) -> ExplorationSession:
    session = db.query(ExplorationSession).filter_by(id=session_id).one()
    session.canvas = canvas
    session.canvas_version = int(session.canvas_version or 0) + 1
    db.commit()
    db.refresh(session)
    return session


def _create_session(client, auth_headers) -> dict:
    response = client.post(
        f"{BASE}/sessions", headers=auth_headers, json={"title": "语义保真测试"})
    assert response.status_code == 201, response.text
    return response.json()["data"]


def _ready_alias_canvas(*, object_approval: bool = False) -> dict:
    canvas = C.empty_canvas()
    canvas, _, errors = C.upsert_elements(canvas, "object", [
        {
            "name": "Order", "displayName": "订单", "keyAttribute": "order_id",
            "attributes": [
                {"name": "order_id", "displayName": "订单号",
                 "typeHint": "文本", "required": True},
                {"name": "status", "displayName": "状态", "typeHint": "枚举",
                 "enum": ["待确认", "已确认", "已取消"]},
            ],
            # 故意使用目标对象 displayName，验证转换器与 readiness 同口径。
            "relations": [{"target": "客户", "displayName": "下单客户",
                           "cardinality": "many-to-one"}],
        },
        {
            "name": "Customer", "displayName": "客户", "keyAttribute": "customer_id",
            "attributes": [
                {"name": "customer_id", "displayName": "客户编号",
                 "typeHint": "文本", "required": True},
            ],
        },
    ])
    assert not errors
    canvas, _, errors = C.upsert_elements(canvas, "actor", [{
        "name": "Operator", "displayName": "运营", "kind": "role",
        "responsibilities": ["确认订单", "处理取消"],
        "attributes": [{"name": "employee_no", "displayName": "员工号",
                        "typeHint": "文本", "required": True}],
        "keyAttribute": "employee_no",
    }])
    assert not errors
    canvas, _, errors = C.upsert_elements(canvas, "behavior", [
        {
            "name": "confirm_order", "displayName": "确认订单",
            # 两个引用均故意使用 displayName。
            "actor": "运营", "object": "订单", "trigger": "收到付款",
            "outcome": "订单从待确认变为已确认",
            "inputs": [{
                "name": "channel", "displayName": "确认渠道", "typeHint": "枚举",
                "required": True, "enum": ["网银", "柜台"], "notes": "原始确认来源",
            }],
        },
        {
            "name": "cancel_order", "displayName": "取消订单",
            "actor": "运营", "object": "订单", "trigger": "客户取消",
            "outcome": "订单从待确认变为已取消",
        },
    ])
    assert not errors
    canvas, _, errors = C.upsert_elements(canvas, "rule", [{
        "name": "approval", "displayName": "大额审批", "kind": "approval",
        "appliesTo": "订单" if object_approval else "确认订单",
        "statement": "订单金额 >= 50000 元需要审批",
    }])
    assert not errors
    canvas, _, errors = C.upsert_elements(canvas, "event", [{
        "name": "confirmed", "displayName": "订单已确认",
        "source": "确认订单", "payload": ["order_id"],
        "consequences": ["通知客户"],
    }])
    assert not errors
    canvas, _, errors = C.upsert_elements(canvas, "scenario", [{
        "name": "order_flow", "displayName": "订单处理流程", "goal": "处理订单",
        "actors": ["运营"],
        "steps": ["运营确认订单", "如果成功则完成", "订单结束"],
        "objects": ["订单", "客户"], "behaviors": ["确认订单", "取消订单"],
        "branches": [
            {"fromStep": 2, "toStep": 3, "condition": "确认成功，状态 = 已确认"},
            {"fromStep": 2, "toStep": 3, "condition": "客户取消，状态 = 已取消"},
        ],
        "expectedOutcome": "订单状态明确",
    }])
    assert not errors
    assert R.evaluate(canvas)["ready"] is True
    return canvas


def test_display_name_references_survive_canvas_to_draft_and_lineage(
        client, auth_headers, db):
    canvas = _ready_alias_canvas()
    draft, report = CV.build_draft(canvas)

    link = draft["linkTypes"][0]
    assert link["sourceKey"] == "obj:order"
    assert link["targetKey"] == "obj:customer"
    assert link["sourceName"] == "Order" and link["targetName"] == "Customer"

    action = next(item for item in draft["actions"] if item["name"] == "confirm_order")
    assert action["objectTypeKey"] == "obj:order"
    assert action["objectTypeName"] == "Order"
    assert action["requiresApproval"] is True
    assert action["rules"] == []  # approval 传播到 requiresApproval，不伪装成 validation
    assert action["actorRefs"][0]["name"] == "Operator"
    assert action["actorRefs"][0]["displayName"] == "运营"
    assert action["actorRefs"][0]["responsibilities"] == ["确认订单", "处理取消"]
    assert "订单已确认" in action["description"]

    sentinel = next(item for item in draft["sentinels"] if item["name"] == "confirmed")
    assert sentinel["bindingObjectKey"] == "obj:order"
    assert report["semanticFidelity"] == {
        "blockingCount": 0, "unsupportedCount": 2, "readyToApply": True}
    assert {item["code"] for item in report["semanticIssues"]} == {
        "actor_runtime_binding_unsupported"}

    # 真正落地后，ActionType 没有虚构 actor 列；完整 actorRefs 留在 source 血缘。
    session = _create_session(client, auth_headers)
    _seed_canvas(db, session["id"], canvas)
    document = client.post(
        f"{BASE}/sessions/{session['id']}/documents", headers=auth_headers, json={})
    assert document.status_code == 201, document.text
    draft_response = client.post(
        f"{BASE}/documents/{document.json()['data']['id']}/drafts",
        headers=auth_headers, json={},
    )
    assert draft_response.status_code == 201, draft_response.text
    stored_draft = draft_response.json()["data"]
    applied = client.post(
        f"{BASE}/drafts/{stored_draft['id']}/apply", headers=auth_headers,
        json={"newOntology": {"name": f"语义保真-{session['id'][:8]}"}},
    )
    assert applied.status_code == 200, applied.text
    ontology_id = applied.json()["data"]["ontologyId"]
    stored_action = db.query(ActionType).filter(
        ActionType.ontology_id == ontology_id,
        ActionType.name == "confirm_order",
    ).one()
    assert stored_action.source["actorRefs"][0]["name"] == "Operator"
    assert stored_action.source["actorRefs"][0]["responsibilities"] == ["确认订单", "处理取消"]
    assert set(stored_action.source["sourceRefs"]) >= {
        action["actorRefs"][0]["id"],
        next(item["id"] for item in canvas["events"] if item["name"] == "confirmed"),
        next(item["id"] for item in canvas["rules"] if item["name"] == "approval"),
    }


def test_same_name_actor_object_merges_semantics_or_reports_conflict():
    canvas = C.empty_canvas()
    canvas, _, _ = C.upsert_elements(canvas, "object", [{
        "name": "Partner", "displayName": "合作方", "description": "交易对象",
        "keyAttribute": "partner_id",
        "attributes": [
            {"name": "partner_id", "typeHint": "文本", "required": True},
            {"name": "credit", "typeHint": "文本"},
        ],
    }])
    canvas, _, _ = C.upsert_elements(canvas, "actor", [{
        "name": "Partner", "displayName": "合作方主体", "kind": "org",
        "description": "签约参与方", "responsibilities": ["签署合同"],
        "keyAttribute": "partner_id",
        "attributes": [
            {"name": "partner_id", "typeHint": "文本", "required": True},
            {"name": "contact_email", "typeHint": "文本"},
            # 同名字段类型冲突必须报告；原始主体契约仍保存在 actorMetadata。
            {"name": "credit", "typeHint": "数字"},
        ],
    }])

    draft, report = CV.build_draft(canvas)
    assert len(draft["objectTypes"]) == 1
    partner = draft["objectTypes"][0]
    assert partner["origin"] == "object+actor"
    assert {prop["name"] for prop in partner["properties"]} == {
        "partner_id", "credit", "contact_email"}
    assert "签约参与方" in partner["description"] and "签署合同" in partner["description"]
    assert len(partner["sourceRefs"]) == 2
    assert partner["actorMetadata"][0]["responsibilities"] == ["签署合同"]
    assert any(item["name"] == "credit"
               for item in partner["actorMetadata"][0]["attributes"])
    issue = next(item for item in report["semanticIssues"]
                 if item["code"] == "actor_object_attribute_conflict")
    assert issue["severity"] == "blocking" and issue["key"] == "obj:partner"
    assert report["semanticFidelity"]["readyToApply"] is False


def test_ambiguous_display_name_is_blocking_not_arbitrarily_resolved():
    canvas = C.empty_canvas()
    canvas, _, _ = C.upsert_elements(canvas, "object", [
        {
            "name": "Order", "displayName": "订单", "keyAttribute": "id",
            "attributes": [{"name": "id", "typeHint": "文本"}],
            "relations": [{"target": "客户", "cardinality": "many-to-one"}],
        },
        {
            "name": "RetailCustomer", "displayName": "客户", "keyAttribute": "id",
            "attributes": [{"name": "id", "typeHint": "文本"}],
        },
        {
            "name": "EnterpriseCustomer", "displayName": "客户", "keyAttribute": "id",
            "attributes": [{"name": "id", "typeHint": "文本"}],
        },
    ])
    draft, report = CV.build_draft(canvas)
    assert draft["linkTypes"] == []
    issue = next(item for item in report["semanticIssues"]
                 if item["code"] == "relation_target_unresolved")
    assert issue["severity"] == "blocking"
    assert "多个同名/同显示名候选" in issue["message"]
    assert report["semanticFidelity"]["readyToApply"] is False


def test_object_rules_map_or_block_instead_of_disappearing(client, auth_headers, db):
    canvas = C.empty_canvas()
    canvas, _, _ = C.upsert_elements(canvas, "object", [{
        "name": "Order", "displayName": "订单", "keyAttribute": "id",
        "attributes": [{"name": "id", "typeHint": "文本", "required": True}],
    }])
    canvas, _, _ = C.upsert_elements(canvas, "rule", [
        {
            "name": "amount_positive", "displayName": "金额为正",
            "kind": "validation", "appliesTo": "订单",
            "statement": "金额 >= 0 元", "errorMessage": "金额不能为负数",
        },
        {
            "name": "object_approval", "displayName": "对象审批",
            "kind": "approval", "appliesTo": "订单",
            "statement": "金额 >= 50000 元需要审批",
        },
    ])
    draft, report = CV.build_draft(canvas)
    function = next(item for item in draft["functions"]
                    if item["name"] == "amount_positive")
    assert function["semanticRole"] == "object_validation"
    assert function["functionType"] == "object"
    assert function["returnType"] == "boolean"
    assert function["enabled"] is False and function["body"] == ""
    assert "金额不能为负数" in function["description"]
    assert function["targetObjectTypeKey"] == "obj:order"
    assert function["sourceRefs"]

    unsupported = next(item for item in report["semanticIssues"]
                       if item["code"] == "object_approval_unsupported")
    assert unsupported["severity"] == "blocking"
    assert not any("暂无法映射" in warning for warning in report["warnings"])

    # ready 画布里的对象级 approval 默认直接阻断草稿生成，而不是产出“全绿但丢规则”的草稿。
    session = _create_session(client, auth_headers)
    ready = _ready_alias_canvas(object_approval=True)
    _seed_canvas(db, session["id"], ready)
    document = client.post(
        f"{BASE}/sessions/{session['id']}/documents", headers=auth_headers, json={})
    response = client.post(
        f"{BASE}/documents/{document.json()['data']['id']}/drafts",
        headers=auth_headers, json={},
    )
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "semantic_conversion_blocked"
    assert {item["code"] for item in detail["semanticIssues"]} == {
        "object_approval_unsupported"}


def test_document_renders_full_contract_and_detects_stale_and_legacy(
        client, auth_headers, db):
    session = _create_session(client, auth_headers)
    canvas = _ready_alias_canvas()
    row = _seed_canvas(db, session["id"], canvas)

    created = client.post(
        f"{BASE}/sessions/{session['id']}/documents", headers=auth_headers, json={})
    assert created.status_code == 201, created.text
    document = created.json()["data"]
    markdown = document["contentMd"]
    assert document["sourceCanvasVersion"] == row.canvas_version
    assert document["currentCanvasVersion"] == row.canvas_version
    assert document["sourceCanvasFingerprint"] == document["currentCanvasFingerprint"]
    assert len(document["sourceCanvasFingerprint"]) == 64
    assert document["isStale"] is False

    # 主体属性/主键、行为输入完整契约、场景 branches 均进入确定性文档。
    assert "**主体属性**" in markdown and "employee_no" in markdown
    assert "| role | employee_no |" in markdown
    assert "**输入契约**" in markdown and "channel" in markdown
    assert "网银 / 柜台" in markdown and "原始确认来源" in markdown
    assert "**条件分支**" in markdown
    assert "确认成功，状态 = 已确认" in markdown
    assert "客户取消，状态 = 已取消" in markdown

    # 当前画布一旦变化，详情和列表都现场返回 stale；旧快照默认禁止生成草稿。
    changed = C._ensure_canvas(canvas)
    changed["objects"][0]["description"] = "画布已更新"
    current = _seed_canvas(db, session["id"], changed)
    fetched = client.get(
        f"{BASE}/documents/{document['id']}", headers=auth_headers).json()["data"]
    assert fetched["isStale"] is True
    assert fetched["currentCanvasVersion"] == current.canvas_version
    listed = client.get(
        f"{BASE}/sessions/{session['id']}/documents", headers=auth_headers).json()["data"]
    assert listed[0]["isStale"] is True
    blocked = client.post(
        f"{BASE}/documents/{document['id']}/drafts", headers=auth_headers, json={})
    assert blocked.status_code == 409
    assert blocked.json()["detail"]["code"] == "stale_document"

    forced = client.post(
        f"{BASE}/documents/{document['id']}/drafts",
        headers=auth_headers, json={"force": True},
    )
    assert forced.status_code == 201, forced.text
    forced_report = forced.json()["data"]["report"]
    assert forced_report["staleDocumentOverride"] is True
    assert forced_report["sourceDocument"]["isStale"] is True

    # 历史文档没有来源版本元数据：API 明确返回 null，但仍以快照哈希识别 stale。
    legacy = ExplorationDocument(
        session_id=session["id"], title="历史文档", content_md="# 历史文档",
        canvas_snapshot=copy.deepcopy(C._ensure_canvas(current.canvas)), version=99,
    )
    db.add(legacy)
    db.commit()
    db.refresh(legacy)
    legacy_data = client.get(
        f"{BASE}/documents/{legacy.id}", headers=auth_headers).json()["data"]
    assert legacy_data["sourceCanvasVersion"] is None
    assert legacy_data["sourceCanvasFingerprint"] == legacy_data["currentCanvasFingerprint"]
    assert legacy_data["isStale"] is False

    changed_again = copy.deepcopy(C._ensure_canvas(current.canvas))
    changed_again["objects"][0]["description"] = "再次更新"
    _seed_canvas(db, session["id"], changed_again)
    legacy_stale = client.get(
        f"{BASE}/documents/{legacy.id}", headers=auth_headers).json()["data"]
    assert legacy_stale["sourceCanvasVersion"] is None
    assert legacy_stale["isStale"] is True
