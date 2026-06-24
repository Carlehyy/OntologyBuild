"""
Mapping Management Router

CRUD for data source mappings.
Mappings define how external data fields map to ontology properties.
"""

from typing import List
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Mapping, AuditLog
from app.schemas import MappingCreate, MappingUpdate, MappingOut

router = APIRouter(prefix="/mappings", tags=["Mappings"])


@router.get("/domain/{domain_id}", response_model=List[MappingOut])
def list_mappings(
    domain_id: str,
    db: Session = Depends(get_db),
    active_only: bool = False,
):
    """List mappings for a domain."""
    query = db.query(Mapping).filter(Mapping.domain_id == domain_id)
    if active_only:
        query = query.filter(Mapping.is_active == True)
    return query.all()


@router.post("/domain/{domain_id}", response_model=MappingOut)
def create_mapping(domain_id: str, data: MappingCreate, db: Session = Depends(get_db)):
    """Create a new mapping."""
    mapping = Mapping(
        domain_id=domain_id,
        **data.model_dump(),
    )
    db.add(mapping)
    db.commit()
    db.refresh(mapping)

    db.add(AuditLog(
        action="create",
        resource_type="mapping",
        resource_id=mapping.id,
        domain_id=domain_id,
        details={"name": mapping.name, "source_type": mapping.source_type},
    ))
    db.commit()

    return mapping


@router.get("/{mapping_id}", response_model=MappingOut)
def get_mapping(mapping_id: str, db: Session = Depends(get_db)):
    """Get a mapping by ID."""
    mapping = db.query(Mapping).filter(Mapping.id == mapping_id).first()
    if not mapping:
        raise HTTPException(status_code=404, detail="Mapping not found")
    return mapping


@router.put("/{mapping_id}", response_model=MappingOut)
def update_mapping(mapping_id: str, data: MappingUpdate, db: Session = Depends(get_db)):
    """Update a mapping."""
    mapping = db.query(Mapping).filter(Mapping.id == mapping_id).first()
    if not mapping:
        raise HTTPException(status_code=404, detail="Mapping not found")

    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(mapping, field, value)

    db.commit()
    db.refresh(mapping)
    return mapping


@router.post("/{mapping_id}/toggle-active")
def toggle_mapping_active(mapping_id: str, db: Session = Depends(get_db)):
    """Toggle mapping active status."""
    mapping = db.query(Mapping).filter(Mapping.id == mapping_id).first()
    if not mapping:
        raise HTTPException(status_code=404, detail="Mapping not found")

    mapping.is_active = not mapping.is_active
    db.commit()

    return {
        "success": True,
        "is_active": mapping.is_active,
        "message": f"Mapping {'activated' if mapping.is_active else 'deactivated'}",
    }


@router.delete("/{mapping_id}")
def delete_mapping(mapping_id: str, db: Session = Depends(get_db)):
    """Delete a mapping."""
    mapping = db.query(Mapping).filter(Mapping.id == mapping_id).first()
    if not mapping:
        raise HTTPException(status_code=404, detail="Mapping not found")

    db.delete(mapping)
    db.commit()
    return {"success": True, "message": "Mapping deleted"}
