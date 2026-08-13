"""转化管线的流程 coverage 扩展测试（设计 §5）：

  1. 含流程/场景的画布 build_draft 后五集合与无流程同构（精确集合断言）——
     流程不进本体，apply_draft 零改动（决策 2）
  2. 流程引用缺失对象/行为 → scenarioCoverage 尾部追加 {process, ...} 判别式条目，
     已知集合口径与场景一致（草稿 objectTypes/actions + 目标本体存量同名）
  3. 场景条目保持 {scenario, missingObjects, missingBehaviors} 形状、位置与语义不变
"""
from __future__ import annotations

from app.exploration import canvas as C
from app.exploration import converter as CV

_COLLECTIONS = ("objectTypes", "linkTypes", "actions", "functions", "sentinels")


def _base_canvas() -> dict:
    """对象/主体/行为齐备的最小画布（默认全部引用可解析）。"""
    cv = C.empty_canvas()
    cv, _, errors = C.upsert_elements(cv, "object", [{
        "name": "Order", "displayName": "订单", "keyAttribute": "order_no",
        "attributes": [{"name": "order_no", "displayName": "订单号", "typeHint": "文本",
                        "required": True}],
    }])
    assert not errors
    cv, _, errors = C.upsert_elements(cv, "actor", [
        {"name": "Sales", "displayName": "销售", "kind": "role"},
    ])
    assert not errors
    cv, _, errors = C.upsert_elements(cv, "behavior", [{
        "name": "confirm_order", "displayName": "确认订单", "actor": "Sales",
        "object": "Order", "trigger": "收到订单", "outcome": "订单已确认",
    }])
    assert not errors
    return cv


def _scenario_payload() -> dict:
    return {
        "name": "pay_flow", "displayName": "支付场景", "goal": "完成订单支付",
        "objects": ["Order"], "behaviors": ["confirm_order"],
    }


def _process_payload() -> dict:
    return {
        "name": "fulfillment", "displayName": "履约流程", "goal": "完成订单履约",
        "steps": [
            {"seq": 1, "name": "确认订单", "actor": "Sales", "behavior": "confirm_order"},
            {"seq": 2, "name": "发货登记", "actor": "Sales"},
        ],
        "objects": ["Order"],
        "expectedOutcome": "订单交付客户",
    }


def _canvas(with_process: bool) -> dict:
    cv = _base_canvas()
    cv, _, errors = C.upsert_elements(cv, "scenario", [_scenario_payload()])
    assert not errors
    if with_process:
        cv, _, errors = C.upsert_elements(cv, "process", [_process_payload()])
        assert not errors
    return cv


def _draft_signature(draft: dict) -> dict:
    """五集合的稳定签名：逐集合排序后的 (name, displayName) 列表（精确同构判据）。"""
    return {
        coll: sorted((item["name"], item["displayName"]) for item in draft[coll])
        for coll in _COLLECTIONS
    }


def test_process_canvas_draft_is_isomorphic_to_process_free_draft():
    base_draft, _ = CV.build_draft(_canvas(False))
    process_draft, report = CV.build_draft(_canvas(True))
    # 流程不进本体：五集合草稿与无流程时精确同构；全部引用可解析 → 无覆盖缺口
    assert _draft_signature(process_draft) == _draft_signature(base_draft)
    assert {coll: [item["name"] for item in process_draft[coll]] for coll in _COLLECTIONS} \
        == {coll: [item["name"] for item in base_draft[coll]] for coll in _COLLECTIONS}
    assert report["scenarioCoverage"] == []


def test_process_missing_refs_append_discriminated_coverage_entry():
    cv = _base_canvas()
    cv, _, errors = C.upsert_elements(cv, "process", [{
        "name": "fulfillment", "displayName": "履约流程", "goal": "完成订单履约",
        "steps": [
            {"seq": 1, "name": "确认订单", "behavior": "confirm_order"},
            {"seq": 2, "name": "退款处理", "behavior": "refund"},       # 未定义行为
            {"seq": 3, "name": "线下对账"},                              # 无 behavior 不参与校验
        ],
        "objects": ["Order", "Invoice"],                                 # Invoice 未定义
        "expectedOutcome": "订单交付客户",
    }])
    assert not errors
    _, report = CV.build_draft(cv)
    assert len(report["scenarioCoverage"]) == 1
    entry = report["scenarioCoverage"][0]
    assert set(entry) == {"process", "missingObjects", "missingBehaviors"}
    assert entry["process"] == "履约流程"
    assert entry["missingObjects"] == ["Invoice"]
    assert entry["missingBehaviors"] == ["refund"]


def test_process_coverage_uses_existing_ontology_names_as_known():
    """目标本体存量同名也算已知（口径与场景一致）。"""
    cv = _base_canvas()
    cv, _, errors = C.upsert_elements(cv, "process", [{
        "name": "fulfillment", "displayName": "履约流程", "goal": "完成订单履约",
        "steps": [{"seq": 1, "name": "确认订单", "behavior": "confirm_order"},
                  {"seq": 2, "name": "退款处理", "behavior": "refund"}],
        "objects": ["Invoice"],
        "expectedOutcome": "订单交付客户",
    }])
    assert not errors
    existing = {"objectTypes": {"invoice"}, "actions": {"refund"}}
    _, report = CV.build_draft(cv, existing=existing)
    assert report["scenarioCoverage"] == []


def test_scenario_coverage_entries_keep_shape_and_position():
    cv = _base_canvas()
    scenario = dict(_scenario_payload())
    scenario["objects"] = ["Order", "Invoice"]                    # Invoice 未定义
    scenario["behaviors"] = ["confirm_order", "refund"]           # refund 未定义
    cv, _, errors = C.upsert_elements(cv, "scenario", [scenario])
    assert not errors
    _, report = CV.build_draft(cv)
    assert report["scenarioCoverage"] == [
        {"scenario": "支付场景", "missingObjects": ["Invoice"],
         "missingBehaviors": ["refund"]},
    ]

    # 同画布追加流程缺口：场景条目位置与内容逐字节不变，流程条目追加在尾部
    process = dict(_process_payload())
    process["objects"] = ["Warehouse"]                            # 未定义
    process["steps"] = [{"seq": 1, "name": "售后回访", "behavior": "follow_up"}]
    cv, _, errors = C.upsert_elements(cv, "process", [process])
    assert not errors
    _, report = CV.build_draft(cv)
    coverage = report["scenarioCoverage"]
    assert len(coverage) == 2
    assert coverage[0] == {"scenario": "支付场景", "missingObjects": ["Invoice"],
                           "missingBehaviors": ["refund"]}
    assert "process" not in coverage[0]
    assert coverage[1] == {"process": "履约流程", "missingObjects": ["Warehouse"],
                           "missingBehaviors": ["follow_up"]}
    assert "scenario" not in coverage[1]
