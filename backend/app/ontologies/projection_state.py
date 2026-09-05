"""Durable SQL-to-Neo4j projection fence.

PostgreSQL and the Formal tables are authoritative. Neo4j is rebuilt only
after that truth commits, and graph/runtime consumers may read it only while
the owning project is ``ready``.  Keeping the fence on the project (rather
than only on Mapping rows) also covers manual and Formal-instance writes.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from sqlalchemy.orm import Session


READY = "ready"
PROJECTING = "projecting"
FAILED = "failed"
REPAIR_REQUIRED = "repair_required"
VALID_STATES = {READY, PROJECTING, FAILED, REPAIR_REQUIRED}


class ProjectionRebuildError(RuntimeError):
    """The committed SQL truth could not be projected completely."""


@dataclass(frozen=True)
class ProjectionSnapshot:
    ontology_id: str
    status: str
    error: str | None
    pending_mapping_count: int

    @property
    def ready(self) -> bool:
        return self.status == READY and self.pending_mapping_count == 0


def _project(
    db: Session,
    ontology_id: str,
    *,
    read_lock: bool = False,
):
    from app.models.ontology import OntologyProject

    query = db.query(OntologyProject).filter(
        OntologyProject.id == ontology_id,
    )
    bind = db.get_bind()
    if (
        read_lock
        and bind is not None
        and bind.dialect.name == "postgresql"
    ):
        # Concurrent graph readers share this row lock. A writer cannot commit
        # ``projecting`` (and therefore cannot start delete/rebuild) until all
        # readers that observed ``ready`` have completed their Neo4j query.
        query = query.with_for_update(read=True)
    return query.first()


def mark_projecting(db: Session, ontology_id: str) -> None:
    project = _project(db, ontology_id)
    if project is None:
        raise ProjectionRebuildError(f"ontology not found: {ontology_id}")
    project.projection_status = PROJECTING
    project.projection_error = None


def mark_ready(db: Session, ontology_id: str) -> None:
    project = _project(db, ontology_id)
    if project is None:
        raise ProjectionRebuildError(f"ontology not found: {ontology_id}")
    project.projection_status = READY
    project.projection_error = None


def mark_failed(db: Session, ontology_id: str, error: object) -> None:
    project = _project(db, ontology_id)
    if project is None:
        raise ProjectionRebuildError(f"ontology not found: {ontology_id}")
    project.projection_status = FAILED
    project.projection_error = str(error)[:1000]


def snapshot(
    db: Session,
    ontology_id: str,
    *,
    lock_for_read: bool = False,
) -> ProjectionSnapshot:
    from app.ontologies.mappings.models import OntologyMapping

    project = _project(db, ontology_id, read_lock=lock_for_read)
    if project is None:
        raise ProjectionRebuildError(f"ontology not found: {ontology_id}")
    pending = (
        db.query(OntologyMapping)
        .filter(
            OntologyMapping.ontology_id == ontology_id,
            OntologyMapping.status != "applied",
        )
        .count()
    )
    return ProjectionSnapshot(
        ontology_id=str(ontology_id),
        status=str(project.projection_status or READY),
        error=project.projection_error,
        pending_mapping_count=int(pending),
    )


def rebuild_after_commit(
    db: Session,
    ontology_id: str,
    *,
    rebuild: Callable[[str], bool] | None = None,
    run_in_test: bool = False,
) -> dict[str, object]:
    """Rebuild from committed truth and durably close or fail the fence.

    Callers must include :func:`mark_projecting` in the same transaction as
    their source mutation before invoking this function. Test mode skips the
    external store unless a fault-injection callback is supplied explicitly.
    """
    from app.config import settings

    if settings.environment == "test" and rebuild is None and not run_in_test:
        mark_ready(db, ontology_id)
        db.commit()
        return {"ready": True, "neo4j": "skipped_test"}

    if rebuild is None:
        from app.ontologies.mappings.projection_rebuild import (
            rebuild_neo4j_projection,
        )

        rebuild = lambda current_id: rebuild_neo4j_projection(db, current_id)

    try:
        ready = bool(rebuild(ontology_id))
    except Exception as exc:  # the durable fence is more important than shape
        db.rollback()
        mark_failed(db, ontology_id, exc)
        db.commit()
        raise ProjectionRebuildError(
            f"Neo4j projection rebuild failed: {exc}"
        ) from exc
    if not ready:
        message = "Neo4j projection rebuild returned incomplete"
        db.rollback()
        mark_failed(db, ontology_id, message)
        db.commit()
        raise ProjectionRebuildError(message)

    try:
        mark_ready(db, ontology_id)
        db.commit()
    except Exception as exc:
        # A driver can report a commit error after PostgreSQL has already
        # accepted it. Re-read after rollback: a durable READY is safe because
        # the Neo4j rebuild above was fully validated. Otherwise persist FAILED
        # so no graph reader can observe the uncertain boundary.
        db.rollback()
        try:
            persisted = _project(db, ontology_id)
            if (
                persisted is not None
                and persisted.projection_status == READY
            ):
                return {"ready": True, "neo4j": "ok"}
            mark_failed(
                db,
                ontology_id,
                f"projection fence finalization failed: {type(exc).__name__}",
            )
            db.commit()
        except Exception as fence_exc:
            db.rollback()
            raise ProjectionRebuildError(
                "Neo4j projection succeeded but its durable fence could not "
                "be finalized"
            ) from fence_exc
        raise ProjectionRebuildError(
            "Neo4j projection succeeded but its durable fence finalization "
            "failed"
        ) from exc
    return {"ready": True, "neo4j": "ok"}


def repair_unready_projections(
    *,
    session_factory=None,
    rebuild: Callable[[Session, str], bool] | None = None,
) -> int:
    """Rebuild every interrupted, failed, or upgrade-fenced projection.

    This is a startup migration barrier, not a background best-effort repair.
    A failed rebuild raises and keeps its durable ``failed`` state so the API
    process cannot advertise readiness over stale graph data.
    """
    from app.database import SessionLocal
    from app.models.ontology import OntologyProject
    from app.ontologies.runtime_fence import _ontology_build_lock

    make_session = session_factory or SessionLocal
    discovery = make_session()
    try:
        ontology_ids = [
            str(row[0])
            for row in (
                discovery.query(OntologyProject.id)
                .filter(OntologyProject.projection_status != READY)
                .order_by(OntologyProject.id.asc())
                .all()
            )
        ]
    finally:
        discovery.close()

    repaired = 0
    for ontology_id in ontology_ids:
        db = make_session()
        try:
            with _ontology_build_lock(db, ontology_id):
                project = _project(db, ontology_id)
                if project is None:
                    continue
                # A concurrent, validated writer may have repaired the project
                # while the discovery session was closing.
                if project.projection_status == READY:
                    continue
                mark_projecting(db, ontology_id)
                db.commit()
                callback = (
                    None
                    if rebuild is None
                    else lambda current_id: rebuild(db, current_id)
                )
                rebuild_after_commit(
                    db,
                    ontology_id,
                    rebuild=callback,
                    run_in_test=True,
                )
                repaired += 1
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
    return repaired


def not_ready_detail(state: ProjectionSnapshot) -> dict[str, object]:
    return {
        "code": "ontology_projection_not_ready",
        "message": "本体图投影尚未就绪，请先完成或修复投影对账",
        "ontology_id": state.ontology_id,
        "projection_status": state.status,
        "projection_error": state.error,
        "pending_mapping_count": state.pending_mapping_count,
    }
