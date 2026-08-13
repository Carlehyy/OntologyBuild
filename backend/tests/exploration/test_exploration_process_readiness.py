"""业务流程质量门（第5门 · 流程编排）与 scope/coverage 门改造测试：

  1. 门序与 gatesTotal == 10 显式断言（防回退）；阶段文案随新门重排
  2. vacuous pass：零流程画布 processes 门无条件通过
  3. 新门 blocking 逐条：steps<2 / step.actor·behavior 不可解析 / branch 越界·condition 空 /
     起点不可达·无法到达终点 / metric.formula 空·未定量 / metric.source_objects 不可解析
  4. 新门 advisory 逐条：无 exception 分支 / 无 metrics / 全部步骤未绑定 behavior
  5. scope 门放宽：只有流程（无场景）也能过阶段0
  6. coverage 门四组：process_ref 不可解析 blocking / 挂接从简 / 未挂维持现状 / 覆盖放宽
"""
from __future__ import annotations

import pytest

from app.exploration import canvas as C
from app.exploration import readiness as R


def _ready_process_canvas() -> dict:
    """一份全绿画布：既有六类齐备 + 一个结构完整、度量定量的流程。"""
    cv = C.empty_canvas()
    cv, _, errors = C.upsert_elements(cv, "object", [{
        "name": "Order", "displayName": "订单", "keyAttribute": "order_no",
        "attributes": [
            {"name": "order_no", "displayName": "订单号", "typeHint": "文本", "required": True},
            {"name": "amount", "displayName": "金额", "typeHint": "金额"},
        ],
    }])
    assert not errors
    cv, _, errors = C.upsert_elements(cv, "actor", [
        {"name": "Sales", "displayName": "销售", "kind": "role"},
        {"name": "Buyer", "displayName": "采购员", "kind": "role"},
    ])
    assert not errors
    cv, _, errors = C.upsert_elements(cv, "behavior", [
        {"name": "confirm_order", "displayName": "确认订单", "actor": "Sales",
         "object": "Order", "trigger": "订单支付成功", "outcome": "订单进入履约"},
        {"name": "ship_order", "displayName": "订单发货", "actor": "Sales",
         "object": "Order", "trigger": "库存锁定", "outcome": "订单发出"},
    ])
    assert not errors
    cv, _, errors = C.upsert_elements(cv, "process", [{
        "name": "order_fulfillment", "displayName": "订单履约流程",
        "goal": "完成订单履约", "trigger": "订单支付成功",
        "steps": [
            {"seq": 1, "name": "确认订单", "actor": "Sales", "behavior": "confirm_order"},
            {"seq": 2, "name": "如果库存不足则采购", "actor": "Buyer"},
            {"seq": 3, "name": "订单发货", "actor": "Sales", "behavior": "ship_order"},
        ],
        "branches": [
            {"fromStep": 2, "toStep": 3, "condition": "库存充足"},
            {"fromStep": 2, "toStep": None, "condition": "缺货取消", "kind": "exception"},
        ],
        "objects": ["Order"],
        "metrics": [{"name": "履约时长", "formula": "从支付到发货 ≤ 48 小时",
                     "sourceObjects": ["Order"]}],
        "expectedOutcome": "订单交付客户",
    }])
    assert not errors
    cv, _, errors = C.upsert_elements(cv, "scenario", [{
        "name": "pay_flow", "displayName": "支付流程", "goal": "完成订单支付",
        "actors": ["Sales"], "steps": ["销售确认回单并登记"],
        "objects": ["Order"], "behaviors": ["confirm_order", "ship_order"],
        "expectedOutcome": "订单进入已支付状态",
    }])
    assert not errors
    assert R.evaluate(cv)["ready"] is True
    return cv


def _gate(canvas: dict, gid: str) -> dict:
    return next(g for g in R.evaluate(canvas)["gates"] if g["id"] == gid)


def test_gate_order_and_total_are_locked():
    report = R.evaluate(_ready_process_canvas())
    assert report["gatesTotal"] == 10
    assert [g["id"] for g in report["gates"]] == [
        "scope", "objects", "relations", "behaviors", "lifecycles",
        "rules", "events", "processes", "questions", "coverage",
    ]
    stages = {gid: stage for gid, _, stage in R._GATE_STAGES}
    assert stages["processes"] == "阶段5 · 流程编排：步骤落位、分支可达、度量定量"
    assert stages["questions"].startswith("阶段6 · 清账")
    assert stages["coverage"].startswith("阶段7 · 验收")
    # 新门未过 → 阶段推进停在阶段5
    canvas = _ready_process_canvas()
    canvas["processes"][0]["metrics"] = [{"name": "履约时长"}]
    assert "阶段5 · 流程编排" in R.evaluate(canvas)["stage"]


def test_processes_gate_vacuous_pass_when_canvas_has_no_process():
    canvas = _ready_process_canvas()
    canvas["processes"] = []
    gate = _gate(canvas, "processes")
    assert gate["passed"] is True
    assert gate["blockingItems"] == [] and gate["advisoryItems"] == []
    assert R.evaluate(canvas)["ready"] is True


def test_processes_gate_blocking_items():
    # steps < 2
    canvas = _ready_process_canvas()
    canvas["processes"][0]["steps"] = canvas["processes"][0]["steps"][:1]
    items = _gate(canvas, "processes")["blockingItems"]
    assert any("少于 2 步" in item for item in items)

    # step.actor 填了不可解析
    canvas = _ready_process_canvas()
    canvas["processes"][0]["steps"][0]["actor"] = "Ghost"
    items = _gate(canvas, "processes")["blockingItems"]
    assert any("步骤引用未定义主体" in item and "Ghost" in item for item in items)

    # step.behavior 填了不可解析
    canvas = _ready_process_canvas()
    canvas["processes"][0]["steps"][0]["behavior"] = "ghost_behavior"
    items = _gate(canvas, "processes")["blockingItems"]
    assert any("步骤引用未定义行为" in item and "ghost_behavior" in item for item in items)

    # branch 越界 / condition 空（绕过 pydantic 直接改画布，模拟历史脏数据）
    canvas = _ready_process_canvas()
    canvas["processes"][0]["branches"][0]["to_step"] = 99
    items = _gate(canvas, "processes")["blockingItems"]
    assert any("不在步骤范围" in item for item in items)

    canvas = _ready_process_canvas()
    canvas["processes"][0]["branches"][0]["condition"] = ""
    items = _gate(canvas, "processes")["blockingItems"]
    assert any("缺少条件标签" in item for item in items)

    # 起点不可达：条件步骤自环，第 3 步失去入边
    canvas = _ready_process_canvas()
    canvas["processes"][0]["branches"] = [
        {"from_step": 2, "to_step": 2, "condition": "继续等货"},
        {"from_step": 2, "to_step": 2, "condition": "再次催单"},
    ]
    items = _gate(canvas, "processes")["blockingItems"]
    assert any("从流程起点不可达" in item for item in items)

    # 无法到达终点：最后一步是条件步骤且分支全部回环（步骤 2 去条件化，隔离变量）
    canvas = _ready_process_canvas()
    canvas["processes"][0]["steps"][1]["name"] = "采购补货"
    canvas["processes"][0]["steps"][2]["name"] = "是否确认发货？"
    canvas["processes"][0]["branches"] = [
        {"from_step": 3, "to_step": 2, "condition": "再核一遍"},
        {"from_step": 3, "to_step": 2, "condition": "等客户回复"},
    ]
    items = _gate(canvas, "processes")["blockingItems"]
    assert any("没有通往结束节点的路径" in item for item in items)

    # metric.formula 空 / 未定量
    canvas = _ready_process_canvas()
    canvas["processes"][0]["metrics"][0]["formula"] = ""
    items = _gate(canvas, "processes")["blockingItems"]
    assert any("缺少计算口径" in item for item in items)

    canvas = _ready_process_canvas()
    canvas["processes"][0]["metrics"][0]["formula"] = "尽快完成履约"
    items = _gate(canvas, "processes")["blockingItems"]
    assert any("口径未定量" in item for item in items)

    # metric.source_objects 不可解析
    canvas = _ready_process_canvas()
    canvas["processes"][0]["metrics"][0]["source_objects"] = ["Ghost"]
    items = _gate(canvas, "processes")["blockingItems"]
    assert any("来源对象「Ghost」未在对象/主体模型中定义" in item for item in items)


def test_processes_gate_advisory_items_do_not_block():
    # 无 exception 分支（条件文案先去正则化，避免触发显式分支要求）
    canvas = _ready_process_canvas()
    canvas["processes"][0]["steps"][1]["name"] = "采购补货"
    canvas["processes"][0]["branches"] = []
    gate = _gate(canvas, "processes")
    assert gate["passed"] is True
    assert any("没有异常分支" in item for item in gate["advisoryItems"])

    # 无 metrics
    canvas = _ready_process_canvas()
    canvas["processes"][0]["metrics"] = []
    gate = _gate(canvas, "processes")
    assert gate["passed"] is True
    assert any("还没有产出度量" in item for item in gate["advisoryItems"])

    # 全部步骤未绑定 behavior
    canvas = _ready_process_canvas()
    for step in canvas["processes"][0]["steps"]:
        step["behavior"] = None
    gate = _gate(canvas, "processes")
    assert gate["passed"] is True
    assert any("全部步骤都未绑定行为" in item for item in gate["advisoryItems"])


def test_scope_gate_accepts_process_as_exploration_start():
    # 只有流程（无场景）也能过阶段0 —— 探索起点放宽为「流程或场景」
    canvas = _ready_process_canvas()
    canvas["scenarios"] = []
    scope = _gate(canvas, "scope")
    assert scope["passed"] is True
    assert not any("还没有任何流程或场景" in item for item in scope["blockingItems"])

    empty = C.empty_canvas()
    scope = _gate(empty, "scope")
    assert scope["passed"] is False
    assert any("还没有任何流程或场景 —— 先让用户讲一个端到端流程或一个典型情境" in item
               for item in scope["blockingItems"])


def test_coverage_process_ref_unresolvable_blocks():
    canvas = _ready_process_canvas()
    canvas, _, errors = C.upsert_elements(canvas, "scenario", [{
        "name": "vip_flow", "goal": "大客户履约变体", "processRef": "ghost_process",
    }])
    assert not errors
    items = _gate(canvas, "coverage")["blockingItems"]
    assert any("挂接的流程「ghost_process」不存在" in item for item in items)
    assert R.evaluate(canvas)["ready"] is False


def test_coverage_linked_scenario_is_simplified():
    # 挂接场景从简：只有 goal 即可，不要求 actors/steps/objects/behaviors/expected_outcome
    canvas = _ready_process_canvas()
    canvas, _, errors = C.upsert_elements(canvas, "scenario", [{
        "name": "vip_flow", "goal": "大客户履约变体", "processRef": "order_fulfillment",
    }])
    assert not errors
    blocking = _gate(canvas, "coverage")["blockingItems"]
    assert not any("vip_flow" in item or "VIP" in item for item in blocking)

    # goal 缺失仍堵门
    canvas = _ready_process_canvas()
    canvas, _, errors = C.upsert_elements(canvas, "scenario", [{
        "name": "vip_flow", "processRef": "order_fulfillment",
    }])
    assert not errors
    blocking = _gate(canvas, "coverage")["blockingItems"]
    assert any("缺少业务目标（goal）" in item for item in blocking)

    # objects/behaviors 若填必须可解析
    canvas = _ready_process_canvas()
    canvas, _, errors = C.upsert_elements(canvas, "scenario", [{
        "name": "vip_flow", "goal": "大客户履约变体", "processRef": "order_fulfillment",
        "objects": ["Ghost"], "behaviors": ["ghost_behavior"],
    }])
    assert not errors
    blocking = _gate(canvas, "coverage")["blockingItems"]
    assert any("引用未定义对象/实体主体：Ghost" in item for item in blocking)
    assert any("引用未定义行为：ghost_behavior" in item for item in blocking)


def test_coverage_unlinked_scenario_keeps_full_validation():
    # 未挂接场景维持现状：空壳场景六字段全部堵门
    canvas = _ready_process_canvas()
    canvas, _, errors = C.upsert_elements(canvas, "scenario", [{"name": "shell_scenario"}])
    assert not errors
    blocking = _gate(canvas, "coverage")["blockingItems"]
    shell = [item for item in blocking if "shell_scenario" in item]
    assert any("goal" in item for item in shell)
    assert any("steps" in item for item in shell)
    assert any("objects" in item for item in shell)
    assert any("behaviors" in item for item in shell)
    assert any("expected_outcome" in item for item in shell)


def test_coverage_counts_process_references_and_triggers_on_process_only():
    # 只被流程引用的对象/行为也算覆盖；无场景有流程时未覆盖 advisory 照常触发
    canvas = C.empty_canvas()
    canvas, _, errors = C.upsert_elements(canvas, "object", [{
        "name": "Order", "keyAttribute": "order_no",
        "attributes": [{"name": "order_no", "typeHint": "文本"}],
    }, {
        "name": "Invoice", "keyAttribute": "invoice_no",
        "attributes": [{"name": "invoice_no", "typeHint": "文本"}],
    }])
    assert not errors
    canvas, _, errors = C.upsert_elements(canvas, "actor", [
        {"name": "Sales", "kind": "role"},
    ])
    assert not errors
    canvas, _, errors = C.upsert_elements(canvas, "behavior", [
        {"name": "confirm_order", "actor": "Sales", "object": "Order",
         "trigger": "支付成功", "outcome": "进入履约"},
        {"name": "audit_invoice", "actor": "Sales", "object": "Invoice",
         "trigger": "月末", "outcome": "完成对账"},
    ])
    assert not errors
    canvas, _, errors = C.upsert_elements(canvas, "process", [{
        "name": "order_fulfillment", "goal": "完成履约",
        "steps": [
            {"seq": 1, "name": "确认订单", "actor": "Sales", "behavior": "confirm_order"},
            {"seq": 2, "name": "订单归档", "actor": "Sales"},
        ],
        "objects": ["Order"],
        "metrics": [{"name": "履约时长", "formula": "≤ 48 小时", "sourceObjects": ["Order"]}],
        "expectedOutcome": "订单交付",
    }])
    assert not errors
    coverage = _gate(canvas, "coverage")
    assert coverage["passed"] is True
    # Order/confirm_order 被流程覆盖；Invoice/audit_invoice 未被任何场景或流程覆盖
    assert any("Invoice" in item for item in coverage["advisoryItems"])
    assert any("audit_invoice" in item for item in coverage["advisoryItems"])
    assert not any("Order" in item for item in coverage["advisoryItems"])
    assert not any("confirm_order" in item for item in coverage["advisoryItems"])
    assert any("未被任何场景或流程覆盖" in item for item in coverage["advisoryItems"])
