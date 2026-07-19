"""Safe diagnosis and explicit adoption of pre-release-identity runtime rows.

Release-scoped readers must never infer ownership from ``ontology_id`` alone.
This module therefore separates two cases:

* a historical projection whose live mappings still exactly match the current
  immutable release; an administrator may explicitly adopt it; and
* mutable runtime data produced by mappings that have not been released; it is
  reported to the UI but must continue through draft -> trial -> promotion.

Nothing in this module mutates data during a GET request.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
import uuid

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session, aliased

from app.models.inference import AuditLog
from app.models.ontology_formal import (
    LinkInstance,
    ObjectInstance,
    PropertyFact,
)
from app.models.ontology_version import OntologyVersion
from app.ontologies.mappings.mapping_service import (
    MappingApplyError,
    MappingReleaseScopeError,
    MappingService,
)
from app.ontologies.release_context import CurrentReleaseContext


_TRUSTED_PROJECTION_SOURCES = {"pipeline"}


def _naive_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _ids(items: list[dict[str, Any]], key: str = "id") -> set[str]:
    return {
        str(item[key]) for item in items
        if isinstance(item, dict) and item.get(key)
    }


@dataclass(frozen=True)
class LegacyProjectionAssessment:
    release: OntologyVersion
    object_instances: int
    link_instances: int
    blockers: tuple[dict[str, str], ...]

    @property
    def total(self) -> int:
        return self.object_instances + self.link_instances

    @property
    def can_adopt(self) -> bool:
        return self.total > 0 and not self.blockers

    @property
    def recommended_action(self) -> str:
        if self.total == 0:
            return "none"
        if self.can_adopt:
            return "adopt_legacy"
        if any(item["code"] in {
            "release_mapping_mismatch",
            "release_mapping_coverage_missing",
        } for item in self.blockers):
            return "publish_draft"
        return "manual_review"

    def payload(self) -> dict[str, Any]:
        return {
            "objectInstances": self.object_instances,
            "linkInstances": self.link_instances,
            "total": self.total,
            "canAdopt": self.can_adopt,
            "recommendedAction": self.recommended_action,
            "blockingReasons": list(self.blockers),
        }


class LegacyProjectionAdoptionError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        assessment: LegacyProjectionAssessment,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.assessment = assessment


def assess_legacy_projection(
    db: Session,
    context: CurrentReleaseContext,
) -> LegacyProjectionAssessment:
    """Return a read-only, release-aware assessment of unattributed rows."""
    ontology_id = str(context.project.id)
    release = context.release
    snapshot = context.snapshot

    object_rows = db.query(
        ObjectInstance.object_type_id,
        ObjectInstance.source,
        func.count(ObjectInstance.id),
        func.min(ObjectInstance.created_at),
    ).filter(
        ObjectInstance.ontology_id == ontology_id,
        ObjectInstance.ontology_release_id.is_(None),
    ).group_by(
        ObjectInstance.object_type_id,
        ObjectInstance.source,
    ).all()
    link_rows = db.query(
        LinkInstance.link_type_id,
        func.count(LinkInstance.id),
        func.min(LinkInstance.created_at),
    ).filter(
        LinkInstance.ontology_id == ontology_id,
        LinkInstance.ontology_release_id.is_(None),
    ).group_by(LinkInstance.link_type_id).all()

    object_count = sum(int(row[2]) for row in object_rows)
    link_count = sum(int(row[1]) for row in link_rows)
    blockers: list[dict[str, str]] = []
    seen_codes: set[str] = set()

    def block(code: str, message: str) -> None:
        if code in seen_codes:
            return
        seen_codes.add(code)
        blockers.append({"code": code, "message": message})

    if object_count + link_count == 0:
        return LegacyProjectionAssessment(release, 0, 0, ())

    released_object_types = _ids(snapshot["objectTypes"])
    released_link_types = _ids(snapshot["linkTypes"])
    legacy_object_types = {str(row[0]) for row in object_rows}
    legacy_link_types = {str(row[0]) for row in link_rows}

    if not legacy_object_types.issubset(released_object_types):
        block(
            "unreleased_object_types",
            "存在不属于当前发布快照的对象类型，拒绝猜测实例归属",
        )
    if not legacy_link_types.issubset(released_link_types):
        block(
            "unreleased_link_types",
            "存在不属于当前发布快照的关系类型，拒绝猜测实例归属",
        )

    sources = {row[1] for row in object_rows}
    if not sources.issubset(_TRUSTED_PROJECTION_SOURCES):
        block(
            "unverifiable_instance_sources",
            "仅能自动认领由数据流水线生成的历史投影；手工或采集数据需要人工复核",
        )

    published_at = _naive_utc(release.published_at)
    if published_at is None:
        block("release_publish_time_missing", "当前发布版本缺少发布时间，无法建立安全时间边界")
    else:
        oldest_candidates = [
            _naive_utc(row[3]) for row in object_rows
        ] + [
            _naive_utc(row[2]) for row in link_rows
        ]
        if any(value is None or value < published_at for value in oldest_candidates):
            block(
                "projection_predates_release",
                "部分历史实例早于当前发布版本，不能安全认领为该版本数据",
            )

    released_mapping_targets = _ids(snapshot["mappings"], "targetObjectTypeId")
    released_link_mapping_targets = _ids(snapshot["linkMappings"], "linkTypeId")
    if (not legacy_object_types.issubset(released_mapping_targets)
            or not legacy_link_types.issubset(released_link_mapping_targets)):
        block(
            "release_mapping_coverage_missing",
            "生成这些实例的映射尚未完整进入当前发布快照，请通过草稿试跑并晋级发布",
        )

    try:
        mapping_service = MappingService(db)
        owner = mapping_service._assert_current_release_scope(
            ontology_id,
            mapping_service.get_mappings(ontology_id),
            project=context.project,
        )
        if owner != release.id:
            block(
                "release_mapping_mismatch",
                "当前运行映射无法证明属于当前发布版本，请通过版本流程发布映射",
            )
    except (MappingReleaseScopeError, MappingApplyError):
        block(
            "release_mapping_mismatch",
            "当前运行映射与发布快照不一致；这些数据仍属于未发布运行工作区",
        )

    source_object = aliased(ObjectInstance)
    target_object = aliased(ObjectInstance)
    bad_endpoint_count = db.query(func.count(LinkInstance.id)).outerjoin(
        source_object,
        and_(
            source_object.id == LinkInstance.source_object_id,
            source_object.ontology_id == ontology_id,
        ),
    ).outerjoin(
        target_object,
        and_(
            target_object.id == LinkInstance.target_object_id,
            target_object.ontology_id == ontology_id,
        ),
    ).filter(
        LinkInstance.ontology_id == ontology_id,
        LinkInstance.ontology_release_id.is_(None),
        or_(
            source_object.id.is_(None),
            target_object.id.is_(None),
            and_(
                source_object.ontology_release_id.is_not(None),
                source_object.ontology_release_id != release.id,
            ),
            and_(
                target_object.ontology_release_id.is_not(None),
                target_object.ontology_release_id != release.id,
            ),
        ),
    ).scalar() or 0
    if bad_endpoint_count:
        block(
            "invalid_link_endpoints",
            "部分历史关系指向缺失或其他发布版本的对象实例，拒绝自动认领",
        )

    if published_at is not None:
        legacy_object_ids = select(ObjectInstance.id).where(
            ObjectInstance.ontology_id == ontology_id,
            ObjectInstance.ontology_release_id.is_(None),
        )
        legacy_link_ids = select(LinkInstance.id).where(
            LinkInstance.ontology_id == ontology_id,
            LinkInstance.ontology_release_id.is_(None),
        )
        legacy_fact_scope = or_(
            and_(
                PropertyFact.instance_id.in_(legacy_object_ids),
                PropertyFact.kind.in_(("property", "derived", "object")),
            ),
            and_(
                PropertyFact.instance_id.in_(legacy_link_ids),
                PropertyFact.kind == "link",
            ),
        )
        old_fact_count = db.query(func.count(PropertyFact.id)).filter(
            PropertyFact.ontology_id == ontology_id,
            PropertyFact.ontology_release_id.is_(None),
            legacy_fact_scope,
            PropertyFact.recorded_at < published_at,
        ).scalar() or 0
        if old_fact_count:
            block(
                "fact_history_predates_release",
                "部分未归属事实早于当前发布版本，拒绝改写历史归属",
            )

    return LegacyProjectionAssessment(
        release=release,
        object_instances=object_count,
        link_instances=link_count,
        blockers=tuple(blockers),
    )


def adopt_legacy_projection(
    db: Session,
    context: CurrentReleaseContext,
    *,
    expected_object_instances: int,
    expected_link_instances: int,
    actor: Any,
) -> dict[str, Any]:
    """Explicitly bind a proven legacy projection to the current release."""
    assessment = assess_legacy_projection(db, context)
    if (
        assessment.object_instances != expected_object_instances
        or assessment.link_instances != expected_link_instances
    ):
        raise LegacyProjectionAdoptionError(
            "legacy_projection_changed",
            "待修复实例数量已变化，请刷新页面后重新确认",
            assessment,
        )
    if not assessment.can_adopt:
        raise LegacyProjectionAdoptionError(
            "legacy_projection_not_adoptable",
            "当前历史投影不能安全归属到发布版本",
            assessment,
        )

    ontology_id = str(context.project.id)
    release = context.release
    legacy_object_ids = select(ObjectInstance.id).where(
        ObjectInstance.ontology_id == ontology_id,
        ObjectInstance.ontology_release_id.is_(None),
    )
    legacy_link_ids = select(LinkInstance.id).where(
        LinkInstance.ontology_id == ontology_id,
        LinkInstance.ontology_release_id.is_(None),
    )
    fact_scope = or_(
        and_(
            PropertyFact.instance_id.in_(legacy_object_ids),
            PropertyFact.kind.in_(("property", "derived", "object")),
        ),
        and_(
            PropertyFact.instance_id.in_(legacy_link_ids),
            PropertyFact.kind == "link",
        ),
    )
    adopted_facts = db.query(PropertyFact).filter(
        PropertyFact.ontology_id == ontology_id,
        PropertyFact.ontology_release_id.is_(None),
        fact_scope,
    ).update({
        PropertyFact.ontology_release_id: release.id,
        PropertyFact.ontology_version: release.version_number,
    }, synchronize_session=False)
    adopted_objects = db.query(ObjectInstance).filter(
        ObjectInstance.ontology_id == ontology_id,
        ObjectInstance.ontology_release_id.is_(None),
    ).update({
        ObjectInstance.ontology_release_id: release.id,
    }, synchronize_session=False)
    adopted_links = db.query(LinkInstance).filter(
        LinkInstance.ontology_id == ontology_id,
        LinkInstance.ontology_release_id.is_(None),
    ).update({
        LinkInstance.ontology_release_id: release.id,
    }, synchronize_session=False)

    if adopted_objects != assessment.object_instances or adopted_links != assessment.link_instances:
        raise LegacyProjectionAdoptionError(
            "legacy_projection_changed",
            "修复期间实例集合发生变化，事务已拒绝提交",
            assessment,
        )

    context.project.updated_at = datetime.now(timezone.utc)
    db.add(AuditLog(
        id=str(uuid.uuid4()),
        ontology_id=ontology_id,
        event_type="mapping",
        event_subtype="legacy_projection_adopted",
        user_id=getattr(actor, "id", None),
        user_name=getattr(actor, "username", None),
        description=f"将安全校验通过的历史实例归属到发布版本 {release.version_number}",
        object_type="ontology_version",
        object_id=release.id,
        meta={
            "release_id": release.id,
            "object_instances": adopted_objects,
            "link_instances": adopted_links,
            "property_facts": adopted_facts,
        },
    ))
    return {
        "release": {
            "id": release.id,
            "version": release.version_number,
        },
        "adopted": {
            "objectInstances": adopted_objects,
            "linkInstances": adopted_links,
            "propertyFacts": adopted_facts,
        },
    }
