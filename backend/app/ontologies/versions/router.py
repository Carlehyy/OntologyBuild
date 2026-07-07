"""本体版本化路由 — 版本历史 / diff / 回滚"""
from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import desc
import uuid
from app.deps import get_db, get_current_user
from app.models.ontology_version import OntologyVersion, OntologyChangeLog
from app.models.ontology import OntologyProject
from app.models.entity import Entity
from app.models.relation import Relation
from app.models.logic import LogicRule
from app.models.action import Action
from app.models.inference import AuditLog
from app.models.ontology_formal import (
    ObjectType as FoObjectType, LinkType as FoLinkType,
    ActionType as FoActionType, OntologyFunction as FoFunction,
    ObjectInstance as FoObjectInstance, LinkInstance as FoLinkInstance,
)
from app.ontologies.formal_modeling import schemas as FS

router = APIRouter(dependencies=[Depends(get_current_user)])


# ============ 正规模型 (fo_*) 快照与差异 ============

def _snapshot_formal(db: Session, ontology_id: str) -> dict:
    """把图谱编辑器的正规模型模式层（对象/关系/动作/函数）序列化为 JSON 快照。

    只快照模式层，不含实例——实例是运行数据，版本化属于 Fact 层的职责。
    """
    def q(model):
        return db.query(model).filter(model.ontology_id == ontology_id).all()

    return {
        "objectTypes": [FS.ObjectTypeOut.model_validate(x).model_dump(mode="json", by_alias=True) for x in q(FoObjectType)],
        "linkTypes": [FS.LinkTypeOut.model_validate(x).model_dump(mode="json", by_alias=True) for x in q(FoLinkType)],
        "actions": [FS.ActionTypeOut.model_validate(x).model_dump(mode="json", by_alias=True) for x in q(FoActionType)],
        "functions": [FS.FunctionOut.model_validate(x).model_dump(mode="json", by_alias=True) for x in q(FoFunction)],
    }


def _diff_formal(prev: dict | None, curr: dict) -> dict:
    """按 id 对比两个正规模型快照，输出各集合的 added/modified/deleted 计数。"""
    prev = prev or {}
    out: dict = {}
    total_added = total_modified = total_deleted = 0
    for key in ("objectTypes", "linkTypes", "actions", "functions"):
        prev_items = {i["id"]: i for i in (prev.get(key) or [])}
        curr_items = {i["id"]: i for i in (curr.get(key) or [])}
        added = len(curr_items.keys() - prev_items.keys())
        deleted = len(prev_items.keys() - curr_items.keys())
        modified = 0
        for iid in curr_items.keys() & prev_items.keys():
            a = {k: v for k, v in prev_items[iid].items() if k not in ("createdAt", "updatedAt")}
            b = {k: v for k, v in curr_items[iid].items() if k not in ("createdAt", "updatedAt")}
            if a != b:
                modified += 1
        out[key] = {"added": added, "modified": modified, "deleted": deleted}
        total_added += added; total_modified += modified; total_deleted += deleted
    out["total"] = {"added": total_added, "modified": total_modified, "deleted": total_deleted}
    return out


@router.get("/{ontology_id}/versions")
def list_versions(ontology_id: str, limit: int = 20, offset: int = 0, db: Session = Depends(get_db)):
    """列出所有版本（分页）"""
    project = db.query(OntologyProject).filter(OntologyProject.id == ontology_id).first()
    if not project:
        raise HTTPException(404, "Ontology not found")
    total = db.query(OntologyVersion).filter(
        OntologyVersion.ontology_id == ontology_id
    ).count()
    versions = db.query(OntologyVersion).filter(
        OntologyVersion.ontology_id == ontology_id
    ).order_by(desc(OntologyVersion.created_at)).offset(offset).limit(limit).all()
    return {"data": [{
        "id": v.id,
        "version_number": v.version_number,
        "version_label": v.version_label,
        "description": v.description,
        "change_summary": v.change_summary or {},
        "created_by": v.created_by,
        "created_at": v.created_at.isoformat() if v.created_at else None,
    } for v in versions], "total": total, "limit": limit, "offset": offset}


@router.post("/{ontology_id}/versions", status_code=201)
def create_version(ontology_id: str, body: dict, db: Session = Depends(get_db),
                   current_user=Depends(get_current_user)):
    """创建新版本快照（通常在发布时调用）"""
    project = db.query(OntologyProject).filter(OntologyProject.id == ontology_id).first()
    if not project:
        raise HTTPException(404, "Ontology not found")

    # 计算新版本号
    latest = db.query(OntologyVersion).filter(
        OntologyVersion.ontology_id == ontology_id
    ).order_by(desc(OntologyVersion.created_at)).first()

    if latest:
        # 简单语义化：v{major}.{minor}.{patch}
        parts = latest.version_number.replace("v", "").split(".")
        try:
            major, minor, patch = int(parts[0]), int(parts[1]), int(parts[2])
            minor += 1
            new_version = f"v{major}.{minor}.0"
        except (ValueError, IndexError):
            new_version = f"v1.0.0"
    else:
        new_version = "v1.0.0"

    # 快照当前数据
    entities = db.query(Entity).filter(Entity.ontology_id == ontology_id).all()
    relations = db.query(Relation).filter(Relation.ontology_id == ontology_id).all()
    logic_rules = db.query(LogicRule).filter(LogicRule.ontology_id == ontology_id).all()
    actions = db.query(Action).filter(Action.ontology_id == ontology_id).all()

    # 计算变更统计
    prev_entities = latest.snapshot_entities if latest else []
    prev_entity_ids = {e.get("id") for e in prev_entities}
    curr_entity_ids = {e.id for e in entities}

    added = len(curr_entity_ids - prev_entity_ids)
    deleted = len(prev_entity_ids - curr_entity_ids)
    modified = 0
    if latest:
        curr_map = {e.id: e for e in entities}
        for prev in prev_entities:
            curr = curr_map.get(prev.get("id"))
            if curr and (prev.get("name_cn") != curr.name_cn or prev.get("type") != curr.type):
                modified += 1

    # 正规模型（图谱编辑器 fo_* 模式层）快照 + 差异
    formal_snapshot = _snapshot_formal(db, ontology_id)
    formal_diff = _diff_formal(latest.snapshot_formal if latest else None, formal_snapshot)

    version = OntologyVersion(
        id=str(uuid.uuid4()),
        ontology_id=ontology_id,
        version_number=new_version,
        version_label=body.get("version_label", ""),
        description=body.get("description", ""),
        snapshot_entities=[{
            "id": e.id, "name_cn": e.name_cn, "name_en": e.name_en,
            "type": e.type, "description": e.description, "confidence": e.confidence,
            "properties": e.properties or {},
        } for e in entities],
        snapshot_relations=[{
            "id": r.id, "source_entity": r.source_entity,
            "target_entity": r.target_entity, "type": r.type,
            "confidence": r.confidence,
        } for r in relations],
        snapshot_logic=[{
            "id": lr.id, "name_cn": lr.name_cn, "formula": lr.formula,
            "enabled": lr.enabled, "status": lr.status,
        } for lr in logic_rules],
        snapshot_actions=[{
            "id": a.id, "name_cn": a.name_cn,
            "enabled": a.enabled, "status": a.status,
        } for a in actions],
        snapshot_formal=formal_snapshot,
        change_summary={
            "added": added, "modified": modified, "deleted": deleted,
            "formal": formal_diff,
        },
        created_by=current_user.id,
    )
    db.add(version)

    # 更新项目版本号
    project.version = new_version

    # 记录审计
    audit = AuditLog(
        id=str(uuid.uuid4()),
        ontology_id=ontology_id,
        event_type="publish",
        event_subtype="version_created",
        user_id=current_user.id,
        user_name=current_user.username,
        description=f"创建版本 {new_version}",
        object_type="ontology_version",
        object_id=version.id,
        meta={"version_number": new_version},
    )
    db.add(audit)
    db.commit()

    return {"data": {
        "id": version.id,
        "version_number": new_version,
        "change_summary": version.change_summary,
    }}


@router.get("/{ontology_id}/versions/{version_id}")
def get_version_detail(ontology_id: str, version_id: str, db: Session = Depends(get_db)):
    """获取版本详情（含完整快照）"""
    v = db.query(OntologyVersion).filter(
        OntologyVersion.id == version_id,
        OntologyVersion.ontology_id == ontology_id,
    ).first()
    if not v:
        raise HTTPException(404, "Version not found")
    return {"data": {
        "id": v.id,
        "version_number": v.version_number,
        "version_label": v.version_label,
        "description": v.description,
        "change_summary": v.change_summary or {},
        "snapshot": {
            "entities": v.snapshot_entities or [],
            "relations": v.snapshot_relations or [],
            "logic": v.snapshot_logic or [],
            "actions": v.snapshot_actions or [],
            "formal": v.snapshot_formal or None,
        },
        "created_at": v.created_at.isoformat() if v.created_at else None,
    }}


@router.post("/{ontology_id}/versions/{version_id}/rollback")
def rollback_version(ontology_id: str, version_id: str, db: Session = Depends(get_db),
                     current_user=Depends(get_current_user)):
    """回滚到指定版本"""
    v = db.query(OntologyVersion).filter(
        OntologyVersion.id == version_id,
        OntologyVersion.ontology_id == ontology_id,
    ).first()
    if not v:
        raise HTTPException(404, "Version not found")

    project = db.query(OntologyProject).filter(OntologyProject.id == ontology_id).first()
    if not project:
        raise HTTPException(404, "Ontology not found")

    # 删除现有数据
    db.query(Entity).filter(Entity.ontology_id == ontology_id).delete()
    db.query(Relation).filter(Relation.ontology_id == ontology_id).delete()
    db.query(LogicRule).filter(LogicRule.ontology_id == ontology_id).delete()
    db.query(Action).filter(Action.ontology_id == ontology_id).delete()

    # 恢复快照
    for e_data in (v.snapshot_entities or []):
        e = Entity(
            id=e_data.get("id", str(uuid.uuid4())),
            ontology_id=ontology_id,
            name_cn=e_data.get("name_cn", ""),
            name_en=e_data.get("name_en", ""),
            type=e_data.get("type", ""),
            description=e_data.get("description", ""),
            confidence=e_data.get("confidence", 1.0),
            properties=e_data.get("properties", {}),
        )
        db.add(e)

    for r_data in (v.snapshot_relations or []):
        r = Relation(
            id=r_data.get("id", str(uuid.uuid4())),
            ontology_id=ontology_id,
            source_entity=r_data.get("source_entity", ""),
            target_entity=r_data.get("target_entity", ""),
            type=r_data.get("type", "关联"),
            confidence=r_data.get("confidence", 1.0),
        )
        db.add(r)

    # 恢复正规模型（fo_* 模式层）——仅当该版本带有 formal 快照
    formal_restored = None
    if v.snapshot_formal:
        snap = v.snapshot_formal
        for model in (FoObjectType, FoLinkType, FoActionType, FoFunction):
            db.query(model).filter(model.ontology_id == ontology_id).delete()

        def _restore(model, create_schema, items):
            for item in items or []:
                parsed = create_schema.model_validate(item)
                data = parsed.model_dump(exclude_none=False)
                db.add(model(id=item.get("id") or str(uuid.uuid4()),
                             ontology_id=ontology_id, **data))

        _restore(FoObjectType, FS.ObjectTypeCreate, snap.get("objectTypes"))
        _restore(FoLinkType, FS.LinkTypeCreate, snap.get("linkTypes"))
        _restore(FoActionType, FS.ActionTypeCreate, snap.get("actions"))
        _restore(FoFunction, FS.FunctionCreate, snap.get("functions"))

        # 实例数据保留，但类型已不在快照中的实例/链接实例会悬挂，需级联清理
        type_ids = {i["id"] for i in (snap.get("objectTypes") or [])}
        link_type_ids = {i["id"] for i in (snap.get("linkTypes") or [])}
        inst_q = db.query(FoObjectInstance).filter(FoObjectInstance.ontology_id == ontology_id)
        if type_ids:
            inst_q = inst_q.filter(~FoObjectInstance.object_type_id.in_(type_ids))
        orphans = inst_q.all()
        removed_instance_ids = {i.id for i in orphans}
        for o in orphans:
            db.delete(o)
        n_inst = len(orphans)
        n_li = 0
        for li in db.query(FoLinkInstance).filter(FoLinkInstance.ontology_id == ontology_id).all():
            if (li.link_type_id not in link_type_ids
                    or li.source_object_id in removed_instance_ids
                    or li.target_object_id in removed_instance_ids):
                db.delete(li); n_li += 1
        formal_restored = {
            "objectTypes": len(snap.get("objectTypes") or []),
            "linkTypes": len(snap.get("linkTypes") or []),
            "actions": len(snap.get("actions") or []),
            "functions": len(snap.get("functions") or []),
            "prunedInstances": n_inst,
            "prunedLinkInstances": n_li,
        }

    # 回滚版本号
    project.version = v.version_number

    # 审计
    audit = AuditLog(
        id=str(uuid.uuid4()),
        ontology_id=ontology_id,
        event_type="rollback",
        user_id=current_user.id,
        user_name=current_user.username,
        description=f"回滚到版本 {v.version_number}",
        object_type="ontology",
        object_id=ontology_id,
        meta={"version_id": version_id, "version_number": v.version_number},
    )
    db.add(audit)
    db.commit()

    return {"data": {
        "version_number": v.version_number,
        "message": "Rollback successful",
        "formal_restored": formal_restored,
    }}


@router.get("/{ontology_id}/change-logs")
def list_change_logs(ontology_id: str, object_type: str = None, limit: int = 100, db: Session = Depends(get_db)):
    """列出变更日志"""
    q = db.query(OntologyChangeLog).filter(OntologyChangeLog.ontology_id == ontology_id)
    if object_type:
        q = q.filter(OntologyChangeLog.object_type == object_type)
    logs = q.order_by(desc(OntologyChangeLog.created_at)).limit(limit).all()
    return {"data": [{
        "id": log.id,
        "action": log.action,
        "object_type": log.object_type,
        "object_name": log.object_name,
        "before": log.before,
        "after": log.after,
        "created_by_name": log.created_by_name,
        "created_at": log.created_at.isoformat() if log.created_at else None,
    } for log in logs]}
