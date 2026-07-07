"""业务画布 — 六类业务模型元素的 schema、操作与完整度评估

画布是「需求 → 本体」转化稳定性的第一道防线：探索 agent 通过工具调用
把对话中已确认的信息沉淀为结构化元素（而非自由文本），转化时以画布为源。

六类模型（kind，单数）与画布键（复数）：
  object   → objects     业务对象（→ ObjectType + 属性 + 关系→LinkType）
  actor    → actors      业务主体（参与方，为动作提供归属/审批语境）
  behavior → behaviors   业务行为（→ ActionType）
  event    → events      业务事件（进文档 + 动作描述）
  rule     → rules       业务规则（→ validation 规则 / requiresApproval）
  scenario → scenarios   业务场景（不进本体，作为草稿的可表达性验收器）
"""
from __future__ import annotations

import re
import uuid
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError


def _to_camel(s: str) -> str:
    parts = s.split("_")
    return parts[0] + "".join(p.capitalize() for p in parts[1:])


class _El(BaseModel):
    """元素基类：宽容解析（忽略未知字段，camel/snake 双收），把校验错误
    留给真正结构性的问题，便于 LLM 按错误信息修正后重试。"""
    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True, extra="ignore")

    id: Optional[str] = None
    name: str
    display_name: Optional[str] = None
    description: Optional[str] = None


class AttributeSpec(_El):
    name: str
    type_hint: Optional[str] = None      # 自然语言类型提示，转化时映射到 PropertyType
    required: bool = False
    enum: Optional[list[str]] = None
    notes: Optional[str] = None


class RelationSpec(BaseModel):
    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True, extra="ignore")
    target: str                          # 目标对象名
    name: Optional[str] = None           # 关系标识（英文）
    display_name: Optional[str] = None   # 关系显示名（如「归属于」）
    cardinality: Optional[str] = None    # one-to-one / one-to-many / many-to-one / many-to-many
    description: Optional[str] = None


class BusinessObject(_El):
    attributes: list[AttributeSpec] = Field(default_factory=list)
    key_attribute: Optional[str] = None  # 业务主键属性名
    relations: list[RelationSpec] = Field(default_factory=list)


class Actor(_El):
    kind: str = "role"                   # person | org | system | role
    responsibilities: list[str] = Field(default_factory=list)


class Behavior(_El):
    actor: Optional[str] = None          # 执行主体名
    object: Optional[str] = None         # 作用对象名
    trigger: Optional[str] = None
    inputs: list[AttributeSpec] = Field(default_factory=list)
    outcome: Optional[str] = None
    constraints: list[str] = Field(default_factory=list)
    needs_approval: bool = False


class Event(_El):
    source: Optional[str] = None         # 触发来源：某行为名 / external / time
    payload: list[str] = Field(default_factory=list)
    consequences: list[str] = Field(default_factory=list)


class Rule(_El):
    kind: str = "constraint"             # constraint | validation | derivation | approval | alert
    applies_to: Optional[str] = None     # 作用对象/行为名
    statement: Optional[str] = None
    error_message: Optional[str] = None


class Scenario(_El):
    goal: Optional[str] = None
    actors: list[str] = Field(default_factory=list)
    steps: list[str] = Field(default_factory=list)
    objects: list[str] = Field(default_factory=list)     # 场景涉及的对象名（覆盖检查用）
    behaviors: list[str] = Field(default_factory=list)   # 场景涉及的行为名（覆盖检查用）
    expected_outcome: Optional[str] = None


KIND_MODELS: dict[str, type[_El]] = {
    "object": BusinessObject, "actor": Actor, "behavior": Behavior,
    "event": Event, "rule": Rule, "scenario": Scenario,
}
KIND_KEYS = {"object": "objects", "actor": "actors", "behavior": "behaviors",
             "event": "events", "rule": "rules", "scenario": "scenarios"}
KIND_LABELS = {"object": "对象", "actor": "主体", "behavior": "行为",
               "event": "事件", "rule": "规则", "scenario": "场景"}


def norm_name(name: str) -> str:
    """名称归一化（匹配/去重键）：小写、去空白与连接符。"""
    return re.sub(r"[\s_\-]+", "", (name or "").strip().lower())


def empty_canvas() -> dict:
    return {k: [] for k in KIND_KEYS.values()}


def _ensure_canvas(canvas: Any) -> dict:
    out = dict(canvas) if isinstance(canvas, dict) else {}
    for k in KIND_KEYS.values():
        items = out.get(k)
        out[k] = list(items) if isinstance(items, list) else []
    return out


def upsert_elements(canvas: Any, kind: str, elements: list[dict]) -> tuple[dict, list[str], list[str]]:
    """按 id（其次归一化 name）upsert；返回 (新画布, 生效元素 id 列表, 错误列表)。

    始终返回全新 dict —— SQLAlchemy JSON 列必须整体重新赋值才会写库。
    """
    model = KIND_MODELS.get(kind)
    if model is None:
        return _ensure_canvas(canvas), [], [f"未知模型类别: {kind}（可选: {', '.join(KIND_MODELS)}）"]

    out = _ensure_canvas(canvas)
    key = KIND_KEYS[kind]
    items: list[dict] = [dict(x) for x in out[key]]
    by_id = {x.get("id"): i for i, x in enumerate(items) if x.get("id")}
    by_name = {norm_name(x.get("name", "")): i for i, x in enumerate(items) if x.get("name")}

    applied: list[str] = []
    errors: list[str] = []
    for raw in elements or []:
        if not isinstance(raw, dict):
            errors.append(f"元素必须是对象，收到: {type(raw).__name__}")
            continue
        try:
            el = model.model_validate(raw)
        except ValidationError as e:
            bad = "; ".join(f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}"
                            for err in e.errors()[:3])
            errors.append(f"元素「{raw.get('name', '?')}」不合法: {bad}")
            continue
        data = el.model_dump(exclude_none=True)
        idx = None
        if el.id and el.id in by_id:
            idx = by_id[el.id]
        elif norm_name(el.name) in by_name:
            idx = by_name[norm_name(el.name)]
        if idx is not None:
            data["id"] = items[idx].get("id") or f"el-{uuid.uuid4().hex[:8]}"
            items[idx] = data
        else:
            data["id"] = el.id or f"el-{uuid.uuid4().hex[:8]}"
            by_id[data["id"]] = len(items)
            by_name[norm_name(el.name)] = len(items)
            items.append(data)
        applied.append(data["id"])

    out[key] = items
    return out, applied, errors


def remove_elements(canvas: Any, kind: str, ids: list[str]) -> tuple[dict, int, list[str]]:
    """按 id 或名称删除；返回 (新画布, 删除数, 未命中列表)。"""
    if kind not in KIND_MODELS:
        return _ensure_canvas(canvas), 0, [f"未知模型类别: {kind}"]
    out = _ensure_canvas(canvas)
    key = KIND_KEYS[kind]
    targets = {str(i) for i in (ids or [])} | {norm_name(str(i)) for i in (ids or [])}
    targets.discard("")
    kept: list[dict] = []
    hit_keys: set[str] = set()
    for x in out[key]:
        hit = ({x.get("id"), norm_name(x.get("name", ""))} - {None, ""}) & targets
        if hit:
            hit_keys |= hit
        else:
            kept.append(x)
    removed = len(out[key]) - len(kept)
    missing = [str(i) for i in (ids or [])
               if str(i) not in hit_keys and norm_name(str(i)) not in hit_keys]
    out[key] = kept
    return out, removed, missing


def completeness(canvas: Any) -> dict:
    """完整度信号：各类计数 + 缺口清单（驱动前端提示与 agent 追问方向）。"""
    c = _ensure_canvas(canvas)
    counts = {k: len(v) for k, v in c.items() if k in KIND_KEYS.values()}
    gaps: list[str] = []

    obj_names = {norm_name(o.get("name", "")) for o in c["objects"]}
    beh_names = {norm_name(b.get("name", "")) for b in c["behaviors"]}

    for o in c["objects"]:
        if not o.get("attributes"):
            gaps.append(f"对象「{o.get('name')}」还没有属性")
        elif not o.get("key_attribute"):
            gaps.append(f"对象「{o.get('name')}」未指定业务主键属性")
        for r in o.get("relations") or []:
            if norm_name(r.get("target", "")) not in obj_names:
                gaps.append(f"对象「{o.get('name')}」的关系指向未定义的对象「{r.get('target')}」")
    for b in c["behaviors"]:
        if not b.get("actor"):
            gaps.append(f"行为「{b.get('name')}」缺少执行主体")
        if not b.get("object"):
            gaps.append(f"行为「{b.get('name')}」缺少作用对象")
        elif norm_name(b.get("object", "")) not in obj_names:
            gaps.append(f"行为「{b.get('name')}」作用的对象「{b.get('object')}」尚未在对象模型中定义")
    for r in c["rules"]:
        tgt = norm_name(r.get("applies_to", "") or "")
        if tgt and tgt not in obj_names | beh_names:
            gaps.append(f"规则「{r.get('name')}」作用的「{r.get('applies_to')}」未在对象/行为模型中定义")
    if counts["objects"] and not counts["scenarios"]:
        gaps.append("还没有场景模型 —— 场景用于验收本体草稿能否表达完整业务流程")
    if counts["objects"] and not counts["actors"]:
        gaps.append("还没有主体模型 —— 谁在使用这些对象、执行这些行为？")

    return {"counts": counts, "gaps": gaps[:20]}


def canvas_summary(canvas: Any, max_items: int = 30) -> str:
    """画布的紧凑文本摘要，注入探索 agent 系统提示。"""
    c = _ensure_canvas(canvas)
    lines: list[str] = []
    for kind, key in KIND_KEYS.items():
        items = c[key]
        if not items:
            lines.append(f"- {KIND_LABELS[kind]}模型: (空)")
            continue
        parts = []
        for x in items[:max_items]:
            name = x.get("name", "?")
            extra = ""
            if kind == "object":
                attrs = [a.get("name", "") for a in (x.get("attributes") or [])]
                rels = [f"→{r.get('target')}" for r in (x.get("relations") or [])]
                extra = f"({', '.join(attrs[:12])}{'…' if len(attrs) > 12 else ''}" \
                        + (f"; 关系: {', '.join(rels)}" if rels else "") + ")"
            elif kind == "behavior":
                extra = f"({x.get('actor') or '?'} 对 {x.get('object') or '?'})"
            elif kind == "rule":
                extra = f"[{x.get('kind', '')}→{x.get('applies_to') or '?'}]"
            elif kind == "actor":
                extra = f"[{x.get('kind', '')}]"
            parts.append(f"{name}{extra} (id={x.get('id')})")
        suffix = f" …共{len(items)}项" if len(items) > max_items else ""
        lines.append(f"- {KIND_LABELS[kind]}模型({len(items)}): " + "; ".join(parts) + suffix)
    return "\n".join(lines)
