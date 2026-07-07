"""
哨兵引擎 (Sentinel Engine) — 三种执行入口，统一汇入评估器

  1. run_manual    手动触发：评估本体下全部启用的哨兵
  2. run_for_change 变化驱动：某对象类型属性变化 → 挑出引用该类型的哨兵 → 逐个评估
  3. run_scheduled  定期扫描：到达各哨兵的扫描间隔 → 逐个评估，更新 last_scanned_at
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from app.models.sentinel import Sentinel
from app.services.sentinel.evaluator import evaluate_sentinel


def _now():
    return datetime.now(timezone.utc)


def _summary(firings) -> dict:
    return {
        "evaluated": len(firings),
        "fired": sum(1 for f in firings if f.status == "fired"),
        "no_change": sum(1 for f in firings if f.status == "no_change"),
        "no_match": sum(1 for f in firings if f.status == "no_match"),
        "firings": [{"sentinelId": f.sentinel_id, "sentinelName": f.sentinel_name,
                     "status": f.status, "matchCount": f.match_count,
                     "entered": f.entered or [], "left": f.left or [],
                     "actionResults": f.action_results} for f in firings],
    }


def run_manual(db: Session, ontology_id: str) -> dict:
    """手动触发：全量评估本体所有启用哨兵。"""
    sentinels = db.query(Sentinel).filter(
        Sentinel.ontology_id == ontology_id,
        Sentinel.enabled == True,  # noqa: E712
    ).all()
    firings = [evaluate_sentinel(db, ontology_id, s, "manual") for s in sentinels]
    return _summary(firings)


def run_for_save(db: Session, ontology_id: str) -> dict:
    """整体保存后触发：评估该本体所有启用且开启 on_change 的哨兵。

    图谱编辑器的实例变更只有保存时才落库——保存即"变化到达"时刻。
    边沿触发语义(SentinelMatchState)保证重复评估幂等，不会重复放炮。
    """
    sentinels = db.query(Sentinel).filter(
        Sentinel.ontology_id == ontology_id,
        Sentinel.enabled == True,  # noqa: E712
        Sentinel.on_change == True,  # noqa: E712
    ).all()
    firings = [evaluate_sentinel(db, ontology_id, s, "change") for s in sentinels]
    return _summary(firings)


def run_for_change(db: Session, ontology_id: str, object_type_id: str,
                   changed_keys: Optional[list] = None) -> dict:
    """变化驱动：挑出 bindings 引用了该对象类型、且开启 on_change 的哨兵并评估。"""
    sentinels = db.query(Sentinel).filter(
        Sentinel.ontology_id == ontology_id,
        Sentinel.enabled == True,  # noqa: E712
        Sentinel.on_change == True,  # noqa: E712
    ).all()
    # 仅挑选监听了该对象类型的哨兵(属性级可在此进一步收窄)
    affected = [s for s in sentinels
                if any(b.get("objectTypeId") == object_type_id for b in (s.bindings or []))]
    firings = [evaluate_sentinel(db, ontology_id, s, "change") for s in affected]
    return _summary(firings)


def run_for_link_change(db: Session, ontology_id: str) -> dict:
    """链接拓扑变化驱动：链接实例增删对声明了 links 约束的跨对象哨兵是"变化"。
    只评估 on_change 且带链接约束的哨兵（无链接约束的由属性 CDC 覆盖）。"""
    sentinels = db.query(Sentinel).filter(
        Sentinel.ontology_id == ontology_id,
        Sentinel.enabled == True,  # noqa: E712
        Sentinel.on_change == True,  # noqa: E712
    ).all()
    affected = [s for s in sentinels if (s.links or [])]
    firings = [evaluate_sentinel(db, ontology_id, s, "change") for s in affected]
    return _summary(firings)


def run_scheduled(db: Session) -> dict:
    """定期扫描：评估所有到达扫描间隔的哨兵(跨本体)，更新 last_scanned_at。"""
    now = _now()
    sentinels = db.query(Sentinel).filter(
        Sentinel.enabled == True,  # noqa: E712
        Sentinel.on_schedule == True,  # noqa: E712
    ).all()
    due = []
    for s in sentinels:
        interval = timedelta(seconds=max(s.scan_interval_seconds or 300, 5))
        last = s.last_scanned_at
        if last is not None and last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        if last is None or (now - last) >= interval:
            due.append(s)
    firings = []
    for s in due:
        firings.append(evaluate_sentinel(db, s.ontology_id, s, "schedule"))
        s.last_scanned_at = now
    db.commit()
    return _summary(firings)
