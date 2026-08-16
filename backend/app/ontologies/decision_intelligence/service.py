"""决策智能查询面 v1 — 事实流之上的因果链追溯（只读）。

借鉴 Semantica 的 CausalChainAnalyzer 思路，落在本平台已有的 append-only
事实流上：PropertyFact 自带 ``caused_by``（因果指针 → 动作日志/事实）、
``supersedes_id``（同属性链上的前一事实）、``derived_from``（派生输入的
事实 id 列表），决策事实（kind='decision'）记录 HITL 审批结果与理由。
本模块把这些指针走成一张可读的因果图：决策 → 动作 → 事实变更 → 后续覆盖。

只读：不写库、不执行动作；所有查询都带安全上限，超限显式 truncated。
"""
from __future__ import annotations

import logging
from collections import deque
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.models.ontology_formal import ActionExecutionLog, PropertyFact
from app.ontologies.formal_modeling.facts import fact_order_clause
from app.shared.time_utils import utc_iso

logger = logging.getLogger(__name__)

# —— 缰绳：防扇出爆炸与巨型响应 ——
_SEED_FACT_CAP = 40          # 起始事实条数（新→旧）
_MAX_NODES = 150             # 节点总量上限
_MAX_EDGES = 300             # 边总量上限
_MAX_LOG_NODES = 40          # 动作日志节点上限
_MAX_DECISION_NODES = 20     # 决策节点上限
_MAX_DOWNSTREAM_FACTS = 60   # 下游 effect/successor 事实每轮上限
_MAX_DERIVED_INPUTS = 20     # 单条事实 derived_from 展开上限
_VALUE_TRUNC = 200           # 单个属性值的输出截断长度

_DIRECTIONS = frozenset({"upstream", "downstream", "both"})


def _trunc(value: Any) -> Any:
    if isinstance(value, str) and len(value) > _VALUE_TRUNC:
        return value[:_VALUE_TRUNC] + "…"
    return value


def _fact_node(fact: PropertyFact) -> dict:
    node: dict[str, Any] = {
        "kind": "fact",
        "id": fact.id,
        "instanceId": fact.instance_id,
        "objectTypeId": fact.object_type_id,
        "property": fact.property_name,
        "value": _trunc((fact.value or {}).get("v")),
        "present": (fact.value or {}).get("present", True),
        "factKind": fact.kind or "property",
        "source": fact.source,
        "actorId": fact.actor_id,
        "confidence": fact.confidence,
        "recordedAt": utc_iso(fact.recorded_at),
        "causedBy": fact.caused_by,
        "supersedesId": fact.supersedes_id,
        "derivedFrom": fact.derived_from,
    }
    # 决策事实（factKind='decision'）把 decision/reason 拆到顶层，
    # 让助手能直接叙述「谁批准/拒绝、理由是什么」。
    if (fact.kind or "property") == "decision":
        raw = (fact.value or {}).get("v")
        if isinstance(raw, dict):
            node["decision"] = _trunc(raw.get("decision"))
            if raw.get("reason") is not None:
                node["reason"] = _trunc(raw.get("reason"))
    return node


def _log_node(log: ActionExecutionLog) -> dict:
    return {
        "kind": "action",
        "id": log.id,
        "actionId": log.action_id,
        "actionName": log.action_name,
        "status": log.status,
        "targetInstanceId": log.object_instance_id,
        "actorId": log.actor_id,
        "decidedBy": log.decided_by,
        "decidedAt": utc_iso(log.decided_at) if log.decided_at else None,
        "decisionReason": log.decision_reason,
    }


def _decision_node(fact: PropertyFact) -> dict:
    raw = (fact.value or {}).get("v")
    if isinstance(raw, dict):
        decision = raw.get("decision")
        reason = raw.get("reason")
    else:
        decision, reason = raw, None
    return {
        "kind": "decision",
        "id": fact.id,
        "decision": _trunc(decision),
        "reason": _trunc(reason) if reason is not None else None,
        "actorId": fact.actor_id,
        "recordedAt": utc_iso(fact.recorded_at),
    }


class _Graph:
    """带上限的因果图累积器；超限一律截断并显式标记，不静默丢数据。"""

    def __init__(self) -> None:
        self.nodes: dict[str, dict] = {}
        self.edges: list[dict] = []
        self.truncated = False

    def add_node(self, key: str, node: dict) -> bool:
        if key in self.nodes:
            return True
        if len(self.nodes) >= _MAX_NODES:
            self.truncated = True
            return False
        self.nodes[key] = node
        return True

    def add_edge(self, kind: str, src: str, dst: str) -> None:
        if len(self.edges) >= _MAX_EDGES:
            self.truncated = True
            return
        self.edges.append({"kind": kind, "from": src, "to": dst})


def _facts_by_ids(
        db: Session, ontology_id: str, fact_ids: list[str],
        authority_release_ids: Optional[list[str]]) -> list[PropertyFact]:
    """按 id 批量加载事实；仅在 authority 段内取数（None = 全历史，兼容旧路径）。"""
    ids = list(dict.fromkeys(fact_ids))
    if not ids:
        return []
    query = db.query(PropertyFact).filter(
        PropertyFact.ontology_id == ontology_id,
        PropertyFact.id.in_(ids),
    )
    if authority_release_ids is not None:
        query = query.filter(
            PropertyFact.ontology_release_id.in_(authority_release_ids))
    return query.all()


def _decision_facts_for_logs(
        db: Session, ontology_id: str, log_ids: list[str],
        authority_release_ids: Optional[list[str]]) -> list[PropertyFact]:
    """动作日志上的 HITL 决策事实（kind='decision'，instance_id=日志 id）。"""
    ids = list(dict.fromkeys(log_ids))
    if not ids:
        return []
    query = db.query(PropertyFact).filter(
        PropertyFact.ontology_id == ontology_id,
        PropertyFact.instance_id.in_(ids),
        PropertyFact.kind == "decision",
    )
    if authority_release_ids is not None:
        query = query.filter(
            PropertyFact.ontology_release_id.in_(authority_release_ids))
    return (query.order_by(*fact_order_clause())
            .limit(_MAX_DECISION_NODES).all())


def _effects_for_logs(
        db: Session, ontology_id: str, log_ids: list[str],
        authority_release_ids: Optional[list[str]]) -> list[PropertyFact]:
    """由动作日志导致的属性变更事实（caused_by = 日志 id）。"""
    ids = list(dict.fromkeys(log_ids))
    if not ids:
        return []
    query = db.query(PropertyFact).filter(
        PropertyFact.ontology_id == ontology_id,
        PropertyFact.caused_by.in_(ids),
    )
    if authority_release_ids is not None:
        query = query.filter(
            PropertyFact.ontology_release_id.in_(authority_release_ids))
    return (query.order_by(*fact_order_clause())
            .limit(_MAX_DOWNSTREAM_FACTS).all())


def _successors_of(
        db: Session, ontology_id: str, fact_ids: list[str],
        authority_release_ids: Optional[list[str]]) -> list[PropertyFact]:
    """同 (instance, property) 链上被这些事实 supersede 的更新事实。"""
    ids = list(dict.fromkeys(fact_ids))
    if not ids:
        return []
    query = db.query(PropertyFact).filter(
        PropertyFact.ontology_id == ontology_id,
        PropertyFact.supersedes_id.in_(ids),
    )
    if authority_release_ids is not None:
        query = query.filter(
            PropertyFact.ontology_release_id.in_(authority_release_ids))
    return (query.order_by(*fact_order_clause())
            .limit(_MAX_DOWNSTREAM_FACTS).all())


def _load_caused_targets(
        db: Session, ontology_id: str, refs: list[str],
        authority_release_ids: Optional[list[str]],
        ) -> tuple[dict[str, ActionExecutionLog], dict[str, PropertyFact]]:
    """caused_by 可能指向动作日志或事实：一次查两表归类，不猜不丢。"""
    unique = list(dict.fromkeys(ref for ref in refs if ref))
    if not unique:
        return {}, {}
    logs = db.query(ActionExecutionLog).filter(
        ActionExecutionLog.ontology_id == ontology_id,
        ActionExecutionLog.id.in_(unique),
    ).limit(_MAX_LOG_NODES).all()
    log_by_id = {log.id: log for log in logs}
    remaining = [ref for ref in unique if ref not in log_by_id]
    facts = _facts_by_ids(
        db, ontology_id, remaining, authority_release_ids)
    return log_by_id, {fact.id: fact for fact in facts}


def trace_causal_chain(
        db: Session,
        *,
        ontology_id: str,
        instance_id: str,
        property_name: Optional[str] = None,
        direction: str = "both",
        max_depth: int = 3,
        authority_release_ids: Optional[list[str]] = None,
) -> dict:
    """沿事实流追溯一个实例的因果链，返回可供助手叙述的节点/边图。

    direction: upstream=什么导致了这些变化；downstream=这些变化后来导致了什么。
    max_depth 为 BFS 层数（1-4）。authority_release_ids 与本体 release 血缘
    语义一致（None = 全历史，旧路径兼容）。
    """
    if direction not in _DIRECTIONS:
        raise ValueError(f"direction 必须是 {sorted(_DIRECTIONS)} 之一")
    depth_limit = max(1, min(int(max_depth or 3), 4))
    want_up = direction in ("upstream", "both")
    want_down = direction in ("downstream", "both")

    graph = _Graph()
    visited_facts: set[str] = set()
    visited_logs: set[str] = set()

    def keep_fact(fact: PropertyFact, *, via: str, edge_kind: str) -> bool:
        """登记一个事实节点并连上来源边；已访问/超限返回 False。"""
        if fact.id in visited_facts:
            return False
        visited_facts.add(fact.id)
        key = f"fact:{fact.id}"
        if not graph.add_node(key, _fact_node(fact)):
            return False
        if via and edge_kind:
            graph.add_edge(edge_kind, via, key)
        return True

    def keep_log(log: ActionExecutionLog, *, via: str, edge_kind: str) -> bool:
        if log.id in visited_logs:
            return False
        visited_logs.add(log.id)
        key = f"log:{log.id}"
        if not graph.add_node(key, _log_node(log)):
            return False
        if via and edge_kind:
            graph.add_edge(edge_kind, via, key)
        return True

    def link_fact(fact: PropertyFact, *, via: str, edge_kind: str) -> bool:
        """keep_fact + 已访问节点补边：种子事实被因果边再指向时，边不能丢。"""
        added = keep_fact(fact, via=via, edge_kind=edge_kind)
        if not added and fact.id in visited_facts and via and edge_kind:
            graph.add_edge(edge_kind, via, f"fact:{fact.id}")
        return added

    def link_log(log: ActionExecutionLog, *, via: str, edge_kind: str) -> bool:
        added = keep_log(log, via=via, edge_kind=edge_kind)
        if not added and log.id in visited_logs and via and edge_kind:
            graph.add_edge(edge_kind, via, f"log:{log.id}")
        return added

    # 1) 起始事实（新→旧，带上限）
    seed_query = db.query(PropertyFact).filter(
        PropertyFact.ontology_id == ontology_id,
        PropertyFact.instance_id == instance_id,
    )
    if property_name:
        seed_query = seed_query.filter(
            PropertyFact.property_name == property_name)
    if authority_release_ids is not None:
        seed_query = seed_query.filter(
            PropertyFact.ontology_release_id.in_(authority_release_ids))
    seeds = (seed_query.order_by(*fact_order_clause())
             .limit(_SEED_FACT_CAP).all())

    up_queue: deque[PropertyFact] = deque()
    down_log_queue: deque[str] = deque()
    down_fact_queue: deque[PropertyFact] = deque()
    for fact in seeds:
        if not keep_fact(fact, via="", edge_kind=""):
            continue
        if want_up:
            up_queue.append(fact)
        if want_down:
            down_fact_queue.append(fact)
            # 下游单方向也要从种子事实的因果指针出发，否则首层为空
            if fact.caused_by:
                down_log_queue.append(fact.caused_by)

    for _depth in range(depth_limit):
        expanded_any = False

        # —— 上游：这些事实从哪来 ——
        if want_up and up_queue:
            next_up: list[PropertyFact] = []
            # caused_by 引用 → 发起它的原始事实节点（边归因要精确到 origin）
            origins_by_cause: dict[str, list[str]] = {}
            for fact in list(up_queue):
                origin_key = f"fact:{fact.id}"
                if fact.supersedes_id:
                    prev = _facts_by_ids(
                        db, ontology_id, [fact.supersedes_id],
                        authority_release_ids)
                    if prev and link_fact(
                            prev[0], via=origin_key,
                            edge_kind="supersedes"):
                        next_up.append(prev[0])
                if fact.caused_by:
                    origins_by_cause.setdefault(
                        fact.caused_by, []).append(origin_key)
                derived = (fact.derived_from or [])[:_MAX_DERIVED_INPUTS]
                for inp in _facts_by_ids(
                        db, ontology_id, derived,
                        authority_release_ids):
                    if link_fact(inp, via=origin_key,
                                 edge_kind="derived_from"):
                        next_up.append(inp)

            log_by_id, cause_fact_by_id = _load_caused_targets(
                db, ontology_id, list(origins_by_cause.keys()),
                authority_release_ids)
            for ref, origins in origins_by_cause.items():
                log = log_by_id.get(ref)
                if log is not None:
                    for origin_key in origins:
                        if link_log(log, via=origin_key,
                                    edge_kind="caused_by"):
                            down_log_queue.append(log.id)
                    continue
                cause_fact = cause_fact_by_id.get(ref)
                if cause_fact is not None:
                    for origin_key in origins:
                        if link_fact(cause_fact, via=origin_key,
                                     edge_kind="caused_by"):
                            next_up.append(cause_fact)

            # 新日志上的 HITL 决策事实（日志节点必须存在，边才有效）
            for decision in _decision_facts_for_logs(
                    db, ontology_id, list(log_by_id.keys()),
                    authority_release_ids):
                log_key = f"log:{decision.instance_id}"
                if log_key not in graph.nodes:
                    continue
                key = f"decision:{decision.id}"
                if graph.add_node(key, _decision_node(decision)):
                    graph.add_edge("decided", log_key, key)

            # HITL 审批日志 ↔ 执行日志互链
            pending = [l for l in log_by_id.values() if l.related_log_id]
            if pending:
                related = db.query(ActionExecutionLog).filter(
                    ActionExecutionLog.ontology_id == ontology_id,
                    ActionExecutionLog.id.in_(
                        [l.related_log_id for l in pending]),
                ).limit(_MAX_LOG_NODES).all()
                related_by_id = {l.id: l for l in related}
                for log in pending:
                    other = related_by_id.get(log.related_log_id)
                    if other is not None and link_log(
                            other, via=f"log:{log.id}",
                            edge_kind="related"):
                        down_log_queue.append(other.id)

            up_queue = deque(next_up[:_SEED_FACT_CAP])
            if next_up or origins_by_cause:
                expanded_any = True

        # —— 下游：这些事实/动作后来导致了什么 ——
        if want_down and (down_log_queue or down_fact_queue):
            next_facts: list[PropertyFact] = []
            if down_log_queue:
                # 下游单方向时上游块被跳过：先补日志节点，effect 边才有落点
                logs = db.query(ActionExecutionLog).filter(
                    ActionExecutionLog.ontology_id == ontology_id,
                    ActionExecutionLog.id.in_(list(down_log_queue)),
                ).limit(_MAX_LOG_NODES).all()
                for log in logs:
                    keep_log(log, via="", edge_kind="")
                effects = _effects_for_logs(
                    db, ontology_id, list(down_log_queue),
                    authority_release_ids)
                for effect in effects:
                    if link_fact(
                            effect, via=f"log:{effect.caused_by}",
                            edge_kind="effect"):
                        next_facts.append(effect)
            if down_fact_queue:
                successors = _successors_of(
                    db, ontology_id,
                    [f.id for f in list(down_fact_queue)[:_SEED_FACT_CAP]],
                    authority_release_ids)
                for newer in successors:
                    if link_fact(
                            newer, via=f"fact:{newer.supersedes_id}",
                            edge_kind="superseded_by"):
                        next_facts.append(newer)
            down_fact_queue = deque(next_facts[:_SEED_FACT_CAP])
            # 下一层：新事实自身的因果指针继续向下展开
            down_log_queue = deque(
                f.caused_by for f in next_facts
                if f.caused_by and f.caused_by not in visited_logs)
            down_log_queue = deque(
                list(down_log_queue)[:_MAX_LOG_NODES])
            if down_log_queue or down_fact_queue:
                expanded_any = True

        if not expanded_any:
            break

    # 汇总（供助手叙述与时间线摘要）
    nodes = list(graph.nodes.values())
    fact_nodes = [n for n in nodes if n["kind"] == "fact"]
    action_nodes = [n for n in nodes if n["kind"] == "action"]
    decision_nodes = [
        n for n in nodes
        if n["kind"] == "decision"
        or (n["kind"] == "fact" and n.get("factKind") == "decision")
    ]
    decision_outcomes = [
        {
            "decision": n.get("decision") or n.get("value"),
            "reason": n.get("reason"),
            "actorId": n.get("actorId"),
        }
        for n in decision_nodes
    ]
    return {
        "instanceId": instance_id,
        "property": property_name,
        "direction": direction,
        "maxDepth": depth_limit,
        "nodes": nodes,
        "edges": graph.edges,
        "summary": {
            "factNodes": len(fact_nodes),
            "actionNodes": len(action_nodes),
            "decisionNodes": len(decision_outcomes),
            "decisionOutcomes": decision_outcomes,
        },
        "truncated": graph.truncated,
    }
