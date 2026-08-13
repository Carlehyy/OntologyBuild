"""业务流程画布元素的 schema/合并语义测试：

  1. Process 全字段创建与 steps/branches/metrics 子项 id 自动补
  2. 稀疏补丁按子项自然键匹配（ProcessStep=seq+name、ProcessBranch=from_step+condition、
     MetricSpec=name），不匹配则追加
  3. 子项 ``_delete`` 显式删除与 ``[]`` 显式清空
  4. 非法子项（空 condition、seq 越界）→ 父级原子回滚，新元素整体拒绝
  5. camel/snake 双收（fromStep/processRef/sourceObjects 等）
  6. Scenario 新字段（process_ref/metrics）合并与子项显式 None 清除
  7. vacuous-pass 回归（最高优先级）：无 processes 键的旧画布 R.evaluate 不炸、
     ready 语义与含空 processes 键的新画布完全一致
"""
from __future__ import annotations

import pytest

from app.exploration import canvas as C
from app.exploration import readiness as R


def _process_payload() -> dict:
    return {
        "name": "order_fulfillment",
        "displayName": "订单履约流程",
        "goal": "完成订单履约",
        "trigger": "订单支付成功",
        "steps": [
            {"seq": 1, "name": "确认订单", "actor": "Sales", "behavior": "confirm_order"},
            {"seq": 2, "name": "库存检查", "actor": "Buyer"},
            {"seq": 3, "name": "发货", "actor": "Sales", "behavior": "ship_order"},
        ],
        "branches": [
            {"fromStep": 2, "toStep": 3, "condition": "库存充足"},
            {"fromStep": 2, "toStep": None, "condition": "缺货取消", "kind": "exception"},
        ],
        "objects": ["Order"],
        "metrics": [
            {"name": "履约时长", "displayName": "履约时长",
             "formula": "从支付到发货 ≤ 48 小时", "sourceObjects": ["Order"]},
        ],
        "expectedOutcome": "订单交付客户",
    }


def _canvas_with_process() -> dict:
    cv = C.empty_canvas()
    cv, _, errors = C.upsert_elements(cv, "process", [_process_payload()])
    assert not errors
    return cv


def test_process_full_fields_and_child_ids_auto_assigned():
    cv = _canvas_with_process()
    assert "processes" in C.empty_canvas()
    process = cv["processes"][0]
    assert process["id"] and process["goal"] == "完成订单履约"
    assert process["trigger"] == "订单支付成功"
    assert process["expected_outcome"] == "订单交付客户"
    assert process["objects"] == ["Order"]
    # 三种结构化子项全部补稳定 id，camelCase 已归一为内部字段名
    assert [step["seq"] for step in process["steps"]] == [1, 2, 3]
    assert all(step["id"] for step in process["steps"])
    assert process["steps"][0]["actor"] == "Sales"
    assert process["steps"][0]["behavior"] == "confirm_order"
    assert all(branch["id"] for branch in process["branches"])
    assert process["branches"][0]["from_step"] == 2
    assert process["branches"][0]["to_step"] == 3
    assert process["branches"][1].get("to_step") is None   # None 表示流程结束（dump 时剔除）
    assert process["branches"][1]["kind"] == "exception"
    assert process["branches"][0]["kind"] == "normal"
    assert all(metric["id"] for metric in process["metrics"])
    assert process["metrics"][0]["source_objects"] == ["Order"]
    # 摘要与完整度计数含流程分区
    assert "流程模型(1): order_fulfillment(3步 2分支 1指标)" in C.canvas_summary(cv)
    assert C.completeness(cv)["counts"]["processes"] == 1


def test_process_sparse_patch_matches_children_by_natural_keys():
    cv = _canvas_with_process()
    process = cv["processes"][0]
    step_id = process["steps"][0]["id"]
    branch_id = process["branches"][0]["id"]
    metric_id = process["metrics"][0]["id"]

    cv, _, errors = C.upsert_elements(cv, "process", [{
        "name": "order_fulfillment",
        "steps": [{"seq": 1, "name": "确认订单", "description": "补充步骤说明"}],
        "branches": [{"fromStep": 2, "condition": "库存充足", "toStep": None}],
        "metrics": [{"name": "履约时长", "target": "≤ 24 小时"}],
    }])
    assert not errors
    process = cv["processes"][0]
    # 自然键命中 → 原地合并，子项数量与 id 稳定
    assert len(process["steps"]) == 3 and process["steps"][0]["id"] == step_id
    assert process["steps"][0]["description"] == "补充步骤说明"
    assert process["steps"][0]["actor"] == "Sales"   # 未随补丁重发的字段不被清空
    assert len(process["branches"]) == 2 and process["branches"][0]["id"] == branch_id
    assert "to_step" not in process["branches"][0]   # 子项显式 null 有实际含义
    assert len(process["metrics"]) == 1 and process["metrics"][0]["id"] == metric_id
    assert process["metrics"][0]["target"] == "≤ 24 小时"

    # 自然键不匹配 → 追加新子项；子项 id 直接命中 → 原地合并
    cv, _, errors = C.upsert_elements(cv, "process", [{
        "name": "order_fulfillment",
        "steps": [{"seq": 1, "name": "换个名字", "actor": "Buyer"},
                  {"id": step_id, "actor": "Sales"}],
    }])
    assert not errors
    process = cv["processes"][0]
    assert len(process["steps"]) == 4
    assert process["steps"][0]["id"] == step_id and process["steps"][0]["actor"] == "Sales"


def test_process_child_delete_and_clear_list():
    cv = _canvas_with_process()
    cv, _, errors = C.upsert_elements(cv, "process", [{
        "name": "order_fulfillment",
        "steps": [{"seq": 2, "name": "库存检查", "_delete": True}],
        "branches": [],
    }])
    assert not errors
    process = cv["processes"][0]
    assert [step["name"] for step in process["steps"]] == ["确认订单", "发货"]
    assert process["branches"] == []      # 显式 [] 仍表示确认清空

    # 删除不存在的自然键目标 → 报错且父级不变
    cv, applied, errors = C.upsert_elements(cv, "process", [{
        "name": "order_fulfillment",
        "metrics": [{"name": "不存在的度量", "_delete": True}],
    }])
    assert not applied and any("删除目标" in error for error in errors)
    assert len(cv["processes"][0]["metrics"]) == 1


def test_process_invalid_child_rolls_back_parent_atomically():
    cv = _canvas_with_process()
    before = cv["processes"][0]

    # 空 condition（min_length=1）与 seq=0（ge=1）都被 pydantic 拒绝；
    # 同一补丁里合法子项的合并也随父级一起回滚。
    cv, applied, errors = C.upsert_elements(cv, "process", [{
        "name": "order_fulfillment",
        "description": "这句描述不应生效",
        "branches": [{"fromStep": 1, "toStep": 2, "condition": ""}],
    }])
    assert not applied and any("condition" in error for error in errors)
    assert cv["processes"][0] == before

    cv, applied, errors = C.upsert_elements(cv, "process", [{
        "name": "order_fulfillment",
        "steps": [{"seq": 1, "name": "确认订单", "description": "不应生效"},
                  {"seq": 0, "name": "坏步骤"}],
    }])
    assert not applied and any("seq" in error for error in errors)
    assert cv["processes"][0] == before

    # 新元素携带非法子项 → 整体拒绝，不留半成品
    cv, applied, errors = C.upsert_elements(cv, "process", [{
        "name": "broken_process",
        "steps": [{"seq": 1, "name": "唯一步骤"}],
        "branches": [{"fromStep": 99, "condition": "起点越界没关系，这里只验证 condition"},
                     {"fromStep": 1, "condition": "  "}],
    }])
    assert not applied and errors
    assert [p["name"] for p in cv["processes"]] == ["order_fulfillment"]


def test_process_and_scenario_accept_camel_and_snake_keys():
    cv = C.empty_canvas()
    cv, _, errors = C.upsert_elements(cv, "process", [{
        "name": "snake_process",
        "steps": [{"seq": 1, "name": "步骤一"}],
        "branches": [{"from_step": 1, "to_step": None, "condition": "完成"}],
        "metrics": [{"name": "时效", "formula": "≤ 2 小时", "source_objects": ["Order"]}],
        "expected_outcome": "完成",
    }])
    assert not errors
    process = cv["processes"][0]
    assert process["branches"][0]["from_step"] == 1
    assert process["metrics"][0]["source_objects"] == ["Order"]
    assert process["expected_outcome"] == "完成"

    cv, _, errors = C.upsert_elements(cv, "scenario", [{
        "name": "vip_flow", "displayName": "VIP 履约", "goal": "大客户履约",
        "processRef": "snake_process",
        "metrics": [{"name": "客诉率", "formula": "客诉 ≤ 1 个百分点", "sourceObjects": ["Order"]}],
    }])
    assert not errors
    scenario = cv["scenarios"][0]
    assert scenario["process_ref"] == "snake_process"
    assert scenario["metrics"][0]["source_objects"] == ["Order"]

    # snake_case 补丁同样命中
    cv, _, errors = C.upsert_elements(cv, "scenario", [{
        "name": "vip_flow", "process_ref": "snake_process",
    }])
    assert not errors and cv["scenarios"][0]["process_ref"] == "snake_process"


def test_scenario_new_fields_merge_and_explicit_none_semantics():
    cv = _canvas_with_process()
    cv, _, errors = C.upsert_elements(cv, "scenario", [{
        "name": "vip_flow", "goal": "大客户履约变体", "processRef": "order_fulfillment",
        "metrics": [{"name": "客诉率", "formula": "客诉 ≤ 1 个百分点", "target": "≤ 0.5%"}],
    }])
    assert not errors
    metric_id = cv["scenarios"][0]["metrics"][0]["id"]

    # 稀疏补丁合并：metrics 按 name 命中，process_ref 覆盖
    cv, _, errors = C.upsert_elements(cv, "scenario", [{
        "name": "vip_flow",
        "metrics": [{"name": "客诉率", "target": None}],   # 子项显式 null = 清除该字段
    }])
    assert not errors
    scenario = cv["scenarios"][0]
    assert scenario["metrics"][0]["id"] == metric_id
    assert "target" not in scenario["metrics"][0]
    assert scenario["metrics"][0]["formula"] == "客诉 ≤ 1 个百分点"
    assert scenario["process_ref"] == "order_fulfillment"

    # 父元素显式 null 不擦除已确认值（与旧语义一致）
    cv, _, errors = C.upsert_elements(cv, "scenario", [{
        "name": "vip_flow", "process_ref": None,
    }])
    assert not errors and cv["scenarios"][0]["process_ref"] == "order_fulfillment"


def _legacy_ready_canvas() -> dict:
    """一份全绿画布（对象/主体/行为/规则/事件/场景齐备），不含任何流程。"""
    cv = C.empty_canvas()
    cv, _, errors = C.upsert_elements(cv, "object", [{
        "name": "Order", "displayName": "订单", "keyAttribute": "order_no",
        "attributes": [
            {"name": "order_no", "displayName": "订单号", "typeHint": "文本", "required": True},
            {"name": "amount", "displayName": "金额", "typeHint": "金额"},
        ],
    }])
    assert not errors
    cv, _, errors = C.upsert_elements(cv, "actor", [{
        "name": "Sales", "displayName": "销售", "kind": "role",
    }])
    assert not errors
    cv, _, errors = C.upsert_elements(cv, "behavior", [{
        "name": "confirm_pay", "displayName": "确认支付", "actor": "Sales",
        "object": "Order", "trigger": "收到银行回单", "outcome": "订单变为已支付",
    }])
    assert not errors
    cv, _, errors = C.upsert_elements(cv, "scenario", [{
        "name": "pay_flow", "displayName": "支付流程", "goal": "完成订单支付",
        "actors": ["Sales"], "steps": ["销售确认回单并登记"],
        "objects": ["Order"], "behaviors": ["confirm_pay"],
        "expectedOutcome": "订单进入已支付状态",
    }])
    assert not errors
    assert R.evaluate(cv)["ready"] is True
    return cv


def test_vacuous_pass_legacy_canvas_without_processes_key():
    """最高优先级回归：旧画布没有 processes 键 —— evaluate 不炸，
    且 ready/门禁报告与显式带空 processes 的新画布逐字节一致。"""
    canvas = _legacy_ready_canvas()
    legacy = {key: value for key, value in canvas.items() if key != "processes"}
    assert "processes" not in legacy

    report_legacy = R.evaluate(legacy)
    assert report_legacy["ready"] is True
    assert report_legacy["gatesTotal"] == 10
    process_gate = next(g for g in report_legacy["gates"] if g["id"] == "processes")
    assert process_gate["passed"] is True
    assert process_gate["blockingItems"] == [] and process_gate["advisoryItems"] == []
    # vacuous pass 铁律：零流程时新门无条件通过，其余门语义不受影响
    assert report_legacy == R.evaluate(canvas)

    # 旧画布上的未就绪语义同样不变：堵门项不来自新门
    broken = _legacy_ready_canvas()
    broken["scenarios"][0]["objects"] = ["Ghost"]
    broken.pop("processes")
    report = R.evaluate(broken)
    assert report["ready"] is False
    assert next(g for g in report["gates"] if g["id"] == "processes")["passed"] is True
    assert any("Ghost" in item
               for g in report["gates"] if g["id"] == "coverage"
               for item in g["blockingItems"])
