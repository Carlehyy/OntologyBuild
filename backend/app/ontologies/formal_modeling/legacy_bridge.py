"""
正规本体 → 旧模型 读桥 (Legacy Read Bridge)
=============================================

把 Palantir 风格的正规本体 (ObjectType / ObjectInstance / LinkType /
LinkInstance) 投影成旧的扁平 Entity / Relation 形状，供尚未迁移的 v1
读端 (EntitiesTab / EnhancedGraphTab / graph API) 使用。

目的：消除"数据双轨"——正规本体是**唯一可信数据源**，旧 tab 看到的
数据与图谱编辑器完全一致。当正规本体存在数据时，v1 读端走本桥；
否则回退到旧表（向后兼容历史数据）。

只读：不写旧表。写入一律走正规本体 (formal router)。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def has_formal_instances(db, ontology_id: str) -> bool:
    """该本体是否已有正规本体实例数据。"""
    from app.models.ontology_formal import ObjectInstance
    return db.query(ObjectInstance).filter(
        ObjectInstance.ontology_id == ontology_id,
    ).first() is not None


def has_formal_object_types(db, ontology_id: str) -> bool:
    """该本体是否已有正规本体对象类型定义。"""
    from app.models.ontology_formal import ObjectType
    return db.query(ObjectType).filter(
        ObjectType.ontology_id == ontology_id,
    ).first() is not None


def _dt_iso(value) -> str:
    if value is None:
        return _now_iso()
    if isinstance(value, str):
        return value
    return value.isoformat()


def _object_type_properties_for_legacy_detail(ot) -> dict:
    """把 ObjectType.properties 投影为旧 Entity 详情页可读的业务属性。

    旧详情页会直接渲染 Entity.properties 的键值对，所以这里不能放
    icon/color/primaryKey 等 ObjectType 元信息；只放图谱编辑器中定义的
    业务属性，避免把建模元数据误展示为实体属性。
    """
    out: dict[str, str] = {}
    for prop in (ot.properties or []):
        if not isinstance(prop, dict):
            continue
        name = prop.get("name") or prop.get("id")
        if not name:
            continue
        display_name = prop.get("displayName") or prop.get("display_name") or name
        prop_type = prop.get("type")
        required = "必填" if prop.get("required") else "可选"
        binding = prop.get("dataBinding") or {}
        source_column = binding.get("sourceColumn") or binding.get("source_column") or name
        source_text = ""
        if source_column:
            inferred = "，推断" if binding.get("inferred", not bool(binding)) else ""
            source_text = f"，来源: {source_column}{inferred}"
        out[str(name)] = f"{display_name} ({prop_type or 'unknown'}, {required}{source_text})"
    return out


def _object_type_to_entity_dict(ot, ontology_id: str) -> dict:
    properties = _object_type_properties_for_legacy_detail(ot)
    return {
        "id": ot.id,
        "ontology_id": ontology_id,
        "name_cn": ot.display_name or ot.name,
        "name_en": ot.name,
        "name_abbr": None,
        "snomed_id": None,
        "canonical_id": ot.id,
        "type": "ObjectType",
        "description": ot.description,
        "properties": properties,
        "confidence": 1.0,
        "version": "v0.1",
        "created_at": _dt_iso(ot.created_at),
        "updated_at": _dt_iso(ot.updated_at),
    }


def list_entities_from_formal_object_types(db, ontology_id: str) -> list[dict]:
    """把 ObjectType 映射为 v1 Entity 形状的 dict 列表。"""
    from app.models.ontology_formal import ObjectType

    object_types = db.query(ObjectType).filter(
        ObjectType.ontology_id == ontology_id,
    ).all()
    return [_object_type_to_entity_dict(ot, ontology_id) for ot in object_types]


def get_entity_from_formal_object_type(db, ontology_id: str, entity_id: str) -> dict | None:
    """按 ObjectType id 取单个（v1 Entity 形状）。"""
    from app.models.ontology_formal import ObjectType

    ot = db.query(ObjectType).filter(
        ObjectType.id == entity_id,
        ObjectType.ontology_id == ontology_id,
    ).first()
    if not ot:
        return None
    return _object_type_to_entity_dict(ot, ontology_id)


def _instance_display_name(props: dict, pk_name: str | None) -> str:
    """从实例属性里挑一个展示名。"""
    for key in (pk_name, "name", "name_cn", "title", "displayName", "id"):
        if key and props.get(key) not in (None, ""):
            return str(props[key])
    return "(未命名)"


def list_entities_from_formal(db, ontology_id: str) -> list[dict]:
    """把 ObjectInstance 映射为 v1 Entity 形状的 dict 列表。"""
    from app.models.ontology_formal import ObjectType, ObjectInstance

    ots = {ot.id: ot for ot in db.query(ObjectType).filter(
        ObjectType.ontology_id == ontology_id).all()}
    # ObjectType.id → 主键属性 name
    pk_name_of: dict[str, str | None] = {}
    for ot in ots.values():
        pk_name = None
        for p in (ot.properties or []):
            if p.get("id") == ot.primary_key:
                pk_name = p.get("name")
                break
        pk_name_of[ot.id] = pk_name

    instances = db.query(ObjectInstance).filter(
        ObjectInstance.ontology_id == ontology_id).all()

    out: list[dict] = []
    for inst in instances:
        ot = ots.get(inst.object_type_id)
        props = dict(inst.properties or {})
        pk_name = pk_name_of.get(inst.object_type_id)
        display = _instance_display_name(props, pk_name)
        # computed 派生属性合并进 properties（只读展示）
        merged_props = dict(props)
        if inst.computed:
            merged_props["_computed"] = inst.computed
        out.append({
            "id": inst.id,
            "ontology_id": ontology_id,
            "name_cn": display,
            "name_en": str(props.get("name_en") or props.get("titleEn") or display),
            "name_abbr": props.get("name_abbr"),
            "snomed_id": props.get("snomed_id"),
            "canonical_id": inst.external_id,
            "type": ot.name if ot else "Object",
            "description": props.get("description") or props.get("summary"),
            "properties": merged_props,
            "confidence": 1.0,
            "version": "v0.1",
            "created_at": _dt_iso(inst.created_at),
            "updated_at": _dt_iso(inst.updated_at),
        })
    return out


def get_entity_from_formal(db, ontology_id: str, entity_id: str) -> dict | None:
    """按实例 id 取单个（v1 Entity 形状）。"""
    for e in list_entities_from_formal(db, ontology_id):
        if e["id"] == entity_id:
            return e
    return None


def has_formal_functions(db, ontology_id: str) -> bool:
    from app.models.ontology_formal import OntologyFunction
    return db.query(OntologyFunction).filter(
        OntologyFunction.ontology_id == ontology_id).first() is not None


def has_formal_actions(db, ontology_id: str) -> bool:
    from app.models.ontology_formal import ActionType
    return db.query(ActionType).filter(
        ActionType.ontology_id == ontology_id).first() is not None


def list_logic_from_formal(db, ontology_id: str) -> list[dict]:
    """把 OntologyFunction 映射为 v1 LogicRule 形状（供 LogicTab 只读展示）。"""
    from app.models.ontology_formal import OntologyFunction, ObjectType

    ot_name = {ot.id: ot.name for ot in db.query(ObjectType).filter(
        ObjectType.ontology_id == ontology_id).all()}
    fns = db.query(OntologyFunction).filter(
        OntologyFunction.ontology_id == ontology_id).all()
    out = []
    for fn in fns:
        linked = [ot_name[fn.target_object_type_id]] if fn.target_object_type_id in ot_name else []
        out.append({
            "id": fn.id,
            "ontology_id": ontology_id,
            "name_cn": fn.display_name or fn.name,
            "name_en": fn.name,
            "description": fn.description,
            "formula": (fn.body or "")[:500],
            "confidence": 1.0,
            "version": "v0.1",
            "enabled": bool(fn.enabled),
            "status": "published",
            "linked_entities": linked,
            "created_at": fn.created_at.isoformat() if not isinstance(fn.created_at, str) else fn.created_at,
            "updated_at": fn.updated_at.isoformat() if not isinstance(fn.updated_at, str) else fn.updated_at,
        })
    return out


def list_actions_from_formal(db, ontology_id: str) -> list[dict]:
    """把 ActionType 映射为 v1 Action 形状（供 ActionsTab 只读展示）。"""
    from app.models.ontology_formal import ActionType, ObjectType

    ot_name = {ot.id: ot.name for ot in db.query(ObjectType).filter(
        ObjectType.ontology_id == ontology_id).all()}
    acts = db.query(ActionType).filter(
        ActionType.ontology_id == ontology_id).all()
    out = []
    for a in acts:
        linked = [ot_name[a.object_type_id]] if a.object_type_id in ot_name else []
        rule_summary = "; ".join(
            str(r.get("description") or r.get("name") or "") for r in (a.rules or [])
        )[:500]
        out.append({
            "id": a.id,
            "ontology_id": ontology_id,
            "name_cn": a.display_name or a.name,
            "name_en": a.name,
            "description": a.description,
            "execution_rule": rule_summary,
            "function_code": None,
            "linked_entities": linked,
            "linked_logic_ids": [a.validation_function_id] if a.validation_function_id else [],
            "confidence": 1.0,
            "version": "v0.1",
            "created_at": a.created_at.isoformat() if not isinstance(a.created_at, str) else a.created_at,
            "updated_at": a.updated_at.isoformat() if not isinstance(a.updated_at, str) else a.updated_at,
        })
    return out


def graph_from_formal(db, ontology_id: str, limit: int = 500) -> dict:
    """
    把正规本体映射为 v1 graph API 的 {nodes, edges, meta} 形状。

    nodes 用 ObjectInstance（带 object_type 分组），edges 用 LinkInstance。
    """
    from app.models.ontology_formal import (
        ObjectType, ObjectInstance, LinkType, LinkInstance,
    )

    ots = {ot.id: ot for ot in db.query(ObjectType).filter(
        ObjectType.ontology_id == ontology_id).all()}
    pk_name_of: dict[str, str | None] = {}
    for ot in ots.values():
        pk_name = None
        for p in (ot.properties or []):
            if p.get("id") == ot.primary_key:
                pk_name = p.get("name")
                break
        pk_name_of[ot.id] = pk_name

    instances = db.query(ObjectInstance).filter(
        ObjectInstance.ontology_id == ontology_id).limit(limit).all()
    inst_ids = {i.id for i in instances}

    nodes = []
    for inst in instances:
        ot = ots.get(inst.object_type_id)
        props = dict(inst.properties or {})
        nodes.append({
            "id": inst.id,
            "label": _instance_display_name(props, pk_name_of.get(inst.object_type_id)),
            "type": ot.name if ot else "Object",
            "group": ot.display_name if ot else "Object",
            "color": ot.color if ot else None,
            "properties": props,
        })

    lts = {lt.id: lt for lt in db.query(LinkType).filter(
        LinkType.ontology_id == ontology_id).all()}
    links = db.query(LinkInstance).filter(
        LinkInstance.ontology_id == ontology_id).all()
    edges = []
    for li in links:
        if li.source_object_id not in inst_ids or li.target_object_id not in inst_ids:
            continue
        lt = lts.get(li.link_type_id)
        edges.append({
            "id": li.id,
            "source": li.source_object_id,
            "target": li.target_object_id,
            "type": lt.name if lt else "RELATED_TO",
            "label": lt.display_name if lt else "",
            "properties": dict(li.properties or {}),
        })

    return {
        "nodes": nodes,
        "edges": edges,
        "meta": {
            "source": "formal_ontology",
            "object_types": len(ots),
            "node_count": len(nodes),
            "edge_count": len(edges),
        },
    }
