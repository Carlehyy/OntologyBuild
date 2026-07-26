"""Durable manual DatasetVersion -> ontology automation contract."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from sqlalchemy import and_, or_
from sqlalchemy.orm import sessionmaker
from sqlalchemy.orm.attributes import set_committed_value

from app.data_channel.datasets.service import DatasetService
from app.data_channel.datasets.version_events import (
    CURATED_REVIEW_APPROVED_EVENT,
    CURATED_REVIEW_PENDING_STATUS,
    CURATED_REVIEW_PROCESSING_STATUS,
    CURATED_REVIEW_RETRY_STATUS,
    VERSION_PUBLISHED_EVENT,
    drain_dataset_version_events,
    manual_dataset_automation_eligibility,
)
from app.data_channel.curated.review_service import ReviewService
from app.data_channel.sync_tasks.incremental_orchestrator import (
    IncrementalOrchestrator,
)
from app.models.v2.dataset import DatasetVersionEvent
from app.models.v2.mapping import OntologyMapping


class MemoryStorage:
    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.fail_put = False

    def put_bytes(self, bucket: str, key: str, data: bytes, content_type: str = "") -> str:
        if self.fail_put:
            raise ConnectionError("storage unavailable")
        uri = f"s3://{bucket}/{key}"
        self.objects[uri] = data
        return uri

    def get_object(self, uri: str) -> bytes:
        return self.objects[uri]

    def delete_object(self, uri: str) -> None:
        self.objects.pop(uri, None)


def _manual_service(db):
    storage = MemoryStorage()
    service = DatasetService(db, storage=storage)
    dataset = service.create_dataset(
        "人工业务表", "structured",
        schema_json={
            "origin": "manual",
            "columns": ["id", "name"],
            "columns_typed": [
                {"name": "id", "type": "string", "nullable": False},
                {"name": "name", "type": "string", "nullable": True},
            ],
            "types_source": "declared",
            "primary_key": "id",
            "pk_source": "manual",
        },
    )
    return service, storage, dataset


def test_version_and_event_commit_together(db):
    service, _storage, dataset = _manual_service(db)

    version = service.create_version(
        dataset.id, b"id,name\n1,A\n", rowcount=1)

    event = db.query(DatasetVersionEvent).one()
    assert event.dataset_id == dataset.id
    assert event.dataset_version_id == version.id
    assert event.status == "pending"
    assert event.attempts == 0


def test_version_publish_locks_dataset_row_before_allocation(db, monkeypatch):
    """版本发布必须与审核共享 Dataset 行锁，而非只依赖外部写锁。"""
    from sqlalchemy.orm import Query

    from app.models.v2.dataset import Dataset

    service, _storage, dataset = _manual_service(db)
    original = Query.with_for_update
    dataset_locks: list[dict] = []

    def track_for_update(query, *args, **kwargs):
        entities = {
            item.get("entity") for item in query.column_descriptions
        }
        if Dataset in entities:
            dataset_locks.append(dict(kwargs))
        return original(query, *args, **kwargs)

    monkeypatch.setattr(Query, "with_for_update", track_for_update)

    service.create_version(
        dataset.id, b"id,name\n1,A\n", rowcount=1)

    assert dataset_locks
    assert dataset_locks[0].get("of") is Dataset


def test_managed_minio_failure_does_not_block_database_version_event(db):
    service, storage, dataset = _manual_service(db)
    storage.fail_put = True

    version = service.create_version(
        dataset.id, b"id,name\n1,A\n", rowcount=1)

    assert version.storage_uri is None
    assert version.data_blob == b"id,name\n1,A\n"
    event = db.query(DatasetVersionEvent).one()
    assert event.dataset_version_id == version.id


def test_curated_approval_and_automation_event_commit_together(db):
    service = DatasetService(db, storage=MemoryStorage())
    dataset = service.create_dataset(
        "成品订单", "curated",
        schema_json={"primary_key": "id", "columns": ["id"]},
    )
    version = service.create_version(
        dataset.id, b"id\n1\n", rowcount=1)
    review = ReviewService(db).start_review(dataset.id)

    ReviewService(db).approve(review.id)

    events = db.query(DatasetVersionEvent).filter_by(
        dataset_version_id=version.id).all()
    assert {event.event_type for event in events} == {
        VERSION_PUBLISHED_EVENT,
        CURATED_REVIEW_APPROVED_EVENT,
    }
    approval_event = next(
        event for event in events
        if event.event_type == CURATED_REVIEW_APPROVED_EVENT)
    assert approval_event.status == CURATED_REVIEW_PENDING_STATUS
    assert approval_event.attempts == 0


def test_curated_approval_status_namespace_fences_legacy_worker(db):
    """旧 worker 的 pending/retry/processing 查询不得看到新审批事件。"""
    service = DatasetService(db, storage=MemoryStorage())
    dataset = service.create_dataset(
        "滚动发布成品订单", "curated",
        schema_json={"primary_key": "id", "columns": ["id"]},
    )
    version = service.create_version(
        dataset.id, b"id\n1\n", rowcount=1)
    review = ReviewService(db).start_review(dataset.id)
    ReviewService(db).approve(review.id)
    approval_event = db.query(DatasetVersionEvent).filter_by(
        dataset_version_id=version.id,
        event_type=CURATED_REVIEW_APPROVED_EVENT,
    ).one()

    now = datetime.now(timezone.utc)
    stale_before = now + timedelta(seconds=1)
    legacy_worker_ready = or_(
        and_(
            DatasetVersionEvent.status.in_(("pending", "retry")),
            DatasetVersionEvent.available_at <= now,
        ),
        and_(
            DatasetVersionEvent.status == "processing",
            DatasetVersionEvent.claimed_at < stale_before,
        ),
    )
    for status in (
        CURATED_REVIEW_PENDING_STATUS,
        CURATED_REVIEW_PROCESSING_STATUS,
        CURATED_REVIEW_RETRY_STATUS,
    ):
        approval_event.status = status
        approval_event.claimed_at = now - timedelta(hours=1)
        db.commit()
        assert db.query(DatasetVersionEvent).filter(
            DatasetVersionEvent.id == approval_event.id,
            legacy_worker_ready,
        ).count() == 0

    # The values must fit the deployed varchar(20) column without a migration.
    assert max(map(len, (
        CURATED_REVIEW_PENDING_STATUS,
        CURATED_REVIEW_PROCESSING_STATUS,
        CURATED_REVIEW_RETRY_STATUS,
    ))) <= DatasetVersionEvent.__table__.c.status.type.length == 20


def test_new_worker_reclaims_stale_namespaced_approval(db):
    """新 worker 能接管自己的超时审批 claim，旧 worker 则看不到它。"""
    service = DatasetService(db, storage=MemoryStorage())
    dataset = service.create_dataset(
        "超时审批成品订单", "curated",
        schema_json={"primary_key": "id", "columns": ["id"]},
    )
    version = service.create_version(
        dataset.id, b"id\n1\n", rowcount=1)
    review = ReviewService(db).start_review(dataset.id)
    ReviewService(db).approve(review.id)
    published = db.query(DatasetVersionEvent).filter_by(
        dataset_version_id=version.id,
        event_type=VERSION_PUBLISHED_EVENT,
    ).one()
    published.status = "completed"
    approval_event = db.query(DatasetVersionEvent).filter_by(
        dataset_version_id=version.id,
        event_type=CURATED_REVIEW_APPROVED_EVENT,
    ).one()
    approval_event.status = CURATED_REVIEW_PROCESSING_STATUS
    approval_event.claim_token = "crashed-new-worker"
    approval_event.claimed_at = datetime.now(timezone.utc) - timedelta(hours=1)
    db.commit()

    def successful_barrier(review_id, *, synchronous):
        active = db.query(DatasetVersionEvent).filter_by(
            id=approval_event.id).one()
        assert active.status == CURATED_REVIEW_PROCESSING_STATUS
        assert active.claim_token != "crashed-new-worker"
        assert review_id == review.id
        assert synchronous is True
        return {
            "dataset_id": dataset.id,
            "review_id": review.id,
            "triggered_mappings": [],
        }

    with patch.object(
        IncrementalOrchestrator,
        "on_review_approved",
        side_effect=successful_barrier,
    ):
        result = drain_dataset_version_events(db, limit=1)

    assert result == {"processed": 1, "retried": 0, "lost_claims": 0}
    db.expire_all()
    stored = db.query(DatasetVersionEvent).filter_by(
        id=approval_event.id).one()
    assert stored.status == "completed"
    assert stored.attempts == 1


def test_new_worker_upgrades_legacy_pending_approval_without_migration(db):
    """升级前已落库的 pending 审批事件仍可由新 worker 正确消费。"""
    service = DatasetService(db, storage=MemoryStorage())
    dataset = service.create_dataset(
        "历史待处理成品订单", "curated",
        schema_json={"primary_key": "id", "columns": ["id"]},
    )
    version = service.create_version(
        dataset.id, b"id\n1\n", rowcount=1)
    review = ReviewService(db).start_review(dataset.id)
    ReviewService(db).approve(review.id)
    published = db.query(DatasetVersionEvent).filter_by(
        dataset_version_id=version.id,
        event_type=VERSION_PUBLISHED_EVENT,
    ).one()
    published.status = "completed"
    approval_event = db.query(DatasetVersionEvent).filter_by(
        dataset_version_id=version.id,
        event_type=CURATED_REVIEW_APPROVED_EVENT,
    ).one()
    approval_event.status = "pending"
    db.commit()

    def successful_barrier(review_id, *, synchronous):
        active = db.query(DatasetVersionEvent).filter_by(
            id=approval_event.id).one()
        assert active.status == CURATED_REVIEW_PROCESSING_STATUS
        assert review_id == review.id
        assert synchronous is True
        return {
            "dataset_id": dataset.id,
            "review_id": review.id,
            "triggered_mappings": [],
        }

    with patch.object(
        IncrementalOrchestrator,
        "on_review_approved",
        side_effect=successful_barrier,
    ):
        result = drain_dataset_version_events(db, limit=1)

    assert result == {"processed": 1, "retried": 0, "lost_claims": 0}
    db.expire_all()
    stored = db.query(DatasetVersionEvent).filter_by(
        id=approval_event.id).one()
    assert stored.status == "completed"
    assert stored.attempts == 1


def test_legacy_pending_review_binds_version_before_approval_event(db):
    """迁移前空 version review 也必须产生精确、可恢复的审批事件。"""
    from app.models.v2.curated import CuratedReview

    service = DatasetService(db, storage=MemoryStorage())
    dataset = service.create_dataset(
        "历史成品订单", "curated",
        schema_json={"primary_key": "id", "columns": ["id"]},
    )
    version = service.create_version(
        dataset.id, b"id\n1\n", rowcount=1)
    legacy_review = CuratedReview(
        curated_dataset_id=dataset.id,
        dataset_version_id=None,
        status="pending",
    )
    db.add(legacy_review)
    db.commit()
    db.refresh(legacy_review)

    approved = ReviewService(db).approve(legacy_review.id)

    assert approved.dataset_version_id == version.id
    event = db.query(DatasetVersionEvent).filter_by(
        dataset_version_id=version.id,
        event_type=CURATED_REVIEW_APPROVED_EVENT,
    ).one()
    assert event.status == CURATED_REVIEW_PENDING_STATUS


def test_stale_reviewer_session_cannot_overwrite_terminal_decision(db):
    """等待锁的旧 identity-map 必须刷新，不能把 approved 覆盖成 rejected。"""
    from app.models.v2.curated import CuratedReview

    service = DatasetService(db, storage=MemoryStorage())
    dataset = service.create_dataset(
        "并发审核订单", "curated",
        schema_json={"primary_key": "id", "columns": ["id"]},
    )
    service.create_version(dataset.id, b"id\n1\n", rowcount=1)
    review = ReviewService(db).start_review(dataset.id)
    review_id = review.id
    db.commit()

    isolated_session = sessionmaker(
        bind=db.get_bind(),
        expire_on_commit=False,
    )
    winner = isolated_session()
    stale = isolated_session()
    try:
        cached = stale.query(CuratedReview).filter_by(id=review_id).one()
        assert cached.status == "pending"
        stale.commit()  # 释放 SQLite 读事务，但刻意保留旧 identity-map。

        ReviewService(winner).approve(review_id)

        with pytest.raises(HTTPException) as exc_info:
            ReviewService(stale).reject(review_id)
        assert exc_info.value.status_code == 409
        assert exc_info.value.detail["code"] == "review_already_decided"
    finally:
        winner.close()
        stale.close()

    db.expire_all()
    stored = db.query(CuratedReview).filter_by(id=review_id).one()
    assert stored.status == "approved"
    assert db.query(DatasetVersionEvent).filter_by(
        dataset_version_id=stored.dataset_version_id,
        event_type=CURATED_REVIEW_APPROVED_EVENT,
    ).count() == 1


def test_curated_approval_event_completes_only_after_sync_mapping_barrier(db):
    service = DatasetService(db, storage=MemoryStorage())
    dataset = service.create_dataset(
        "成品订单", "curated",
        schema_json={"primary_key": "id", "columns": ["id"]},
    )
    version = service.create_version(
        dataset.id, b"id\n1\n", rowcount=1)
    review = ReviewService(db).start_review(dataset.id)
    ReviewService(db).approve(review.id)

    with patch.object(
        IncrementalOrchestrator,
        "on_review_approved",
        return_value={
            "dataset_id": dataset.id,
            "review_id": review.id,
            "triggered_mappings": [],
        },
    ) as dispatch:
        result = drain_dataset_version_events(db, limit=10)

    assert result == {"processed": 2, "retried": 0, "lost_claims": 0}
    dispatch.assert_called_once_with(review.id, synchronous=True)
    event = db.query(DatasetVersionEvent).filter_by(
        dataset_version_id=version.id,
        event_type=CURATED_REVIEW_APPROVED_EVENT,
    ).one()
    assert event.status == "completed"
    assert event.result_json["review_id"] == review.id


def test_curated_approval_refreshes_stale_event_identity_before_routing(db):
    """调用方复用旧 Session 时也必须按数据库中的事件类型派发。"""
    service = DatasetService(db, storage=MemoryStorage())
    dataset = service.create_dataset(
        "成品订单", "curated",
        schema_json={"primary_key": "id", "columns": ["id"]},
    )
    version = service.create_version(
        dataset.id, b"id\n1\n", rowcount=1)
    review = ReviewService(db).start_review(dataset.id)
    ReviewService(db).approve(review.id)
    published = db.query(DatasetVersionEvent).filter_by(
        dataset_version_id=version.id,
        event_type=VERSION_PUBLISHED_EVENT,
    ).one()
    published.status = "completed"
    db.commit()

    isolated_session = sessionmaker(
        bind=db.get_bind(),
        expire_on_commit=False,
    )()
    try:
        approval_event = isolated_session.query(
            DatasetVersionEvent,
        ).filter_by(
            dataset_version_id=version.id,
            event_type=CURATED_REVIEW_APPROVED_EVENT,
        ).one()
        # Simulate a caller-owned identity map that predates the claim.  This
        # changes only SQLAlchemy's committed in-memory value, not the row.
        set_committed_value(
            approval_event, "event_type", VERSION_PUBLISHED_EVENT)

        with patch.object(
            IncrementalOrchestrator,
            "on_review_approved",
            return_value={
                "dataset_id": dataset.id,
                "review_id": review.id,
                "triggered_mappings": [],
            },
        ) as review_dispatch, patch.object(
            IncrementalOrchestrator,
            "on_dataset_version_published",
        ) as version_dispatch:
            result = drain_dataset_version_events(
                isolated_session, limit=1)

        assert result == {
            "processed": 1, "retried": 0, "lost_claims": 0}
        review_dispatch.assert_called_once_with(
            review.id, synchronous=True)
        version_dispatch.assert_not_called()
    finally:
        isolated_session.close()


def test_curated_approval_rejects_version_handler_result_identity(db):
    """审批事件拿到发布 handler 的结果形状时不得被伪确认完成。"""
    service = DatasetService(db, storage=MemoryStorage())
    dataset = service.create_dataset(
        "成品订单", "curated",
        schema_json={"primary_key": "id", "columns": ["id"]},
    )
    version = service.create_version(
        dataset.id, b"id\n1\n", rowcount=1)
    review = ReviewService(db).start_review(dataset.id)
    ReviewService(db).approve(review.id)
    published = db.query(DatasetVersionEvent).filter_by(
        dataset_version_id=version.id,
        event_type=VERSION_PUBLISHED_EVENT,
    ).one()
    published.status = "completed"
    db.commit()

    wrong_result = {
        "status": "completed",
        "dataset_id": dataset.id,
        "dataset_version_id": version.id,
        "manual_mapping": {
            "status": "skipped",
            "reason": "curated versions require the review-approved trigger",
        },
    }
    with patch.object(
        IncrementalOrchestrator,
        "on_review_approved",
        return_value=wrong_result,
    ):
        result = drain_dataset_version_events(db, limit=1)

    assert result == {"processed": 0, "retried": 1, "lost_claims": 0}
    db.expire_all()
    event = db.query(DatasetVersionEvent).filter_by(
        dataset_version_id=version.id,
        event_type=CURATED_REVIEW_APPROVED_EVENT,
    ).one()
    assert event.status == CURATED_REVIEW_RETRY_STATUS
    assert "curated_review_approved handler returned" in event.last_error
    assert event.result_json is None


def test_curated_approval_mapping_failure_remains_retryable(db):
    service = DatasetService(db, storage=MemoryStorage())
    dataset = service.create_dataset(
        "成品订单", "curated",
        schema_json={"primary_key": "id", "columns": ["id"]},
    )
    version = service.create_version(
        dataset.id, b"id\n1\n", rowcount=1)
    review = ReviewService(db).start_review(dataset.id)
    ReviewService(db).approve(review.id)
    published = db.query(DatasetVersionEvent).filter_by(
        dataset_version_id=version.id,
        event_type=VERSION_PUBLISHED_EVENT,
    ).one()
    published.status = "completed"
    db.commit()

    with patch.object(
        IncrementalOrchestrator,
        "on_review_approved",
        side_effect=RuntimeError("mapping barrier unavailable"),
    ):
        result = drain_dataset_version_events(db, limit=1)

    assert result == {"processed": 0, "retried": 1, "lost_claims": 0}
    event = db.query(DatasetVersionEvent).filter_by(
        dataset_version_id=version.id,
        event_type=CURATED_REVIEW_APPROVED_EVENT,
    ).one()
    assert event.status == CURATED_REVIEW_RETRY_STATUS
    assert event.attempts == 1
    assert "mapping barrier unavailable" in event.last_error


def test_superseded_events_are_coalesced_to_latest_snapshot(db):
    service, _storage, dataset = _manual_service(db)
    first = service.create_version(dataset.id, b"id,name\n1,A\n", rowcount=1)
    second = service.create_version(dataset.id, b"id,name\n1,B\n", rowcount=1)

    with patch.object(
        IncrementalOrchestrator,
        "on_dataset_version_published",
        return_value={
            "status": "completed",
            "dataset_id": dataset.id,
            "dataset_version_id": second.id,
            "manual_mapping": {
                "status": "no_subscribers",
                "ontologies": [],
            },
        },
    ) as dispatch:
        result = drain_dataset_version_events(db, limit=10)

    assert result == {"processed": 2, "retried": 0, "lost_claims": 0}
    dispatch.assert_called_once_with(dataset.id, second.id)
    events = db.query(DatasetVersionEvent).order_by(
        DatasetVersionEvent.created_at, DatasetVersionEvent.id).all()
    by_version = {event.dataset_version_id: event for event in events}
    assert by_version[first.id].result_json["reason"] == "superseded"
    assert by_version[second.id].status == "completed"


def test_dispatch_failure_is_persisted_for_retry(db):
    service, _storage, dataset = _manual_service(db)
    version = service.create_version(dataset.id, b"id,name\n1,A\n", rowcount=1)

    def fail_legacy_version_handler(_dataset_id, _version_id):
        active = db.query(DatasetVersionEvent).filter_by(
            dataset_version_id=version.id).one()
        # Ordinary version_published retains the legacy namespace.
        assert active.status == "processing"
        raise RuntimeError("mapping projection unavailable")

    with patch.object(
        IncrementalOrchestrator,
        "on_dataset_version_published",
        side_effect=fail_legacy_version_handler,
    ):
        result = drain_dataset_version_events(db, limit=1)

    assert result == {"processed": 0, "retried": 1, "lost_claims": 0}
    event = db.query(DatasetVersionEvent).filter_by(
        dataset_version_id=version.id).one()
    assert event.status == "retry"
    assert event.attempts == 1
    assert "mapping projection unavailable" in event.last_error
    assert event.claim_token is None and event.claimed_at is None
    assert event.available_at.replace(tzinfo=timezone.utc) > datetime.now(timezone.utc)


def test_stale_failure_owner_cannot_clobber_successor_claim(db):
    """超时 owner 报错时不得把已接管的 processing 事件改回 retry。"""
    service, _storage, dataset = _manual_service(db)
    version = service.create_version(
        dataset.id, b"id,name\n1,A\n", rowcount=1)
    successor_token = "successor-claim-token"

    def takeover_then_fail(active_db, event_id, _old_token):
        active_db.query(DatasetVersionEvent).filter(
            DatasetVersionEvent.id == event_id,
        ).update({
            DatasetVersionEvent.status: "processing",
            DatasetVersionEvent.claim_token: successor_token,
            DatasetVersionEvent.claimed_at: datetime.now(timezone.utc),
        }, synchronize_session=False)
        active_db.commit()
        raise RuntimeError("stale owner failed after takeover")

    with patch(
        "app.data_channel.datasets.version_events._process_claimed_event",
        side_effect=takeover_then_fail,
    ):
        result = drain_dataset_version_events(db, limit=1)

    assert result == {"processed": 0, "retried": 0, "lost_claims": 1}
    db.expire_all()
    event = db.query(DatasetVersionEvent).filter_by(
        dataset_version_id=version.id,
    ).one()
    assert event.status == "processing"
    assert event.claim_token == successor_token
    assert event.last_error is None


def test_only_explicit_manual_mapping_subscription_runs_build_all(db):
    service, _storage, dataset = _manual_service(db)
    version = service.create_version(dataset.id, b"id,name\n1,A\n", rowcount=1)
    db.add_all([
        OntologyMapping(
            id="subscribed", ontology_id="ontology-a",
            curated_dataset_id=dataset.id, entity_class="BusinessRow",
            field_mapping={
                "id": "id", "__primary_key__": "id",
                "__auto_apply_on_version__": True,
            }, status="applied",
        ),
        OntologyMapping(
            id="manual-only", ontology_id="ontology-b",
            curated_dataset_id=dataset.id, entity_class="BusinessRow",
            field_mapping={"id": "id", "__primary_key__": "id"},
            status="applied",
        ),
    ])
    db.commit()

    with patch(
        "app.services.v2.mapping.mapping_service.MappingService.build_all",
        return_value={
            "total_entities": 1, "total_relations": 0,
            "sentinel_dispatch": {"fired": 1, "errors": [], "runs": [{}]},
        },
    ) as build_all:
        result = IncrementalOrchestrator(db).on_dataset_version_published(
            dataset.id, version.id)

    build_all.assert_called_once_with("ontology-a", require_approved=True)
    assert result["manual_mapping"]["status"] == "applied"
    assert result["manual_mapping"]["ontologies"][0]["sentinel_dispatch"]["fired"] == 1


def test_dataset_version_does_not_hide_durable_sentinel_failure_with_manual_scan(
        db):
    service, _storage, dataset = _manual_service(db)
    version = service.create_version(
        dataset.id, b"id,name\n1,A\n", rowcount=1)
    db.add(OntologyMapping(
        id="sentinel-failure-subscription",
        ontology_id="ontology-sentinel-failure",
        curated_dataset_id=dataset.id,
        entity_class="BusinessRow",
        field_mapping={
            "id": "id",
            "__primary_key__": "id",
            "__auto_apply_on_version__": True,
        },
        status="applied",
    ))
    db.commit()

    with patch(
        "app.services.v2.mapping.mapping_service.MappingService.build_all",
        return_value={
            "total_entities": 1,
            "total_relations": 0,
            "sentinel_dispatch": {
                "fired": 0,
                "errors": [{"eventId": "failed-outbox"}],
                "runs": [{"status": "retry"}],
            },
        },
    ), patch(
        "app.services.sentinel.engine.run_manual",
    ) as manual_scan:
        with pytest.raises(
                RuntimeError, match="durable Sentinel 级联失败"):
            IncrementalOrchestrator(db).on_dataset_version_published(
                dataset.id, version.id)

    manual_scan.assert_not_called()


def test_manual_automation_requires_contract_and_verifiable_version(db):
    service, _storage, dataset = _manual_service(db)
    version = service.create_version(dataset.id, b"id,name\n1,A\n", rowcount=1)
    assert manual_dataset_automation_eligibility(dataset, version) == (
        True, "eligible")

    dataset.schema_json = {"origin": "manual"}
    db.commit()
    eligible, reason = manual_dataset_automation_eligibility(dataset, version)
    assert not eligible and "primary-key" in reason


def test_real_manual_version_to_sentinel_notification_closed_loop(
    db, monkeypatch,
):
    """Exercise the real relational Mapping, CDC, sentinel and action sinks.

    Neo4j/Chroma are rebuildable query projections and are replaced with their
    successful adapters here; every authoritative database transition and the
    notification side effect runs through production code.
    """
    from app.models.ontology import OntologyProject
    from app.models.ontology_formal import ActionType, ObjectInstance, ObjectType
    from app.models.sentinel import Notification, Sentinel, SentinelFiring
    from app.ontologies.mappings.mapping_service import MappingService
    from app.ontologies.sentinels.cdc import register_cdc

    storage = MemoryStorage()
    monkeypatch.setattr(
        "app.data_channel.datasets.service.get_storage_service",
        lambda: storage,
    )
    monkeypatch.setattr(
        "app.database.SessionLocal", sessionmaker(bind=db.get_bind()))
    monkeypatch.setattr(
        MappingService, "_rebuild_neo4j_projection", lambda *_args: True)
    monkeypatch.setattr(
        MappingService, "_rebuild_chroma_projection", lambda *_args: 1)
    # Mapping owns a synchronous CDC barrier in this test; a second background
    # consumer would only race it for the same SQLite outbox row.
    register_cdc(start_worker=False)

    ontology_id = "manual-closed-loop"
    project = OntologyProject(
        id=ontology_id, name="人工数据闭环", domain="test",
        created_by="closed-loop-user", status="draft", version="v0.1",
    )
    object_type = ObjectType(
        id="representative-type", ontology_id=ontology_id,
        name="Representative", display_name="业务代表",
        primary_key="prop_rep_id",
        properties=[
            {
                "id": "prop_rep_id", "name": "rep_id",
                "displayName": "代表编号", "type": "string",
                "required": True, "source": "stored",
            },
            {
                "id": "prop_inconsistent", "name": "inconsistent",
                "displayName": "是否不一致", "type": "boolean",
                "required": True, "source": "stored",
            },
        ],
        interfaces=[], position_x=0, position_y=0,
    )
    action = ActionType(
        id="notify-inconsistency", ontology_id=ontology_id,
        name="notify_inconsistency", display_name="不一致名单通知",
        object_type_id=object_type.id, parameters=[], requires_approval=False,
        rules=[{
            "id": "internal-notification",
            "type": "notification",
            "name": "发送站内通知",
            "enabled": True,
            "order": 0,
            "config": {
                "channel": "internal",
                "recipientSource": "constant",
                "recipient": "admin",
                "subject": "发现业务代表不一致",
                "messageTemplate": "人工数据变更后发现不一致记录",
            },
        }],
    )
    sentinel = Sentinel(
        id="watch-inconsistency", ontology_id=ontology_id,
        name="watch_inconsistency", display_name="业务代表不一致哨兵",
        bindings=[{"alias": "rep", "objectTypeId": object_type.id}],
        links=[], condition="rep.inconsistent == True", primary_alias="rep",
        action_ids=[action.id], action_parameters={},
        on_change=True, on_schedule=False, trigger_mode="on_enter",
        muted=False, enabled=True, status="draft",
    )
    db.add_all([project, object_type, action, sentinel])
    db.commit()

    service = DatasetService(db, storage=storage)
    dataset = service.create_dataset(
        "业务代表人工核对表", "structured",
        schema_json={
            "origin": "manual", "types_source": "declared",
            "primary_key": "rep_id", "pk_source": "manual",
            "columns": ["rep_id", "inconsistent"],
            "columns_typed": [
                {"name": "rep_id", "type": "string", "nullable": False},
                {"name": "inconsistent", "type": "boolean", "nullable": False},
            ],
        },
    )
    first_version = service.create_version(
        dataset.id, b"rep_id,inconsistent\nR-001,false\n", rowcount=1)
    mapping = OntologyMapping(
        id="representative-mapping", ontology_id=ontology_id,
        curated_dataset_id=dataset.id, entity_class="Representative",
        target_object_type_id=object_type.id, status="draft",
        field_mapping={
            "rep_id": "rep_id", "inconsistent": "inconsistent",
            "__primary_key__": "rep_id", "__pk_source__": "lake",
            "__auto_apply_on_version__": True,
        },
    )
    db.add(mapping)
    db.commit()

    # Runtime automation is release-only. Freeze the modeled definition and
    # mapping before either projection is allowed to reach the Sentinel engine;
    # the first (false) version then establishes the release-scoped baseline.
    from app.models.ontology_version import OntologyVersion
    from app.ontologies.versions.evolution_service import (
        complete_snapshot,
        snapshot_hash,
    )
    from app.ontologies.versions.router import _snapshot_formal

    release_id = "manual-closed-loop-release-v1"
    project.status = "published"
    project.version = "v1.0.0"
    sentinel.status = "published"
    release_snapshot = complete_snapshot(
        _snapshot_formal(db, ontology_id))
    release = OntologyVersion(
        id=release_id,
        ontology_id=ontology_id,
        version_number=project.version,
        version_label="人工数据闭环发布",
        base_release_id=release_id,
        node_kind="release",
        lifecycle_status="released",
        revision=0,
        snapshot_formal=release_snapshot,
        snapshot_hash=snapshot_hash(release_snapshot),
        published_at=datetime.now(timezone.utc),
        created_by=project.created_by,
    )
    db.add(release)
    db.flush()
    project.current_release_id = release_id
    db.commit()

    initial = MappingService(db).build_all(ontology_id, require_approved=True)
    assert initial["total_entities"] == 1
    assert mapping.status == "applied"
    assert (mapping.field_mapping or {})[
        "__applied_dataset_version_id__"] == first_version.id

    second_version = service.create_version(
        dataset.id, b"rep_id,inconsistent\nR-001,true\n", rowcount=1)
    outbox_result = drain_dataset_version_events(db, limit=10)

    db.expire_all()
    instance = db.query(ObjectInstance).filter_by(
        ontology_id=ontology_id, object_type_id=object_type.id).one()
    event = db.query(DatasetVersionEvent).filter_by(
        dataset_version_id=second_version.id).one()
    firing = db.query(SentinelFiring).filter_by(
        ontology_id=ontology_id, sentinel_id=sentinel.id,
        status="fired").one()
    notification = db.query(Notification).filter_by(
        ontology_id=ontology_id, action_id=action.id).one()

    assert outbox_result["retried"] == 0
    assert event.status == "completed"
    assert event.result_json["manual_mapping"]["status"] == "applied"
    assert instance.properties["inconsistent"] is True
    assert firing.trigger_source == "change" and firing.entered
    assert notification.recipient == "admin"
    assert notification.status == "delivered"
