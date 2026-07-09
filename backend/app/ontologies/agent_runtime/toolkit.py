"""
受限工具集 (Bounded Toolkit) — agent 与本体交互的全部动词

每个工具都以 AgentScope 为唯一世界观：越界引用 → ToolError（返回给 LLM 自我
修正）。工具永远不接触数据集 / SQL / Neo4j —— 只读 fo_* 投影表与事实流，
"写"只能以 propose_action(dry_run) 的形式提出，真实执行在 router 层由用户
确认后经 action_engine 完成（requires_approval 的动作仍会进 HITL 审批队列）。

工具清单：
  search_objects     按类型 + 属性过滤 + 关键词检索实例
  get_object         单实例详情（含各链接类型的邻居概览）
  traverse_links     沿链接类型遍历邻居
  aggregate_objects  分组计数 / 求和 / 均值 / 最值
  get_object_history 实例的事实流（谁、何时、为何改的 — 溯源问答）
  list_actions       授权范围内的动作及参数说明
  propose_action     动作预演（引擎 dry-run：校验 + 模拟效果，不落实际变更）
"""
from __future__ import annotations

import time
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.models.ontology_formal import (
    ObjectInstance, LinkInstance, PropertyFact,
)
from app.ontologies.agent_runtime.boundary import AgentScope, ToolError
from app.ontologies.formal_modeling.facts import fact_order_clause

_SCAN_CAP = 5000          # 单次工具最多扫描的实例行数（演示规模防护）
_VALUE_TRUNC = 200        # 单个属性值的输出截断长度
_CITATION_CAP = 20        # 一次回答最多引用的实例数
_CHART_POINT_CAP = 20     # 聚合图表最多展示的数据点（已按值降序取前 N）
_SNIPPET_FIELDS = 3       # 引用卡片附带的属性摘要字段数


# ---------------------------------------------------------------- 工具定义

TOOL_DEFS: list[dict] = [
    {
        "name": "search_objects",
        "description": "在指定对象类型下检索实例。支持属性条件过滤（filters）与全文关键词（keyword）。回答任何关于'有哪些/多少/是什么'的问题前都应先用本工具查证，不要凭空作答。",
        "parameters": {
            "type": "object",
            "properties": {
                "object_type": {"type": "string", "description": "对象类型的 name 或显示名"},
                "filters": {
                    "type": "array",
                    "description": "属性过滤条件，AND 关系",
                    "items": {
                        "type": "object",
                        "properties": {
                            "property": {"type": "string", "description": "属性 name"},
                            "op": {"type": "string", "enum": ["eq", "neq", "gt", "gte", "lt", "lte", "contains", "in"]},
                            "value": {"description": "比较值；op=in 时为数组"},
                        },
                        "required": ["property", "op", "value"],
                    },
                },
                "keyword": {"type": "string", "description": "在所有属性值中做包含匹配的关键词"},
                "limit": {"type": "integer", "description": "返回条数上限（受授权配额约束）"},
            },
            "required": ["object_type"],
        },
    },
    {
        "name": "get_object",
        "description": "查看单个对象实例的全部属性、派生属性，以及它在各链接类型上的邻居概览。",
        "parameters": {
            "type": "object",
            "properties": {
                "instance_id": {"type": "string", "description": "对象实例 id"},
            },
            "required": ["instance_id"],
        },
    },
    {
        "name": "traverse_links",
        "description": "从一个对象实例出发，沿指定链接类型遍历，返回相邻对象。用于回答'X 关联了哪些 Y'类问题。",
        "parameters": {
            "type": "object",
            "properties": {
                "instance_id": {"type": "string", "description": "起点对象实例 id"},
                "link_type": {"type": "string", "description": "链接类型的 name 或显示名"},
                "direction": {"type": "string", "enum": ["out", "in"], "description": "out=作为源对象沿链接向外, in=作为目标对象反向"},
                "limit": {"type": "integer"},
            },
            "required": ["instance_id", "link_type"],
        },
    },
    {
        "name": "aggregate_objects",
        "description": "对某对象类型的实例做聚合统计：count/sum/avg/min/max，可按属性分组。回答'总共/平均/最多'类问题用这个，不要逐条列举。",
        "parameters": {
            "type": "object",
            "properties": {
                "object_type": {"type": "string"},
                "metric": {"type": "string", "enum": ["count", "sum", "avg", "min", "max"]},
                "metric_property": {"type": "string", "description": "metric 非 count 时必填：参与计算的数值属性 name"},
                "group_by": {"type": "string", "description": "可选：按此属性分组"},
                "filters": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "property": {"type": "string"},
                            "op": {"type": "string", "enum": ["eq", "neq", "gt", "gte", "lt", "lte", "contains", "in"]},
                            "value": {},
                        },
                        "required": ["property", "op", "value"],
                    },
                },
            },
            "required": ["object_type", "metric"],
        },
    },
    {
        "name": "get_object_history",
        "description": "查看对象实例的事实流（属性级变更历史）：每次变更的值、来源（人工/采集/某动作）、操作者、因果与置信度。回答'为什么是这个值/谁改的/什么时候变的'用这个。",
        "parameters": {
            "type": "object",
            "properties": {
                "instance_id": {"type": "string"},
                "property_name": {"type": "string", "description": "可选：只看某个属性"},
                "limit": {"type": "integer"},
            },
            "required": ["instance_id"],
        },
    },
    {
        "name": "list_actions",
        "description": "列出授权范围内可用的业务动作及其参数说明。",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "propose_action",
        "description": "对某动作做预演（dry-run）：跑完整校验并模拟全部效果，但不落任何实际变更。这是 agent 唯一的'写'入口——预演结果会作为提案展示给用户，由用户决定是否真实执行。需要修改数据时必须用本工具，禁止假装已经执行。",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "动作的 name 或显示名"},
                "parameters": {"type": "object", "description": "动作参数", "additionalProperties": True},
                "target_instance_id": {"type": "string", "description": "动作作用的目标实例 id（动作绑定对象类型时必填）"},
            },
            "required": ["action"],
        },
    },
]


# ---------------------------------------------------------------- 过滤与格式化

def _num(v: Any) -> Optional[float]:
    try:
        if isinstance(v, bool):
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _match(props: dict, f: dict) -> bool:
    prop, op, want = f.get("property"), f.get("op", "eq"), f.get("value")
    got = (props or {}).get(prop)
    if op == "eq":
        return got == want or (str(got) == str(want) and got is not None)
    if op == "neq":
        return not (got == want or (str(got) == str(want) and got is not None))
    if op == "contains":
        return want is not None and got is not None and str(want).lower() in str(got).lower()
    if op == "in":
        options = want if isinstance(want, list) else [want]
        return got in options or str(got) in [str(x) for x in options]
    # 数值比较：双方都可转数值才比较，否则退化为字符串比较
    a, b = _num(got), _num(want)
    if a is None or b is None:
        if got is None or want is None:
            return False
        a, b = str(got), str(want)  # type: ignore[assignment]
    if op == "gt":
        return a > b
    if op == "gte":
        return a >= b
    if op == "lt":
        return a < b
    if op == "lte":
        return a <= b
    raise ToolError(f"不支持的过滤操作符: {op}")


def _trunc(v: Any) -> Any:
    if isinstance(v, str) and len(v) > _VALUE_TRUNC:
        return v[:_VALUE_TRUNC] + f"…(截断,共{len(v)}字符)"
    return v


def _row(inst: ObjectInstance) -> dict:
    return {
        "id": inst.id,
        "properties": {k: _trunc(v) for k, v in (inst.properties or {}).items()},
        "computed": {k: _trunc(v) for k, v in (inst.computed or {}).items()},
    }


def _label(scope: AgentScope, inst: ObjectInstance) -> str:
    """实例的人类可读标签：主键值 > name/title 属性 > id 前缀。"""
    ot = scope.object_types.get(inst.object_type_id)
    props = inst.properties or {}
    if ot and ot.primary_key:
        pk_prop = next((p for p in (ot.properties or []) if isinstance(p, dict)
                        and (p.get("id") == ot.primary_key or p.get("name") == ot.primary_key)), None)
        if pk_prop and props.get(pk_prop.get("name")) not in (None, ""):
            return str(props[pk_prop["name"]])
    for k in ("name", "title", "name_cn", "display_name"):
        if props.get(k) not in (None, ""):
            return str(props[k])
    # 兜底：第一个非空的标量属性值（比 id 前缀对人友好）
    for v in props.values():
        if isinstance(v, (str, int, float)) and str(v).strip():
            return str(v)[:40]
    return inst.id[:8]


_TEMPORAL_HINTS = ("date", "time", "day", "week", "month", "year", "quarter", "ts",
                   "时间", "日期", "年", "月", "日", "季", "周")
_METRIC_LABELS = {"count": "数量", "sum": "合计", "avg": "平均值", "min": "最小值", "max": "最大值"}


def _is_temporal(name: str) -> bool:
    n = (name or "").lower()
    return any(h in n for h in _TEMPORAL_HINTS)


def _build_aggregate_chart(ot_display: str, metric: str, metric_prop: Optional[str],
                           group_by: str, groups: list[dict]) -> Optional[dict]:
    """从聚合分组结果确定性地生成图表 spec —— 数字全部来自真实结果，零幻觉。

    仅在「分组 + ≥2 个有效数据点」时出图；单值聚合只是一个统计量，不出图。
    类型推断：时间维→折线(line)；计数且类别很少→饼图(pie，看占比)；否则柱状(bar，做对比)。
    图表 spec 与前端 ```chart 块同构，直接交给 AgentChart 渲染。
    """
    points = [{"label": str(g.get("group")), "value": g.get("value")}
              for g in groups if g.get("value") is not None][:_CHART_POINT_CAP]
    if len(points) < 2:
        return None
    metric_label = _METRIC_LABELS.get(metric, metric)
    if _is_temporal(group_by):
        ctype = "line"
    elif metric == "count" and len(points) <= 6:
        ctype = "pie"
    else:
        ctype = "bar"
    y_label = metric_label if metric == "count" else f"{metric_label}({metric_prop})"
    return {
        "type": ctype,
        "title": f"{ot_display}：按{group_by}{metric_label}",
        "xLabel": group_by,
        "yLabel": y_label,
        "data": points,
    }


def _snippet(scope: AgentScope, inst: ObjectInstance, max_fields: int = _SNIPPET_FIELDS) -> str:
    """引用实例的一句话属性摘要（跳过与标签重复的值）——让引用卡片不只有一个名字。"""
    ot = scope.object_types.get(inst.object_type_id)
    prop_defs = {p.get("name"): p for p in (ot.properties or [])
                 if isinstance(p, dict)} if ot else {}
    label = _label(scope, inst)
    parts: list[str] = []
    for k, v in (inst.properties or {}).items():
        if len(parts) >= max_fields:
            break
        if v in (None, "") or isinstance(v, (list, dict)):
            continue
        sval = str(v).strip()
        if not sval or sval == label:
            continue
        if len(sval) > 24:
            sval = sval[:24] + "…"
        disp = (prop_defs.get(k) or {}).get("displayName") or k
        parts.append(f"{disp}: {sval}")
    return " · ".join(parts)


class ToolRunner:
    """把 scope 装进闭包，给 orchestrator 一个统一的 run(name, args) 入口。
    顺带收集引用（citations）——回答里出现过的实例都可追溯。"""

    def __init__(self, db: Session, scope: AgentScope):
        self.db = db
        self.scope = scope
        self.citations: list[dict] = []
        self.proposals: list[dict] = []
        self._cited: set[str] = set()

    def _cite(self, inst: ObjectInstance):
        if inst.id in self._cited or len(self.citations) >= _CITATION_CAP:
            return
        ot = self.scope.object_types.get(inst.object_type_id)
        ot_name = ot.display_name if ot else inst.object_type_id
        label = _label(self.scope, inst)
        self._cited.add(inst.id)
        self.citations.append({
            "instanceId": inst.id,
            "objectType": ot_name,
            "label": label,
            # 统一引用契约：预拼展示串 + 属性摘要，前端直接用
            "sourceLabel": f"{ot_name} · {label}",
            "snippet": _snippet(self.scope, inst),
        })

    def run(self, name: str, args: dict) -> dict:
        handler = getattr(self, f"_tool_{name}", None)
        if handler is None:
            raise ToolError(f"未知工具: {name}")
        return handler(args or {})

    # ------------------------------------------------------------ 各工具实现

    def _instances_of(self, type_id: str) -> list[ObjectInstance]:
        return (self.db.query(ObjectInstance)
                .filter(ObjectInstance.ontology_id == self.scope.ontology.id,
                        ObjectInstance.object_type_id == type_id)
                .order_by(ObjectInstance.created_at.desc())
                .limit(_SCAN_CAP).all())

    def _apply_filters(self, rows: list[ObjectInstance], filters, keyword) -> list[ObjectInstance]:
        out = []
        kw = (keyword or "").strip().lower()
        for inst in rows:
            props = {**(inst.properties or {}), **(inst.computed or {})}
            if filters and not all(_match(props, f) for f in filters):
                continue
            if kw:
                hay = " ".join(str(v) for v in props.values()) + " " + inst.id + " " + (inst.external_id or "")
                if kw not in hay.lower():
                    continue
            out.append(inst)
        return out

    def _limit(self, requested) -> int:
        cap = self.scope.profile.max_rows_per_query or 50
        try:
            n = int(requested) if requested else cap
        except (TypeError, ValueError):
            n = cap
        return max(1, min(n, cap))

    def _tool_search_objects(self, args: dict) -> dict:
        ot = self.scope.require_object_type(args.get("object_type", ""))
        rows = self._instances_of(ot.id)
        matched = self._apply_filters(rows, args.get("filters"), args.get("keyword"))
        limit = self._limit(args.get("limit"))
        page = matched[:limit]
        for inst in page:
            self._cite(inst)
        return {
            "objectType": ot.display_name,
            "total": len(matched),
            "returned": len(page),
            "truncated": len(matched) > len(page),
            "scannedCap": len(rows) >= _SCAN_CAP,
            "items": [_row(i) for i in page],
        }

    def _tool_get_object(self, args: dict) -> dict:
        inst = self.scope.visible_instance(
            self.db.query(ObjectInstance).filter(
                ObjectInstance.id == args.get("instance_id", "")).first())
        self._cite(inst)
        ot = self.scope.object_types[inst.object_type_id]

        neighbors = []
        for lt in self.scope.link_types.values():
            for direction, self_col, other_col in (
                    ("out", LinkInstance.source_object_id, LinkInstance.target_object_id),
                    ("in", LinkInstance.target_object_id, LinkInstance.source_object_id)):
                links = (self.db.query(LinkInstance)
                         .filter(LinkInstance.ontology_id == self.scope.ontology.id,
                                 LinkInstance.link_type_id == lt.id,
                                 self_col == inst.id)
                         .limit(50).all())
                if not links:
                    continue
                sample = []
                for li in links[:5]:
                    other_id = li.target_object_id if direction == "out" else li.source_object_id
                    other = self.db.query(ObjectInstance).filter(
                        ObjectInstance.id == other_id).first()
                    if other and other.object_type_id in self.scope.object_types:
                        sample.append({"instanceId": other.id, "label": _label(self.scope, other)})
                neighbors.append({
                    "linkType": lt.display_name, "linkTypeName": lt.name,
                    "direction": direction, "count": len(links), "sample": sample,
                })
        return {"objectType": ot.display_name, **_row(inst), "links": neighbors}

    def _tool_traverse_links(self, args: dict) -> dict:
        inst = self.scope.visible_instance(
            self.db.query(ObjectInstance).filter(
                ObjectInstance.id == args.get("instance_id", "")).first())
        lt = self.scope.require_link_type(args.get("link_type", ""))
        direction = args.get("direction") or "out"
        self_col = LinkInstance.source_object_id if direction == "out" else LinkInstance.target_object_id
        limit = self._limit(args.get("limit"))

        links = (self.db.query(LinkInstance)
                 .filter(LinkInstance.ontology_id == self.scope.ontology.id,
                         LinkInstance.link_type_id == lt.id,
                         self_col == inst.id)
                 .limit(limit + 1).all())
        items = []
        for li in links[:limit]:
            other_id = li.target_object_id if direction == "out" else li.source_object_id
            other = self.db.query(ObjectInstance).filter(ObjectInstance.id == other_id).first()
            if not other or other.object_type_id not in self.scope.object_types:
                continue
            self._cite(other)
            ot = self.scope.object_types[other.object_type_id]
            items.append({"objectType": ot.display_name, **_row(other),
                          "linkProperties": li.properties or {}})
        return {
            "from": _label(self.scope, inst), "linkType": lt.display_name,
            "direction": direction, "returned": len(items),
            "truncated": len(links) > limit, "items": items,
        }

    def _tool_aggregate_objects(self, args: dict) -> dict:
        ot = self.scope.require_object_type(args.get("object_type", ""))
        metric = args.get("metric") or "count"
        metric_prop = args.get("metric_property")
        if metric != "count" and not metric_prop:
            raise ToolError(f"metric={metric} 需要提供 metric_property（数值属性名）")

        rows = self._apply_filters(self._instances_of(ot.id), args.get("filters"), None)
        group_by = args.get("group_by")

        def compute(group: list[ObjectInstance]):
            if metric == "count":
                return len(group)
            vals = []
            for inst in group:
                v = _num({**(inst.properties or {}), **(inst.computed or {})}.get(metric_prop))
                if v is not None:
                    vals.append(v)
            if not vals:
                return None
            if metric == "sum":
                return round(sum(vals), 6)
            if metric == "avg":
                return round(sum(vals) / len(vals), 6)
            if metric == "min":
                return min(vals)
            if metric == "max":
                return max(vals)
            raise ToolError(f"不支持的 metric: {metric}")

        if not group_by:
            return {"objectType": ot.display_name, "metric": metric,
                    "metricProperty": metric_prop, "scanned": len(rows),
                    "value": compute(rows)}

        groups: dict[str, list[ObjectInstance]] = {}
        for inst in rows:
            key = str({**(inst.properties or {}), **(inst.computed or {})}.get(group_by))
            groups.setdefault(key, []).append(inst)
        result = [{"group": k, "value": compute(v), "count": len(v)} for k, v in groups.items()]
        result.sort(key=lambda x: (x["value"] is None, -(x["value"] or 0)))
        groups_out = result[:50]
        out = {"objectType": ot.display_name, "metric": metric,
               "metricProperty": metric_prop, "groupBy": group_by,
               "scanned": len(rows), "groups": groups_out,
               "truncated": len(result) > 50}
        # 确定性图表：直接从真实分组值生成，前端渲染，无需 LLM 再吐 chart
        chart = _build_aggregate_chart(ot.display_name, metric, metric_prop, group_by, groups_out)
        if chart:
            out["chart"] = chart
            out["chartRendered"] = True
        return out

    def _tool_get_object_history(self, args: dict) -> dict:
        inst = self.scope.visible_instance(
            self.db.query(ObjectInstance).filter(
                ObjectInstance.id == args.get("instance_id", "")).first())
        self._cite(inst)
        limit = min(self._limit(args.get("limit")), 50)
        q = self.db.query(PropertyFact).filter(
            PropertyFact.ontology_id == self.scope.ontology.id,
            PropertyFact.instance_id == inst.id)
        if args.get("property_name"):
            q = q.filter(PropertyFact.property_name == args["property_name"])
        facts = q.order_by(*fact_order_clause()).limit(limit).all()
        return {
            "instance": _label(self.scope, inst),
            "facts": [{
                "property": f.property_name,
                "value": _trunc((f.value or {}).get("v")),
                "kind": f.kind or "property",
                "source": f.source,
                "actorId": f.actor_id,
                "causedBy": f.caused_by,
                "confidence": f.confidence,
                "recordedAt": f.recorded_at.isoformat() if f.recorded_at else None,
            } for f in facts],
        }

    def _tool_list_actions(self, args: dict) -> dict:
        items = []
        for a in self.scope.actions.values():
            ot = self.scope.object_types.get(a.object_type_id) if a.object_type_id else None
            items.append({
                "name": a.name, "displayName": a.display_name,
                "description": a.description,
                "targetObjectType": ot.display_name if ot else None,
                "requiresApproval": bool(a.requires_approval),
                "parameters": [
                    {"name": p.get("name"), "displayName": p.get("displayName"),
                     "type": p.get("type", "string"), "required": bool(p.get("required")),
                     "description": p.get("description")}
                    for p in (a.parameters or []) if isinstance(p, dict)
                ],
            })
        return {"actions": items}

    def _tool_propose_action(self, args: dict) -> dict:
        if not self.scope.profile.allow_action_proposals:
            raise ToolError("当前授权边界禁止提出动作提案（allow_action_proposals=false）")
        action = self.scope.require_action(args.get("action", ""))
        target_id = args.get("target_instance_id")
        if target_id:
            # 目标实例必须可见 —— 不允许对隐藏类型的实例发起动作
            self.scope.visible_instance(
                self.db.query(ObjectInstance).filter(ObjectInstance.id == target_id).first())

        from app.ontologies.formal_modeling.schemas import RunActionRequest
        from app.services.formal.action_engine import execute_action
        body = RunActionRequest(action_id=action.id,
                                parameters=args.get("parameters") or {},
                                target_instance_id=target_id, dry_run=True)
        log = execute_action(self.db, self.scope.ontology.id, body)

        proposal = {
            "proposalId": f"prop-{int(time.time() * 1000)}-{len(self.proposals)}",
            "actionId": action.id,
            "actionName": action.display_name,
            "parameters": args.get("parameters") or {},
            "targetInstanceId": target_id,
            "requiresApproval": bool(action.requires_approval),
            "status": log.get("status"),
            "validationErrors": log.get("validationErrors") or [],
            "effects": log.get("effects") or [],
        }
        self.proposals.append(proposal)
        return {
            "proposal": proposal,
            "note": ("预演通过。已生成提案卡片，等待用户确认后才会真实执行"
                     + ("（该动作还需人工审批）。" if action.requires_approval else "。"))
            if log.get("status") == "success"
            else "预演未通过，请根据 validationErrors 修正参数后重试。",
        }
