"""Release activation support shared by promotion and rollback.

SQL mutations in this module deliberately stop at the caller-owned transaction
boundary. Query-store rebuilds reconcile Neo4j from committed SQL truth and
report readiness without changing HTTP behavior.
"""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy.orm import Session

from app.ontologies.sentinels.models import Sentinel, SentinelMatchState
from app.ontologies.versions.snapshot_contract import (
    next_release_number,
)
from app.ontologies.versions.models import OntologyVersion


def rebuild_required_query_projections(
    db: Session,
    ontology_id: str,
    *,
    mapping_service_factory=None,
) -> dict:
    """Reconcile non-transactional query stores from committed SQL truth."""
    if mapping_service_factory is None:
        from app.ontologies.mappings.mapping_service import MappingService
        mapping_service_factory = MappingService
    service = mapping_service_factory(db)
    neo4j_ok = service._rebuild_neo4j_projection(ontology_id)
    return {
        "ready": bool(neo4j_ok),
        "neo4j": "ok" if neo4j_ok else "error",
    }


def next_release_activation_number(
    db: Session,
    ontology_id: str,
    *,
    number_allocator: Callable[[str | None], str] = next_release_number,
) -> str:
    """Allocate after every historic release, including legacy pointer reuse."""
    highest = 0
    for (number,) in db.query(OntologyVersion.version_number).filter(
        OntologyVersion.ontology_id == ontology_id,
        OntologyVersion.node_kind == "release",
    ).all():
        raw = str(number or "").removeprefix("v").split(".", 1)[0]
        if raw.isdigit():
            highest = max(highest, int(raw))
    return number_allocator(f"v{highest}")


def invalidate_dynamic_sentinels_for_release(
    db: Session,
    ontology_id: str,
    release_id: str,
) -> int:
    """Disable assistant overlays still bound to an older release."""
    rows = db.query(Sentinel).filter(
        Sentinel.ontology_id == ontology_id,
        Sentinel.origin == "assistant_dynamic",
        Sentinel.retired_at.is_(None),
    ).with_for_update().all()
    stale = [row for row in rows if row.bound_release_id != release_id]
    if not stale:
        return 0
    stale_ids = [row.id for row in stale]
    for row in stale:
        row.enabled = False
        row.last_trial_at = None
        row.last_trial_release_id = None
        row.last_trial_revision = None
        row.last_trial_report = None
    db.query(SentinelMatchState).filter(
        SentinelMatchState.ontology_id == ontology_id,
        SentinelMatchState.sentinel_id.in_(stale_ids),
    ).delete(synchronize_session=False)
    return len(stale)
