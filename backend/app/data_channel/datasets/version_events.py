"""Durable DatasetVersion publication events and automation dispatch."""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from app.data_channel.datasets.models import (
    Dataset, DatasetVersion, DatasetVersionEvent,
)
from app.data_channel.datasets.service import version_has_content

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def manual_dataset_automation_eligibility(
    dataset: Dataset, version: DatasetVersion | None,
) -> tuple[bool, str]:
    """Return whether a manual version may drive ontology automation.

    Origin is deliberately independent from governance.  A manual asset becomes
    trusted only when it is truly user-maintained, has a stable primary-key
    contract and was published as a checksummed immutable version.
    """
    if dataset.kind == "curated":
        return False, "curated versions require the review-approved trigger"
    if (dataset.source_connection_id or dataset.producer_pipeline_id
            or dataset.name.startswith("SYNC::")):
        return False, "dataset is maintained by a connection or pipeline"
    schema = dataset.schema_json if isinstance(dataset.schema_json, dict) else {}
    if not str(schema.get("primary_key") or "").strip():
        return False, "manual dataset has no primary-key contract"
    if version is None:
        return False, "dataset version is missing"
    if not version_has_content(version) or not version.checksum:
        return False, "dataset version has no verifiable payload/checksum lineage"
    return True, "eligible"


def _claim_one(db: Session, event_id: str, now: datetime, stale_before: datetime) -> str | None:
    token = str(uuid.uuid4())
    changed = db.query(DatasetVersionEvent).filter(
        DatasetVersionEvent.id == event_id,
        or_(
            and_(
                DatasetVersionEvent.status.in_(("pending", "retry")),
                DatasetVersionEvent.available_at <= now,
            ),
            and_(
                DatasetVersionEvent.status == "processing",
                DatasetVersionEvent.claimed_at < stale_before,
            ),
        ),
    ).update({
        DatasetVersionEvent.status: "processing",
        DatasetVersionEvent.claimed_at: now,
        DatasetVersionEvent.claim_token: token,
        DatasetVersionEvent.attempts: DatasetVersionEvent.attempts + 1,
        DatasetVersionEvent.updated_at: now,
    }, synchronize_session=False)
    db.commit()
    return token if changed == 1 else None


def _process_claimed_event(db: Session, event_id: str, token: str) -> dict:
    event = db.query(DatasetVersionEvent).filter(
        DatasetVersionEvent.id == event_id,
        DatasetVersionEvent.status == "processing",
        DatasetVersionEvent.claim_token == token,
    ).first()
    if event is None:
        return {"status": "lost_claim", "event_id": event_id}

    dataset = db.query(Dataset).filter(Dataset.id == event.dataset_id).first()
    version = db.query(DatasetVersion).filter(
        DatasetVersion.id == event.dataset_version_id,
        DatasetVersion.dataset_id == event.dataset_id,
    ).first()
    if dataset is None or version is None:
        return {"status": "skipped", "reason": "dataset_or_version_deleted"}
    if dataset.latest_version_id != version.id:
        # Full-reconciliation consumers only need the newest snapshot.  Marking
        # older queued versions complete prevents a restart backlog from
        # repeatedly rebuilding states that have already been superseded.
        return {
            "status": "skipped", "reason": "superseded",
            "latest_version_id": dataset.latest_version_id,
        }

    from app.data_channel.sync_tasks.incremental_orchestrator import (
        IncrementalOrchestrator,
    )
    return IncrementalOrchestrator(db).on_dataset_version_published(
        dataset.id, version.id)


def drain_dataset_version_events(
    db: Session | None = None, *, limit: int | None = None,
    strict_schema: bool = False,
) -> dict:
    """Claim and synchronously dispatch a bounded event batch.

    Mapping is a full idempotent reconciliation.  The event is acknowledged
    only after it completes; a crash between projection commit and ack can
    replay safely, while sentinel edge state prevents duplicate notifications.
    """
    from app.config import settings
    from app.database import SessionLocal

    own_session = db is None
    session = db or SessionLocal()
    batch_size = max(1, int(limit or settings.dataset_event_batch_size or 20))
    result = {"processed": 0, "retried": 0, "lost_claims": 0}
    try:
        now = _now()
        stale_before = now - timedelta(
            seconds=max(30, int(settings.dataset_event_claim_timeout_seconds or 300)))
        candidates = session.query(DatasetVersionEvent.id).filter(
            or_(
                and_(
                    DatasetVersionEvent.status.in_(("pending", "retry")),
                    DatasetVersionEvent.available_at <= now,
                ),
                and_(
                    DatasetVersionEvent.status == "processing",
                    DatasetVersionEvent.claimed_at < stale_before,
                ),
            ),
        ).order_by(
            DatasetVersionEvent.created_at, DatasetVersionEvent.id,
        ).limit(batch_size).all()
    except Exception:
        session.rollback()
        if own_session:
            session.close()
        if strict_schema:
            raise
        logger.exception("读取 DatasetVersion 事件 outbox 失败")
        return {**result, "schema_error": True}

    for (event_id,) in candidates:
        claim_now = _now()
        try:
            token = _claim_one(session, event_id, claim_now, stale_before)
            if token is None:
                result["lost_claims"] += 1
                continue
            dispatch_result = _process_claimed_event(session, event_id, token)
            event = session.query(DatasetVersionEvent).filter(
                DatasetVersionEvent.id == event_id,
                DatasetVersionEvent.claim_token == token,
            ).first()
            if event is None:
                result["lost_claims"] += 1
                continue
            event.status = "completed"
            from fastapi.encoders import jsonable_encoder
            event.result_json = jsonable_encoder(dispatch_result)
            event.last_error = None
            event.processed_at = _now()
            event.claimed_at = None
            event.claim_token = None
            session.commit()
            result["processed"] += 1
        except Exception as exc:  # noqa: BLE001 - durable retry boundary
            session.rollback()
            event = session.query(DatasetVersionEvent).filter(
                DatasetVersionEvent.id == event_id).first()
            if event is not None:
                delay = min(300, 2 ** min(max(event.attempts, 1), 8))
                event.status = "retry"
                event.available_at = _now() + timedelta(seconds=delay)
                event.claimed_at = None
                event.claim_token = None
                event.last_error = str(exc)[:4000]
                session.commit()
            result["retried"] += 1
            logger.exception("DatasetVersion 自动化派发失败，已进入重试: %s", event_id)
    if own_session:
        session.close()
    return result
