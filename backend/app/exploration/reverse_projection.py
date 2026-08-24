"""结构快照 → 业务画布反向投影（从本体版本引导探索会话）

正向管线（converter._deterministic_draft）把七类画布确定性映射为本体五类
集合；本模块提供反方向投影，把版本结构快照（snapshot_formal）还原为画布，
作为新探索会话的起点。元素命名原样沿用结构标识（converter/canvas 的
norm_name/_slug 归一化对其幂等），保证投影画布再次正向映射后，五类集合的
归一化名集合与原结构一致（round-trip 名集合恒等）。

结构快照丢失的业务语义（行为执行主体/触发、事件载荷等）无法恢复，一律留空
待对话补齐；description 为空的元素统一标注「（来自本体结构，待补充业务描述）」。
端点/绑定无法解析或无法通过画布 schema 校验的病态元素按 best-effort 跳过。
"""
from __future__ import annotations

from typing import Any, Optional

from app.exploration import canvas as C
from app.exploration.converter import CARDINALITIES
from app.ontologies.versions.snapshot_contract import complete_snapshot

_PLACEHOLDER = "（来自本体结构，待补充业务描述）"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _description(value: Any) -> str:
    return _text(value) or _PLACEHOLDER


def _enum_of(prop: dict) -> Optional[list[str]]:
    """属性枚举回投影；不满足 AttributeSpec 校验（≥2 个且归一化不重复）时丢弃。"""
    validation = prop.get("validation")
    raw = validation.get("enum") if isinstance(validation, dict) else None
    if not isinstance(raw, list):
        return None
    cleaned = [str(v).strip() for v in raw if str(v).strip()]
    if len(cleaned) < 2 or len({C.norm_name(v) for v in cleaned}) != len(cleaned):
        return None
    return cleaned


def _attribute(prop: dict) -> Optional[dict]:
    """结构属性/动作参数 → 画布属性；type 原样作 type_hint（map_type_hint 对其幂等）。"""
    name = _text(prop.get("name"))
    if not name:
        return None
    attr: dict[str, Any] = {"name": name, "required": bool(prop.get("required"))}
    display_name = _text(prop.get("displayName"))
    if display_name and display_name != name:
        attr["display_name"] = display_name
    type_hint = _text(prop.get("type"))
    if type_hint:
        attr["type_hint"] = type_hint
    enum = _enum_of(prop)
    if enum:
        attr["enum"] = enum
    notes = _text(prop.get("description"))
    if notes:
        attr["notes"] = notes
    return attr


def _key_attribute(object_type: dict) -> Optional[str]:
    """从 primaryKey 找回属性名；兼容属性 id（现行）与属性名（历史）两种引用。"""
    pk = _text(object_type.get("primaryKey"))
    if not pk:
        return None
    props = [p for p in (object_type.get("properties") or []) if isinstance(p, dict)]
    for prop in props:
        if _text(prop.get("id")) == pk and _text(prop.get("name")):
            return _text(prop.get("name"))
    for prop in props:
        if C.norm_name(_text(prop.get("name"))) == C.norm_name(pk):
            return _text(prop.get("name")) or None
    return None


def project_snapshot_to_canvas(snapshot_formal: dict | None) -> dict:
    """把结构快照反向投影为七类业务画布（纯函数，不读库）。"""
    formal = complete_snapshot(snapshot_formal)
    object_types = [ot for ot in formal["objectTypes"]
                    if isinstance(ot, dict) and _text(ot.get("name"))]
    name_by_id = {_text(ot.get("id")): _text(ot.get("name")) for ot in object_types}
    label_by_id = {
        _text(ot.get("id")): (_text(ot.get("displayName")) or _text(ot.get("name")))
        for ot in object_types
    }

    # linkTypes → 挂在源对象元素上的 relations；端点无法解析的链接在正向映射中
    # 也会被解析失败拦下、无法还原，反向投影同样跳过。
    relations_by_source: dict[str, list[dict]] = {}
    for link in formal["linkTypes"]:
        if not isinstance(link, dict):
            continue
        name = _text(link.get("name"))
        source_id = _text(link.get("sourceObjectTypeId"))
        target_id = _text(link.get("targetObjectTypeId"))
        if not name or source_id not in name_by_id or target_id not in name_by_id:
            continue
        relation: dict[str, Any] = {
            "name": name,
            "display_name": _text(link.get("displayName")) or name,
            "target": label_by_id[target_id],
        }
        cardinality = _text(link.get("cardinality")).lower()
        if cardinality in CARDINALITIES:
            relation["cardinality"] = cardinality
        description = _text(link.get("description"))
        if description:
            relation["description"] = description
        relations_by_source.setdefault(source_id, []).append(relation)

    objects: list[dict] = []
    for ot in object_types:
        name = _text(ot.get("name"))
        raw: dict[str, Any] = {
            "name": name,
            "display_name": _text(ot.get("displayName")) or name,
            "description": _description(ot.get("description")),
            "attributes": [a for a in (_attribute(p) for p in (ot.get("properties") or [])
                                       if isinstance(p, dict)) if a],
            "relations": relations_by_source.get(_text(ot.get("id")), []),
        }
        key_attribute = _key_attribute(ot)
        if key_attribute:
            raw["key_attribute"] = key_attribute
        objects.append(raw)

    # actions → behaviors：执行主体/触发在结构快照中没有对应列，留空待对话补齐。
    behaviors: list[dict] = []
    for action in formal["actions"]:
        if not isinstance(action, dict):
            continue
        name = _text(action.get("name"))
        if not name:
            continue
        raw = {
            "name": name,
            "display_name": _text(action.get("displayName")) or name,
            "description": _description(action.get("description")),
            "inputs": [a for a in (_attribute(p) for p in (action.get("parameters") or [])
                                   if isinstance(p, dict)) if a],
            "needs_approval": bool(action.get("requiresApproval")),
        }
        object_name = name_by_id.get(_text(action.get("objectTypeId")))
        if object_name:
            raw["object"] = object_name
        behaviors.append(raw)

    # functions → rules：source.semanticRole 是落库时保留的规则血缘
    #（object_validation ↔ 对象级 constraint/validation，derivation ↔ derivation）。
    # object_validation 但绑定对象已不可解析时回退 derivation —— 对象级
    # constraint 在正向映射中必须解析到实体目标才产出函数，回退才能保住名集合恒等。
    rules: list[dict] = []
    for function in formal["functions"]:
        if not isinstance(function, dict):
            continue
        name = _text(function.get("name"))
        if not name:
            continue
        source = function.get("source") if isinstance(function.get("source"), dict) else {}
        target_name = name_by_id.get(_text(function.get("targetObjectTypeId")))
        kind = "constraint" if (_text(source.get("semanticRole")) == "object_validation"
                                and target_name) else "derivation"
        raw = {
            "name": name,
            "display_name": _text(function.get("displayName")) or name,
            "description": _description(function.get("description")),
            "kind": kind,
        }
        if target_name:
            raw["applies_to"] = target_name
        statement = _text(function.get("description"))
        if statement:
            raw["statement"] = statement
        rules.append(raw)

    # sentinels → events：触发来源只恢复可判定的一档——纯定期扫描还原为 time；
    # 其余（变化驱动/血缘为告警规则/工程自建）统一回退 external：原始行为级来源
    # 不在哨兵记录中（只有绑定对象），待对话补齐。
    events: list[dict] = []
    for sentinel in formal["sentinels"]:
        if not isinstance(sentinel, dict):
            continue
        name = _text(sentinel.get("name"))
        if not name:
            continue
        source = "time" if (bool(sentinel.get("onSchedule"))
                            and not bool(sentinel.get("onChange"))) else "external"
        raw = {
            "name": name,
            "display_name": _text(sentinel.get("displayName")) or name,
            "description": _description(sentinel.get("description")),
            "source": source,
        }
        consequences = _text(sentinel.get("description"))
        if consequences:
            raw["consequences"] = [consequences]
        events.append(raw)

    # 经公开 upsert 入口写入：复用画布 schema 校验与 el-/sub- id 规范；
    # 无法通过校验的病态元素按 best-effort 跳过（见模块 docstring）。
    canvas = C.empty_canvas()
    for kind, raws in (("object", objects), ("behavior", behaviors),
                       ("rule", rules), ("event", events)):
        if raws:
            canvas, _, _errors = C.upsert_elements(canvas, kind, raws)
    return canvas
