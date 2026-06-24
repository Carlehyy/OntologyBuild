"""
Ontology Management Router

CRUD for ObjectTypes, PropertyTypes, and RelationTypes.
All operations are domain-scoped.
"""

from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import (
    Domain, ObjectType, PropertyType, RelationType,
    generate_uuid, AuditLog
)
from app.schemas import (
    ObjectTypeCreate, ObjectTypeUpdate, ObjectTypeOut,
    PropertyTypeCreate, PropertyTypeUpdate, PropertyTypeOut,
    RelationTypeCreate, RelationTypeUpdate, RelationTypeOut,
    DomainCreate, DomainUpdate, DomainOut,
)

router = APIRouter(prefix="/ontology", tags=["Ontology"])


# ──────────────────────────────────────────────
# Domain Management
# ──────────────────────────────────────────────

@router.get("/domains", response_model=List[DomainOut])
def list_domains(
    db: Session = Depends(get_db),
    skip: int = 0,
    limit: int = 100,
):
    """List all domains."""
    return db.query(Domain).offset(skip).limit(limit).all()


@router.post("/domains", response_model=DomainOut)
def create_domain(data: DomainCreate, db: Session = Depends(get_db)):
    """Create a new domain."""
    existing = db.query(Domain).filter(Domain.name == data.name).first()
    if existing:
        raise HTTPException(status_code=400, detail="Domain name already exists")

    domain = Domain(**data.model_dump())
    db.add(domain)
    db.commit()
    db.refresh(domain)

    # Audit log
    db.add(AuditLog(
        action="create",
        resource_type="domain",
        resource_id=domain.id,
        details={"name": domain.name},
    ))
    db.commit()

    return domain


@router.get("/domains/{domain_id}", response_model=DomainOut)
def get_domain(domain_id: str, db: Session = Depends(get_db)):
    """Get a domain by ID."""
    domain = db.query(Domain).filter(Domain.id == domain_id).first()
    if not domain:
        raise HTTPException(status_code=404, detail="Domain not found")
    return domain


@router.put("/domains/{domain_id}", response_model=DomainOut)
def update_domain(domain_id: str, data: DomainUpdate, db: Session = Depends(get_db)):
    """Update a domain."""
    domain = db.query(Domain).filter(Domain.id == domain_id).first()
    if not domain:
        raise HTTPException(status_code=404, detail="Domain not found")

    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(domain, field, value)

    db.commit()
    db.refresh(domain)
    return domain


@router.delete("/domains/{domain_id}")
def delete_domain(domain_id: str, db: Session = Depends(get_db)):
    """Delete a domain and all its data."""
    domain = db.query(Domain).filter(Domain.id == domain_id).first()
    if not domain:
        raise HTTPException(status_code=404, detail="Domain not found")

    db.delete(domain)
    db.commit()
    return {"success": True, "message": "Domain deleted"}


# ──────────────────────────────────────────────
# ObjectType Management
# ──────────────────────────────────────────────

@router.get("/domains/{domain_id}/object-types", response_model=List[ObjectTypeOut])
def list_object_types(
    domain_id: str,
    db: Session = Depends(get_db),
    include_inactive: bool = False,
):
    """List all object types for a domain."""
    query = db.query(ObjectType).filter(ObjectType.domain_id == domain_id)
    if not include_inactive:
        query = query.filter(ObjectType.is_active == True)
    return query.all()


@router.post("/domains/{domain_id}/object-types", response_model=ObjectTypeOut)
def create_object_type(domain_id: str, data: ObjectTypeCreate, db: Session = Depends(get_db)):
    """Create a new object type with optional properties."""
    domain = db.query(Domain).filter(Domain.id == domain_id).first()
    if not domain:
        raise HTTPException(status_code=404, detail="Domain not found")

    # Check for duplicate name
    existing = db.query(ObjectType).filter(
        ObjectType.domain_id == domain_id,
        ObjectType.name == data.name,
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="Object type name already exists in this domain")

    # Create object type
    obj_data = data.model_dump(exclude={"properties"})
    obj_type = ObjectType(domain_id=domain_id, **obj_data)
    db.add(obj_type)
    db.flush()  # Get the ID

    # Create properties if provided
    if data.properties:
        for prop_data in data.properties:
            prop = PropertyType(object_type_id=obj_type.id, **prop_data.model_dump())
            db.add(prop)

    db.commit()
    db.refresh(obj_type)

    # Audit log
    db.add(AuditLog(
        action="create",
        resource_type="object_type",
        resource_id=obj_type.id,
        domain_id=domain_id,
        details={"name": obj_type.name},
    ))
    db.commit()

    return obj_type


@router.get("/object-types/{object_type_id}", response_model=ObjectTypeOut)
def get_object_type(object_type_id: str, db: Session = Depends(get_db)):
    """Get an object type by ID."""
    obj_type = db.query(ObjectType).filter(ObjectType.id == object_type_id).first()
    if not obj_type:
        raise HTTPException(status_code=404, detail="Object type not found")
    return obj_type


@router.put("/object-types/{object_type_id}", response_model=ObjectTypeOut)
def update_object_type(object_type_id: str, data: ObjectTypeUpdate, db: Session = Depends(get_db)):
    """Update an object type."""
    obj_type = db.query(ObjectType).filter(ObjectType.id == object_type_id).first()
    if not obj_type:
        raise HTTPException(status_code=404, detail="Object type not found")

    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(obj_type, field, value)

    obj_type.version += 1
    db.commit()
    db.refresh(obj_type)
    return obj_type


@router.delete("/object-types/{object_type_id}")
def delete_object_type(object_type_id: str, db: Session = Depends(get_db)):
    """Delete an object type."""
    obj_type = db.query(ObjectType).filter(ObjectType.id == object_type_id).first()
    if not obj_type:
        raise HTTPException(status_code=404, detail="Object type not found")

    db.delete(obj_type)
    db.commit()
    return {"success": True, "message": "Object type deleted"}


# ──────────────────────────────────────────────
# PropertyType Management
# ──────────────────────────────────────────────

@router.post("/object-types/{object_type_id}/properties", response_model=PropertyTypeOut)
def add_property(object_type_id: str, data: PropertyTypeCreate, db: Session = Depends(get_db)):
    """Add a property to an object type."""
    obj_type = db.query(ObjectType).filter(ObjectType.id == object_type_id).first()
    if not obj_type:
        raise HTTPException(status_code=404, detail="Object type not found")

    # Check for duplicate
    existing = db.query(PropertyType).filter(
        PropertyType.object_type_id == object_type_id,
        PropertyType.name == data.name,
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="Property name already exists for this type")

    prop = PropertyType(object_type_id=object_type_id, **data.model_dump())
    db.add(prop)
    db.commit()
    db.refresh(prop)

    # Version bump
    obj_type.version += 1
    db.commit()

    return prop


@router.put("/properties/{property_id}", response_model=PropertyTypeOut)
def update_property(property_id: str, data: PropertyTypeUpdate, db: Session = Depends(get_db)):
    """Update a property."""
    prop = db.query(PropertyType).filter(PropertyType.id == property_id).first()
    if not prop:
        raise HTTPException(status_code=404, detail="Property not found")

    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(prop, field, value)

    # Version bump
    prop.object_type.version += 1
    db.commit()
    db.refresh(prop)
    return prop


@router.delete("/properties/{property_id}")
def delete_property(property_id: str, db: Session = Depends(get_db)):
    """Delete a property."""
    prop = db.query(PropertyType).filter(PropertyType.id == property_id).first()
    if not prop:
        raise HTTPException(status_code=404, detail="Property not found")

    db.delete(prop)
    db.commit()
    return {"success": True, "message": "Property deleted"}


# ──────────────────────────────────────────────
# RelationType Management
# ──────────────────────────────────────────────

@router.get("/domains/{domain_id}/relation-types", response_model=List[RelationTypeOut])
def list_relation_types(
    domain_id: str,
    db: Session = Depends(get_db),
    include_inactive: bool = False,
):
    """List all relation types for a domain."""
    query = db.query(RelationType).filter(RelationType.domain_id == domain_id)
    if not include_inactive:
        query = query.filter(RelationType.is_active == True)
    return query.all()


@router.post("/domains/{domain_id}/relation-types", response_model=RelationTypeOut)
def create_relation_type(domain_id: str, data: RelationTypeCreate, db: Session = Depends(get_db)):
    """Create a new relation type."""
    domain = db.query(Domain).filter(Domain.id == domain_id).first()
    if not domain:
        raise HTTPException(status_code=404, detail="Domain not found")

    # Validate source/target types exist
    source_type = db.query(ObjectType).filter(ObjectType.id == data.source_type_id).first()
    target_type = db.query(ObjectType).filter(ObjectType.id == data.target_type_id).first()
    if not source_type or not target_type:
        raise HTTPException(status_code=400, detail="Source or target type not found")

    # Check duplicate
    existing = db.query(RelationType).filter(
        RelationType.domain_id == domain_id,
        RelationType.name == data.name,
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="Relation type name already exists")

    rel_type = RelationType(domain_id=domain_id, **data.model_dump())
    db.add(rel_type)
    db.commit()
    db.refresh(rel_type)

    # Audit log
    db.add(AuditLog(
        action="create",
        resource_type="relation_type",
        resource_id=rel_type.id,
        domain_id=domain_id,
        details={"name": rel_type.name},
    ))
    db.commit()

    return rel_type


@router.get("/relation-types/{relation_type_id}", response_model=RelationTypeOut)
def get_relation_type(relation_type_id: str, db: Session = Depends(get_db)):
    """Get a relation type by ID."""
    rel_type = db.query(RelationType).filter(RelationType.id == relation_type_id).first()
    if not rel_type:
        raise HTTPException(status_code=404, detail="Relation type not found")
    return rel_type


@router.put("/relation-types/{relation_type_id}", response_model=RelationTypeOut)
def update_relation_type(relation_type_id: str, data: RelationTypeUpdate, db: Session = Depends(get_db)):
    """Update a relation type."""
    rel_type = db.query(RelationType).filter(RelationType.id == relation_type_id).first()
    if not rel_type:
        raise HTTPException(status_code=404, detail="Relation type not found")

    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(rel_type, field, value)

    db.commit()
    db.refresh(rel_type)
    return rel_type


@router.delete("/relation-types/{relation_type_id}")
def delete_relation_type(relation_type_id: str, db: Session = Depends(get_db)):
    """Delete a relation type."""
    rel_type = db.query(RelationType).filter(RelationType.id == relation_type_id).first()
    if not rel_type:
        raise HTTPException(status_code=404, detail="Relation type not found")

    db.delete(rel_type)
    db.commit()
    return {"success": True, "message": "Relation type deleted"}
