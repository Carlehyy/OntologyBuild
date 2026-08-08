"""Transactional helpers for the canonical domain registry."""

from __future__ import annotations

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.settings.domains.models import Domain


LOCAL_IMPORT_DESCRIPTION = "由本体本地导入自动创建"
LEGACY_ONTOLOGY_DESCRIPTION = "由历史兼容本体自动登记"


def find_domain(
    db: Session,
    name: str,
    *,
    for_update: bool = False,
) -> Domain | None:
    """Return one exact-name registry row, optionally locking it for mutation."""
    query = db.query(Domain).filter(Domain.name == name)
    if for_update:
        query = query.with_for_update()
    return query.first()


def ensure_domain(
    db: Session,
    *,
    name: str,
    created_by: str,
    description: str = "",
) -> tuple[Domain, bool]:
    """Ensure a registry row exists without committing the caller's transaction.

    The savepoint handles concurrent imports that try to register the same new
    label.  A later failure in the ontology import still rolls the new domain
    back together with the rest of that import.
    """
    normalized = name.strip()
    if not normalized:
        raise ValueError("domain name must not be empty")

    existing = find_domain(db, normalized, for_update=True)
    if existing is not None:
        return existing, False

    candidate = Domain(
        name=normalized,
        description=description,
        created_by=created_by,
    )
    try:
        with db.begin_nested():
            db.add(candidate)
            db.flush()
    except IntegrityError:
        # Another transaction may have committed the same unique name while
        # this request was waiting.  The savepoint keeps the outer import
        # transaction usable, so converge on that row instead of failing.
        existing = find_domain(db, normalized, for_update=True)
        if existing is None:
            raise
        return existing, False
    return candidate, True
