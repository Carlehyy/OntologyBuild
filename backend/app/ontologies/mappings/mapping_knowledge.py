"""映射知识库：数据飞轮的沉淀与检索层。

只收人工保存过的映射（harvest），LLM 未确认产出永不入库，防止飞轮被污染。
锚定点用语义名（object_name/property_name）而非本体内部 id，保证跨本体可复用；
检索命中后仍需过人工确认队列，跨本体语义串扰由确认层吸收。
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.ontologies.mappings.models import MappingKnowledgeEntry
from app.ontologies.mappings.suggestion_candidates import (
    normal_type,
    normalize_token,
    types_compatible,
)

logger = logging.getLogger(__name__)


def _dataset_column_docs(db: Session, dataset_id: str) -> dict[str, dict]:
    """湖列契约（复用 /datasets/{id}/schema 同一份权威）；失败返回空。"""
    from app.data_channel.datasets.query_service import get_schema

    try:
        payload = get_schema(dataset_id, db)
    except Exception:
        logger.info("mapping knowledge: schema unavailable for %s", dataset_id)
        return {}
    return {
        str(col["name"]): col
        for col in (payload.get("columns") or [])
        if isinstance(col, dict) and col.get("name")
    }


def harvest_snapshot_mappings(db: Session, snapshot: dict) -> int:
    """把快照中人工保存过的对象映射回流为知识条目（幂等 upsert）。

    返回 upsert 条数。列已从湖中删除或 schema 不可用时就地跳过——
    无法核实显示名与类型的知识不入库，保持知识库高精度。
    """
    mappings = (snapshot or {}).get("mappings") or []
    schema_cache: dict[str, dict[str, dict]] = {}
    touched = 0
    for mapping in mappings:
        if not isinstance(mapping, dict):
            continue
        dataset_id = mapping.get("curated_dataset_id") or mapping.get("curatedDatasetId")
        object_name = str(
            mapping.get("entity_class") or mapping.get("entityClass") or ""
        ).strip()
        field_mapping = mapping.get("field_mapping") or mapping.get("fieldMapping") or {}
        if not dataset_id or not object_name or not isinstance(field_mapping, dict):
            continue
        if dataset_id not in schema_cache:
            schema_cache[dataset_id] = _dataset_column_docs(db, str(dataset_id))
        column_docs = schema_cache[dataset_id]
        if not column_docs:
            continue
        for column, prop in field_mapping.items():
            if str(column).startswith("__") or not isinstance(prop, str) or not prop.strip():
                continue
            doc = column_docs.get(str(column))
            if doc is None:
                continue
            touched += _upsert_entry(
                db,
                column_key=normalize_token(column),
                display_name=str(doc.get("display_name") or "").strip(),
                col_type=normal_type(doc.get("type")),
                object_name=object_name,
                property_name=prop.strip(),
            )
    if touched:
        db.commit()
    return touched


def _upsert_entry(
    db: Session,
    *,
    column_key: str,
    display_name: str,
    col_type: str,
    object_name: str,
    property_name: str,
) -> int:
    if not column_key or not property_name:
        return 0
    entry = db.query(MappingKnowledgeEntry).filter(
        MappingKnowledgeEntry.column_key == column_key,
        MappingKnowledgeEntry.display_name == display_name,
        MappingKnowledgeEntry.col_type == col_type,
        MappingKnowledgeEntry.object_name == object_name,
        MappingKnowledgeEntry.property_name == property_name,
    ).first()
    now = datetime.now(timezone.utc)
    if entry is not None:
        entry.confirm_count = (entry.confirm_count or 0) + 1
        entry.last_confirmed_at = now
    else:
        db.add(MappingKnowledgeEntry(
            column_key=column_key,
            display_name=display_name,
            col_type=col_type,
            object_name=object_name,
            property_name=property_name,
            confirm_count=1,
            last_confirmed_at=now,
        ))
    return 1


def lookup(
    db: Session,
    column_doc: dict,
    property_index: dict[tuple[str, str], str],
    *,
    limit: int = 3,
) -> list[MappingKnowledgeEntry]:
    """检索可复用的历史映射。

    property_index: {(object_name, property_name): 归一化前的属性类型}，
    仅保留在当前本体清单中存在且类型兼容的锚定点。
    """
    column_key = normalize_token(column_doc.get("name"))
    display_name = str(column_doc.get("display_name") or "").strip()
    if not column_key and not display_name:
        return []
    query = db.query(MappingKnowledgeEntry).filter(
        MappingKnowledgeEntry.column_key == column_key
    )
    entries = list(query.all())
    if display_name:
        entries.extend(
            db.query(MappingKnowledgeEntry).filter(
                MappingKnowledgeEntry.display_name == display_name,
                MappingKnowledgeEntry.column_key != column_key,
            ).all()
        )
    valid = []
    for entry in entries:
        anchor_type = property_index.get((entry.object_name, entry.property_name))
        if anchor_type is None:
            continue
        if not types_compatible(column_doc.get("type"), anchor_type):
            continue
        valid.append(entry)
    valid.sort(
        key=lambda entry: (
            entry.column_key == column_key,
            entry.confirm_count or 0,
        ),
        reverse=True,
    )
    return valid[:limit]


def few_shot_examples(
    db: Session,
    column_docs: list[dict],
    property_index: dict[tuple[str, str], str],
    *,
    limit: int = 8,
) -> list[str]:
    """与当前数据集列相关的历史映射示例行，供 LLM prompt 注入（relevance feedback）。"""
    lines: list[str] = []
    seen: set[tuple[str, str, str]] = set()
    for doc in column_docs:
        for entry in lookup(db, doc, property_index, limit=1):
            key = (str(doc.get("name")), entry.object_name, entry.property_name)
            if key in seen:
                continue
            seen.add(key)
            display = str(doc.get("display_name") or "").strip()
            label = f"{doc.get('name')}（{display}）" if display else str(doc.get("name"))
            lines.append(f'列 "{label}" → {entry.object_name}.{entry.property_name}')
            break
        if len(lines) >= limit:
            break
    return lines[:limit]
