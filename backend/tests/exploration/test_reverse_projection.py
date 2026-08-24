"""结构快照 → 画布反向投影测试（reverse_projection）：

  1. round-trip 名集合恒等：投影画布再经 _deterministic_draft 正向映射，
     五类集合的归一化名集合必须与原结构一致（手工快照 + 画布正向草稿换装快照）
  2. 空快照/None → 空画布（七键全空）
  3. 血缘恢复：function.source.semanticRole → rule.kind（object_validation→constraint、
     derivation→derivation、无绑定对象的 object_validation 回退 derivation、无血缘缺省
     derivation）；action.requiresApproval → needs_approval；objectType.primaryKey
     （属性 id 与属性名两种口径）→ key_attribute；sentinel 纯定期扫描 → event.source=time，
     其余 → external
  4. description 为空的元素标注「（来自本体结构，待补充业务描述）」；
     元素/子项 id 遵循画布 el-/sub- 规范

fixture 全部用纯字典构造，不依赖数据库。
"""
from __future__ import annotations

import re

from app.exploration import canvas as C
from app.exploration import converter as CV
from app.exploration.canvas import norm_name
from app.exploration.reverse_projection import project_snapshot_to_canvas

_COLLECTIONS = ("objectTypes", "linkTypes", "actions", "functions", "sentinels")
_PLACEHOLDER = "（来自本体结构，待补充业务描述）"


def _formal() -> dict:
    """覆盖全部映射分支的手工结构快照。"""
    return {
        "objectTypes": [
            {"id": "ot-1", "name": "Order", "displayName": "订单",
             "description": "客户订单", "primaryKey": "prop-1",
             "properties": [
                 {"id": "prop-1", "name": "order_no", "displayName": "订单号",
                  "type": "string", "required": True},
                 {"id": "prop-2", "name": "amount", "displayName": "金额",
                  "type": "number", "description": "订单金额"},
             ]},
            {"id": "ot-2", "name": "Customer", "displayName": "客户",
             "description": "",  # 空描述 → 占位标注
             "primaryKey": "customer_no",  # 历史口径：主键直接记属性名
             "properties": [
                 {"id": "prop-3", "name": "customer_no", "displayName": "客户编号",
                  "type": "string", "required": True},
             ]},
        ],
        "linkTypes": [
            {"id": "lt-1", "name": "order_customer", "displayName": "下单客户",
             "sourceObjectTypeId": "ot-1", "targetObjectTypeId": "ot-2",
             "cardinality": "many-to-one", "description": "订单归属客户"},
            {"id": "lt-2", "name": "dangling_link", "displayName": "悬空链接",
             "sourceObjectTypeId": "ot-1", "targetObjectTypeId": "ot-missing",
             "cardinality": "one-to-many"},  # 端点不可解析 → 跳过
        ],
        "actions": [
            {"id": "act-1", "name": "confirm_order", "displayName": "确认订单",
             "description": "", "objectTypeId": "ot-1",
             "parameters": [{"id": "param-1", "name": "channel", "displayName": "渠道",
                             "type": "string", "required": True}],
             "requiresApproval": True},
            {"id": "act-2", "name": "archive_order", "displayName": "归档订单",
             "description": "订单归档", "objectTypeId": None,
             "parameters": [], "requiresApproval": False},
        ],
        "functions": [
            {"id": "fn-1", "name": "profit_rule", "displayName": "毛利规则",
             "description": "毛利 = 金额 - 成本", "targetObjectTypeId": "ot-1",
             "source": {"semanticRole": "derivation", "originKind": "rule"}},
            {"id": "fn-2", "name": "amount_check", "displayName": "金额校验",
             "description": "金额必须非负", "targetObjectTypeId": "ot-1",
             "source": {"semanticRole": "object_validation", "originKind": "rule"}},
            {"id": "fn-3", "name": "orphan_check", "displayName": "孤儿校验",
             "description": "无绑定对象", "targetObjectTypeId": None,
             "source": {"semanticRole": "object_validation", "originKind": "rule"}},
            {"id": "fn-4", "name": "manual_fn", "displayName": "手工函数",
             "description": "工程自建", "targetObjectTypeId": None},  # 无血缘 → 缺省 derivation
        ],
        "sentinels": [
            {"id": "sen-1", "name": "daily_scan", "displayName": "每日扫描",
             "description": "每日全量扫描", "onChange": False, "onSchedule": True,
             "bindings": [], "source": {"originKind": "event"}},
            {"id": "sen-2", "name": "order_watch", "displayName": "订单监听",
             "description": "", "onChange": True, "onSchedule": False,
             "bindings": [{"alias": "a", "objectTypeId": "ot-1"}],
             "source": {"originKind": "rule"}},
        ],
    }


def _name_sets(snapshot: dict) -> dict[str, set[str]]:
    return {coll: {norm_name(str(item.get("name") or "")) for item in (snapshot.get(coll) or [])}
            for coll in _COLLECTIONS}


def _replay_name_sets(canvas: dict) -> dict[str, set[str]]:
    warnings: list[str] = []
    draft = CV._deterministic_draft(canvas, warnings)
    return _name_sets(draft)


def _by_name(items: list[dict]) -> dict[str, dict]:
    return {item["name"]: item for item in items}


# ---------------------------------------------------------------- round-trip 名集合恒等


def test_round_trip_name_identity_on_handbuilt_snapshot():
    formal = _formal()
    canvas = project_snapshot_to_canvas(formal)
    replayed = _replay_name_sets(canvas)
    expected = _name_sets(formal)
    expected["linkTypes"].discard(norm_name("dangling_link"))  # 端点不可解析，双向都跳过
    assert replayed == expected


def test_round_trip_name_identity_via_forward_draft():
    """画布 → 正向草稿 → 结构快照换装 → 反向投影 → 正向重放，名集合全程恒等。"""
    canvas = C.empty_canvas()
    canvas, _, errors = C.upsert_elements(canvas, "object", [
        {"name": "Order", "displayName": "订单", "keyAttribute": "order_no",
         "attributes": [
             {"name": "order_no", "displayName": "订单号", "typeHint": "文本", "required": True},
             {"name": "status", "displayName": "状态", "typeHint": "枚举",
              "enum": ["待确认", "已确认"]},
         ],
         "relations": [{"name": "order_customer", "displayName": "下单客户",
                        "target": "客户", "cardinality": "many-to-one"}]},
        {"name": "Customer", "displayName": "客户", "keyAttribute": "customer_no",
         "attributes": [{"name": "customer_no", "displayName": "客户编号",
                         "typeHint": "文本", "required": True}]},
    ])
    assert not errors
    canvas, _, errors = C.upsert_elements(canvas, "behavior", [{
        "name": "confirm_order", "displayName": "确认订单", "object": "Order",
        "inputs": [{"name": "channel", "displayName": "渠道", "typeHint": "文本"}],
        "needsApproval": True,
    }])
    assert not errors
    canvas, _, errors = C.upsert_elements(canvas, "rule", [
        {"name": "profit_rule", "displayName": "毛利规则", "kind": "derivation",
         "appliesTo": "Order", "statement": "毛利 = 金额 - 成本"},
        {"name": "amount_check", "displayName": "金额校验", "kind": "constraint",
         "appliesTo": "Order", "statement": "金额必须非负"},
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

    warnings: list[str] = []
    draft = CV._deterministic_draft(canvas, warnings)
    assert not [i for i in draft["semanticIssues"] if i.get("severity") == "blocking"]

    # 草稿 → 结构快照形状（key→uuid、端点记 id、血缘收进 source，同 apply_draft 口径）
    id_by_key = {ot["key"]: f"ot-{i:03d}" for i, ot in enumerate(draft["objectTypes"])}
    formal = {
        "objectTypes": [{
            "id": id_by_key[ot["key"]], "name": ot["name"], "displayName": ot["displayName"],
            "description": ot.get("description") or "", "primaryKey": ot.get("primaryKey"),
            "properties": ot.get("properties") or [],
        } for ot in draft["objectTypes"]],
        "linkTypes": [{
            "id": f"lt-{i:03d}", "name": lt["name"], "displayName": lt["displayName"],
            "sourceObjectTypeId": id_by_key.get(lt.get("sourceKey"), ""),
            "targetObjectTypeId": id_by_key.get(lt.get("targetKey"), ""),
            "cardinality": lt.get("cardinality") or "one-to-many",
        } for i, lt in enumerate(draft["linkTypes"])],
        "actions": [{
            "id": f"act-{i:03d}", "name": a["name"], "displayName": a["displayName"],
            "objectTypeId": id_by_key.get(a.get("objectTypeKey")),
            "parameters": a.get("parameters") or [],
            "requiresApproval": bool(a.get("requiresApproval")),
        } for i, a in enumerate(draft["actions"])],
        "functions": [{
            "id": f"fn-{i:03d}", "name": f["name"], "displayName": f["displayName"],
            "description": f.get("description") or "",
            "targetObjectTypeId": id_by_key.get(f.get("targetObjectTypeKey")),
            "source": {"semanticRole": f.get("semanticRole"),
                       "originKind": f.get("originKind")},
        } for i, f in enumerate(draft["functions"])],
        "sentinels": [{
            "id": f"sen-{i:03d}", "name": s["name"], "displayName": s["displayName"],
            "description": s.get("description") or "",
            "onChange": bool(s.get("onChange")), "onSchedule": bool(s.get("onSchedule")),
            "bindings": ([{"alias": "a", "objectTypeId": id_by_key[s["bindingObjectKey"]]}]
                         if s.get("bindingObjectKey") in id_by_key else []),
            "source": {"originKind": s.get("originKind")},
        } for i, s in enumerate(draft["sentinels"])],
    }

    projected = project_snapshot_to_canvas(formal)
    assert _replay_name_sets(projected) == _name_sets(formal)


# ---------------------------------------------------------------- 空快照


def test_empty_snapshot_projects_to_empty_canvas():
    for snapshot in (None, {}, {"objectTypes": None}):
        canvas = project_snapshot_to_canvas(snapshot)
        assert set(canvas.keys()) == set(C.KIND_KEYS.values())
        assert all(canvas[key] == [] for key in C.KIND_KEYS.values())


# ---------------------------------------------------------------- 血缘字段恢复


def test_object_projection_recovers_attributes_key_and_relations():
    canvas = project_snapshot_to_canvas(_formal())
    objects = _by_name(canvas["objects"])
    assert set(objects) == {"Order", "Customer"}

    order = objects["Order"]
    assert order["display_name"] == "订单"
    assert order["description"] == "客户订单"
    assert order["key_attribute"] == "order_no"  # primaryKey 记的是属性 id
    attrs = _by_name(order["attributes"])
    assert attrs["order_no"]["required"] is True
    assert attrs["amount"]["type_hint"] == "number"
    assert attrs["amount"]["notes"] == "订单金额"

    customer = objects["Customer"]
    assert customer["key_attribute"] == "customer_no"  # 历史口径：primaryKey 记属性名
    assert customer["description"] == _PLACEHOLDER

    relations = order["relations"]
    assert len(relations) == 1  # dangling_link 端点不可解析，被跳过
    relation = relations[0]
    assert relation["name"] == "order_customer"
    assert relation["target"] == "客户"  # 目标对象的 displayName
    assert relation["cardinality"] == "many-to-one"
    assert relation["description"] == "订单归属客户"


def test_action_projection_recovers_approval_and_inputs():
    canvas = project_snapshot_to_canvas(_formal())
    behaviors = _by_name(canvas["behaviors"])
    assert set(behaviors) == {"confirm_order", "archive_order"}

    confirm = behaviors["confirm_order"]
    assert confirm["object"] == "Order"
    assert confirm["needs_approval"] is True
    assert confirm["description"] == _PLACEHOLDER
    inputs = _by_name(confirm["inputs"])
    assert inputs["channel"]["required"] is True
    assert inputs["channel"]["type_hint"] == "string"
    # 执行主体/触发无法从结构恢复，留空待对话补齐
    assert "actor" not in confirm
    assert "trigger" not in confirm

    archive = behaviors["archive_order"]
    assert "object" not in archive
    assert archive["needs_approval"] is False
    assert archive["description"] == "订单归档"


def test_function_projection_recovers_rule_kind_from_semantic_role():
    canvas = project_snapshot_to_canvas(_formal())
    rules = _by_name(canvas["rules"])
    assert set(rules) == {"profit_rule", "amount_check", "orphan_check", "manual_fn"}

    assert rules["profit_rule"]["kind"] == "derivation"
    assert rules["profit_rule"]["applies_to"] == "Order"
    assert rules["profit_rule"]["statement"] == "毛利 = 金额 - 成本"

    assert rules["amount_check"]["kind"] == "constraint"
    assert rules["amount_check"]["applies_to"] == "Order"

    # object_validation 但绑定对象不可解析 → 回退 derivation 保住 round-trip
    assert rules["orphan_check"]["kind"] == "derivation"
    assert "applies_to" not in rules["orphan_check"]

    # 无血缘 → 缺省 derivation
    assert rules["manual_fn"]["kind"] == "derivation"


def test_sentinel_projection_recovers_event_source():
    canvas = project_snapshot_to_canvas(_formal())
    events = _by_name(canvas["events"])
    assert set(events) == {"daily_scan", "order_watch"}

    scan = events["daily_scan"]
    assert scan["source"] == "time"  # 纯定期扫描
    assert scan["consequences"] == ["每日全量扫描"]

    watch = events["order_watch"]
    assert watch["source"] == "external"  # 变化驱动的原始行为来源不可恢复
    assert watch["description"] == _PLACEHOLDER
    assert watch["consequences"] == []


# ---------------------------------------------------------------- 画布规范


def test_projected_canvas_follows_canvas_id_conventions():
    canvas = project_snapshot_to_canvas(_formal())
    for key in ("objects", "behaviors", "rules", "events"):
        for element in canvas[key]:
            assert re.fullmatch(r"el-[0-9a-f]{8}", element["id"]), element
    order = _by_name(canvas["objects"])["Order"]
    for child in order["attributes"] + order["relations"]:
        assert re.fullmatch(r"sub-[0-9a-f]{10}", child["id"]), child
    # 投影画布可被画布管线继续编辑/评估（schema 合法）
    canvas, applied, errors = C.upsert_elements(canvas, "object", [
        {"id": order["id"], "description": "客户订单（已确认）"}])
    assert not errors and applied == [order["id"]]
