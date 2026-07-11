from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import Optional
from app.deps import get_db, get_current_user
from app.models.ontology import OntologyProject
from app.models.ontology_formal import ObjectType, LinkType, ActionType
from app.models.user import User
from app.models.domain import Domain
from app.ontologies.access import require_ontology_access
from app.schemas.ontology import OntologyCreate, OntologyOut, OntologyListItem, OntologyUpdate
import uuid

router = APIRouter()

# Historical installations can contain ontologies created before configurable
# domains existed. Keep those names valid while accepting every domain managed
# through System Settings.
LEGACY_DOMAINS = {
    "供应链", "采购", "财务", "医疗", "金融", "法律", "教育", "科技",
    "制造", "能源", "其他",
}


def _validate_domain(db: Session, domain: str) -> None:
    exists = db.query(Domain.id).filter(Domain.name == domain).first()
    if exists is None and domain not in LEGACY_DOMAINS:
        raise HTTPException(422, detail={
            "error": "INVALID_DOMAIN",
            "message": f"领域「{domain}」不存在，请先在系统设置中添加",
        })

@router.get("")
def list_ontologies(
    name: Optional[str] = None,
    domain: Optional[str] = None,
    page: int = 1, page_size: int = 20,
    db: Session = Depends(get_db), _=Depends(get_current_user)
):
    q = db.query(OntologyProject)
    if name:
        q = q.filter(OntologyProject.name.ilike(f"%{name}%"))
    if domain:
        q = q.filter(OntologyProject.domain == domain)
    total = q.count()
    items = q.order_by(OntologyProject.created_at.desc()).offset((page-1)*page_size).limit(page_size).all()
    result = []
    for item in items:
        d = OntologyListItem.model_validate(item).model_dump()
        d['entity_count'] = db.query(func.count(ObjectType.id)).filter(ObjectType.ontology_id == item.id).scalar() or 0
        d['relation_count'] = db.query(func.count(LinkType.id)).filter(LinkType.ontology_id == item.id).scalar() or 0
        d['action_count'] = db.query(func.count(ActionType.id)).filter(ActionType.ontology_id == item.id).scalar() or 0
        result.append(d)
    return {"data": {"items": result, "total": total, "page": page, "page_size": page_size}}

@router.post("", status_code=201)
def create_ontology(body: OntologyCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    if getattr(current_user, "role", "") not in ("admin", "editor"):
        raise HTTPException(403, "Viewer role is read-only")
    _validate_domain(db, body.domain)
    existing = db.query(OntologyProject).filter(OntologyProject.name.ilike(body.name)).first()
    if existing:
        raise HTTPException(status_code=409, detail={"error": "DUPLICATE_NAME", "message": f"Ontology 名称「{body.name}」已存在", "existing_id": existing.id})
    project = OntologyProject(id=str(uuid.uuid4()), name=body.name, domain=body.domain,
                               description=body.description, icon=body.icon,
                               # Internal compatibility value only; creation no
                               # longer branches into LLM/Pipeline workflows.
                               build_mode=body.build_mode or "manual",
                               created_by=current_user.id)
    db.add(project); db.commit(); db.refresh(project)
    return {"data": OntologyOut.model_validate(project).model_dump()}

@router.get("/{ontology_id}")
def get_ontology(ontology_id: str, db: Session = Depends(get_db), _=Depends(get_current_user)):
    p = db.query(OntologyProject).filter(OntologyProject.id == ontology_id).first()
    if not p:
        raise HTTPException(404, "Not found")
    return {"data": OntologyOut.model_validate(p).model_dump()}

@router.put("/{ontology_id}")
def update_ontology(ontology_id: str, body: OntologyUpdate, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    p = require_ontology_access(db, ontology_id, current_user, write=True)
    update = body.model_dump(exclude_none=True)
    if "domain" in update and update["domain"] != p.domain:
        _validate_domain(db, update["domain"])
    if "name" in update:
        existing = db.query(OntologyProject).filter(
            OntologyProject.name.ilike(update["name"]),
            OntologyProject.id != ontology_id,
        ).first()
        if existing:
            raise HTTPException(status_code=409, detail={
                "error": "DUPLICATE_NAME",
                "message": f"Ontology 名称「{update['name']}」已存在",
                "existing_id": existing.id,
            })
    for k, v in update.items():
        setattr(p, k, v)
    db.commit(); db.refresh(p)
    return {"data": OntologyOut.model_validate(p).model_dump()}

@router.delete("/{ontology_id}", status_code=204)
def delete_ontology(ontology_id: str, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    p = require_ontology_access(db, ontology_id, current_user, write=True)
    db.delete(p); db.commit()
