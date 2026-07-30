"""Stable DatasetVersion outbox contract shared by writers and dispatcher."""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.data_channel.datasets.models import DatasetVersionEvent


VERSION_PUBLISHED_EVENT = "version_published"
CURATED_REVIEW_APPROVED_EVENT = "curated_review_approved"

# Approval events use a namespace that legacy workers cannot claim.  Keep
# these values in the writer-facing contract so approval and dispatch cannot
# silently disagree during rolling deployments.
CURATED_REVIEW_PENDING_STATUS = "review_pending"
CURATED_REVIEW_PROCESSING_STATUS = "review_processing"
CURATED_REVIEW_RETRY_STATUS = "review_retry"


def enqueue_curated_review_approved(
    db: Session,
    *,
    dataset_id: str,
    dataset_version_id: str,
) -> DatasetVersionEvent:
    """Add the idempotent approval event to the caller's current transaction.

    This function deliberately never flushes or commits.  Review approval,
    dataset status and the durable handoff must become visible atomically.
    """
    event = db.query(DatasetVersionEvent).filter(
        DatasetVersionEvent.dataset_version_id == dataset_version_id,
        DatasetVersionEvent.event_type == CURATED_REVIEW_APPROVED_EVENT,
    ).first()
    if event is None:
        event = DatasetVersionEvent(
            dataset_id=dataset_id,
            dataset_version_id=dataset_version_id,
            event_type=CURATED_REVIEW_APPROVED_EVENT,
            status=CURATED_REVIEW_PENDING_STATUS,
        )
        db.add(event)
    return event
