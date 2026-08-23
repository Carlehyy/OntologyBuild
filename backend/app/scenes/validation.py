"""场景定义 DSL 校验器（纯函数，无框架依赖）。

词汇基线来自三维白模技能包（threejs-white-twin）的三件套 Schema：
manifest(meta) / scene(objects+relations) / bindings(dataBindings)，
在此之上做平台化收敛：

  - meta.id 全链路锚点，kebab-case；
  - 对象类型枚举五种（office/tower/warehouse/podium/plant），
    extras 枚举两种（parking/solar）；动效不开放自由字段——
    由引擎装饰系统按开关消费，保证 AI/人工产物可控；
  - 关系引用对象 id，接受旧写法 flows:[[a,b]] 并归一为 relations；
  - 数据绑定禁函数，规则仅支持比较表达式 + else 兜底，status 三态。

校验结果统一为 issue 列表 [{"path": "...", "message": "..."}]，
空列表即合法。归一化（normalize_definition）在保存前执行，
保证库内定义形态唯一。
"""
from __future__ import annotations

import re

OBJECT_TYPES = ("office", "tower", "warehouse", "podium", "plant")
OBJECT_EXTRAS = ("parking", "solar")
RELATION_KINDS = ("flow", "dependency", "hierarchy")
BINDING_STATUSES = ("normal", "warning", "alarm")
SOURCE_TYPES = ("client", "polling", "static", "websocket")

KEBAB_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
WHEN_RE = re.compile(r"^(>=|<=|==|!=|>|<)\s*(-?\d+(?:\.\d+)?)$")
BETWEEN_RE = re.compile(r"^between\s+(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)$")

MAX_OBJECTS = 200
MAX_BINDINGS = 200


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _issue(path: str, message: str) -> dict:
    return {"path": path, "message": message}


def validate_definition(definition) -> list[dict]:
    """校验平台场景定义，返回 issue 列表（空列表 = 合法）。"""
    issues: list[dict] = []
    if not isinstance(definition, dict):
        return [_issue("", "场景定义必须是 JSON 对象")]

    # —— meta ——
    meta = definition.get("meta")
    if not isinstance(meta, dict):
        issues.append(_issue("meta", "缺少 meta（场景身份信息）"))
    else:
        meta_id = meta.get("id")
        if not isinstance(meta_id, str) or not KEBAB_RE.match(meta_id or ""):
            issues.append(_issue("meta.id", "必须是非空 kebab-case 字符串"))
        if not isinstance(meta.get("name"), str) or not meta.get("name").strip():
            issues.append(_issue("meta.name", "必须是非空字符串"))
        if not isinstance(meta.get("version"), str) or not meta.get("version").strip():
            issues.append(_issue("meta.version", "必须是非空字符串"))

    # —— stage（可选）——
    stage = definition.get("stage")
    if stage is not None:
        if not isinstance(stage, dict):
            issues.append(_issue("stage", "必须是 JSON 对象"))
        else:
            camera = stage.get("camera")
            if camera is not None:
                if not isinstance(camera, dict):
                    issues.append(_issue("stage.camera", "必须是 JSON 对象"))
                else:
                    for key in ("pos", "target"):
                        vec = camera.get(key)
                        if not (
                            isinstance(vec, list) and len(vec) == 3
                            and all(_is_number(v) for v in vec)
                        ):
                            issues.append(
                                _issue(f"stage.camera.{key}", "必须是长度为 3 的数字数组"))
                    fov = camera.get("fov")
                    if fov is not None and (
                        not _is_number(fov) or not 10 <= fov <= 100
                    ):
                        issues.append(_issue("stage.camera.fov", "必须是 10~100 的数字"))
            background = stage.get("background")
            if background is not None and (
                not isinstance(background, str)
                or not re.match(r"^#[0-9a-fA-F]{6}$", background)
            ):
                issues.append(_issue("stage.background", "必须是 #RRGGBB 颜色"))

    # —— objects ——
    objects = definition.get("objects")
    if not isinstance(objects, list) or not objects:
        issues.append(_issue("objects", "必须是非空数组"))
        objects = []
    elif len(objects) > MAX_OBJECTS:
        issues.append(_issue("objects", f"对象数量不能超过 {MAX_OBJECTS}"))
    seen_ids: set[str] = set()
    for index, obj in enumerate(objects):
        base = f"objects[{index}]"
        if not isinstance(obj, dict):
            issues.append(_issue(base, "必须是 JSON 对象"))
            continue
        obj_id = obj.get("id")
        if not isinstance(obj_id, str) or not KEBAB_RE.match(obj_id or ""):
            issues.append(_issue(f"{base}.id", "必须是非空 kebab-case 字符串"))
        elif obj_id in seen_ids:
            issues.append(_issue(f"{base}.id", f"对象 id 重复：{obj_id}"))
        else:
            seen_ids.add(obj_id)
        if not isinstance(obj.get("label"), str) or not obj.get("label").strip():
            issues.append(_issue(f"{base}.label", "必须是非空字符串"))
        if obj.get("type") not in OBJECT_TYPES:
            issues.append(
                _issue(f"{base}.type",
                       f"必须是 {'/'.join(OBJECT_TYPES)} 之一"))
        layout = obj.get("layout")
        if not isinstance(layout, dict):
            issues.append(_issue(f"{base}.layout", "缺少 layout 布局信息"))
        else:
            for key in ("x", "z"):
                if not _is_number(layout.get(key)):
                    issues.append(_issue(f"{base}.layout.{key}", "必须是数字"))
            for key in ("w", "d", "h"):
                value = layout.get(key)
                if not _is_number(value) or value <= 0:
                    issues.append(_issue(f"{base}.layout.{key}", "必须是正数"))
        extras = obj.get("extras")
        if extras is not None:
            if not isinstance(extras, list):
                issues.append(_issue(f"{base}.extras", "必须是数组"))
            else:
                for extra in extras:
                    if extra not in OBJECT_EXTRAS:
                        issues.append(
                            _issue(f"{base}.extras",
                                   f"未知装饰项 {extra}，可选：{'/'.join(OBJECT_EXTRAS)}"))

    # —— relations（兼容旧 flows 写法）——
    relations = definition.get("relations")
    flows = definition.get("flows")
    if relations is None and flows is not None:
        relations = _flows_to_relations(flows)
    if relations is not None:
        if not isinstance(relations, list):
            issues.append(_issue("relations", "必须是数组"))
        else:
            for index, rel in enumerate(relations):
                base = f"relations[{index}]"
                if not isinstance(rel, dict):
                    issues.append(_issue(base, "必须是 JSON 对象"))
                    continue
                for endpoint_key in ("from", "to"):
                    endpoint = rel.get(endpoint_key)
                    if endpoint not in seen_ids:
                        issues.append(_issue(
                            f"{base}.{endpoint_key}",
                            f"引用了不存在的对象 id：{endpoint}"))
                kind = rel.get("kind", "flow")
                if kind not in RELATION_KINDS:
                    issues.append(_issue(
                        f"{base}.kind",
                        f"必须是 {'/'.join(RELATION_KINDS)} 之一"))

    # —— dataBindings ——
    bindings = definition.get("dataBindings")
    if bindings is not None:
        if not isinstance(bindings, list):
            issues.append(_issue("dataBindings", "必须是数组"))
        elif len(bindings) > MAX_BINDINGS:
            issues.append(
                _issue("dataBindings", f"绑定数量不能超过 {MAX_BINDINGS}"))
        else:
            for index, binding in enumerate(bindings):
                base = f"dataBindings[{index}]"
                if not isinstance(binding, dict):
                    issues.append(_issue(base, "必须是 JSON 对象"))
                    continue
                if binding.get("target") not in seen_ids:
                    issues.append(_issue(
                        f"{base}.target",
                        f"引用了不存在的对象 id：{binding.get('target')}"))
                rules = binding.get("rules")
                if not isinstance(rules, list) or not rules:
                    issues.append(_issue(f"{base}.rules", "必须是非空规则数组"))
                    continue
                for rule_index, rule in enumerate(rules):
                    rule_base = f"{base}.rules[{rule_index}]"
                    if not isinstance(rule, dict):
                        issues.append(_issue(rule_base, "必须是 JSON 对象"))
                        continue
                    when = rule.get("when")
                    if not isinstance(when, str) or not (
                        when == "else"
                        or WHEN_RE.match(when.strip())
                        or BETWEEN_RE.match(when.strip())
                    ):
                        issues.append(_issue(
                            f"{rule_base}.when",
                            '必须是 else 或形如 "> 95" / "between 60 85" 的表达式'))
                    if rule.get("status") not in BINDING_STATUSES:
                        issues.append(_issue(
                            f"{rule_base}.status",
                            f"必须是 {'/'.join(BINDING_STATUSES)} 之一"))
                if rules and isinstance(rules[-1], dict) and rules[-1].get("when") != "else":
                    issues.append(_issue(
                        f"{base}.rules", "最后一条规则必须是 else 兜底"))

    # —— sources（可选）——
    sources = definition.get("sources")
    if sources is not None:
        if not isinstance(sources, dict):
            issues.append(_issue("sources", "必须是 JSON 对象"))
        else:
            for source_name, source in sources.items():
                base = f"sources.{source_name}"
                if not isinstance(source, dict):
                    issues.append(_issue(base, "必须是 JSON 对象"))
                    continue
                if source.get("type") not in SOURCE_TYPES:
                    issues.append(_issue(
                        f"{base}.type",
                        f"必须是 {'/'.join(SOURCE_TYPES)} 之一"))
                if source.get("type") == "polling":
                    interval = source.get("interval")
                    if not _is_number(interval) or interval < 500:
                        issues.append(_issue(
                            f"{base}.interval", "轮询间隔不能低于 500ms"))
                    if not isinstance(source.get("url"), str) or not source.get("url"):
                        issues.append(_issue(f"{base}.url", "轮询源必须提供 url"))

    return issues


def _flows_to_relations(flows) -> list[dict]:
    normalized: list[dict] = []
    if isinstance(flows, list):
        for flow in flows:
            if (
                isinstance(flow, (list, tuple)) and len(flow) == 2
                and all(isinstance(v, str) for v in flow)
            ):
                normalized.append({"from": flow[0], "to": flow[1], "kind": "flow"})
    return normalized


def normalize_definition(definition: dict) -> dict:
    """保存前的形态归一：flows → relations(kind=flow)。其余键原样保留。"""
    normalized = dict(definition)
    if "relations" not in normalized and "flows" in normalized:
        normalized["relations"] = _flows_to_relations(normalized.pop("flows"))
    else:
        normalized.pop("flows", None)
    return normalized
