"""需求文档生成 — 骨架确定性渲染，LLM 只写叙述

稳定性原则：七类模型的清单章节由画布**确定性**渲染成 markdown 表格
（文档永远忠实于画布）；LLM 仅补「背景与目标 / 业务概述」两节叙述，
调用失败时降级为占位文本，文档生成永不因 LLM 失败而失败。
"""
from __future__ import annotations

import copy
import hashlib
import json
import logging
from typing import Optional

from sqlalchemy.orm import Session

from app.ontologies.agent_runtime import llm_bridge
from app.exploration import canvas as C
from app.exploration import questions as Q
from app.exploration import readiness as R
from app.exploration.models import ExplorationDocument, ExplorationMessage, ExplorationSession

logger = logging.getLogger(__name__)

_NARRATIVE_FALLBACK = "> （未配置 LLM 或叙述生成失败，此节留待补充。清单章节由业务画布确定性生成，不受影响。）"
_SOURCE_META_KEY = "_document_source"


def canvas_fingerprint(canvas: dict) -> str:
    """对业务画布做稳定 SHA-256 指纹。

    文档自己的来源元数据不参与哈希，避免快照封装改变业务内容指纹。问题账本等
    其余画布字段会参与，因此任何影响需求结论的改动都能使旧文档变 stale。

    规范化时剔除值为空列表的 ``processes`` 键（仅剔这一个键）：旧快照（无此键）
    与新画布（空 processes）指纹一致，存量文档不因流程模型上线而全局 stale；
    画布一旦写入流程内容，指纹自然变化（正确）。
    """
    canonical = C._ensure_canvas(canvas)
    canonical.pop(_SOURCE_META_KEY, None)
    if canonical.get("processes") == []:
        canonical.pop("processes", None)
    payload = json.dumps(
        canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def snapshot_with_source(canvas: dict, canvas_version: int) -> dict:
    snapshot = copy.deepcopy(C._ensure_canvas(canvas))
    snapshot[_SOURCE_META_KEY] = {
        "canvasVersion": int(canvas_version or 0),
        "canvasFingerprint": canvas_fingerprint(snapshot),
        "fingerprintAlgorithm": "sha256",
    }
    return snapshot


def document_source_state(document: ExplorationDocument,
                          session: ExplorationSession) -> dict:
    """返回文档来源与当前画布的对比状态；兼容没有元数据的历史文档。

    历史文档无法恢复生成时的 canvasVersion，故返回 None；但其快照仍可现场计算
    指纹并可靠识别内容是否 stale。
    """
    snapshot = document.canvas_snapshot or {}
    meta = snapshot.get(_SOURCE_META_KEY) if isinstance(snapshot, dict) else None
    meta = meta if isinstance(meta, dict) else {}
    source_version = meta.get("canvasVersion")
    try:
        source_version = int(source_version) if source_version is not None else None
    except (TypeError, ValueError):
        source_version = None
    source_fingerprint = str(meta.get("canvasFingerprint") or canvas_fingerprint(snapshot))
    current_fingerprint = canvas_fingerprint(session.canvas or {})
    return {
        "source_canvas_version": source_version,
        "source_canvas_fingerprint": source_fingerprint,
        "current_canvas_version": int(session.canvas_version or 0),
        "current_canvas_fingerprint": current_fingerprint,
        "is_stale": source_fingerprint != current_fingerprint,
    }


def _md_escape(v) -> str:
    return str(v if v is not None else "").replace("|", "\\|").replace("\n", " ")


def _table(headers: list[str], rows: list[list]) -> str:
    if not rows:
        return "（空）\n"
    out = ["| " + " | ".join(headers) + " |",
           "| " + " | ".join("---" for _ in headers) + " |"]
    for r in rows:
        out.append("| " + " | ".join(_md_escape(c) for c in r) + " |")
    return "\n".join(out) + "\n"


def _label(x: dict) -> str:
    dn = x.get("display_name")
    return f"{dn}（{x.get('name')}）" if dn and dn != x.get("name") else str(x.get("name", ""))


def _render_attributes(items: list[dict]) -> str:
    return _table(
        ["属性", "显示名", "类型提示", "必填", "枚举", "说明"],
        [[a.get("name"), a.get("display_name", ""), a.get("type_hint", ""),
          "是" if a.get("required") else "否",
          " / ".join(str(v) for v in (a.get("enum") or [])),
          a.get("notes") or a.get("description") or ""]
         for a in items],
    )


def _render_actors(items: list[dict]) -> str:
    parts = []
    for actor in items:
        parts.append(f"### {_label(actor)}\n")
        parts.append(_table(
            ["类型", "业务主键", "说明", "职责"],
            [[actor.get("kind", ""), actor.get("key_attribute") or "未指定",
              actor.get("description", ""),
              "；".join(actor.get("responsibilities") or [])]],
        ))
        parts.append("**主体属性**\n")
        parts.append(_render_attributes(actor.get("attributes") or []))
    return "\n".join(parts) if parts else "（空）\n"


def _render_objects(items: list[dict]) -> str:
    parts = []
    for o in items:
        parts.append(f"### {_label(o)}\n")
        if o.get("description"):
            parts.append(o["description"] + "\n")
        parts.append("**属性**（业务主键：" + (o.get("key_attribute") or "未指定") + "）\n")
        parts.append(_render_attributes(o.get("attributes") or []))
        rels = o.get("relations") or []
        if rels:
            parts.append("**关系**\n")
            parts.append(_table(["关系", "目标对象", "基数", "说明"],
                                [[r.get("display_name") or r.get("name", ""), r.get("target"),
                                  r.get("cardinality", ""), r.get("description", "")] for r in rels]))
    return "\n".join(parts) if parts else "（空）\n"


def _render_behaviors(items: list[dict]) -> str:
    parts = []
    for behavior in items:
        parts.append(f"### {_label(behavior)}\n")
        parts.append(_table(
            ["执行主体", "作用对象", "触发", "结果", "约束", "需审批", "说明"],
            [[behavior.get("actor", ""), behavior.get("object", ""),
              behavior.get("trigger", ""), behavior.get("outcome", ""),
              "；".join(behavior.get("constraints") or []),
              "是" if behavior.get("needs_approval") else "否",
              behavior.get("description", "")]],
        ))
        parts.append("**输入契约**\n")
        parts.append(_render_attributes(behavior.get("inputs") or []))
    return "\n".join(parts) if parts else "（空）\n"


def _render_events(items: list[dict]) -> str:
    return _table(["事件", "来源", "载荷", "后果", "说明"],
                  [[_label(e), e.get("source", ""), "，".join(e.get("payload") or []),
                    "；".join(e.get("consequences") or []), e.get("description", "")] for e in items])


def _render_rules(items: list[dict]) -> str:
    return _table(["规则", "类别", "作用于", "表述", "违反提示"],
                  [[_label(r), r.get("kind", ""), r.get("applies_to", ""),
                    r.get("statement") or r.get("description", ""),
                    r.get("error_message", "")] for r in items])


def _render_metrics(items: list[dict]) -> str:
    """流程/场景共用的产出度量表（formula 是定量口径，source_objects 是数据来源）。"""
    return _table(["指标", "口径", "数据来源", "目标值"],
                  [[_label(m), m.get("formula", ""),
                    "、".join(str(v) for v in (m.get("source_objects") or [])),
                    m.get("target", "")] for m in items])


def _render_processes(items: list[dict]) -> str:
    parts = []
    for i, p in enumerate(items, 1):
        parts.append(f"### 流程 {i}：{_label(p)}\n")
        if p.get("goal"):
            parts.append(f"**目标**：{p['goal']}\n")
        if p.get("trigger"):
            parts.append(f"**触发**：{p['trigger']}\n")
        # 步骤按 seq 升序渲染，与流程图/质量门同一口径
        steps = sorted((st for st in (p.get("steps") or []) if isinstance(st, dict)),
                       key=lambda st: int(st.get("seq") or 0))
        if steps:
            parts.append("**流程步骤**\n")
            parts.append(_table(
                ["序号", "步骤", "负责主体", "对应行为", "输入", "产出"],
                [[step.get("seq", j), step.get("name", ""),
                  step.get("actor", ""), step.get("behavior", ""),
                  "、".join(str(v) for v in (step.get("inputs") or [])),
                  "、".join(str(v) for v in (step.get("outputs") or []))]
                 for j, step in enumerate(steps, 1)],
            ))
        branches = p.get("branches") or []
        if branches:
            parts.append("**条件分支**\n")
            parts.append(_table(
                ["起始步骤", "目标步骤", "条件", "类型", "起始内容", "目标内容"],
                [[branch.get("from_step"), branch.get("to_step") or "结束",
                  branch.get("condition", ""),
                  {"normal": "正常", "exception": "异常"}.get(
                      branch.get("kind") or "normal", branch.get("kind") or ""),
                  (steps[branch["from_step"] - 1].get("name", "")
                   if isinstance(branch.get("from_step"), int)
                   and 1 <= branch["from_step"] <= len(steps) else ""),
                  (steps[branch["to_step"] - 1].get("name", "")
                   if isinstance(branch.get("to_step"), int)
                   and 1 <= branch["to_step"] <= len(steps) else "流程结束")]
                 for branch in branches],
            ))
        metrics = p.get("metrics") or []
        if metrics:
            parts.append("**产出度量**\n")
            parts.append(_render_metrics(metrics))
        if p.get("expected_outcome"):
            parts.append(f"**预期结果**：{p['expected_outcome']}\n")
        if p.get("objects"):
            parts.append("**关联对象**：" + "、".join(p["objects"]) + "\n")
    return "\n".join(parts) if parts else "（空）\n"


def _render_scenarios(items: list[dict]) -> str:
    parts = []
    for i, s in enumerate(items, 1):
        parts.append(f"### 场景 {i}：{_label(s)}\n")
        if s.get("goal"):
            parts.append(f"**目标**：{s['goal']}\n")
        if s.get("process_ref"):
            parts.append(f"**所属流程**：{s['process_ref']}\n")
        if s.get("actors"):
            parts.append("**参与主体**：" + "、".join(s["actors"]) + "\n")
        steps = s.get("steps") or []
        if steps:
            parts.append("\n".join(f"{j}. {st}" for j, st in enumerate(steps, 1)) + "\n")
        branches = s.get("branches") or []
        if branches:
            parts.append("**条件分支**\n")
            parts.append(_table(
                ["起始步骤", "目标步骤", "条件", "起始内容", "目标内容"],
                [[branch.get("from_step"), branch.get("to_step") or "结束",
                  branch.get("condition", ""),
                  (steps[branch["from_step"] - 1]
                   if isinstance(branch.get("from_step"), int)
                   and 1 <= branch["from_step"] <= len(steps) else ""),
                  (steps[branch["to_step"] - 1]
                   if isinstance(branch.get("to_step"), int)
                   and 1 <= branch["to_step"] <= len(steps) else "流程结束")]
                 for branch in branches],
            ))
        if s.get("expected_outcome"):
            parts.append(f"**预期结果**：{s['expected_outcome']}\n")
        metrics = s.get("metrics") or []
        if metrics:
            parts.append("**产出度量**\n")
            parts.append(_render_metrics(metrics))
        refs = []
        if s.get("objects"):
            refs.append("对象：" + "、".join(s["objects"]))
        if s.get("behaviors"):
            refs.append("行为：" + "、".join(s["behaviors"]))
        if refs:
            parts.append("**关联模型** — " + "；".join(refs) + "\n")
    return "\n".join(parts) if parts else "（空）\n"


def _narrative(db: Session, session: ExplorationSession, call_kwargs: Optional[dict]) -> str:
    """LLM 叙述节：背景与目标 + 业务概述。失败降级为占位。"""
    if not call_kwargs:
        return _NARRATIVE_FALLBACK
    recent = (db.query(ExplorationMessage)
              .filter(ExplorationMessage.session_id == session.id)
              .order_by(ExplorationMessage.created_at.desc())
              .limit(16).all())[::-1]
    dialogue = "\n".join(f"{'用户' if m.role == 'user' else '助手'}: {(m.content or '')[:400]}"
                         for m in recent if (m.content or "").strip())
    prompt = f"""根据业务画布摘要与对话摘录，为需求文档撰写开头两节，输出 markdown（不要代码块包裹），
恰好两个二级标题：「## 1. 背景与目标」与「## 2. 业务概述」。忠实于已知信息，不要虚构画布中不存在的概念。

# 业务画布摘要
{C.canvas_summary(session.canvas)}

# 对话摘录
{dialogue[:6000]}"""
    try:
        resp = llm_bridge.chat(call_kwargs, [
            {"role": "system", "content": "你是资深业务分析师，用简洁专业的中文撰写需求文档。"},
            {"role": "user", "content": prompt}], tools=[])
        content = (resp.get("content") or "").strip()
        return content or _NARRATIVE_FALLBACK
    except llm_bridge.LLMError as e:
        logger.warning("需求文档叙述生成失败，使用占位: %s", e)
        return "## 1. 背景与目标\n\n" + _NARRATIVE_FALLBACK + "\n\n## 2. 业务概述\n\n" + _NARRATIVE_FALLBACK


_Q_STATUS = {"open": "⏳ 待澄清", "resolved": "✅ 已定量", "dismissed": "➖ 已搁置"}
_Q_KIND = {"blocking": "B·堵门", "advisory": "A·建议"}


def _q_conclusion(q: dict) -> str:
    if q.get("resolution"):
        return str(q["resolution"])
    if q.get("options"):
        return "候选: " + " / ".join(str(o) for o in q["options"])
    return str(q.get("suggestion") or "")


def _render_ledger(canvas: dict) -> str:
    """澄清账本 → markdown 表格：全部问题（含已销账）留档，可追溯定量过程。"""
    items = Q.get_questions(canvas)
    if not items:
        return "（账本为空 —— 本次探索未登记澄清问题）\n"
    rows = [[q.get("question", ""), _Q_KIND.get(q.get("kind") or "blocking", q.get("kind")),
             q.get("target", ""), _Q_STATUS.get(q.get("status") or "open", q.get("status")),
             _q_conclusion(q)]
            for q in items]
    return _table(["问题", "类别", "关联元素", "状态", "定量结论"], rows)


def _render_readiness(rd: dict) -> str:
    """质量门报告 → markdown：与草稿生成闸门同一口径。"""
    head = (f"**{'✅ 已就绪' if rd['ready'] else '⛔ 未就绪'}** — "
            f"{rd['gatesPassed']}/{rd['gatesTotal']} 门通过，"
            f"堵门项 {rd['blockingCount']}，建议项 {rd['advisoryCount']}。"
            f"当前阶段：{rd['stage']}\n")
    rows = []
    for g in rd["gates"]:
        detail = "；".join(g["blockingItems"][:4]) or "—"
        if len(g["blockingItems"]) > 4:
            detail += f"（等 {len(g['blockingItems'])} 项）"
        rows.append([("✅" if g["passed"] else "⛔") + " " + g["label"],
                     len(g["blockingItems"]), len(g["advisoryItems"]), detail])
    table = _table(["质量门", "堵门项", "建议项", "未决明细"], rows)
    advisory = [f"- {item}" for g in rd["gates"] for item in g["advisoryItems"]][:12]
    adv_md = ("\n**建议补齐（不拦路）**\n\n" + "\n".join(advisory) + "\n") if advisory else ""
    return head + "\n" + table + adv_md


def generate_document(db: Session, session: ExplorationSession,
                      call_kwargs: Optional[dict]) -> ExplorationDocument:
    canvas = C._ensure_canvas(session.canvas)
    source_fingerprint = canvas_fingerprint(canvas)
    snapshot = snapshot_with_source(canvas, session.canvas_version)
    rd = R.evaluate(canvas)
    version = 1 + (db.query(ExplorationDocument)
                   .filter(ExplorationDocument.session_id == session.id).count())
    title = f"{session.title} · 需求文档 v{version}"

    body = f"""# {title}

> **来源画布**：版本 {int(session.canvas_version or 0)} · SHA-256 `{source_fingerprint}`

{_narrative(db, session, call_kwargs)}

## 3. 主体模型

{_render_actors(canvas["actors"])}

## 4. 对象模型

{_render_objects(canvas["objects"])}

## 5. 行为模型

{_render_behaviors(canvas["behaviors"])}

## 6. 事件模型

{_render_events(canvas["events"])}

## 7. 规则模型

{_render_rules(canvas["rules"])}

## 8. 流程模型

{_render_processes(canvas["processes"])}

## 9. 场景模型

{_render_scenarios(canvas["scenarios"])}

## 10. 澄清账本

{_render_ledger(canvas)}

## 11. 质量门检查

{_render_readiness(rd)}
"""
    doc = ExplorationDocument(session_id=session.id, title=title,
                              content_md=body, canvas_snapshot=snapshot, version=version)
    db.add(doc)
    db.commit()
    db.refresh(doc)
    return doc
