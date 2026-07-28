"""
数据采集器 API — /api/v2/collectors/...

平台核心能力之一：从真实数据源采集数据，落地为本体对象实例。
目前内置 AI HOT 采集器（真实 AI 资讯）。
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel, model_validator
from typing import Optional, Any

from app.deps import get_db, get_current_user
from app.models.ontology import OntologyProject
from app.models.ontology_formal import ObjectType, ObjectInstance, LinkType, LinkInstance
from app.ontologies.formal_modeling.facts import (
    record_link_fact,
    record_object_presence,
    record_property_facts,
)
from app.ontologies.formal_modeling.derived import recompute_instance_derived
from app.ontologies.mappings.mapping_service import _ontology_build_lock
from app.ontologies.access import ontology_access_guard
from app.services.collectors import aihot

router = APIRouter()


def _ok(data):
    return {"data": data}


@router.get("")
def list_collectors(_=Depends(get_current_user)):
    """可用采集器清单"""
    return _ok([
        {
            "id": "aihot",
            "name": "AI HOT 资讯",
            "description": "从 aihot.virxact.com 采集真实 AI 行业资讯，落地为「资讯条目」对象实例",
            "source": "https://aihot.virxact.com",
            "modes": ["selected", "all"],
            "categories": list(aihot.CATEGORY_LABELS.items()),
            "icon": "📰",
        }
    ])


@router.get("/aihot/preview")
def preview_aihot(mode: str = "selected", category: Optional[str] = None,
                  take: int = 10, q: Optional[str] = None,
                  _=Depends(get_current_user)):
    """预览 AI HOT 数据（不落库）"""
    try:
        data = aihot.fetch_items(mode=mode, category=category, take=take, q=q)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"采集源不可用: {e}")
    return _ok(data)


class CollectRequest(BaseModel):
    object_type_id: str            # 落地到哪个对象类型
    mode: str = "selected"
    category: Optional[str] = None
    take: int = 50
    q: Optional[str] = None
    # 可选：自动按 source 字段建「信源」实例并连边
    source_object_type_id: Optional[str] = None
    source_link_type_id: Optional[str] = None

    @model_validator(mode="after")
    def validate_source_type_pair(self):
        self.source_object_type_id = (
            (self.source_object_type_id or "").strip() or None
        )
        self.source_link_type_id = (
            (self.source_link_type_id or "").strip() or None
        )
        if ((self.source_object_type_id is None)
                != (self.source_link_type_id is None)):
            raise ValueError(
                "source_object_type_id 与 source_link_type_id "
                "必须同时提供或同时省略"
            )
        return self


@router.post("/aihot/collect/{ontology_id}")
def collect_aihot(ontology_id: str, body: CollectRequest,
                  db: Session = Depends(get_db),
                  _access=Depends(ontology_access_guard)):
    """采集 AI HOT 数据 → 落地为对象实例（按 external_id 去重）"""
    # Remote I/O must not hold the ontology projection lock.  Everything read
    # before this fetch is untrusted; release/type state is resolved only after
    # acquiring the lock below.
    try:
        data = aihot.fetch_items(
            mode=body.mode, category=body.category, take=body.take, q=body.q)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"采集源不可用: {e}")
    # Runtime writers and promotion use one advisory→project-row lock order.
    # Holding the wider lock through commit guarantees a collector that began
    # before promotion is visible to its fail-closed guard, while one that
    # begins later observes the newly activated release.
    with _ontology_build_lock(db, ontology_id):
        return _collect_aihot_locked(ontology_id, body, data, db)


def _collect_aihot_locked(
    ontology_id: str, body: CollectRequest, data: dict, db: Session,
):
    onto = db.query(OntologyProject).filter(
        OntologyProject.id == ontology_id,
    ).with_for_update().first()
    if not onto:
        raise HTTPException(404, "Ontology not found")
    from app.ontologies.release_context import runtime_release_identity
    release_identity = runtime_release_identity(db, ontology_id)
    ontology_release_id = (
        release_identity.id
        if release_identity and (onto.status or "") == "published"
        else None
    )

    ot = db.query(ObjectType).filter(ObjectType.id == body.object_type_id,
                                     ObjectType.ontology_id == ontology_id).first()
    if not ot:
        raise HTTPException(404, "目标对象类型不存在")

    source_ot = None
    source_lt = None
    if body.source_object_type_id and body.source_link_type_id:
        source_ot = db.query(ObjectType).filter(
            ObjectType.id == body.source_object_type_id,
            ObjectType.ontology_id == ontology_id,
        ).first()
        if not source_ot:
            raise HTTPException(422, detail={
                "code": "collector_source_object_type_invalid",
                "message": "信源对象类型不存在或不属于当前本体",
            })

        source_lt = db.query(LinkType).filter(
            LinkType.id == body.source_link_type_id,
            LinkType.ontology_id == ontology_id,
        ).first()
        if not source_lt:
            raise HTTPException(422, detail={
                "code": "collector_source_link_type_invalid",
                "message": "信源关系类型不存在或不属于当前本体",
            })
        if (
            source_lt.source_object_type_id != ot.id
            or source_lt.target_object_type_id != source_ot.id
        ):
            raise HTTPException(422, detail={
                "code": "collector_source_link_endpoints_invalid",
                "message": (
                    "信源关系类型的起点必须是采集目标对象类型，"
                    "终点必须是信源对象类型"
                ),
                "expected": {
                    "source_object_type_id": ot.id,
                    "target_object_type_id": source_ot.id,
                },
                "actual": {
                    "source_object_type_id": source_lt.source_object_type_id,
                    "target_object_type_id": source_lt.target_object_type_id,
                },
            })

    items = data.get("items", [])
    created, updated = 0, 0
    source_cache: dict[str, str] = {}  # source name -> instance id

    # 预载已有 source 实例
    if source_ot:
        source_query = db.query(ObjectInstance).filter(
                ObjectInstance.ontology_id == ontology_id,
                ObjectInstance.object_type_id == source_ot.id)
        if ontology_release_id is not None:
            source_query = source_query.filter(
                ObjectInstance.ontology_release_id == ontology_release_id)
        for si in source_query.all():
            nm = (si.properties or {}).get("name")
            if nm:
                source_cache[nm] = si.id

    for item in items:
        ext = item.get("id")
        props = aihot.item_to_properties(item)

        existing_query = db.query(ObjectInstance).filter(
            ObjectInstance.ontology_id == ontology_id,
            ObjectInstance.object_type_id == ot.id,
            ObjectInstance.external_id == ext)
        if ontology_release_id is not None:
            existing_query = existing_query.filter(
                ObjectInstance.ontology_release_id == ontology_release_id)
        existing = existing_query.first()

        if existing:
            old_props = dict(existing.properties or {})
            existing.properties = props
            updated += 1
            inst = existing
        else:
            old_props = None
            inst = ObjectInstance(ontology_id=ontology_id,
                                  ontology_release_id=ontology_release_id,
                                  object_type_id=ot.id,
                                  properties=props, source="collector", external_id=ext)
            db.add(inst); db.flush()
            record_object_presence(
                db, ontology_id=ontology_id, instance_id=inst.id,
                object_type_id=inst.object_type_id,
                source="collector://aihot",
            )
            created += 1

        # 采集写入同样进入事实流（出处=collector://aihot），并触发派生重算——
        # 否则采集的数据没有溯源、computed 属性一直是空、哨兵看不到"变化前后"
        new_facts = record_property_facts(
            db, ontology_id=ontology_id, instance_id=inst.id,
            object_type_id=inst.object_type_id,
            old_props=old_props, new_props=props,
            source="collector://aihot")
        if new_facts:
            recompute_instance_derived(
                db, ontology_id=ontology_id, instance=inst, trigger_facts=new_facts)

        # 自动建 source 实例 + 连边
        if source_ot and source_lt:
            sname = item.get("source")
            if sname:
                sid = source_cache.get(sname)
                if not sid:
                    sinst = ObjectInstance(
                        ontology_id=ontology_id,
                        ontology_release_id=ontology_release_id,
                        object_type_id=source_ot.id,
                        properties={"name": sname}, source="collector", external_id=f"src:{sname}")
                    db.add(sinst); db.flush()
                    sid = sinst.id
                    source_cache[sname] = sid
                    record_object_presence(
                        db, ontology_id=ontology_id, instance_id=sid,
                        object_type_id=source_ot.id,
                        source="collector://aihot",
                    )
                    record_property_facts(
                        db, ontology_id=ontology_id, instance_id=sid,
                        object_type_id=source_ot.id,
                        old_props=None, new_props={"name": sname},
                        source="collector://aihot")
                # 去重连边
                exists_link_query = db.query(LinkInstance).filter(
                    LinkInstance.ontology_id == ontology_id,
                    LinkInstance.link_type_id == source_lt.id,
                    LinkInstance.source_object_id == inst.id,
                    LinkInstance.target_object_id == sid)
                if ontology_release_id is not None:
                    exists_link_query = exists_link_query.filter(
                        LinkInstance.ontology_release_id == ontology_release_id)
                exists_link = exists_link_query.first()
                if not exists_link:
                    li = LinkInstance(
                        ontology_id=ontology_id,
                        ontology_release_id=ontology_release_id,
                        link_type_id=source_lt.id,
                        source_object_id=inst.id, target_object_id=sid)
                    db.add(li); db.flush()
                    record_link_fact(
                        db, ontology_id=ontology_id, link_instance_id=li.id,
                        link_type_id=source_lt.id, exists=True,
                        source="collector://aihot")

    db.commit()
    return _ok({
        "collected": len(items),
        "created": created,
        "updated": updated,
        "sources": len(source_cache),
    })
