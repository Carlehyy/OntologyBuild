"""Fail-closed helpers for APIs that must be pinned to the current release.

The mutable ``fo_*`` tables are the runtime projection.  Governance reads must
not infer their version from that projection or from ``project.status``; the
only release identity is ``OntologyProject.current_release_id``.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import uuid

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.ontology import OntologyProject
from app.models.ontology_version import OntologyVersion
from app.ontologies.versions.snapshot_contract import (
    complete_snapshot,
    snapshot_hash,
)


def _json_safe(value):
    """快照只保留 JSON 值；不把 ORM/时间对象渗入 JSON 列。"""
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


@dataclass(frozen=True)
class CurrentReleaseContext:
    project: OntologyProject
    release: OntologyVersion
    snapshot: dict

    @property
    def id(self) -> str:
        return self.release.id

    @property
    def version(self) -> str:
        return self.release.version_number


@dataclass(frozen=True)
class RuntimeReleaseIdentity:
    """Exact immutable lineage for newly-created runtime records."""

    id: str
    version: str


def create_initial_release(
    db: Session,
    project: OntologyProject,
    *,
    snapshot: dict | None,
    created_by: str,
    version_label: str = "初始基线",
    description: str = "系统创建的完整发布基线",
    semantic: dict | None = None,
) -> OntologyVersion:
    """Create the immutable v0 release that every ontology owns from birth."""
    if project.current_release_id:
        raise ValueError("ontology already has a current release")
    existing = db.query(OntologyVersion.id).filter(
        OntologyVersion.ontology_id == project.id,
    ).first()
    if existing is not None:
        raise ValueError("ontology already has version history")
    frozen = complete_snapshot(snapshot)
    release_id = str(uuid.uuid4())
    release = OntologyVersion(
        id=release_id,
        ontology_id=project.id,
        version_number="v0",
        version_label=version_label,
        description=description,
        base_release_id=release_id,
        node_kind="release",
        lifecycle_status="released",
        revision=0,
        snapshot_formal=frozen,
        snapshot_hash=snapshot_hash(frozen),
        snapshot_semantic=_json_safe(semantic),
        published_at=project.created_at or datetime.now(timezone.utc),
        created_by=created_by,
    )
    db.add(release)
    db.flush()
    project.current_release_id = release.id
    project.version = release.version_number
    return release


def current_release_context(
    db: Session,
    ontology_id: str,
    *,
    expected_release_id: str | None = None,
) -> CurrentReleaseContext:
    """Resolve the immutable release selected by the project's release pointer.

    ``expected_release_id`` turns a frontend read/command into a compare-and-read
    operation.  If a release is promoted while a governance page is open, the
    stale request is rejected instead of silently mixing two releases.
    """
    project = db.query(OntologyProject).filter(
        OntologyProject.id == ontology_id,
    ).first()
    if project is None:
        raise HTTPException(404, "Ontology not found")
    if not project.current_release_id:
        raise HTTPException(409, detail={
            "code": "current_release_missing",
            "message": "本体尚未建立当前发布指针，治理数据已拒绝加载",
        })
    if expected_release_id and expected_release_id != project.current_release_id:
        raise HTTPException(409, detail={
            "code": "release_context_changed",
            "message": "当前发布版本已变化，请刷新治理推演页面",
            "expectedReleaseId": expected_release_id,
            "currentReleaseId": project.current_release_id,
        })
    release = db.query(OntologyVersion).filter(
        OntologyVersion.id == project.current_release_id,
        OntologyVersion.ontology_id == ontology_id,
        OntologyVersion.node_kind == "release",
        OntologyVersion.lifecycle_status == "released",
    ).first()
    if release is None:
        raise HTTPException(409, detail={
            "code": "current_release_invalid",
            "message": "当前发布指针未指向有效发布快照，治理数据已拒绝加载",
            "currentReleaseId": project.current_release_id,
        })
    return CurrentReleaseContext(
        project=project,
        release=release,
        snapshot=complete_snapshot(release.snapshot_formal),
    )


def runtime_release_identity(
    db: Session,
    ontology_id: str,
) -> RuntimeReleaseIdentity | None:
    """Return exact current-release lineage, or ``None`` when it is unsafe.

    Unlike governance reads this helper does not raise: runtime writers must be
    able to preserve legacy behavior, but a missing/invalid release pointer must
    never be guessed into an immutable release id.
    """
    project = db.query(OntologyProject).filter(
        OntologyProject.id == ontology_id,
    ).first()
    if project is None or not project.current_release_id:
        return None
    release = db.query(OntologyVersion).filter(
        OntologyVersion.id == project.current_release_id,
        OntologyVersion.ontology_id == ontology_id,
        OntologyVersion.node_kind == "release",
        OntologyVersion.lifecycle_status == "released",
    ).first()
    if release is None:
        return None
    return RuntimeReleaseIdentity(
        id=str(release.id),
        version=str(release.version_number),
    )


def runtime_release_version(db: Session, ontology_id: str) -> str | None:
    """Return the release version that owns a newly-created runtime record.

    Legacy fixtures/installations without a release pointer keep their existing
    ``project.version`` behavior.  New installations always use the immutable
    release row, avoiding a stale compatibility field.
    """
    project = db.query(OntologyProject).filter(
        OntologyProject.id == ontology_id,
    ).first()
    if project is None:
        return None
    identity = runtime_release_identity(db, ontology_id)
    if identity is not None:
        return identity.version
    return str(project.version) if project.version else None
