"""
Graph Router

Graph visualization, querying, and entity/relation management.
This is the interface to the property graph database (Kùzu).
"""

from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Entity, Relation, ObjectType, RelationType, AuditLog
from app.schemas import (
    EntityCreate, EntityUpdate, EntityOut,
    RelationCreate, RelationOut,
    GraphData, GraphQuery, GraphNode, GraphEdge,
)
from app.services.graph_service import get_graph_service

router = APIRouter(prefix="/graph", tags=["Graph"])


# ──────────────────────────────────────────────
# Entity Management
# ──────────────────────────────────────────────

@router.get("/domain/{domain_id}/entities", response_model=List[EntityOut])
def list_entities(
    domain_id: str,
    db: Session = Depends(get_db),
    object_type_id: Optional[str] = None,
    search: Optional[str] = None,
    limit: int = 100,
):
    """List entities in the graph for a domain."""
    query = db.query(Entity).filter(Entity.domain_id == domain_id)

    if object_type_id:
        query = query.filter(Entity.object_type_id == object_type_id)
    if search:
        query = query.filter(Entity.name.contains(search))

    return query.limit(limit).all()


@router.post("/domain/{domain_id}/entities", response_model=EntityOut)
def create_entity(domain_id: str, data: EntityCreate, db: Session = Depends(get_db)):
    """Create a new entity in the graph."""
    # Validate object type
    ot = db.query(ObjectType).filter(ObjectType.id == data.object_type_id).first()
    if not ot:
        raise HTTPException(status_code=404, detail="Object type not found")

    entity = Entity(
        domain_id=domain_id,
        object_type_id=data.object_type_id,
        name=data.name,
        properties=data.properties or {},
        confidence=data.confidence or 1.0,
    )
    db.add(entity)
    db.flush()

    # Sync to graph database
    graph_svc = get_graph_service()
    graph_svc.sync_entity(
        domain_id=domain_id,
        entity_id=entity.id,
        object_type_id=data.object_type_id,
        object_type_name=ot.name,
        name=data.name,
        properties=data.properties or {},
        confidence=data.confidence or 1.0,
    )

    db.commit()
    db.refresh(entity)

    db.add(AuditLog(
        action="create",
        resource_type="entity",
        resource_id=entity.id,
        domain_id=domain_id,
        details={"name": entity.name, "type": ot.name},
    ))
    db.commit()

    return entity


@router.get("/entities/{entity_id}", response_model=EntityOut)
def get_entity(entity_id: str, db: Session = Depends(get_db)):
    """Get an entity by ID."""
    entity = db.query(Entity).filter(Entity.id == entity_id).first()
    if not entity:
        raise HTTPException(status_code=404, detail="Entity not found")
    return entity


@router.put("/entities/{entity_id}", response_model=EntityOut)
def update_entity(entity_id: str, data: EntityUpdate, db: Session = Depends(get_db)):
    """Update an entity."""
    entity = db.query(Entity).filter(Entity.id == entity_id).first()
    if not entity:
        raise HTTPException(status_code=404, detail="Entity not found")

    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(entity, field, value)

    db.commit()
    db.flush()

    # Sync to graph
    graph_svc = get_graph_service()
    ot = db.query(ObjectType).filter(ObjectType.id == entity.object_type_id).first()
    graph_svc.sync_entity(
        domain_id=entity.domain_id,
        entity_id=entity.id,
        object_type_id=entity.object_type_id,
        object_type_name=ot.name if ot else "Unknown",
        name=entity.name,
        properties=entity.properties or {},
        confidence=entity.confidence or 1.0,
        is_verified=entity.is_verified,
    )

    db.commit()
    db.refresh(entity)
    return entity


@router.delete("/entities/{entity_id}")
def delete_entity(entity_id: str, db: Session = Depends(get_db)):
    """Delete an entity and its relations."""
    entity = db.query(Entity).filter(Entity.id == entity_id).first()
    if not entity:
        raise HTTPException(status_code=404, detail="Entity not found")

    # Delete from graph
    graph_svc = get_graph_service()
    graph_svc.delete_entity(entity.domain_id, entity_id)

    # Delete related relations
    db.query(Relation).filter(
        (Relation.source_id == entity_id) | (Relation.target_id == entity_id)
    ).delete(synchronize_session=False)

    db.delete(entity)
    db.commit()
    return {"success": True, "message": "Entity deleted"}


# ──────────────────────────────────────────────
# Relation Management
# ──────────────────────────────────────────────

@router.get("/domain/{domain_id}/relations", response_model=List[RelationOut])
def list_relations(
    domain_id: str,
    db: Session = Depends(get_db),
    entity_id: Optional[str] = None,
    relation_type_id: Optional[str] = None,
):
    """List relations for a domain."""
    query = db.query(Relation).filter(Relation.domain_id == domain_id)

    if entity_id:
        query = query.filter(
            (Relation.source_id == entity_id) | (Relation.target_id == entity_id)
        )
    if relation_type_id:
        query = query.filter(Relation.relation_type_id == relation_type_id)

    return query.all()


@router.post("/domain/{domain_id}/relations", response_model=RelationOut)
def create_relation(domain_id: str, data: RelationCreate, db: Session = Depends(get_db)):
    """Create a new relation between entities."""
    # Validate entities and relation type
    source = db.query(Entity).filter(Entity.id == data.source_id).first()
    target = db.query(Entity).filter(Entity.id == data.target_id).first()
    rt = db.query(RelationType).filter(RelationType.id == data.relation_type_id).first()

    if not source or not target:
        raise HTTPException(status_code=404, detail="Source or target entity not found")
    if not rt:
        raise HTTPException(status_code=404, detail="Relation type not found")

    relation = Relation(
        domain_id=domain_id,
        relation_type_id=data.relation_type_id,
        source_id=data.source_id,
        target_id=data.target_id,
        properties=data.properties or {},
        confidence=data.confidence or 1.0,
    )
    db.add(relation)
    db.flush()

    # Sync to graph
    graph_svc = get_graph_service()
    graph_svc.sync_relation(
        domain_id=domain_id,
        relation_id=relation.id,
        relation_type_id=data.relation_type_id,
        relation_name=rt.name,
        source_id=data.source_id,
        target_id=data.target_id,
        properties=data.properties or {},
        confidence=data.confidence or 1.0,
    )

    db.commit()
    db.refresh(relation)
    return relation


@router.delete("/relations/{relation_id}")
def delete_relation(relation_id: str, db: Session = Depends(get_db)):
    """Delete a relation."""
    relation = db.query(Relation).filter(Relation.id == relation_id).first()
    if not relation:
        raise HTTPException(status_code=404, detail="Relation not found")

    graph_svc = get_graph_service()
    graph_svc.delete_relation(
        relation.domain_id,
        relation.relation_type_id,
        relation.id,
        relation.source_id,
        relation.target_id,
    )

    db.delete(relation)
    db.commit()
    return {"success": True, "message": "Relation deleted"}


# ──────────────────────────────────────────────
# Graph Visualization
# ──────────────────────────────────────────────

@router.get("/domain/{domain_id}/visualization")
def get_graph_visualization(
    domain_id: str,
    center_entity_id: Optional[str] = None,
    depth: int = 1,
    limit: int = 500,
    db: Session = Depends(get_db),
):
    """Get graph data formatted for visualization."""
    # Get entities
    entities_query = db.query(Entity).filter(Entity.domain_id == domain_id)
    if center_entity_id:
        # Get center entity and its neighbors
        entities = entities_query.limit(limit).all()
    else:
        entities = entities_query.limit(limit).all()

    # Get relations
    entity_ids = {e.id for e in entities}
    relations = db.query(Relation).filter(
        Relation.domain_id == domain_id,
        Relation.source_id.in_(entity_ids),
        Relation.target_id.in_(entity_ids),
    ).all()

    # Build color map for types
    type_colors = {}
    object_types = db.query(ObjectType).filter(ObjectType.domain_id == domain_id).all()
    for ot in object_types:
        type_colors[ot.id] = ot.color or "#3b82f6"

    # Format nodes
    nodes = []
    for entity in entities:
        ot = next((t for t in object_types if t.id == entity.object_type_id), None)
        nodes.append({
            "id": entity.id,
            "label": entity.name,
            "type": ot.name if ot else "Unknown",
            "type_id": entity.object_type_id,
            "properties": entity.properties or {},
            "color": type_colors.get(entity.object_type_id, "#3b82f6"),
            "confidence": entity.confidence,
            "is_verified": entity.is_verified,
        })

    # Format edges
    edges = []
    rel_types = {rt.id: rt.name for rt in db.query(RelationType).filter(RelationType.domain_id == domain_id).all()}
    for relation in relations:
        edges.append({
            "id": relation.id,
            "source": relation.source_id,
            "target": relation.target_id,
            "label": rel_types.get(relation.relation_type_id, "relates_to"),
            "relation_type_id": relation.relation_type_id,
            "properties": relation.properties or {},
            "confidence": relation.confidence,
        })

    return {"nodes": nodes, "edges": edges}


@router.post("/domain/{domain_id}/query")
def query_graph(domain_id: str, data: GraphQuery, db: Session = Depends(get_db)):
    """Execute a graph query."""
    # For now, support natural language search
    # Full Cypher support would require the graph service
    search_term = data.query.strip()

    if not search_term:
        return {"nodes": [], "edges": []}

    # Search entities by name or properties
    entities = db.query(Entity).filter(
        Entity.domain_id == domain_id,
        (Entity.name.contains(search_term)) |
        (Entity.properties.cast(str).contains(search_term))
    ).limit(data.limit).all()

    entity_ids = {e.id for e in entities}

    # Get relations between found entities
    relations = db.query(Relation).filter(
        Relation.domain_id == domain_id,
        Relation.source_id.in_(entity_ids),
        Relation.target_id.in_(entity_ids),
    ).all()

    # Format response
    type_colors = {}
    object_types = db.query(ObjectType).filter(ObjectType.domain_id == domain_id).all()
    for ot in object_types:
        type_colors[ot.id] = ot.color or "#3b82f6"

    nodes = []
    for entity in entities:
        ot = next((t for t in object_types if t.id == entity.object_type_id), None)
        nodes.append({
            "id": entity.id,
            "label": entity.name,
            "type": ot.name if ot else "Unknown",
            "type_id": entity.object_type_id,
            "properties": entity.properties or {},
            "color": type_colors.get(entity.object_type_id, "#3b82f6"),
        })

    rel_types = {rt.id: rt.name for rt in db.query(RelationType).filter(RelationType.domain_id == domain_id).all()}
    edges = []
    for relation in relations:
        edges.append({
            "id": relation.id,
            "source": relation.source_id,
            "target": relation.target_id,
            "label": rel_types.get(relation.relation_type_id, "relates_to"),
            "relation_type_id": relation.relation_type_id,
        })

    return {"nodes": nodes, "edges": edges}


@router.get("/domain/{domain_id}/stats")
def get_graph_stats(domain_id: str, db: Session = Depends(get_db)):
    """Get graph statistics."""
    from sqlalchemy import func

    entity_count = db.query(Entity).filter(Entity.domain_id == domain_id).count()
    relation_count = db.query(Relation).filter(Relation.domain_id == domain_id).count()

    # Count by type
    type_counts = db.query(
        Entity.object_type_id,
        func.count(Entity.id).label("count")
    ).filter(Entity.domain_id == domain_id).group_by(Entity.object_type_id).all()

    type_stats = []
    for ot_id, count in type_counts:
        ot = db.query(ObjectType).filter(ObjectType.id == ot_id).first()
        type_stats.append({
            "type_id": ot_id,
            "type_name": ot.name if ot else "Unknown",
            "count": count,
            "color": ot.color if ot else "#3b82f6",
        })

    return {
        "entity_count": entity_count,
        "relation_count": relation_count,
        "type_breakdown": type_stats,
    }


@router.get("/search")
def search_graph(
    domain_id: str,
    q: str,
    limit: int = 20,
    db: Session = Depends(get_db),
):
    """Full-text search across the graph."""
    entities = db.query(Entity).filter(
        Entity.domain_id == domain_id,
        Entity.name.contains(q),
    ).limit(limit).all()

    results = []
    for entity in entities:
        ot = db.query(ObjectType).filter(ObjectType.id == entity.object_type_id).first()
        results.append({
            "id": entity.id,
            "name": entity.name,
            "type": ot.name if ot else "Unknown",
            "type_id": entity.object_type_id,
            "properties": entity.properties or {},
        })

    return {"results": results, "total": len(results)}
