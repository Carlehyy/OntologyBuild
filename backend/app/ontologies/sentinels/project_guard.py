"""Ontology lifecycle guards shared by Sentinel application workflows."""
from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.ontology import OntologyProject


def _project(
    db: Session,
    ontology_id: str,
    *,
    for_update: bool = False,
) -> OntologyProject:
    query = db.query(OntologyProject).filter(
        OntologyProject.id == ontology_id
    )
    if for_update:
        query = query.with_for_update().populate_existing()
    project = query.first()
    if project is None:
        raise HTTPException(404, "Ontology not found")
    return project


def _require_draft(db: Session, ontology_id: str) -> OntologyProject:
    project = _project(db, ontology_id, for_update=True)
    if (project.status or "") != "draft":
        raise HTTPException(
            409,
            "Sentinel 结构只能在 draft 本体中维护；请先撤回发布",
        )
    return project
