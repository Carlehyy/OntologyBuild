"""领域设置 — CRUD API"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import Optional

from app.deps import get_db, get_current_user, require_admin
from app.models.user import User
from app.ontologies.projects.models import OntologyProject
from app.settings.domains.models import Domain
from app.settings.domains.schemas import DomainCreate, DomainUpdate, DomainOut
from app.settings.domains.service import ensure_domain

router = APIRouter()


def _out(d: Domain) -> dict:
    return DomainOut.model_validate(d).model_dump()


@router.get("")
def list_domains(
    search: Optional[str] = Query(None, description="按名称模糊搜索"),
    db: Session = Depends(get_db),
    _=Depends(get_current_user),
):
    q = db.query(Domain)
    if search:
        q = q.filter(Domain.name.ilike(f"%{search}%"))
    rows = q.order_by(Domain.updated_at.desc()).all()
    return {"data": [_out(r) for r in rows]}


@router.post("", status_code=201)
def create_domain(
    body: DomainCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    d, created = ensure_domain(
        db,
        name=body.name,
        description=body.description,
        created_by=current_user.id,
    )
    if not created:
        raise HTTPException(409, f"领域「{body.name}」已存在")
    db.commit()
    db.refresh(d)
    return {"data": _out(d)}


@router.put("/{domain_id}")
def update_domain(
    domain_id: str,
    body: DomainUpdate,
    db: Session = Depends(get_db),
    _=Depends(require_admin),
):
    d = db.query(Domain).filter(Domain.id == domain_id).with_for_update().first()
    if not d:
        raise HTTPException(404, "领域不存在")
    if body.name is not None and body.name != d.name:
        conflict = db.query(Domain).filter(Domain.name == body.name, Domain.id != domain_id).first()
        if conflict:
            raise HTTPException(409, f"领域「{body.name}」已存在")
        previous_name = d.name
        d.name = body.name
        db.query(OntologyProject).filter(
            OntologyProject.domain == previous_name,
        ).update(
            {
                OntologyProject.domain: body.name,
                OntologyProject.updated_at: datetime.now(timezone.utc),
            },
            synchronize_session=False,
        )
    if body.description is not None:
        d.description = body.description
    db.commit()
    db.refresh(d)
    return {"data": _out(d)}


@router.delete("/{domain_id}", status_code=204)
def delete_domain(
    domain_id: str,
    db: Session = Depends(get_db),
    _=Depends(require_admin),
):
    d = db.query(Domain).filter(Domain.id == domain_id).with_for_update().first()
    if not d:
        raise HTTPException(404, "领域不存在")
    usage_count = db.query(OntologyProject.id).filter(
        OntologyProject.domain == d.name,
    ).count()
    if usage_count:
        raise HTTPException(
            409,
            f"领域「{d.name}」已被 {usage_count} 个本体使用，请先调整这些本体的所属领域",
        )
    db.delete(d)
    db.commit()
