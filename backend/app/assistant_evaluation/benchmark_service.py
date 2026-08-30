"""基准集服务 — 从评估坏例 / 手工挑选沉淀固定复评集合。

切分策略：条目按 conversation_id 稳定哈希落到 train / heldout，同一
会话无论重建多少次都落在同一侧，保证留出集不被优化迭代"见过"。
条目引用活会话（不快照轨迹）：源会话被删除时条目在复评时自然失效，
由使用方（M2 沙箱回放）负责呈现失效原因。
"""
from __future__ import annotations

import hashlib

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.assistant_evaluation.adapters import get_adapters
from app.assistant_evaluation.models import (
    AssistantEvalBenchmarkItem,
    AssistantEvalBenchmarkSet,
    AssistantEvalItem,
    AssistantEvalTask,
)
from app.assistant_evaluation.service import ServiceError
from app.assistant_evaluation.timeline import (
    EVENT_BENCHMARK_CREATED,
    EVENT_BENCHMARK_DELETED,
    EVENT_BENCHMARK_ITEM_REMOVED,
    EVENT_BENCHMARK_ITEMS_ADDED,
    ACTOR_ADMIN,
    record_event,
)

MAX_ITEMS_PER_SET = 200
HELDOUT_RATIO = 0.3
_SPLITS = ("train", "heldout")
_ORIGINS = ("manual", "badcase", "task")


def split_for(conversation_id: str, heldout_ratio: float = HELDOUT_RATIO) -> str:
    """会话到切分侧的稳定映射：重建基准集时切分可复现。"""
    digest = hashlib.md5(conversation_id.encode("utf-8")).hexdigest()
    return "heldout" if (int(digest[:8], 16) % 1000) / 1000 < heldout_ratio else "train"


def _validate_entries(db: Session, adapter, entries: list[dict]) -> list[dict]:
    """校验会话归属与字段合法性，返回去重后的标准条目。"""
    seen: set[str] = set()
    normalized: list[dict] = []
    for entry in entries:
        conversation_id = str(entry.get("conversation_id") or "").strip()
        if not conversation_id:
            raise ServiceError("基准集条目缺少 conversation_id。")
        title = adapter.get_title(db, conversation_id)
        if not title:
            raise ServiceError(
                f"会话 {conversation_id[:8]} 不属于「{adapter.label}」或已不存在。"
            )
        split = entry.get("split") or split_for(conversation_id)
        if split not in _SPLITS:
            raise ServiceError(f"非法切分标记：{split}（仅支持 train / heldout）。")
        origin = entry.get("origin") or "manual"
        if origin not in _ORIGINS:
            raise ServiceError(f"非法条目来源：{origin}。")
        if conversation_id in seen:
            continue
        seen.add(conversation_id)
        normalized.append({
            "conversation_id": conversation_id,
            "conversation_title": title,
            "split": split,
            "origin": origin,
        })
    return normalized


def create_set(db: Session, *, assistant_key: str, name: str, description: str,
               entries: list[dict] | None, created_by: str | None,
               source_task_id: str | None = None,
               actor: str = ACTOR_ADMIN) -> AssistantEvalBenchmarkSet:
    adapter = get_adapters().get(assistant_key)
    if not adapter:
        raise ServiceError(f"未知的助手类型：{assistant_key}")
    name = (name or "").strip()
    if not name:
        raise ServiceError("基准集名称不能为空。")

    normalized = _validate_entries(db, adapter, list(entries or []))
    if not normalized:
        raise ServiceError("基准集至少需要一条会话条目。")
    if len(normalized) > MAX_ITEMS_PER_SET:
        raise ServiceError(f"单个基准集最多 {MAX_ITEMS_PER_SET} 条条目。")

    row = AssistantEvalBenchmarkSet(
        assistant_key=assistant_key, name=name, description=description or "",
        source_task_id=source_task_id, created_by=created_by,
    )
    db.add(row)
    db.flush()  # 取 row.id 供条目外键使用
    for item in normalized:
        db.add(AssistantEvalBenchmarkItem(set_id=row.id, **item))
    record_event(db, event_type=EVENT_BENCHMARK_CREATED, assistant_key=assistant_key,
                 actor=actor, actor_user_id=created_by if actor == ACTOR_ADMIN else None,
                 ref_type="benchmark_set", ref_id=row.id,
                 detail={
                     "name": name,
                     "item_count": len(normalized),
                     "train_count": sum(1 for i in normalized if i["split"] == "train"),
                     "heldout_count": sum(1 for i in normalized if i["split"] == "heldout"),
                     **({"source_task_id": source_task_id} if source_task_id else {}),
                 })
    db.commit()
    db.refresh(row)
    return row


def create_from_task(db: Session, *, task_id: str, name: str | None,
                     include: str, description: str,
                     created_by: str | None) -> AssistantEvalBenchmarkSet:
    """把一次评估任务的结果沉淀为基准集（坏例优先，数据飞轮的资产入口）。"""
    task = db.query(AssistantEvalTask).filter(AssistantEvalTask.id == task_id).first()
    if task is None:
        raise ServiceError("评估任务不存在。")
    if task.status != "success":
        raise ServiceError("只能从已成功的评估任务沉淀基准集。")
    if include not in ("badcase", "all"):
        raise ServiceError("include 仅支持 badcase / all。")

    items = (
        db.query(AssistantEvalItem)
        .filter(AssistantEvalItem.task_id == task_id)
        .order_by(AssistantEvalItem.overall_score.asc().nullslast(),
                  AssistantEvalItem.created_at.asc())
        .all()
    )
    if include == "badcase":
        picked = [i for i in items if i.overall_score is not None and i.overall_score < 60]
        origin = "badcase"
    else:
        picked = [i for i in items if i.overall_score is not None]
        origin = "task"
    if not picked:
        raise ServiceError("该任务没有可沉淀的会话（未产出评分或没有坏例）。")

    adapter = get_adapters().get(task.assistant_key)
    if adapter is None:
        raise ServiceError(f"未知的助手类型：{task.assistant_key}")
    entries = [{
        "conversation_id": i.conversation_id,
        "split": split_for(i.conversation_id),
        "origin": origin,
    } for i in picked]
    fallback_name = name or f"{adapter.label} · {task.title} · {'坏例' if include == 'badcase' else '全量'}基准"
    return create_set(db, assistant_key=task.assistant_key, name=fallback_name,
                      description=description or "", entries=entries,
                      created_by=created_by, source_task_id=task.id)


def add_items(db: Session, set_id: str, entries: list[dict],
              actor_user_id: str | None) -> AssistantEvalBenchmarkSet:
    row = get_set(db, set_id)
    adapter = get_adapters().get(row.assistant_key)
    if adapter is None:
        raise ServiceError(f"未知的助手类型：{row.assistant_key}")

    existing = {i.conversation_id for i in items_of(db, row.id)}
    normalized = _validate_entries(db, adapter, entries)
    fresh = [e for e in normalized if e["conversation_id"] not in existing]
    if not fresh:
        raise ServiceError("条目已全部存在于基准集中。")
    if len(existing) + len(fresh) > MAX_ITEMS_PER_SET:
        raise ServiceError(f"单个基准集最多 {MAX_ITEMS_PER_SET} 条条目。")
    for item in fresh:
        db.add(AssistantEvalBenchmarkItem(set_id=row.id, **item))
    record_event(db, event_type=EVENT_BENCHMARK_ITEMS_ADDED, assistant_key=row.assistant_key,
                 actor=ACTOR_ADMIN, actor_user_id=actor_user_id,
                 ref_type="benchmark_set", ref_id=row.id,
                 detail={"added": len(fresh),
                         "conversation_ids": [i["conversation_id"] for i in fresh][:20]})
    db.commit()
    db.refresh(row)
    return row


def remove_item(db: Session, set_id: str, item_id: str,
                actor_user_id: str | None) -> bool:
    row = get_set(db, set_id)
    item = (
        db.query(AssistantEvalBenchmarkItem)
        .filter(AssistantEvalBenchmarkItem.id == item_id,
                AssistantEvalBenchmarkItem.set_id == set_id)
        .first()
    )
    if item is None:
        return False
    conversation_id = item.conversation_id
    db.delete(item)
    record_event(db, event_type=EVENT_BENCHMARK_ITEM_REMOVED, assistant_key=row.assistant_key,
                 actor=ACTOR_ADMIN, actor_user_id=actor_user_id,
                 ref_type="benchmark_set", ref_id=row.id,
                 detail={"conversation_id": conversation_id})
    db.commit()
    return True


def get_set(db: Session, set_id: str) -> AssistantEvalBenchmarkSet:
    row = (
        db.query(AssistantEvalBenchmarkSet)
        .filter(AssistantEvalBenchmarkSet.id == set_id)
        .first()
    )
    if row is None:
        raise ServiceError("基准集不存在。")
    return row


def items_of(db: Session, set_id: str) -> list[AssistantEvalBenchmarkItem]:
    return (
        db.query(AssistantEvalBenchmarkItem)
        .filter(AssistantEvalBenchmarkItem.set_id == set_id)
        .order_by(AssistantEvalBenchmarkItem.created_at.asc(),
                  AssistantEvalBenchmarkItem.id.asc())
        .all()
    )


def set_counts(db: Session) -> dict[str, dict[str, int]]:
    """按集合聚合条目计数：{set_id: {total, train, heldout}}。"""
    rows = (
        db.query(AssistantEvalBenchmarkItem.set_id,
                 AssistantEvalBenchmarkItem.split,
                 func.count(AssistantEvalBenchmarkItem.id))
        .group_by(AssistantEvalBenchmarkItem.set_id, AssistantEvalBenchmarkItem.split)
        .all()
    )
    counts: dict[str, dict[str, int]] = {}
    for set_id, split, count in rows:
        slot = counts.setdefault(set_id, {"total": 0, "train": 0, "heldout": 0})
        slot[split] = int(count)
        slot["total"] += int(count)
    return counts


def list_sets(db: Session, assistant_key: str | None = None) -> list[AssistantEvalBenchmarkSet]:
    query = db.query(AssistantEvalBenchmarkSet)
    if assistant_key:
        query = query.filter(AssistantEvalBenchmarkSet.assistant_key == assistant_key)
    return query.order_by(AssistantEvalBenchmarkSet.created_at.desc()).all()


def delete_set(db: Session, set_id: str, actor_user_id: str | None) -> bool:
    row = (
        db.query(AssistantEvalBenchmarkSet)
        .filter(AssistantEvalBenchmarkSet.id == set_id)
        .first()
    )
    if row is None:
        return False
    assistant_key, name = row.assistant_key, row.name
    db.delete(row)  # 条目经 CASCADE 级联删除
    record_event(db, event_type=EVENT_BENCHMARK_DELETED, assistant_key=assistant_key,
                 actor=ACTOR_ADMIN, actor_user_id=actor_user_id,
                 ref_type="benchmark_set", ref_id=set_id,
                 detail={"name": name})
    db.commit()
    return True
