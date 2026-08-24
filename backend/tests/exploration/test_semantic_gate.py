"""业务语义层一致性闸门测试（semantic_gate）：

  1. 空画布 ≡ 空结构 → 无 issue；空画布但结构非空（含语义层整体缺失）→
     结构元素全部记 semantic_business_missing
  2. 画布 ⇄ 结构全量一致（含文档/画布指纹）→ 零 issue
  3. 结构多出元素 → semantic_business_missing；结构缺失 → semantic_structure_missing
  4. 同名元素签名不一致（对象属性集/链接端点+基数/动作参数集）→
     semantic_signature_mismatch；functions/sentinels 只比存在性
  5. 画布非空但文档缺失 → semantic_document_missing；
     文档/画布指纹与现算值不符 → semantic_document_stale
  6. semantic_overview 计数、hasSemanticLayer、documentStale 与 byCode 聚合

fixture 全部用纯字典构造：画布经 C.upsert_elements 生成，结构快照由
_deterministic_draft 的草稿机械换装（key→uuid）而来，不依赖数据库。
"""
from __future__ import annotations

import hashlib

from app.exploration import canvas as C
from app.exploration import converter as CV
from app.exploration.document import canvas_fingerprint
from app.exploration.semantic_gate import semantic_consistency_issues, semantic_overview

_DOC_MD = "# 订单域 · 需求文档 v1\n\n确定性测试文档。\n"


def _canvas() -> dict:
    """五类集合均有产物的最小画布（全部引用可解析，无 blocking）。"""
    canvas = C.empty_canvas()
    canvas, _, errors = C.upsert_elements(canvas, "object", [
        {"name": "Order", "displayName": "订单", "keyAttribute": "order_no",
         "attributes": [
             {"name": "order_no", "displayName": "订单号", "typeHint": "文本", "required": True},
             {"name": "amount", "displayName": "金额", "typeHint": "金额"},
         ],
         "relations": [{"name": "order_customer", "displayName": "下单客户",
                        "target": "客户", "cardinality": "many-to-one"}]},
        {"name": "Customer", "displayName": "客户", "keyAttribute": "customer_no",
         "attributes": [
             {"name": "customer_no", "displayName": "客户编号", "typeHint": "文本",
              "required": True},
         ]},
    ])
    assert not errors
    canvas, _, errors = C.upsert_elements(canvas, "actor", [
        {"name": "Sales", "displayName": "销售", "kind": "role"},
    ])
    assert not errors
    canvas, _, errors = C.upsert_elements(canvas, "behavior", [{
        "name": "confirm_order", "displayName": "确认订单", "actor": "Sales",
        "object": "Order", "trigger": "收到订单", "outcome": "订单已确认",
        "inputs": [{"name": "channel", "displayName": "确认渠道", "typeHint": "文本"}],
    }])
    assert not errors
    canvas, _, errors = C.upsert_elements(canvas, "rule", [
        {"name": "profit_rule", "displayName": "毛利规则", "kind": "derivation",
         "appliesTo": "Order", "statement": "毛利 = 金额 - 成本"},
        {"name": "high_value_alert", "displayName": "高额告警", "kind": "alert",
         "appliesTo": "Order", "statement": "金额超过阈值时告警"},
    ])
    assert not errors
    canvas, _, errors = C.upsert_elements(canvas, "event", [
        {"name": "daily_scan", "displayName": "每日扫描", "source": "time"},
        {"name": "order_confirmed", "displayName": "订单已确认",
         "source": "confirm_order", "consequences": ["通知仓库备货"]},
    ])
    assert not errors
    return canvas


def _draft(canvas: dict) -> dict:
    warnings: list[str] = []
    draft = CV._deterministic_draft(canvas, warnings)
    # 测试画布全部引用可解析，重放不应产生 blocking
    assert not [i for i in draft["semanticIssues"] if i.get("severity") == "blocking"]
    return draft


def _formal_from_draft(draft: dict) -> dict:
    """把草稿机械换装成结构快照形状（key→uuid，端点改记 sourceObjectTypeId），
    血缘字段（semanticRole/originKind）按 apply_draft 的口径收进 source。"""
    id_by_key: dict[str, str] = {}
    object_types = []
    for index, ot in enumerate(draft["objectTypes"]):
        oid = f"ot-{index:03d}"
        id_by_key[ot["key"]] = oid
        object_types.append({
            "id": oid, "name": ot["name"], "displayName": ot["displayName"],
            "description": ot.get("description") or "", "primaryKey": ot.get("primaryKey"),
            "properties": ot.get("properties") or [],
        })
    link_types = [{
        "id": f"lt-{i:03d}", "name": lt["name"], "displayName": lt["displayName"],
        "sourceObjectTypeId": id_by_key.get(lt.get("sourceKey"), ""),
        "targetObjectTypeId": id_by_key.get(lt.get("targetKey"), ""),
        "cardinality": lt.get("cardinality") or "one-to-many",
    } for i, lt in enumerate(draft["linkTypes"])]
    actions = [{
        "id": f"act-{i:03d}", "name": a["name"], "displayName": a["displayName"],
        "objectTypeId": id_by_key.get(a.get("objectTypeKey")),
        "parameters": a.get("parameters") or [],
        "requiresApproval": bool(a.get("requiresApproval")),
    } for i, a in enumerate(draft["actions"])]
    functions = [{
        "id": f"fn-{i:03d}", "name": f["name"], "displayName": f["displayName"],
        "description": f.get("description") or "",
        "targetObjectTypeId": id_by_key.get(f.get("targetObjectTypeKey")),
        "source": {"semanticRole": f.get("semanticRole"), "originKind": f.get("originKind")},
    } for i, f in enumerate(draft["functions"])]
    sentinels = [{
        "id": f"sen-{i:03d}", "name": s["name"], "displayName": s["displayName"],
        "description": s.get("description") or "",
        "onChange": bool(s.get("onChange")), "onSchedule": bool(s.get("onSchedule")),
        "bindings": ([{"alias": "a", "objectTypeId": id_by_key[s["bindingObjectKey"]]}]
                     if s.get("bindingObjectKey") in id_by_key else []),
        "source": {"originKind": s.get("originKind")},
    } for i, s in enumerate(draft["sentinels"])]
    return {"objectTypes": object_types, "linkTypes": link_types, "actions": actions,
            "functions": functions, "sentinels": sentinels}


def _semantic_layer(canvas: dict, *, document_md: str | None = _DOC_MD,
                    document_fp: str | None = None, canvas_fp: str | None = None) -> dict:
    layer: dict = {"canvas": canvas, "semanticRevision": 1}
    if document_md is not None:
        layer["documentMd"] = document_md
        layer["documentTitle"] = "订单域 · 需求文档 v1"
        layer["documentFingerprint"] = document_fp if document_fp is not None else \
            hashlib.sha256(document_md.encode("utf-8")).hexdigest()
    layer["canvasFingerprint"] = canvas_fp if canvas_fp is not None else canvas_fingerprint(canvas)
    return layer


def _codes(issues: list[dict]) -> list[str]:
    return [i["code"] for i in issues]


# ---------------------------------------------------------------- 空/空 与 空/非空


def test_empty_canvas_and_empty_structure_have_no_issues():
    assert semantic_consistency_issues(None, None) == []
    assert semantic_consistency_issues({}, {}) == []
    # 空画布且没有文档不属于缺失：文档缺失只对非空画布判定
    assert semantic_consistency_issues({"canvas": C.empty_canvas()}, None) == []


def test_empty_canvas_with_fingerprints_only_checks_fingerprint():
    # 空画布、无文档：documentFingerprint 因 documentMd 缺失而不参与比对，
    # canvasFingerprint 与空画布现算值一致 → 无 issue
    layer = {"canvas": C.empty_canvas(),
             "canvasFingerprint": canvas_fingerprint(C.empty_canvas()),
             "documentFingerprint": "0" * 64}
    assert semantic_consistency_issues(layer, None) == []


def test_structure_without_semantic_layer_is_all_business_missing():
    formal = _formal_from_draft(_draft(_canvas()))
    issues = semantic_consistency_issues(None, formal)
    expected = sum(len(formal[c]) for c in
                   ("objectTypes", "linkTypes", "actions", "functions", "sentinels"))
    assert len(issues) == expected
    assert set(_codes(issues)) == {"semantic_business_missing"}
    for issue in issues:
        assert issue["id"]
        assert "在业务画布中没有对应" in issue["message"]


# ---------------------------------------------------------------- 全量一致


def test_consistent_layer_has_zero_issues():
    canvas = _canvas()
    formal = _formal_from_draft(_draft(canvas))
    assert semantic_consistency_issues(_semantic_layer(canvas), formal) == []


# ---------------------------------------------------------------- 画布 ⊆ 结构


def test_extra_structure_elements_are_business_missing():
    canvas = _canvas()
    formal = _formal_from_draft(_draft(canvas))
    formal["objectTypes"].append({
        "id": "ot-legacy", "name": "Legacy", "displayName": "遗留对象",
        "primaryKey": "prop-x",
        "properties": [{"id": "prop-x", "name": "legacy_no", "type": "string"}],
    })
    formal["actions"].append({
        "id": "act-legacy", "name": "legacy_action", "displayName": "遗留动作",
        "objectTypeId": None, "parameters": [], "requiresApproval": False,
    })
    issues = semantic_consistency_issues(_semantic_layer(canvas), formal)
    assert len(issues) == 2
    assert set(_codes(issues)) == {"semantic_business_missing"}
    by_id = {i["id"]: i for i in issues}
    assert set(by_id) == {"Legacy", "legacy_action"}
    assert by_id["Legacy"]["kind"] == "objectType"
    assert by_id["legacy_action"]["kind"] == "action"
    assert "请到本体建模补齐业务语义" in by_id["legacy_action"]["message"]


# ---------------------------------------------------------------- 结构 ⊆ 画布


def test_missing_structure_elements_are_structure_missing():
    canvas = _canvas()
    formal = _formal_from_draft(_draft(canvas))
    formal["actions"] = [a for a in formal["actions"] if a["name"] != "confirm_order"]
    formal["sentinels"] = [s for s in formal["sentinels"] if s["name"] != "daily_scan"]
    issues = semantic_consistency_issues(_semantic_layer(canvas), formal)
    assert len(issues) == 2
    assert set(_codes(issues)) == {"semantic_structure_missing"}
    by_id = {i["id"]: i for i in issues}
    assert set(by_id) == {"confirm_order", "daily_scan"}
    assert by_id["confirm_order"]["kind"] == "action"
    assert by_id["daily_scan"]["kind"] == "sentinel"
    assert "结构中缺少画布模型对应的动作「确认订单」" == by_id["confirm_order"]["message"]


def test_missing_object_type_also_flags_dangling_link_endpoint():
    canvas = _canvas()
    formal = _formal_from_draft(_draft(canvas))
    formal["objectTypes"] = [o for o in formal["objectTypes"] if o["name"] != "Customer"]
    # 链接本身仍在结构中，但端点已无法解析 → 结构缺对象 + 链接签名不一致
    issues = semantic_consistency_issues(_semantic_layer(canvas), formal)
    by_id = {i["id"]: i["code"] for i in issues}
    assert by_id["Customer"] == "semantic_structure_missing"
    assert by_id["order_customer"] == "semantic_signature_mismatch"


# ---------------------------------------------------------------- 签名不一致


def test_signature_mismatch_matrix():
    canvas = _canvas()
    formal = _formal_from_draft(_draft(canvas))
    order = next(o for o in formal["objectTypes"] if o["name"] == "Order")
    order["properties"].append(
        {"id": "prop-extra", "name": "extra_field", "type": "string"})
    formal["linkTypes"][0]["cardinality"] = "many-to-many"
    formal["actions"][0]["parameters"] = []
    issues = semantic_consistency_issues(_semantic_layer(canvas), formal)
    assert len(issues) == 3
    assert set(_codes(issues)) == {"semantic_signature_mismatch"}
    by_id = {i["id"]: i for i in issues}
    assert set(by_id) == {"Order", "order_customer", "confirm_order"}
    assert by_id["Order"]["kind"] == "objectType"
    assert "extra_field" in by_id["Order"]["message"]
    assert "many-to-many" in by_id["order_customer"]["message"]
    assert "channel" in by_id["confirm_order"]["message"]


def test_function_and_sentinel_compare_existence_only():
    # 同名函数/哨兵的内容被工程侧改写（函数体、绑定）不构成签名不一致
    canvas = _canvas()
    formal = _formal_from_draft(_draft(canvas))
    formal["functions"][0]["body"] = "return amount - cost"
    formal["functions"][0]["enabled"] = True
    formal["sentinels"][0]["enabled"] = True
    formal["sentinels"][0]["condition"] = "amount > 1000"
    assert semantic_consistency_issues(_semantic_layer(canvas), formal) == []


# ---------------------------------------------------------------- 文档检查


def test_document_missing_when_canvas_non_empty():
    canvas = _canvas()
    formal = _formal_from_draft(_draft(canvas))
    layer = _semantic_layer(canvas, document_md=None)
    issues = semantic_consistency_issues(layer, formal)
    assert _codes(issues) == ["semantic_document_missing"]
    assert issues[0]["kind"] == "document"


def test_document_stale_on_document_fingerprint_mismatch():
    canvas = _canvas()
    formal = _formal_from_draft(_draft(canvas))
    layer = _semantic_layer(canvas, document_fp="0" * 64)
    issues = semantic_consistency_issues(layer, formal)
    assert _codes(issues) == ["semantic_document_stale"]
    assert issues[0]["field"] == "documentFingerprint"


def test_document_stale_on_canvas_fingerprint_mismatch():
    canvas = _canvas()
    formal = _formal_from_draft(_draft(canvas))
    layer = _semantic_layer(canvas, canvas_fp="0" * 64)
    issues = semantic_consistency_issues(layer, formal)
    assert _codes(issues) == ["semantic_document_stale"]
    assert issues[0]["field"] == "canvasFingerprint"


# ---------------------------------------------------------------- 总览


def test_overview_counts_and_flags():
    canvas = _canvas()
    formal = _formal_from_draft(_draft(canvas))
    overview = semantic_overview(_semantic_layer(canvas), formal)
    assert overview["hasSemanticLayer"] is True
    assert overview["documentTitle"] == "订单域 · 需求文档 v1"
    assert overview["documentStale"] is False
    assert overview["canvasCounts"] == {
        "objects": 2, "actors": 1, "behaviors": 1, "events": 2,
        "rules": 2, "scenarios": 0, "processes": 0,
    }
    # role 主体也是数据实体，正向映射同样产出 objectType（Order/Customer/Sales）
    assert overview["structureCounts"] == {
        "objectTypes": 3, "linkTypes": 1, "actions": 1, "functions": 1, "sentinels": 3,
    }
    assert overview["consistency"] == {"issueCount": 0, "byCode": {}}


def test_overview_aggregates_issues_by_code():
    canvas = _canvas()
    formal = _formal_from_draft(_draft(canvas))
    formal["actions"] = []
    layer = _semantic_layer(canvas, canvas_fp="0" * 64)
    overview = semantic_overview(layer, formal)
    assert overview["documentStale"] is True
    assert overview["consistency"] == {
        "issueCount": 2,
        "byCode": {"semantic_structure_missing": 1, "semantic_document_stale": 1},
    }


def test_overview_without_semantic_layer():
    formal = _formal_from_draft(_draft(_canvas()))
    overview = semantic_overview(None, formal)
    assert overview["hasSemanticLayer"] is False
    assert overview["documentTitle"] is None
    assert overview["documentStale"] is False
    assert overview["canvasCounts"] == {
        "objects": 0, "actors": 0, "behaviors": 0, "events": 0,
        "rules": 0, "scenarios": 0, "processes": 0,
    }
    assert overview["structureCounts"]["objectTypes"] == 3
    # 无语义层 ≡ 空画布：结构元素全部计为 business_missing
    assert overview["consistency"]["issueCount"] == 9
    assert overview["consistency"]["byCode"] == {"semantic_business_missing": 9}
