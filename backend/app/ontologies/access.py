"""Shared ontology authorization guard.

Read access remains available to authenticated users.  Mutations are limited to
administrators or the editor that owns the ontology.  Keeping this check at the
router boundary prevents a newly added CRUD endpoint from accidentally falling
back to authentication-only semantics.
"""
from __future__ import annotations

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.deps import get_current_user, get_db
from app.models.ontology import OntologyProject
from app.config import settings


_READ_METHODS = {"GET", "HEAD", "OPTIONS"}


def require_ontology_access(
    db: Session,
    ontology_id: str,
    user,
    *,
    write: bool,
) -> OntologyProject:
    project = db.query(OntologyProject).filter(OntologyProject.id == ontology_id).first()
    if project is None:
        raise HTTPException(404, "Ontology not found")
    if not write:
        return project

    role = str(getattr(user, "role", "") or "")
    user_id = str(getattr(user, "id", "") or "")
    if role == "admin":
        return project
    if role == "editor" and user_id and project.created_by == user_id:
        return project
    if role in {"viewer", "custom"}:
        raise HTTPException(403, "This role is read-only")
    raise HTTPException(403, "Only the ontology owner or an administrator may modify this ontology")


def ontology_access_guard(
    request: Request,
    ontology_id: str,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
) -> OntologyProject:
    return require_ontology_access(
        db, ontology_id, user, write=request.method.upper() not in _READ_METHODS)


def legacy_ontology_write_guard(request: Request) -> None:
    """Production ontology truth comes only from Formal + approved lake mapping."""
    if (settings.environment == "production"
            and request.method.upper() not in _READ_METHODS):
        raise HTTPException(
            410,
            "Legacy ontology write path is disabled in production; "
            "use Formal schema APIs and approved asset-lake mappings",
        )
