"""Canonical application commands for ontology Mapping reconciliation."""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.ontologies.mappings.mapping_service import MappingService


def rebuild_ontology_projection(
    db: Session,
    ontology_id: str,
    *,
    require_approved: bool = True,
) -> dict:
    """Run the complete Mapping/Formal/query-projection/Sentinel barrier."""
    return MappingService(db).build_all(
        ontology_id,
        require_approved=require_approved,
    )
