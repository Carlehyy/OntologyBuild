"""
属性事实记录 — Fact 溯源层的统一写入口

所有对 fo_object_instances.properties 的写路径（编辑器整体保存、实例 CRUD、
Action 执行、采集器）都应经过 record_property_facts，把"改了什么、从哪来、
谁改的、因何而改"追加进 fo_property_facts。投影表照常更新，本模块只负责历史真理流。

链接存在性 / 实例存在性（墓碑）也是事实：
  - record_link_fact:   kind='link'，instance_id=链接实例 id，property_name='exists'
  - record_object_tombstone: kind='object'，property_name='exists'，value=False
决策也是事实（HITL 批准/拒绝）：
  - record_decision_fact: kind='decision'，instance_id=动作日志 id，property_name='decision'
"""
from __future__ import annotations

from typing import Any, Optional

from sqlalchemy.orm import Session

from .models import PropertyFact
from app.ontologies.release_context import (
    runtime_release_identity,
    runtime_release_version,
)


def _wrap(v: Any) -> dict:
    """JSON 列统一包一层 {"v": ...}，标量/None/嵌套结构都能存。"""
    return {"v": v}


def _runtime_lineage(
    db: Session,
    ontology_id: str,
    ontology_version: Optional[str],
    ontology_release_id: Optional[str],
) -> tuple[Optional[str], Optional[str]]:
    """Resolve lineage without attributing explicit legacy versions by guess.

    Callers forwarding lineage from another record must pass both fields.  If
    they only provide a version, the release id intentionally remains NULL:
    version labels are mutable/reusable and are not an immutable identity.
    """
    if ontology_version is not None:
        return ontology_version, ontology_release_id
    identity = runtime_release_identity(db, ontology_id)
    if identity is not None:
        return identity.version, ontology_release_id or identity.id
    return runtime_release_version(db, ontology_id), ontology_release_id


def fact_order_clause():
    """事实的确定性时间序（新→旧）。seq 在同一 (instance, property) 链内单调递增，
    解决同一批次 recorded_at 相同、uuid 主键排序随机的问题。"""
    return (PropertyFact.recorded_at.desc(), PropertyFact.seq.desc(), PropertyFact.id.desc())


def record_property_facts(
    db: Session,
    *,
    ontology_id: str,
    instance_id: str,
    object_type_id: Optional[str],
    old_props: Optional[dict],
    new_props: dict,
    source: str,
    actor_id: Optional[str] = None,
    caused_by: Optional[str] = None,
    kind: str = "property",
    derived_from: Optional[list[str]] = None,
    confidence: Optional[float] = None,
    ontology_version: Optional[str] = None,
    ontology_release_id: Optional[str] = None,
) -> list[PropertyFact]:
    """对比新旧属性，把发生变化的属性逐条追加为事实。返回新追加的事实列表。

    old_props 为 None 表示实例新建——所有属性都是首个事实（无 supersedes）。
    只追加，不 commit；由调用方的事务统一提交（会 flush 以拿到事实 id）。
    """
    if old_props is None:
        changed = [(k, v) for k, v in (new_props or {}).items()]
    else:
        changed = [(k, v) for k, v in (new_props or {}).items()
                   if k not in old_props or old_props.get(k) != v]
    if not changed:
        return []
    release_version, release_id = _runtime_lineage(
        db, ontology_id, ontology_version, ontology_release_id)

    # 每个变更属性当前最新的事实 —— 作为 supersedes 指针与 seq 基准
    changed_keys = [k for k, _ in changed]
    latest: dict[str, PropertyFact] = {}
    rows = (db.query(PropertyFact)
            .filter(PropertyFact.ontology_id == ontology_id,
                    PropertyFact.instance_id == instance_id,
                    PropertyFact.property_name.in_(changed_keys))
            .order_by(*fact_order_clause())
            .all())
    for r in rows:
        latest.setdefault(r.property_name, r)

    created: list[PropertyFact] = []
    for key, value in changed:
        prev = latest.get(key)
        fact = PropertyFact(
            ontology_id=ontology_id,
            instance_id=instance_id,
            object_type_id=object_type_id,
            property_name=key,
            value=_wrap(value),
            kind=kind,
            source=source,
            actor_id=actor_id,
            caused_by=caused_by,
            supersedes_id=(prev.id if (prev is not None and old_props is not None) else None),
            derived_from=list(derived_from) if derived_from else None,
            confidence=confidence,
            ontology_version=release_version,
            ontology_release_id=release_id,
            seq=(prev.seq or 0) + 1 if prev is not None else 1,
        )
        db.add(fact)
        created.append(fact)
    if created:
        db.flush()  # 拿到 id，供派生事实的 derived_from 引用
    return created


def _latest_fact(db: Session, ontology_id: str, instance_id: str,
                 property_name: str) -> Optional[PropertyFact]:
    return (db.query(PropertyFact)
            .filter(PropertyFact.ontology_id == ontology_id,
                    PropertyFact.instance_id == instance_id,
                    PropertyFact.property_name == property_name)
            .order_by(*fact_order_clause())
            .first())


def record_link_fact(
    db: Session,
    *,
    ontology_id: str,
    link_instance_id: str,
    link_type_id: Optional[str],
    exists: bool,
    source: str,
    actor_id: Optional[str] = None,
    caused_by: Optional[str] = None,
    ontology_version: Optional[str] = None,
    ontology_release_id: Optional[str] = None,
) -> Optional[PropertyFact]:
    """链接存在性事实（kind='link'）。建链 exists=True，删链 exists=False 并
    supersede 建链事实——关系变化从此可时态回放。同值幂等（不重复追加）。"""
    prev = _latest_fact(db, ontology_id, link_instance_id, "exists")
    if prev is not None and (prev.value or {}).get("v") == exists:
        return None  # 幂等：状态没变不追加
    release_version, release_id = _runtime_lineage(
        db, ontology_id, ontology_version, ontology_release_id)
    fact = PropertyFact(
        ontology_id=ontology_id,
        instance_id=link_instance_id,
        object_type_id=link_type_id,
        property_name="exists",
        value=_wrap(exists),
        kind="link",
        source=source,
        actor_id=actor_id,
        caused_by=caused_by,
        ontology_version=release_version,
        ontology_release_id=release_id,
        supersedes_id=prev.id if prev is not None else None,
        seq=(prev.seq or 0) + 1 if prev is not None else 1,
    )
    db.add(fact)
    db.flush()
    return fact


def record_object_tombstone(
    db: Session,
    *,
    ontology_id: str,
    instance_id: str,
    object_type_id: Optional[str],
    source: str,
    actor_id: Optional[str] = None,
    caused_by: Optional[str] = None,
    ontology_version: Optional[str] = None,
    ontology_release_id: Optional[str] = None,
) -> Optional[PropertyFact]:
    """实例删除墓碑（kind='object'，exists=False）。投影硬删后，
    事实流仍能回答"它存在过、何时被谁删除"。幂等。"""
    prev = _latest_fact(db, ontology_id, instance_id, "exists")
    if prev is not None and (prev.value or {}).get("v") is False:
        return None
    release_version, release_id = _runtime_lineage(
        db, ontology_id, ontology_version, ontology_release_id)
    fact = PropertyFact(
        ontology_id=ontology_id,
        instance_id=instance_id,
        object_type_id=object_type_id,
        property_name="exists",
        value=_wrap(False),
        kind="object",
        source=source,
        actor_id=actor_id,
        caused_by=caused_by,
        ontology_version=release_version,
        ontology_release_id=release_id,
        supersedes_id=prev.id if prev is not None else None,
        seq=(prev.seq or 0) + 1 if prev is not None else 1,
    )
    db.add(fact)
    db.flush()
    return fact


def record_absence_fact(
    db: Session,
    *,
    ontology_id: str,
    subject_id: str,
    empty: bool,
    scanned: int,
    source: str,
    detail: Optional[dict] = None,
    ontology_version: Optional[str] = None,
    ontology_release_id: Optional[str] = None,
) -> Optional[PropertyFact]:
    """缺席事实（kind='absence'）：连"没有"也要溯源。

    对齐演示语义（f008: BackupCrew(CA1234) = ∅, scanned_facts: 42）：
    常驻查询（哨兵）每次评估都会扫描候选集，查询结果为空/非空的**状态翻转**
    被冻结为事实——将来即使数据补上了，也能回放"当时确实查过、确实是空的，
    结论是基于这个缺席推出的"。同状态不重复追加（边沿语义，防止定时扫描刷屏）。

    subject_id 为查询主体（哨兵 id）；value = {empty, scanned, ...detail}。
    """
    prev = _latest_fact(db, ontology_id, subject_id, "query_result")
    prev_empty = None
    if prev is not None:
        pv = (prev.value or {}).get("v")
        if isinstance(pv, dict):
            prev_empty = pv.get("empty")
    if prev_empty is not None and prev_empty == empty:
        return None  # 状态未翻转，不追加
    payload: dict = {"empty": empty, "scanned": scanned}
    if detail:
        payload.update(detail)
    release_version, release_id = _runtime_lineage(
        db, ontology_id, ontology_version, ontology_release_id)
    fact = PropertyFact(
        ontology_id=ontology_id,
        instance_id=subject_id,
        object_type_id=None,
        property_name="query_result",
        value=_wrap(payload),
        kind="absence",
        source=source,
        ontology_version=release_version,
        ontology_release_id=release_id,
        supersedes_id=prev.id if prev is not None else None,
        seq=(prev.seq or 0) + 1 if prev is not None else 1,
    )
    db.add(fact)
    db.flush()
    return fact


def record_decision_fact(
    db: Session,
    *,
    ontology_id: str,
    action_log_id: str,
    decision: str,
    source: str,
    actor_id: Optional[str],
    reason: Optional[str] = None,
    ontology_version: Optional[str] = None,
    ontology_release_id: Optional[str] = None,
) -> PropertyFact:
    """HITL 决策事实（kind='decision'）：批准与拒绝都要记录、都可回放。
    subject=动作执行日志，predicate=decision，value=APPROVED/REJECTED。"""
    prev = _latest_fact(db, ontology_id, action_log_id, "decision")
    release_version, release_id = _runtime_lineage(
        db, ontology_id, ontology_version, ontology_release_id)
    fact = PropertyFact(
        ontology_id=ontology_id,
        instance_id=action_log_id,
        object_type_id=None,
        property_name="decision",
        value=_wrap({"decision": decision, "reason": reason} if reason else decision),
        kind="decision",
        source=source,
        actor_id=actor_id,
        ontology_version=release_version,
        ontology_release_id=release_id,
        supersedes_id=prev.id if prev is not None else None,
        seq=(prev.seq or 0) + 1 if prev is not None else 1,
    )
    db.add(fact)
    db.flush()
    return fact
