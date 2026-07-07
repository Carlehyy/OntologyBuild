"""需求文档生成 — 骨架确定性渲染，LLM 只写叙述

稳定性原则：六类模型的清单章节由画布**确定性**渲染成 markdown 表格
（文档永远忠实于画布）；LLM 仅补「背景与目标 / 业务概述」两节叙述，
调用失败时降级为占位文本，文档生成永不因 LLM 失败而失败。
"""
from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy.orm import Session

from app.ontologies.agent_runtime import llm_bridge
from app.exploration import canvas as C
from app.exploration.models import ExplorationDocument, ExplorationMessage, ExplorationSession

logger = logging.getLogger(__name__)

_NARRATIVE_FALLBACK = "> （未配置 LLM 或叙述生成失败，此节留待补充。清单章节由业务画布确定性生成，不受影响。）"


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


def _render_actors(items: list[dict]) -> str:
    return _table(["主体", "类型", "说明", "职责"],
                  [[_label(a), a.get("kind", ""), a.get("description", ""),
                    "；".join(a.get("responsibilities") or [])] for a in items])


def _render_objects(items: list[dict]) -> str:
    parts = []
    for o in items:
        parts.append(f"### {_label(o)}\n")
        if o.get("description"):
            parts.append(o["description"] + "\n")
        parts.append("**属性**（业务主键：" + (o.get("key_attribute") or "未指定") + "）\n")
        parts.append(_table(
            ["属性", "显示名", "类型提示", "必填", "说明"],
            [[a.get("name"), a.get("display_name", ""), a.get("type_hint", ""),
              "是" if a.get("required") else "",
              (("枚举: " + "/".join(a["enum"]) + " ") if a.get("enum") else "") + (a.get("notes") or "")]
             for a in (o.get("attributes") or [])]))
        rels = o.get("relations") or []
        if rels:
            parts.append("**关系**\n")
            parts.append(_table(["关系", "目标对象", "基数", "说明"],
                                [[r.get("display_name") or r.get("name", ""), r.get("target"),
                                  r.get("cardinality", ""), r.get("description", "")] for r in rels]))
    return "\n".join(parts) if parts else "（空）\n"


def _render_behaviors(items: list[dict]) -> str:
    return _table(
        ["行为", "执行主体", "作用对象", "触发", "输入", "结果", "约束", "需审批"],
        [[_label(b), b.get("actor", ""), b.get("object", ""), b.get("trigger", ""),
          "，".join(i.get("name", "") for i in (b.get("inputs") or [])),
          b.get("outcome", ""), "；".join(b.get("constraints") or []),
          "是" if b.get("needs_approval") else ""] for b in items])


def _render_events(items: list[dict]) -> str:
    return _table(["事件", "来源", "载荷", "后果", "说明"],
                  [[_label(e), e.get("source", ""), "，".join(e.get("payload") or []),
                    "；".join(e.get("consequences") or []), e.get("description", "")] for e in items])


def _render_rules(items: list[dict]) -> str:
    return _table(["规则", "类别", "作用于", "表述", "违反提示"],
                  [[_label(r), r.get("kind", ""), r.get("applies_to", ""),
                    r.get("statement") or r.get("description", ""),
                    r.get("error_message", "")] for r in items])


def _render_scenarios(items: list[dict]) -> str:
    parts = []
    for i, s in enumerate(items, 1):
        parts.append(f"### 场景 {i}：{_label(s)}\n")
        if s.get("goal"):
            parts.append(f"**目标**：{s['goal']}\n")
        if s.get("actors"):
            parts.append("**参与主体**：" + "、".join(s["actors"]) + "\n")
        steps = s.get("steps") or []
        if steps:
            parts.append("\n".join(f"{j}. {st}" for j, st in enumerate(steps, 1)) + "\n")
        if s.get("expected_outcome"):
            parts.append(f"**预期结果**：{s['expected_outcome']}\n")
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


def generate_document(db: Session, session: ExplorationSession,
                      call_kwargs: Optional[dict]) -> ExplorationDocument:
    canvas = C._ensure_canvas(session.canvas)
    comp = C.completeness(canvas)
    version = 1 + (db.query(ExplorationDocument)
                   .filter(ExplorationDocument.session_id == session.id).count())
    title = f"{session.title} · 需求文档 v{version}"

    gaps_md = "\n".join(f"- {g}" for g in comp["gaps"]) or "- 无"
    body = f"""# {title}

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

## 8. 场景模型

{_render_scenarios(canvas["scenarios"])}

## 9. 待澄清问题

{gaps_md}
"""
    doc = ExplorationDocument(session_id=session.id, title=title,
                              content_md=body, canvas_snapshot=canvas, version=version)
    db.add(doc)
    db.commit()
    db.refresh(doc)
    return doc
