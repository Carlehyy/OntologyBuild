"""领域设置 — CRUD API"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import Optional

from app.deps import get_db, get_current_user
from app.models.user import User
from app.settings.domains.models import Domain
from app.settings.domains.schemas import DomainCreate, DomainUpdate, DomainOut

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
    current_user: User = Depends(get_current_user),
):
    if db.query(Domain).filter(Domain.name == body.name).first():
        raise HTTPException(409, f"领域「{body.name}」已存在")
    d = Domain(name=body.name, description=body.description, created_by=current_user.id)
    db.add(d)
    db.commit()
    db.refresh(d)
    return {"data": _out(d)}


@router.put("/{domain_id}")
def update_domain(
    domain_id: str,
    body: DomainUpdate,
    db: Session = Depends(get_db),
    _=Depends(get_current_user),
):
    d = db.query(Domain).filter(Domain.id == domain_id).first()
    if not d:
        raise HTTPException(404, "领域不存在")
    if body.name is not None:
        conflict = db.query(Domain).filter(Domain.name == body.name, Domain.id != domain_id).first()
        if conflict:
            raise HTTPException(409, f"领域「{body.name}」已存在")
        d.name = body.name
    if body.description is not None:
        d.description = body.description
    db.commit()
    db.refresh(d)
    return {"data": _out(d)}


@router.delete("/{domain_id}", status_code=204)
def delete_domain(
    domain_id: str,
    db: Session = Depends(get_db),
    _=Depends(get_current_user),
):
    d = db.query(Domain).filter(Domain.id == domain_id).first()
    if not d:
        raise HTTPException(404, "领域不存在")
    db.delete(d)
    db.commit()
