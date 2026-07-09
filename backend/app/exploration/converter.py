"""画布 → 本体草稿转化管线（需求→本体稳定性的核心实现）

三步管线，确定性优先：
  1. 确定性映射：对象→ObjectType、关系→LinkType、行为→ActionType、
     规则(constraint|validation)→validation 规则（默认 disabled，待人工形式化）、
     规则(approval)→requiresApproval、规则(derivation)→激活函数草稿（enabled=false）、
     规则(alert)+事件→哨兵草稿（muted 影子 + enabled=false + status=draft，
     event.source=time→定期扫描 / source=行为→变化驱动，绑定行为作用对象）。
     骨架完全由代码生成，LLM 永不生成 id —— 引用一律按名称解析。
  2. LLM 补缺（可选）：仅允许补 描述/显示名/属性类型/基数/主键 这几个白名单
     字段，pydantic 校验失败带错误回炉 ≤2 轮，仍失败则整体丢弃补丁只用第 1 步结果。
  3. lint + 自动修复：主键必在属性中、链接端点必须可解析、名称唯一化、
     枚举白名单外回退默认值；全部修复记入 report.warnings。
     叠加与目标本体的同名冲突标记（保守合并：冲突项应用时跳过）、
     场景可表达性检查（场景引用的对象/行为是否都在草稿+目标本体中）。

草稿永不直写本体 —— apply_draft 只落用户勾选且无冲突的元素，且转出的
函数/哨兵带三重闸门（enabled=false / muted / status=draft），落地即休眠待人工形式化。
落库元素写 source 血缘列（sessionId/documentId/draftId/draftKey/sourceRefs），
元素级可回溯到探索会话与画布卡片。
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.orm import Session

from app.ontologies.agent_runtime import llm_bridge
from app.ontologies.formal_modeling.models import ActionType, LinkType, ObjectType, OntologyFunction
from app.ontologies.sentinels.models import Sentinel
from app.exploration import canvas as C
from app.exploration.canvas import norm_name

logger = logging.getLogger(__name__)

PROPERTY_TYPES = {"string", "number", "boolean", "date", "datetime", "array", "object", "reference"}
CARDINALITIES = {"one-to-one", "one-to-many", "many-to-one", "many-to-many"}

_COLORS = ["#4C6EF5", "#12B886", "#F59F00", "#FA5252", "#7950F2", "#15AABF", "#E64980", "#82C91E"]

_NUM_HINTS = ("数字", "数量", "金额", "价格", "费用", "单价", "总额", "百分比", "重量", "评分",
              "number", "int", "float", "decimal", "amount", "price", "count", "qty")
_DATETIME_HINTS = ("时间", "datetime", "timestamp", "时刻")
_DATE_HINTS = ("日期", "date", "生日", "年月日")
_BOOL_HINTS = ("是否", "布尔", "bool", "标志", "flag")
_ARRAY_HINTS = ("列表", "数组", "多个", "array", "list", "多值")


def map_type_hint(hint: Optional[str]) -> str:
    h = (hint or "").strip().lower()
    if not h:
        return "string"
    if h in PROPERTY_TYPES:
        return h
    if any(k in h for k in _BOOL_HINTS):
        return "boolean"
    if any(k in h for k in _DATETIME_HINTS):
        return "datetime"
    if any(k in h for k in _DATE_HINTS):
        return "date"
    if any(k in h for k in _ARRAY_HINTS):
        return "array"
    if any(k in h for k in _NUM_HINTS):
        return "number"
    return "string"


def _slug(name: str, fallback: str) -> str:
    """标识符化：保留字母/数字/下划线/中文；空则用 fallback。"""
    s = re.sub(r"[^\w一-鿿]+", "_", (name or "").strip()).strip("_")
    return s or fallback


def _pid() -> str:
    return f"prop-{uuid.uuid4().hex[:8]}"


def _prop_from_attr(a: dict) -> dict:
    p: dict = {
        "id": _pid(),
        "name": _slug(a.get("name", ""), "field"),
        "displayName": a.get("display_name") or a.get("name", ""),
        "type": map_type_hint(a.get("type_hint")),
        "required": bool(a.get("required")),
    }
    desc = a.get("notes") or a.get("description")
    if desc:
        p["description"] = desc
    if a.get("enum"):
        p["validation"] = {"enum": [str(v) for v in a["enum"]]}
    return p


def _mk_validation_rule(name: str, statement: str, error_message: str, order: int) -> dict:
    """规则以 disabled 形式挂载：错误提示与表述保真，条件表达式留待人工形式化。"""
    return {"id": f"rule-{uuid.uuid4().hex[:8]}", "type": "validation",
            "name": f"待形式化: {name}", "description": statement,
            "enabled": False, "order": order,
            "config": {"type": "validation", "condition": "",
                       "errorMessage": error_message or statement or name}}


# ---------------------------------------------------------------- 第 1 步：确定性映射


def _deterministic_draft(canvas: dict, warnings: list[str]) -> dict:
    objects: list[dict] = canvas.get("objects") or []
    actors: list[dict] = canvas.get("actors") or []
    behaviors: list[dict] = canvas.get("behaviors") or []
    events: list[dict] = canvas.get("events") or []
    rules: list[dict] = canvas.get("rules") or []

    draft_objects: list[dict] = []
    obj_key_by_name: dict[str, str] = {}

    def add_object_type(name: str, display_name: str, description: str,
                        props: list[dict], key_attr: Optional[str],
                        origin: str, source_ref: Optional[str]) -> None:
        key = f"obj:{norm_name(name)}"
        prop_names = {norm_name(p["name"]) for p in props}
        primary = None
        if key_attr and norm_name(key_attr) in prop_names:
            primary = next(p["name"] for p in props if norm_name(p["name"]) == norm_name(key_attr))
        if primary is None:
            id_prop = {"id": _pid(), "name": "id", "displayName": "ID",
                       "type": "string", "required": True}
            if "id" not in prop_names:
                props = [id_prop] + props
            primary = "id"
            if key_attr:
                warnings.append(f"对象「{name}」的主键属性「{key_attr}」不在属性列表中，已回退为 id")
            else:
                warnings.append(f"对象「{name}」未指定业务主键，已自动补 id 属性作为主键")
        draft_objects.append({
            "key": key, "name": _slug(name, f"Object{len(draft_objects) + 1}"),
            "displayName": display_name or name, "description": description or "",
            "color": _COLORS[len(draft_objects) % len(_COLORS)],
            "primaryKey": primary, "properties": props,
            "origin": origin, "sourceRefs": [r for r in [source_ref] if r],
        })
        obj_key_by_name[norm_name(name)] = key

    for o in objects:
        add_object_type(
            o.get("name", ""), o.get("display_name") or o.get("name", ""),
            o.get("description") or "",
            [_prop_from_attr(a) for a in (o.get("attributes") or [])],
            o.get("key_attribute"), "object", o.get("id"))

    # 主体 → 参与方对象类型（system 类主体不建模为对象；与对象重名时合并不重复建）
    for a in actors:
        if (a.get("kind") or "role") == "system":
            continue
        if norm_name(a.get("name", "")) in obj_key_by_name:
            warnings.append(f"主体「{a.get('name')}」与同名对象合并（不重复生成对象类型）")
            continue
        desc = f"业务主体（{a.get('kind', 'role')}）。" + (a.get("description") or "")
        if a.get("responsibilities"):
            desc += " 职责: " + "；".join(a["responsibilities"])
        add_object_type(
            a.get("name", ""), a.get("display_name") or a.get("name", ""), desc,
            [{"id": _pid(), "name": "name", "displayName": "名称",
              "type": "string", "required": True}],
            "name", "actor", a.get("id"))

    # 对象关系 → 链接类型
    draft_links: list[dict] = []
    seen_link_names: set[str] = set()
    for o in objects:
        src_key = obj_key_by_name.get(norm_name(o.get("name", "")))
        for r in o.get("relations") or []:
            tgt_key = obj_key_by_name.get(norm_name(r.get("target", "")))
            if not src_key or not tgt_key:
                warnings.append(f"关系「{o.get('name')} → {r.get('target')}」的目标对象未定义，已跳过")
                continue
            cardinality = (r.get("cardinality") or "").strip().lower()
            if cardinality not in CARDINALITIES:
                if cardinality:
                    warnings.append(f"关系「{o.get('name')} → {r.get('target')}」基数「{cardinality}」"
                                    f"不合法，已回退 one-to-many")
                cardinality = "one-to-many"
            base = _slug(r.get("name") or "", "") or \
                f"{_slug(o.get('name', ''), 'src')}_{_slug(r.get('target', ''), 'tgt')}"
            name = base
            n = 2
            while norm_name(name) in seen_link_names:
                name = f"{base}_{n}"
                n += 1
            seen_link_names.add(norm_name(name))
            draft_links.append({
                "key": f"link:{norm_name(name)}", "name": name,
                "displayName": r.get("display_name") or name,
                "description": r.get("description") or "",
                "sourceKey": src_key, "targetKey": tgt_key,
                "sourceName": o.get("name", ""), "targetName": r.get("target", ""),
                "cardinality": cardinality,
                "sourceRefs": [x for x in [o.get("id")] if x],
            })

    # 规则索引：按作用目标（归一化名）分桶
    rules_by_target: dict[str, list[dict]] = {}
    for r in rules:
        rules_by_target.setdefault(norm_name(r.get("applies_to") or ""), []).append(r)
    events_by_source = {}
    for e in events:
        events_by_source.setdefault(norm_name(e.get("source") or ""), []).append(e)

    # 行为 → 动作类型
    draft_actions: list[dict] = []
    for b in behaviors:
        bname = b.get("name", "")
        desc_parts = [b.get("description") or ""]
        if b.get("actor"):
            desc_parts.append(f"执行主体: {b['actor']}")
        if b.get("trigger"):
            desc_parts.append(f"触发: {b['trigger']}")
        if b.get("outcome"):
            desc_parts.append(f"结果: {b['outcome']}")
        for e in events_by_source.get(norm_name(bname), []):
            cons = "；".join(e.get("consequences") or [])
            desc_parts.append(f"触发事件「{e.get('display_name') or e.get('name')}」"
                              + (f"（后果: {cons}）" if cons else ""))

        obj_key = obj_key_by_name.get(norm_name(b.get("object") or ""))
        if b.get("object") and not obj_key:
            warnings.append(f"行为「{bname}」作用的对象「{b.get('object')}」未定义，动作未绑定对象类型")

        requires_approval = bool(b.get("needs_approval"))
        action_rules: list[dict] = []
        for i, cst in enumerate(b.get("constraints") or []):
            action_rules.append(_mk_validation_rule(f"约束{i + 1}", cst, cst, len(action_rules)))
        for r in rules_by_target.get(norm_name(bname), []):
            if (r.get("kind") or "constraint") == "approval":
                requires_approval = True
                continue
            if (r.get("kind") or "constraint") in ("constraint", "validation"):
                action_rules.append(_mk_validation_rule(
                    r.get("display_name") or r.get("name", "规则"),
                    r.get("statement") or r.get("description") or "",
                    r.get("error_message") or "", len(action_rules)))
        if action_rules:
            warnings.append(f"动作「{bname}」挂载了 {len(action_rules)} 条待形式化规则"
                            f"（disabled，请在编辑器中补条件表达式后启用）")

        draft_actions.append({
            "key": f"act:{norm_name(bname)}",
            "name": _slug(bname, f"action{len(draft_actions) + 1}"),
            "displayName": b.get("display_name") or bname,
            "description": "。".join(p for p in desc_parts if p),
            "objectTypeKey": obj_key,
            "parameters": [{**_prop_from_attr(i2), "id": f"param-{uuid.uuid4().hex[:8]}"}
                           for i2 in (b.get("inputs") or [])],
            "rules": action_rules,
            "requiresApproval": requires_approval,
            "sourceRefs": [x for x in [b.get("id")] if x],
        })

    # 作用目标 → 对象 key 解析（直接命中对象；或命中行为则归到行为的作用对象）
    behavior_object: dict[str, str] = {
        norm_name(b.get("name", "")): (b.get("object") or "") for b in behaviors}

    def resolve_target_obj(target: str) -> Optional[str]:
        t = norm_name(target or "")
        if t in obj_key_by_name:
            return obj_key_by_name[t]
        if t in behavior_object:
            return obj_key_by_name.get(norm_name(behavior_object[t]))
        return None

    # 派生规则 → 激活函数草稿（enabled=false，条件/函数体留待人工形式化）
    draft_functions: list[dict] = []
    seen_fn_names: set[str] = set()
    for r in rules:
        if (r.get("kind") or "constraint") != "derivation":
            continue
        rname = r.get("name", "")
        base = _slug(rname, f"function{len(draft_functions) + 1}")
        fname, n = base, 2
        while norm_name(fname) in seen_fn_names:
            fname = f"{base}_{n}"
            n += 1
        seen_fn_names.add(norm_name(fname))
        obj_key = resolve_target_obj(r.get("applies_to") or "")
        desc = r.get("statement") or r.get("description") or ""
        if r.get("error_message"):
            desc += f"；违规提示: {r['error_message']}"
        if not obj_key:
            warnings.append(f"派生规则「{rname}」作用的「{r.get('applies_to') or '未指定'}」"
                            f"未解析到对象，函数草稿未绑定对象类型（落地后请在编辑器中补绑定）")
        draft_functions.append({
            "key": f"fn:{norm_name(fname)}", "name": fname,
            "displayName": f"待形式化: {r.get('display_name') or rname}",
            "description": desc,
            "functionType": "object" if obj_key else "query",
            "language": "expression", "returnType": "string", "body": "",
            "enabled": False,
            "targetObjectTypeKey": obj_key,
            "targetObjectTypeName": r.get("applies_to") or "",
            "sourceRefs": [x for x in [r.get("id")] if x],
        })
    if draft_functions:
        warnings.append(f"生成 {len(draft_functions)} 个激活函数草稿"
                        f"（enabled=false，请在编辑器中补函数体后启用）")

    # 告警规则 + 事件 → 哨兵草稿（muted 影子 + enabled=false，条件留待人工形式化）
    draft_sentinels: list[dict] = []
    seen_sen_names: set[str] = set()

    def add_sentinel(name: str, display_name: str, description: str,
                     bind_key: Optional[str], bind_name: str,
                     on_change: bool, on_schedule: bool, interval: int,
                     origin_kind: str, source_ref: Optional[str]) -> None:
        base = _slug(name, f"sentinel{len(draft_sentinels) + 1}")
        sname, n = base, 2
        while norm_name(sname) in seen_sen_names:
            sname = f"{base}_{n}"
            n += 1
        seen_sen_names.add(norm_name(sname))
        draft_sentinels.append({
            "key": f"sen:{norm_name(sname)}", "name": sname,
            "displayName": f"待形式化: {display_name or name}",
            "description": description,
            "bindingObjectKey": bind_key, "bindingObjectName": bind_name,
            "onChange": on_change, "onSchedule": on_schedule,
            "scanIntervalSeconds": interval,
            "muted": True, "enabled": False, "status": "draft",
            "originKind": origin_kind,
            "sourceRefs": [x for x in [source_ref] if x],
        })

    for r in rules:
        if (r.get("kind") or "constraint") != "alert":
            continue
        tgt = r.get("applies_to") or ""
        bind_key = resolve_target_obj(tgt)
        if not bind_key:
            warnings.append(f"告警规则「{r.get('name')}」作用的「{tgt or '未指定'}」"
                            f"未解析到对象，哨兵草稿未绑定监听对象（落地后请在编辑器中补绑定）")
        desc = r.get("statement") or r.get("description") or ""
        if r.get("error_message"):
            desc += f"；告警提示: {r['error_message']}"
        add_sentinel(r.get("name", ""), r.get("display_name") or r.get("name", ""), desc,
                     bind_key, tgt, on_change=True, on_schedule=False, interval=300,
                     origin_kind="rule", source_ref=r.get("id"))

    for e in events:
        src = (e.get("source") or "").strip()
        is_time = norm_name(src) == "time"
        bind_key = None
        bind_name = ""
        if src and not is_time and norm_name(src) != "external":
            bind_name = behavior_object.get(norm_name(src)) or ""
            bind_key = obj_key_by_name.get(norm_name(bind_name)) if bind_name else None
            if not bind_key:
                warnings.append(f"事件「{e.get('name')}」的来源「{src}」未解析到行为的作用对象，"
                                f"哨兵草稿未绑定监听对象（落地后请在编辑器中补绑定）")
        parts = [e.get("description") or ""]
        if e.get("payload"):
            parts.append("载荷: " + "、".join(e["payload"]))
        if e.get("consequences"):
            parts.append("后果: " + "、".join(e["consequences"]))
        parts.append(f"来源: {src or '未指定'}")
        add_sentinel(e.get("name", ""), e.get("display_name") or e.get("name", ""),
                     "。".join(p for p in parts if p),
                     bind_key, bind_name,
                     on_change=not is_time, on_schedule=is_time,
                     interval=3600 if is_time else 300,
                     origin_kind="event", source_ref=e.get("id"))
    if draft_sentinels:
        warnings.append(f"生成 {len(draft_sentinels)} 个哨兵草稿"
                        f"（muted 影子 + 停用，请在编辑器中补条件表达式后发布）")

    # 未挂到任何行为的 constraint/validation/approval 规则 → 报告备注（不再静默丢失）
    mapped_targets = {norm_name(b.get("name", "")) for b in behaviors}
    for r in rules:
        kind = r.get("kind") or "constraint"
        if kind in ("derivation", "alert"):
            continue  # 已分别转化为函数/哨兵草稿
        tgt = norm_name(r.get("applies_to") or "")
        if tgt in mapped_targets:
            continue
        if kind == "approval":
            warnings.append(f"审批规则「{r.get('name')}」作用于「{r.get('applies_to') or '未指定'}」"
                            f"未命中任何行为，requiresApproval 未能挂载——请确认作用目标"
                            f"或落地后在编辑器中手工开启审批")
        else:
            warnings.append(f"规则「{r.get('name')}」作用于「{r.get('applies_to') or '未指定'}」，"
                            f"暂无法映射为动作校验（对象级校验请后续在编辑器中形式化）")

    return {"objectTypes": draft_objects, "linkTypes": draft_links, "actions": draft_actions,
            "functions": draft_functions, "sentinels": draft_sentinels}


# ---------------------------------------------------------------- 第 2 步：LLM 补缺


class _PatchProp(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")
    name: str
    type: Optional[str] = None
    displayName: Optional[str] = None
    description: Optional[str] = None


class _PatchItem(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")
    key: str
    displayName: Optional[str] = None
    description: Optional[str] = None
    cardinality: Optional[str] = None
    primaryKey: Optional[str] = None
    properties: list[_PatchProp] = Field(default_factory=list)


class _Patch(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")
    objectTypes: list[_PatchItem] = Field(default_factory=list)
    linkTypes: list[_PatchItem] = Field(default_factory=list)
    actions: list[_PatchItem] = Field(default_factory=list)
    functions: list[_PatchItem] = Field(default_factory=list)
    sentinels: list[_PatchItem] = Field(default_factory=list)


def _strip_fence(text: str) -> str:
    t = (text or "").strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", t, re.S)
    return m.group(1).strip() if m else t


def _merge_patch(draft: dict, patch: _Patch, warnings: list[str]) -> None:
    """白名单合并：只接受 描述/显示名/属性类型/基数/主键；非法值忽略并记警告。

    函数/哨兵草稿只接受 描述/显示名 润色——functionType/绑定/触发方式等
    结构字段全部确定性生成，LLM 不可改。
    """
    by_key = {coll: {x["key"]: x for x in draft.get(coll) or []}
              for coll in ("objectTypes", "linkTypes", "actions", "functions", "sentinels")}
    for coll in ("objectTypes", "linkTypes", "actions", "functions", "sentinels"):
        for item in getattr(patch, coll):
            target = by_key[coll].get(item.key)
            if not target:
                continue
            if item.description:
                target["description"] = item.description
            if item.displayName:
                target["displayName"] = item.displayName
                # 函数/哨兵草稿的「待形式化」标记不可被润色抹掉
                if coll in ("functions", "sentinels") \
                        and not target["displayName"].startswith("待形式化"):
                    target["displayName"] = f"待形式化: {target['displayName']}"
            if coll == "linkTypes" and item.cardinality:
                if item.cardinality in CARDINALITIES:
                    target["cardinality"] = item.cardinality
                else:
                    warnings.append(f"LLM 补缺给出的基数「{item.cardinality}」不合法，已忽略")
            if coll == "objectTypes":
                props = {norm_name(p["name"]): p for p in target.get("properties", [])}
                for pp in item.properties:
                    tp = props.get(norm_name(pp.name))
                    if not tp:
                        continue  # 不允许 LLM 增删属性，只允许润色既有属性
                    if pp.type:
                        if pp.type in PROPERTY_TYPES:
                            tp["type"] = pp.type
                        else:
                            warnings.append(f"LLM 补缺给出的属性类型「{pp.type}」不合法，已忽略")
                    if pp.displayName:
                        tp["displayName"] = pp.displayName
                    if pp.description:
                        tp["description"] = pp.description
                if item.primaryKey and norm_name(item.primaryKey) in props:
                    target["primaryKey"] = props[norm_name(item.primaryKey)]["name"]


def refine_draft(draft: dict, canvas: dict, call_kwargs: Optional[dict],
                 warnings: list[str]) -> bool:
    """LLM 补缺一轮（校验失败回炉 ≤2 次）。任何失败都只丢弃补丁，不影响草稿。"""
    if not call_kwargs:
        return False
    skeleton = json.dumps(draft, ensure_ascii=False)[:12000]
    base_prompt = f"""下面是从业务画布确定性生成的本体草稿骨架。请只做「补缺」：
- 属性 type 不准确的（全是 string 的地方按语义改成 number/date/datetime/boolean）
- 链接 cardinality 按业务语义修正
- 补充更好的中文 displayName 与一句话 description
- 对象 primaryKey 若有更合适的既有属性可指定

只输出 JSON（不要解释、不要代码块），结构：
{{"objectTypes": [{{"key", "displayName"?, "description"?, "primaryKey"?, "properties": [{{"name", "type"?, "displayName"?, "description"?}}]}}],
 "linkTypes": [{{"key", "displayName"?, "description"?, "cardinality"?}}],
 "actions": [{{"key", "displayName"?, "description"?}}],
 "functions": [{{"key", "displayName"?, "description"?}}],
 "sentinels": [{{"key", "displayName"?, "description"?}}]}}
只引用骨架中已有的 key 和属性 name，不要新增/删除任何元素或属性。

# 业务画布
{C.canvas_summary(canvas)}

# 草稿骨架
{skeleton}"""
    messages = [{"role": "system", "content": "你是本体建模专家，严格按要求输出 JSON。"},
                {"role": "user", "content": base_prompt}]
    for attempt in range(3):
        try:
            resp = llm_bridge.chat(call_kwargs, messages, tools=[])
        except llm_bridge.LLMError as e:
            warnings.append(f"LLM 补缺调用失败，仅使用确定性映射结果: {e}")
            return False
        raw = _strip_fence(resp.get("content") or "")
        try:
            patch = _Patch.model_validate(json.loads(raw))
        except (json.JSONDecodeError, ValidationError) as e:
            if attempt < 2:
                messages.append({"role": "assistant", "content": raw})
                messages.append({"role": "user",
                                 "content": f"输出不合法: {str(e)[:500]}。请重新只输出合法 JSON。"})
                continue
            warnings.append("LLM 补缺输出两次校验失败，已丢弃补丁，仅使用确定性映射结果")
            return False
        _merge_patch(draft, patch, warnings)
        return True
    return False


# ---------------------------------------------------------------- 第 3 步：lint / 冲突 / 场景覆盖


def _lint(draft: dict, warnings: list[str]) -> None:
    for coll, label in (("objectTypes", "对象类型"), ("linkTypes", "链接类型"), ("actions", "动作"),
                        ("functions", "激活函数"), ("sentinels", "哨兵")):
        seen: set[str] = set()
        for item in draft.get(coll) or []:
            base = item["name"]
            n = 2
            while norm_name(item["name"]) in seen:
                item["name"] = f"{base}_{n}"
                n += 1
            if item["name"] != base:
                warnings.append(f"{label}名称「{base}」重复，已改为「{item['name']}」")
            seen.add(norm_name(item["name"]))
    obj_keys = {o["key"] for o in draft["objectTypes"]}
    kept_links = []
    for lk in draft["linkTypes"]:
        if lk["sourceKey"] in obj_keys and lk["targetKey"] in obj_keys:
            kept_links.append(lk)
        else:
            warnings.append(f"链接「{lk['displayName']}」端点缺失，已从草稿剔除")
    draft["linkTypes"] = kept_links
    for ot in draft["objectTypes"]:
        names = {p["name"] for p in ot["properties"]}
        if ot["primaryKey"] not in names:
            ot["primaryKey"] = "id"
            if "id" not in names:
                ot["properties"].insert(0, {"id": _pid(), "name": "id", "displayName": "ID",
                                            "type": "string", "required": True})
            warnings.append(f"对象「{ot['displayName']}」主键失效，已回退 id")


def _mark_conflicts(draft: dict, existing: Optional[dict[str, set[str]]],
                    conflicts: list[str]) -> None:
    ex = existing or {}
    for coll, label in (("objectTypes", "对象类型"), ("linkTypes", "链接类型"), ("actions", "动作"),
                        ("functions", "激活函数"), ("sentinels", "哨兵")):
        for item in draft.get(coll) or []:
            item["conflict"] = norm_name(item["name"]) in ex.get(coll, set())
            if item["conflict"]:
                conflicts.append(f"{label}「{item['displayName']}（{item['name']}）」"
                                 f"与目标本体同名 —— 保守合并将跳过该项")


def _scenario_coverage(canvas: dict, draft: dict,
                       existing: Optional[dict[str, set[str]]]) -> list[dict]:
    ex = existing or {}
    known_objects = {norm_name(o["name"]) for o in draft["objectTypes"]} \
        | {norm_name(o["displayName"]) for o in draft["objectTypes"]} | ex.get("objectTypes", set())
    known_actions = {norm_name(a["name"]) for a in draft["actions"]} \
        | {norm_name(a["displayName"]) for a in draft["actions"]} | ex.get("actions", set())
    out = []
    for s in canvas.get("scenarios") or []:
        missing_o = [x for x in (s.get("objects") or []) if norm_name(x) not in known_objects]
        missing_b = [x for x in (s.get("behaviors") or []) if norm_name(x) not in known_actions]
        if missing_o or missing_b:
            out.append({"scenario": s.get("display_name") or s.get("name"),
                        "missingObjects": missing_o, "missingBehaviors": missing_b})
    return out


# ---------------------------------------------------------------- 对外入口


def existing_name_sets(db: Session, ontology_id: str) -> dict[str, set[str]]:
    return {
        "objectTypes": {norm_name(x.name) for x in db.query(ObjectType.name)
                        .filter(ObjectType.ontology_id == ontology_id)},
        "linkTypes": {norm_name(x.name) for x in db.query(LinkType.name)
                      .filter(LinkType.ontology_id == ontology_id)},
        "actions": {norm_name(x.name) for x in db.query(ActionType.name)
                    .filter(ActionType.ontology_id == ontology_id)},
        "functions": {norm_name(x.name) for x in db.query(OntologyFunction.name)
                      .filter(OntologyFunction.ontology_id == ontology_id)},
        "sentinels": {norm_name(x.name) for x in db.query(Sentinel.name)
                      .filter(Sentinel.ontology_id == ontology_id)},
    }


def build_draft(canvas: dict, existing: Optional[dict[str, set[str]]] = None,
                call_kwargs: Optional[dict] = None) -> tuple[dict, dict]:
    """完整管线：确定性映射 → lint → 冲突标记 → 场景覆盖。
    LLM 补缺已移除 —— 确定性映射依靠 map_type_hint() 推断属性类型，已足够。"""
    warnings: list[str] = []
    conflicts: list[str] = []
    draft = _deterministic_draft(canvas, warnings)
    refined = False  # LLM 补缺已跳过，确定性映射已满足需求
    _lint(draft, warnings)
    _mark_conflicts(draft, existing, conflicts)
    coverage = _scenario_coverage(canvas, draft, existing)
    report = {"warnings": warnings, "conflicts": conflicts,
              "scenarioCoverage": coverage, "llmRefined": refined}
    return draft, report


def apply_draft(db: Session, draft_data: dict, selected_keys: Optional[list[str]],
                ontology_id: str, lineage: Optional[dict] = None) -> dict:
    """把勾选且无冲突的草稿元素落入本体（保守合并：同名一律跳过）。

    链接端点/动作绑定解析顺序：本次落地的新对象 → 目标本体已有同名对象 → 跳过。
    同名跳过使重复 apply 天然幂等——已落地元素再次应用只会进 skipped。
    lineage（sessionId/documentId/draftId）与元素的 draftKey/sourceRefs 一起
    写入 source 血缘列，落地后可回溯到探索会话与画布卡片。
    """
    selected = set(selected_keys) if selected_keys is not None else None

    def picked(item: dict) -> bool:
        return selected is None or item["key"] in selected

    def src_of(item: dict) -> Optional[dict]:
        if not lineage:
            return None
        return {"kind": "business_exploration", **lineage,
                "draftKey": item.get("key"), "sourceRefs": item.get("sourceRefs") or []}

    existing = existing_name_sets(db, ontology_id)
    existing_obj_ids = {norm_name(x.name): x.id for x in
                        db.query(ObjectType).filter(ObjectType.ontology_id == ontology_id)}
    created = {"objectTypes": 0, "linkTypes": 0, "actions": 0, "functions": 0, "sentinels": 0}
    skipped: list[dict] = []
    key2id: dict[str, str] = {}

    base_count = len(existing_obj_ids)
    for item in draft_data.get("objectTypes", []):
        if not picked(item):
            continue
        if norm_name(item["name"]) in existing["objectTypes"]:
            skipped.append({"key": item["key"], "reason": f"目标本体已存在同名对象类型「{item['name']}」"})
            continue
        oid = str(uuid.uuid4())
        idx = base_count + created["objectTypes"]
        db.add(ObjectType(
            id=oid, ontology_id=ontology_id, name=item["name"],
            display_name=item["displayName"], description=item.get("description"),
            color=item.get("color"), primary_key=item.get("primaryKey"),
            properties=item.get("properties") or [], interfaces=[],
            position_x=120 + (idx % 4) * 340, position_y=120 + (idx // 4) * 260,
            source=src_of(item),
        ))
        key2id[item["key"]] = oid
        existing["objectTypes"].add(norm_name(item["name"]))
        created["objectTypes"] += 1

    def resolve_obj(key: Optional[str], name_hint: str) -> Optional[str]:
        if key and key in key2id:
            return key2id[key]
        return existing_obj_ids.get(norm_name(name_hint))

    for item in draft_data.get("linkTypes", []):
        if not picked(item):
            continue
        if norm_name(item["name"]) in existing["linkTypes"]:
            skipped.append({"key": item["key"], "reason": f"目标本体已存在同名链接类型「{item['name']}」"})
            continue
        src = resolve_obj(item.get("sourceKey"), item.get("sourceName", ""))
        tgt = resolve_obj(item.get("targetKey"), item.get("targetName", ""))
        if not src or not tgt:
            skipped.append({"key": item["key"],
                            "reason": f"链接「{item['displayName']}」的端点对象未落地也不在目标本体中"})
            continue
        db.add(LinkType(
            id=str(uuid.uuid4()), ontology_id=ontology_id, name=item["name"],
            display_name=item["displayName"], description=item.get("description"),
            source_object_type_id=src, target_object_type_id=tgt,
            cardinality=item.get("cardinality") or "one-to-many",
            properties=[],
            source=src_of(item),
        ))
        existing["linkTypes"].add(norm_name(item["name"]))
        created["linkTypes"] += 1

    for item in draft_data.get("actions", []):
        if not picked(item):
            continue
        if norm_name(item["name"]) in existing["actions"]:
            skipped.append({"key": item["key"], "reason": f"目标本体已存在同名动作「{item['name']}」"})
            continue
        obj_key = item.get("objectTypeKey")
        obj_id = key2id.get(obj_key) if obj_key else None
        if obj_key and not obj_id:
            # 端点对象是冲突/未勾选项 → 尝试按名字绑到已有对象
            src_name = obj_key.split(":", 1)[-1]
            obj_id = existing_obj_ids.get(src_name)
        db.add(ActionType(
            id=str(uuid.uuid4()), ontology_id=ontology_id, name=item["name"],
            display_name=item["displayName"], description=item.get("description"),
            object_type_id=obj_id, parameters=item.get("parameters") or [],
            rules=item.get("rules") or [],
            requires_approval=bool(item.get("requiresApproval")),
            source=src_of(item),
        ))
        existing["actions"].add(norm_name(item["name"]))
        created["actions"] += 1

    # 激活函数草稿 → OntologyFunction（enabled=false，函数体待人工形式化后启用）
    for item in draft_data.get("functions", []):
        if not picked(item):
            continue
        if norm_name(item["name"]) in existing.get("functions", set()):
            skipped.append({"key": item["key"], "reason": f"目标本体已存在同名函数「{item['name']}」"})
            continue
        target_id = resolve_obj(item.get("targetObjectTypeKey"),
                                item.get("targetObjectTypeName", ""))
        db.add(OntologyFunction(
            id=str(uuid.uuid4()), ontology_id=ontology_id, name=item["name"],
            display_name=item["displayName"], description=item.get("description"),
            function_type=(item.get("functionType") or "query") if target_id else "query",
            language="expression", target_object_type_id=target_id,
            parameters=[], return_type=item.get("returnType") or "string",
            body=item.get("body") or "", enabled=False,
            source=src_of(item),
        ))
        existing.setdefault("functions", set()).add(norm_name(item["name"]))
        created["functions"] += 1

    # 哨兵草稿 → Sentinel（muted 影子 + enabled=false + status=draft，三重闸门确保不进执行链路）
    for item in draft_data.get("sentinels", []):
        if not picked(item):
            continue
        if norm_name(item["name"]) in existing.get("sentinels", set()):
            skipped.append({"key": item["key"], "reason": f"目标本体已存在同名哨兵「{item['name']}」"})
            continue
        bind_id = resolve_obj(item.get("bindingObjectKey"), item.get("bindingObjectName", ""))
        bindings = [{"alias": "a", "objectTypeId": bind_id, "filter": None}] if bind_id else []
        db.add(Sentinel(
            id=str(uuid.uuid4()), ontology_id=ontology_id, name=item["name"],
            display_name=item["displayName"], description=item.get("description"),
            bindings=bindings, links=[], condition=None,
            condition_rows=[], condition_logic="and",
            primary_alias="a" if bindings else None, action_ids=[],
            on_change=bool(item.get("onChange", True)),
            on_schedule=bool(item.get("onSchedule")),
            scan_interval_seconds=int(item.get("scanIntervalSeconds") or 300),
            trigger_mode="on_enter", muted=True, enabled=False, status="draft",
            source=src_of(item),
        ))
        existing.setdefault("sentinels", set()).add(norm_name(item["name"]))
        created["sentinels"] += 1

    db.flush()
    return {"created": created, "skipped": skipped}
