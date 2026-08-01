from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.deps import get_db, get_current_user
from app.models.entity import Entity
from app.schemas.entity import EntityCreate, EntityUpdate, EntityOut
import uuid

router = APIRouter()


def _finish_projection(db: Session, ontology_id: str) -> None:
    from app.ontologies.projection_state import (
        ProjectionRebuildError,
        rebuild_after_commit,
    )

    try:
        rebuild_after_commit(db, ontology_id)
    except ProjectionRebuildError as exc:
        raise HTTPException(
            503,
            detail={
                "code": "ontology_projection_failed",
                "message": "实体已保存到关系型真相，但 Neo4j 图投影失败；请执行图修复",
                "ontology_id": ontology_id,
            },
        ) from exc

@router.get("")
def list_entities(ontology_id: str, db: Session = Depends(get_db), _=Depends(get_current_user)):
    # 单一可信数据源：正规本体存在对象类型时，直接投影其 ObjectType，
    # 确保旧 tab 能看到图谱编辑器新增的对象实体定义（消除数据双轨）。
    from app.services.formal.legacy_bridge import (
        has_formal_object_types, has_formal_instances,
        list_entities_from_formal_object_types, list_entities_from_formal,
    )
    if has_formal_object_types(db, ontology_id):
        return {"data": list_entities_from_formal_object_types(db, ontology_id)}
    if has_formal_instances(db, ontology_id):
        return {"data": list_entities_from_formal(db, ontology_id)}
    # 回退：历史本体（尚无正规本体数据）仍读旧表
    items = db.query(Entity).filter(Entity.ontology_id == ontology_id).all()
    return {"data": [EntityOut.model_validate(e).model_dump() for e in items]}

@router.post("", status_code=201)
def create_entity(ontology_id: str, body: EntityCreate, db: Session = Depends(get_db), _=Depends(get_current_user)):
    from app.ontologies.projection_state import mark_projecting
    from app.ontologies.runtime_fence import _ontology_build_lock

    with _ontology_build_lock(db, ontology_id):
        data = {k: v for k, v in body.model_dump().items() if v is not None}
        e = Entity(id=str(uuid.uuid4()), ontology_id=ontology_id, **data)
        db.add(e)
        mark_projecting(db, ontology_id)
        db.commit()
        db.refresh(e)
        _finish_projection(db, ontology_id)
    return {"data": EntityOut.model_validate(e).model_dump()}

@router.get("/{entity_id}")
def get_entity(ontology_id: str, entity_id: str, db: Session = Depends(get_db), _=Depends(get_current_user)):
    from app.services.formal.legacy_bridge import (
        has_formal_object_types, has_formal_instances,
        get_entity_from_formal_object_type, get_entity_from_formal,
    )
    if has_formal_object_types(db, ontology_id):
        bridged = get_entity_from_formal_object_type(db, ontology_id, entity_id)
        if bridged:
            return {"data": bridged}
    if has_formal_instances(db, ontology_id):
        bridged = get_entity_from_formal(db, ontology_id, entity_id)
        if bridged:
            return {"data": bridged}
        # 落到旧表查询（兼容混合数据）
    e = db.query(Entity).filter(Entity.id == entity_id, Entity.ontology_id == ontology_id).first()
    if not e:
        raise HTTPException(404, "Not found")
    return {"data": EntityOut.model_validate(e).model_dump()}

@router.put("/{entity_id}")
def update_entity(ontology_id: str, entity_id: str, body: EntityUpdate, db: Session = Depends(get_db), _=Depends(get_current_user)):
    from app.ontologies.projection_state import mark_projecting
    from app.ontologies.runtime_fence import _ontology_build_lock

    with _ontology_build_lock(db, ontology_id):
        e = db.query(Entity).filter(Entity.id == entity_id, Entity.ontology_id == ontology_id).first()
        if not e:
            raise HTTPException(404, "Not found")
        for k, v in body.model_dump(exclude_none=True).items():
            setattr(e, k, v)
        mark_projecting(db, ontology_id)
        db.commit()
        db.refresh(e)
        _finish_projection(db, ontology_id)
    return {"data": EntityOut.model_validate(e).model_dump()}

@router.delete("/{entity_id}", status_code=204)
def delete_entity(ontology_id: str, entity_id: str, db: Session = Depends(get_db), _=Depends(get_current_user)):
    from app.ontologies.projection_state import mark_projecting
    from app.ontologies.runtime_fence import _ontology_build_lock

    with _ontology_build_lock(db, ontology_id):
        e = db.query(Entity).filter(Entity.id == entity_id, Entity.ontology_id == ontology_id).first()
        if not e:
            raise HTTPException(404, "Not found")
        db.delete(e)
        mark_projecting(db, ontology_id)
        db.commit()
        _finish_projection(db, ontology_id)

@router.get("/{entity_id}/related")
def get_related_for_entity(
    ontology_id: str,
    entity_id: str,
    db: Session = Depends(get_db),
    _=Depends(get_current_user),
):
    import json as _json
    from app.models.logic import LogicRule
    from app.models.action import Action

    entity = db.query(Entity).filter(
        Entity.id == entity_id,
        Entity.ontology_id == ontology_id
    ).first()
    if not entity:
        raise HTTPException(status_code=404, detail="Entity not found")

    name_cn = entity.name_cn

    # Query LogicRules - linked_entities is TEXT storing JSON list (property getter returns list)
    related_logic = []
    for lr in db.query(LogicRule).filter(LogicRule.ontology_id == ontology_id).all():
        try:
            linked = lr.linked_entities or []
            if isinstance(linked, str):
                try:
                    linked = _json.loads(linked)
                except _json.JSONDecodeError:
                    linked = []
        except Exception:
            linked = []
        if isinstance(linked, list) and name_cn in linked:
            related_logic.append({
                "id": lr.id,
                "name_cn": lr.name_cn,
                "name_en": getattr(lr, 'name_en', None),
                "formula": getattr(lr, 'formula', None),
                "confidence": getattr(lr, 'confidence', None),
            })

    # Query Actions - linked_entities is JSON column
    related_actions = []
    for ac in db.query(Action).filter(Action.ontology_id == ontology_id).all():
        linked = ac.linked_entities or []
        if isinstance(linked, str):
            try:
                linked = _json.loads(linked)
            except _json.JSONDecodeError:
                linked = []
        if isinstance(linked, list) and name_cn in linked:
            related_actions.append({
                "id": ac.id,
                "name_cn": ac.name_cn,
                "name_en": getattr(ac, 'name_en', None),
                "description": getattr(ac, 'description', None),
                "confidence": getattr(ac, 'confidence', None),
            })

    return {"logic": related_logic, "actions": related_actions}
