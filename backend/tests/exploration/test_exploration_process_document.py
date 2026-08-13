"""需求文档的流程章节与指纹兼容测试（设计 §6）：

  1. 「## 8. 流程模型」确定性渲染：步骤表 / 分支表（含正常·异常列）/
     度量表 / 预期结果
  2. 章节号顺延：## 8 流程模型 → ## 9 场景模型 → ## 10 澄清账本 → ## 11 质量门检查
  3. 场景节新增「所属流程」行与产出度量表（仅 process_ref/metrics 有值时）
  4. 未挂接场景渲染字节级不变（向后兼容铁律）
  5. canvas_fingerprint 剔除空 processes 键：旧快照不 stale / 写入流程必 stale
"""
from __future__ import annotations

import uuid

from app.exploration import canvas as C
from app.exploration import document as D
from app.exploration.models import ExplorationSession


def _process_payload() -> dict:
    return {
        "name": "fulfillment", "displayName": "履约流程",
        "goal": "完成订单履约", "trigger": "订单支付成功",
        "steps": [
            {"seq": 1, "name": "确认订单", "actor": "Sales", "behavior": "confirm_order",
             "inputs": ["订单"], "outputs": ["确认结果"]},
            {"seq": 2, "name": "库存检查", "actor": "Sales"},
            {"seq": 3, "name": "发货", "behavior": "ship_order"},
        ],
        "branches": [
            {"fromStep": 2, "toStep": 3, "condition": "库存充足"},
            {"fromStep": 2, "toStep": None, "condition": "缺货取消", "kind": "exception"},
        ],
        "objects": ["Order"],
        "metrics": [{"name": "履约时长", "formula": "从支付到发货 ≤ 48 小时",
                     "sourceObjects": ["Order"], "target": "≤ 24 小时"}],
        "expectedOutcome": "订单交付客户",
    }


def _canvas_with_process() -> dict:
    cv = C.empty_canvas()
    cv, _, errors = C.upsert_elements(cv, "process", [_process_payload()])
    assert not errors
    return cv


def _full_canvas() -> dict:
    cv = _canvas_with_process()
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
        "object": "Order", "outcome": "订单已确认",
    }])
    assert not errors
    cv, _, errors = C.upsert_elements(cv, "scenario", [{
        "name": "vip_flow", "displayName": "VIP 履约", "goal": "大客户履约变体",
        "processRef": "fulfillment",
        "metrics": [{"name": "客诉率", "formula": "客诉 ≤ 1 个百分点",
                     "sourceObjects": ["Order"], "target": "≤ 0.5%"}],
    }])
    assert not errors
    return cv


# ---------------------------------------------------------------- 流程章节渲染


def test_render_processes_deterministic_tables():
    cv = _canvas_with_process()
    md = D._render_processes(cv["processes"])
    assert "### 流程 1：履约流程（fulfillment）" in md
    assert "**目标**：完成订单履约" in md and "**触发**：订单支付成功" in md
    # 步骤表：序号/步骤/负责主体/对应行为/输入/产出（按 seq 升序）
    assert "| 序号 | 步骤 | 负责主体 | 对应行为 | 输入 | 产出 |" in md
    assert "| 1 | 确认订单 | Sales | confirm_order | 订单 | 确认结果 |" in md
    assert "| 2 | 库存检查 | Sales |  |  |  |" in md
    # 分支表含「类型」列：normal→正常 / exception→异常；to_step=None→结束/流程结束
    assert "| 起始步骤 | 目标步骤 | 条件 | 类型 | 起始内容 | 目标内容 |" in md
    assert "| 2 | 3 | 库存充足 | 正常 | 库存检查 | 发货 |" in md
    assert "| 2 | 结束 | 缺货取消 | 异常 | 库存检查 | 流程结束 |" in md
    # 度量表：指标/口径/数据来源/目标值
    assert "| 指标 | 口径 | 数据来源 | 目标值 |" in md
    assert "| 履约时长 | 从支付到发货 ≤ 48 小时 | Order | ≤ 24 小时 |" in md
    assert "**预期结果**：订单交付客户" in md
    assert "**关联对象**：Order" in md
    # 空流程集合渲染占位（与旧画布兼容）
    assert D._render_processes([]) == "（空）\n"


def test_render_processes_respects_seq_order():
    cv = C.empty_canvas()
    cv, _, errors = C.upsert_elements(cv, "process", [{
        "name": "p", "steps": [{"seq": 2, "name": "后"}, {"seq": 1, "name": "先"}],
    }])
    assert not errors
    md = D._render_processes(cv["processes"])
    assert md.index("| 1 | 先 |") < md.index("| 2 | 后 |")


# ---------------------------------------------------------------- 场景节增量

_LEGACY_SCENARIO = {
    "id": "el-legacy01",
    "name": "pay_flow",
    "display_name": "支付流程",
    "goal": "完成订单支付",
    "actors": ["Sales"],
    "steps": ["销售确认回单", "订单变为已支付"],
    "objects": ["Order"],
    "behaviors": ["confirm_pay"],
    "expected_outcome": "订单进入已支付状态",
}

_EXPECTED_LEGACY_MD = (
    "### 场景 1：支付流程（pay_flow）\n"
    "\n"
    "**目标**：完成订单支付\n"
    "\n"
    "**参与主体**：Sales\n"
    "\n"
    "1. 销售确认回单\n"
    "2. 订单变为已支付\n"
    "\n"
    "**预期结果**：订单进入已支付状态\n"
    "\n"
    "**关联模型** — 对象：Order；行为：confirm_pay\n"
)


def test_unattached_scenario_rendering_is_byte_identical():
    md = D._render_scenarios([dict(_LEGACY_SCENARIO)])
    assert md == _EXPECTED_LEGACY_MD
    assert "所属流程" not in md and "产出度量" not in md
    # 旧快照缺新字段 / 新画布 metrics=[] 两种形态渲染一致
    assert D._render_scenarios([{**_LEGACY_SCENARIO, "metrics": []}]) == _EXPECTED_LEGACY_MD


def test_attached_scenario_renders_process_ref_and_metrics():
    cv = C.empty_canvas()
    cv, _, errors = C.upsert_elements(cv, "scenario", [{
        "name": "vip_flow", "displayName": "VIP 履约", "goal": "大客户履约变体",
        "processRef": "fulfillment",
        "metrics": [{"name": "客诉率", "formula": "客诉 ≤ 1 个百分点",
                     "sourceObjects": ["Order"], "target": "≤ 0.5%"}],
    }])
    assert not errors
    md = D._render_scenarios(cv["scenarios"])
    assert "**所属流程**：fulfillment" in md
    assert "**产出度量**" in md
    assert "| 指标 | 口径 | 数据来源 | 目标值 |" in md
    assert "| 客诉率 | 客诉 ≤ 1 个百分点 | Order | ≤ 0.5% |" in md


# ---------------------------------------------------------------- 整篇文档章节号


def _make_session(db, canvas: dict) -> ExplorationSession:
    session = ExplorationSession(id=str(uuid.uuid4()), title="流程文档测试",
                                 canvas=canvas, canvas_version=1)
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


def test_document_contains_process_section_and_renumbered_headings(db):
    session = _make_session(db, _full_canvas())
    doc = D.generate_document(db, session, None)
    md = doc.content_md
    for heading in ("## 7. 规则模型", "## 8. 流程模型", "## 9. 场景模型",
                    "## 10. 澄清账本", "## 11. 质量门检查"):
        assert heading in md, f"缺少章节: {heading}"
    # 流程模型插在规则与场景之间，后续章节顺延且顺序正确
    assert md.index("## 7. 规则模型") < md.index("## 8. 流程模型") \
        < md.index("## 9. 场景模型") < md.index("## 10. 澄清账本") \
        < md.index("## 11. 质量门检查")
    # 流程内容与场景增量进入整篇文档
    assert "### 流程 1：履约流程（fulfillment）" in md
    assert "| 序号 | 步骤 | 负责主体 | 对应行为 | 输入 | 产出 |" in md
    assert "**所属流程**：fulfillment" in md
    assert "| 客诉率 | 客诉 ≤ 1 个百分点 | Order | ≤ 0.5% |" in md


# ---------------------------------------------------------------- 指纹兼容


def _legacy_canvas() -> dict:
    """无 processes 键的旧画布（其余键齐全）。"""
    cv = C.empty_canvas()
    cv, _, errors = C.upsert_elements(cv, "object", [{
        "name": "Order", "displayName": "订单",
        "attributes": [{"name": "order_no", "typeHint": "文本"}],
    }])
    assert not errors
    legacy = {key: value for key, value in cv.items() if key != "processes"}
    assert "processes" not in legacy
    return legacy


def test_fingerprint_ignores_empty_processes_key():
    legacy = _legacy_canvas()
    modern = C._ensure_canvas(legacy)
    assert modern["processes"] == []
    # 旧快照与新画布指纹一致 → 存量文档不全局 stale
    assert D.canvas_fingerprint(legacy) == D.canvas_fingerprint(modern)
    # 快照封装（_document_source 元数据）不参与指纹
    assert D.canvas_fingerprint(D.snapshot_with_source(legacy, 3)) \
        == D.canvas_fingerprint(legacy)


def test_fingerprint_changes_once_process_content_written():
    legacy = _legacy_canvas()
    with_process = C._ensure_canvas(legacy)
    with_process, _, errors = C.upsert_elements(
        with_process, "process", [_process_payload()])
    assert not errors
    assert D.canvas_fingerprint(with_process) != D.canvas_fingerprint(legacy)
