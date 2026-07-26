"""Mapping CDC is released only after the projection is marked applied."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from sqlalchemy.orm import sessionmaker

from app.models.ontology import OntologyProject
from app.models.ontology_formal import (
    ActionType,
    ObjectInstance,
    ObjectType,
)
from app.models.ontology_version import OntologyVersion
from app.models.sentinel import Notification, SentinelCdcOutbox
from app.models.v2.mapping import OntologyMapping
from app.ontologies.sentinels.cdc import (
    CAPTURE_SUPPRESSED_KEY,
    MAPPING_SCOPE_KEY,
    SUPPRESS_KEY,
    cdc_dispatch_status,
    dispatch_captured_changes,
    discard_captured_changes,
    recover_held_outbox,
    register_cdc,
)
from app.services.sentinel.evaluator import in_sentinel_run


def _set_current_release(
        db, project: OntologyProject, release_id: str,
        version: str = "v1") -> OntologyVersion:
    release = OntologyVersion(
        id=release_id,
        ontology_id=project.id,
        version_number=version,
        version_label=version,
        base_release_id=release_id,
        node_kind="release",
        lifecycle_status="released",
        revision=0,
        snapshot_formal={},
        snapshot_hash=f"hash-{release_id}",
        created_by=project.created_by,
    )
    db.add(release)
    db.flush()
    project.current_release_id = release.id
    project.version = version
    db.commit()
    return release


def test_captured_mapping_change_observes_applied_fence(db, monkeypatch):
    register_cdc()
    project = OntologyProject(
        id="ontology-cdc", name="CDC", domain="test", status="published",
        created_by="user-not-required-by-sqlite",
    )
    object_type = ObjectType(
        id="object-type-cdc", ontology_id=project.id,
        name="Record", display_name="记录", properties=[], interfaces=[],
        position_x=0, position_y=0,
    )
    mapping = OntologyMapping(
        id="mapping-cdc", ontology_id=project.id,
        curated_dataset_id=None, entity_class="Record",
        field_mapping={}, status="projecting",
    )
    db.add_all([project, object_type, mapping])
    db.commit()
    release = _set_current_release(
        db, project, "ontology-cdc-release")

    db.info[SUPPRESS_KEY] = True
    db.info[CAPTURE_SUPPRESSED_KEY] = True
    db.info[MAPPING_SCOPE_KEY] = {mapping.id}
    db.add(ObjectInstance(
        id="instance-cdc", ontology_id=project.id,
        ontology_release_id=release.id,
        object_type_id=object_type.id, properties={"state": "changed"},
        computed={}, source="pipeline", external_id="row-1",
    ))
    db.commit()

    seen_statuses: list[str] = []
    held = db.query(SentinelCdcOutbox).filter_by(
        ontology_id=project.id,
        object_type_id=object_type.id,
    ).one()
    assert held.status == "held"
    assert held.mapping_ids == [mapping.id]
    assert held.ontology_release_id == release.id

    def fake_run_for_change(run_db, ontology_id, object_type_id, changed_keys):
        seen_statuses.append(run_db.query(OntologyMapping).filter_by(
            id=mapping.id).one().status)
        return {"evaluated": 1, "fired": 1, "firings": []}

    from app.services.sentinel import engine as sentinel_engine
    monkeypatch.setattr(sentinel_engine, "run_for_change", fake_run_for_change)
    monkeypatch.setattr(
        "app.database.SessionLocal", sessionmaker(bind=db.get_bind()))

    # The first commit captured the object delta but did not evaluate it.
    assert seen_statuses == []
    mapping.status = "applied"
    db.commit()
    result = dispatch_captured_changes(db)

    assert seen_statuses == ["applied"]
    assert result["evaluated"] == 1 and result["fired"] == 1
    assert db.info.get(SUPPRESS_KEY) is None
    db.expire_all()
    assert db.query(SentinelCdcOutbox).filter_by(
        id=held.id).one().status == "completed"


def test_failed_mapping_capture_is_discarded_before_session_reuse(db):
    db.info[SUPPRESS_KEY] = True
    db.info[CAPTURE_SUPPRESSED_KEY] = True
    held = SentinelCdcOutbox(
        id="discard-held",
        chain_id="discard-chain",
        ontology_id="discard-ontology",
        object_type_id="discard-type",
        changed_keys=[],
        cascade_depth=0,
        status="held",
        attempts=0,
        available_at=datetime.now(timezone.utc),
    )
    db.add(held)
    db.commit()
    held_id = held.id
    db.info[SUPPRESS_KEY] = True
    db.info[CAPTURE_SUPPRESSED_KEY] = True
    db.info["_sentinel_captured_outbox_ids"] = {held_id}

    discard_captured_changes(db)
    db.commit()

    assert db.info.get(SUPPRESS_KEY) is None
    assert db.info.get(CAPTURE_SUPPRESSED_KEY) is None
    assert db.query(SentinelCdcOutbox).filter_by(
        id=held_id).count() == 0
    assert dispatch_captured_changes(db) == {
        "evaluated": 0, "fired": 0, "errors": [],
    }


def test_captured_mapping_change_surfaces_nested_sentinel_errors(
        db, monkeypatch):
    db.info["_sentinel_captured_changes"] = {
        ("nested-error-ontology", "nested-error-type"): {"status"},
    }

    def fake_run_for_change(
            run_db, ontology_id, object_type_id, changed_keys):
        return {
            "evaluated": 1,
            "fired": 0,
            "errors": 1,
            "firings": [{
                "sentinelId": "broken-sentinel",
                "status": "error",
            }],
        }

    from app.services.sentinel import engine as sentinel_engine
    monkeypatch.setattr(
        sentinel_engine, "run_for_change", fake_run_for_change)
    monkeypatch.setattr(
        "app.database.SessionLocal", sessionmaker(bind=db.get_bind()))

    result = dispatch_captured_changes(db)

    assert result["evaluated"] == 1
    assert len(result["errors"]) == 1
    assert "1 个执行错误" in result["errors"][0]["error"]
    assert result["errors"][0]["firings"][0]["sentinelId"] == (
        "broken-sentinel")


def test_sentinel_action_write_is_enqueued_as_durable_cascade(
        db, monkeypatch):
    from app.ontologies.sentinels import cdc

    project = OntologyProject(
        id="cascade-ontology", name="Cascade", domain="test",
        status="published", created_by="tester",
    )
    db.add(project)
    db.commit()
    release = _set_current_release(
        db, project, "cascade-ontology-release")

    queued: list[set[str]] = []
    monkeypatch.setattr(cdc, "_enqueue_dispatch", queued.append)
    token = in_sentinel_run.set(True)
    try:
        row = cdc._outbox_row(
            db,
            ontology_id=project.id,
            ontology_release_id=release.id,
            object_type_id="cascade-type",
        )
    finally:
        in_sentinel_run.reset(token)
    assert row is not None
    assert row.cascade_depth == 1

    fake_session = type("FakeSession", (), {
        "info": {
            cdc._OUTBOX_IDS_KEY: {row.id},
        },
    })()
    cdc._after_commit(fake_session)

    assert queued == [{row.id}]


def test_cdc_skips_draft_projection_without_current_release(db):
    """Draft mapping rows are not production runtime events.

    CDC registration is process-global, so this also guards against test/order
    and worker-lifecycle effects: a directly-created legacy draft remains safe
    even after another request has started the Sentinel listeners.
    """
    register_cdc()
    project = OntologyProject(
        id="draft-only-cdc-ontology", name="Draft only CDC",
        domain="test", status="draft", created_by="tester",
    )
    object_type = ObjectType(
        id="draft-only-cdc-type", ontology_id=project.id,
        name="DraftRecord", display_name="Draft record",
        properties=[], interfaces=[], position_x=0, position_y=0,
    )
    db.add_all([project, object_type])
    db.commit()
    assert project.current_release_id is None

    db.info[SUPPRESS_KEY] = True
    db.info[CAPTURE_SUPPRESSED_KEY] = True
    db.add(ObjectInstance(
        id="draft-only-cdc-instance",
        ontology_id=project.id,
        ontology_release_id=None,
        object_type_id=object_type.id,
        properties={"state": "draft"},
        computed={},
        source="pipeline",
    ))
    db.commit()

    assert db.query(SentinelCdcOutbox).filter_by(
        ontology_id=project.id).count() == 0
    assert dispatch_captured_changes(db) == {
        "evaluated": 0, "fired": 0, "errors": [],
    }


def test_cdc_tracks_computed_keys_on_create_and_computed_only_update(db):
    register_cdc()
    project = OntologyProject(
        id="computed-cdc-ontology", name="Computed CDC", domain="test",
        status="published", created_by="tester",
    )
    object_type = ObjectType(
        id="computed-cdc-type", ontology_id=project.id,
        name="Computed", display_name="Computed",
        properties=[], interfaces=[], position_x=0, position_y=0,
    )
    db.add_all([project, object_type])
    db.commit()
    release = _set_current_release(
        db, project, "computed-cdc-release")
    db.info[SUPPRESS_KEY] = True
    db.info[CAPTURE_SUPPRESSED_KEY] = True
    instance = ObjectInstance(
        id="computed-cdc-instance", ontology_id=project.id,
        ontology_release_id=release.id,
        object_type_id=object_type.id,
        properties={"source": 1}, computed={"risk": 10},
    )
    db.add(instance)
    db.commit()

    created = db.query(SentinelCdcOutbox).filter_by(
        ontology_id=project.id).order_by(
        SentinelCdcOutbox.created_at.asc()).all()
    assert len(created) == 1
    assert created[0].ontology_release_id == release.id
    assert set(created[0].changed_keys) == {"source", "risk"}
    created_id = created[0].id

    instance.computed = {"risk": 11, "score": 99}
    db.commit()
    updated = db.query(SentinelCdcOutbox).filter(
        SentinelCdcOutbox.ontology_id == project.id,
        SentinelCdcOutbox.id != created_id,
    ).one()
    assert set(updated.changed_keys) == {"risk", "score"}
    assert "source" not in updated.changed_keys
    discard_captured_changes(db)
    db.commit()


def test_cdc_capture_falls_back_to_transaction_current_release(db):
    register_cdc()
    project = OntologyProject(
        id="capture-current-ontology", name="Capture current",
        domain="test", status="published", created_by="tester",
    )
    object_type = ObjectType(
        id="capture-current-type", ontology_id=project.id,
        name="Current", display_name="Current",
        properties=[], interfaces=[], position_x=0, position_y=0,
    )
    db.add_all([project, object_type])
    db.commit()
    release = _set_current_release(
        db, project, "capture-current-release")
    db.info[SUPPRESS_KEY] = True
    db.info[CAPTURE_SUPPRESSED_KEY] = True
    db.add(ObjectInstance(
        id="capture-current-instance",
        ontology_id=project.id,
        # Legacy/unattributed input: capture must pin the event now instead of
        # resolving whichever release happens to be current at consumption.
        ontology_release_id=None,
        object_type_id=object_type.id,
        properties={"state": "ready"},
        computed={},
    ))
    db.commit()

    event_row = db.query(SentinelCdcOutbox).filter_by(
        ontology_id=project.id).one()
    assert event_row.ontology_release_id == release.id
    discard_captured_changes(db)
    db.commit()


def test_durable_outbox_recovers_without_in_memory_queue(db, monkeypatch):
    from app.ontologies.sentinels import cdc
    from app.services.sentinel import engine as sentinel_engine

    event = SentinelCdcOutbox(
        id="restart-event",
        chain_id="restart-chain",
        ontology_id="restart-ontology",
        object_type_id="restart-type",
        changed_keys=["status"],
        cascade_depth=0,
        status="pending",
        attempts=0,
        available_at=cdc._now(),
    )
    db.add(event)
    db.commit()
    observed: list[tuple[str, str, list[str]]] = []

    def fake_run_for_change(
            run_db, ontology_id, object_type_id, changed_keys):
        observed.append((ontology_id, object_type_id, changed_keys))
        return {"evaluated": 1, "fired": 1, "errors": 0, "firings": []}

    monkeypatch.setattr(
        sentinel_engine, "run_for_change", fake_run_for_change)
    factory = sessionmaker(bind=db.get_bind())

    result = cdc.drain_cdc_outbox(
        event_ids={event.id}, session_factory=factory)

    db.expire_all()
    recovered = db.query(SentinelCdcOutbox).filter_by(id=event.id).one()
    assert result["processed"] == 1
    assert observed == [(
        "restart-ontology", "restart-type", ["status"])]
    assert recovered.status == "completed"
    assert recovered.attempts == 1


def test_superseded_release_event_completes_stale_without_execution(
        db, monkeypatch):
    from app.ontologies.sentinels import cdc
    from app.services.sentinel import engine as sentinel_engine

    project = OntologyProject(
        id="stale-cdc-ontology", name="Stale CDC", domain="test",
        status="published", created_by="tester",
    )
    db.add(project)
    db.commit()
    old_release = _set_current_release(
        db, project, "stale-release-v1", "v1")
    current_release = _set_current_release(
        db, project, "stale-release-v2", "v2")
    event = SentinelCdcOutbox(
        id="stale-release-event",
        chain_id="stale-release-chain",
        ontology_id=project.id,
        ontology_release_id=old_release.id,
        object_type_id="stale-release-type",
        changed_keys=["state"],
        cascade_depth=0,
        status="pending",
        attempts=0,
        available_at=cdc._now(),
    )
    db.add(event)
    db.commit()

    def must_not_run(*args, **kwargs):
        raise AssertionError("superseded event must not enter Sentinel engine")

    monkeypatch.setattr(
        sentinel_engine, "run_for_change", must_not_run)
    factory = sessionmaker(bind=db.get_bind(), expire_on_commit=False)

    result = cdc.drain_cdc_outbox(
        event_ids={event.id}, session_factory=factory)

    db.expire_all()
    completed = db.query(SentinelCdcOutbox).filter_by(
        id=event.id).one()
    assert result["processed"] == 1
    assert result["stale"] == 1
    assert result["errors"] == []
    assert completed.status == "completed"
    assert completed.attempts == 1
    assert completed.last_error is None
    assert completed.result_json == {
        "evaluated": 0,
        "fired": 0,
        "errors": 0,
        "firings": [],
        "status": "stale",
        "outcome": "superseded",
        "superseded": True,
        "skipped": "release_superseded",
        "eventReleaseId": old_release.id,
        "currentReleaseId": current_release.id,
    }


def test_release_change_between_engine_transactions_stops_cascade(
        db, monkeypatch):
    from app.ontologies.sentinels import cdc
    from app.services.sentinel import engine as sentinel_engine

    project = OntologyProject(
        id="mid-run-release-ontology", name="Mid-run release",
        domain="test", status="published", created_by="tester",
    )
    db.add(project)
    db.commit()
    original_release = _set_current_release(
        db, project, "mid-run-release-v1", "v1")
    replacement_release = OntologyVersion(
        id="mid-run-release-v2",
        ontology_id=project.id,
        version_number="v2",
        version_label="v2",
        base_release_id="mid-run-release-v2",
        node_kind="release",
        lifecycle_status="released",
        revision=0,
        snapshot_formal={},
        snapshot_hash="hash-mid-run-release-v2",
        created_by=project.created_by,
    )
    event_row = SentinelCdcOutbox(
        id="mid-run-release-event",
        chain_id="mid-run-release-chain",
        ontology_id=project.id,
        ontology_release_id=original_release.id,
        object_type_id="mid-run-release-type",
        changed_keys=["state"],
        cascade_depth=0,
        status="pending",
        attempts=0,
        available_at=cdc._now(),
    )
    db.add_all([replacement_release, event_row])
    db.commit()
    factory = sessionmaker(bind=db.get_bind(), expire_on_commit=False)
    reached_after_promotion: list[bool] = []

    def promote_between_transactions(
            run_db, ontology_id, object_type_id, changed_keys):
        # Mirrors evaluator's staged durable commits.
        run_db.commit()
        promotion_db = factory()
        try:
            promoted_project = promotion_db.query(
                OntologyProject).filter_by(id=ontology_id).one()
            promoted_project.current_release_id = replacement_release.id
            promoted_project.version = replacement_release.version_number
            promotion_db.commit()
        finally:
            promotion_db.close()
        # Beginning the next action/evaluation transaction must hit the guard
        # before any new-release state can be read or acted upon.
        run_db.query(OntologyProject.id).filter_by(
            id=ontology_id).one()
        reached_after_promotion.append(True)
        return {
            "evaluated": 1, "fired": 1,
            "errors": 0, "firings": [],
        }

    monkeypatch.setattr(
        sentinel_engine, "run_for_change",
        promote_between_transactions,
    )

    result = cdc.drain_cdc_outbox(
        event_ids={event_row.id}, session_factory=factory)

    db.expire_all()
    completed = db.query(SentinelCdcOutbox).filter_by(
        id=event_row.id).one()
    assert reached_after_promotion == []
    assert result["processed"] == 1
    assert result["stale"] == 1
    assert completed.status == "completed"
    assert completed.result_json["outcome"] == "superseded"
    assert completed.result_json["eventReleaseId"] == original_release.id
    assert completed.result_json["currentReleaseId"] == (
        replacement_release.id)


def test_durable_retry_can_execute_production_action_after_projection_commit(
        db, monkeypatch):
    from app.config import settings
    from app.ontologies.sentinels import cdc
    from app.services.formal.action_engine import execute_action
    from app.services.sentinel import engine as sentinel_engine

    project = OntologyProject(
        id="retry-action-ontology", name="Retry action", domain="test",
        status="published", created_by="tester",
    )
    object_type = ObjectType(
        id="retry-action-type", ontology_id=project.id,
        name="Record", display_name="Record",
        properties=[{
            "id": "state", "name": "state", "type": "string",
        }],
        interfaces=[], position_x=0, position_y=0,
    )
    instance = ObjectInstance(
        id="retry-action-instance", ontology_id=project.id,
        object_type_id=object_type.id, properties={"state": "ready"},
        computed={},
    )
    mapping = OntologyMapping(
        id="retry-action-mapping", ontology_id=project.id,
        curated_dataset_id=None, entity_class="Record",
        field_mapping={}, status="applied",
    )
    action = ActionType(
        id="retry-action", ontology_id=project.id,
        name="notify_retry", display_name="Notify retry",
        object_type_id=object_type.id, parameters=[], rules=[{
            "id": "notify", "type": "notification", "name": "notify",
            "enabled": True, "order": 0,
            "config": {
                "channel": "internal",
                "recipientSource": "constant",
                "recipient": "ops",
                "messageTemplate": "recovered={{object.state}}",
            },
        }],
    )
    event = SentinelCdcOutbox(
        id="retry-action-event", chain_id="retry-action-chain",
        ontology_id=project.id, object_type_id=object_type.id,
        changed_keys=["state"], cascade_depth=0,
        mapping_ids=[mapping.id], status="retry", attempts=1,
        available_at=cdc._now() - timedelta(seconds=1),
        last_error="first synchronous dispatch failed",
    )
    db.add_all([
        project, object_type, instance, mapping, action, event])
    db.commit()
    monkeypatch.setattr(settings, "environment", "production")

    def run_action_for_change(
            run_db, ontology_id, object_type_id, changed_keys):
        result = execute_action(
            run_db,
            ontology_id,
            SimpleNamespace(
                action_id=action.id,
                target_instance_id=instance.id,
                parameters={},
                dry_run=False,
                idempotency_key="durable-retry-action",
            ),
        )
        succeeded = result["status"] == "success"
        return {
            "evaluated": 1,
            "fired": int(succeeded),
            "errors": 0 if succeeded else 1,
            "firings": [{
                "actionId": action.id,
                "status": result["status"],
                "error": result.get("errorMessage"),
            }],
        }

    monkeypatch.setattr(
        sentinel_engine, "run_for_change", run_action_for_change)
    factory = sessionmaker(bind=db.get_bind(), expire_on_commit=False)

    result = cdc.drain_cdc_outbox(
        event_ids={event.id}, session_factory=factory)

    db.expire_all()
    recovered = db.query(SentinelCdcOutbox).filter_by(id=event.id).one()
    notification = db.query(Notification).filter_by(
        ontology_id=project.id, action_id=action.id).one()
    assert result["processed"] == 1
    assert result["errors"] == []
    assert recovered.status == "completed"
    assert recovered.last_error is None
    assert notification.body == "recovered=ready"


def test_outbox_claim_is_compare_and_set_safe_on_sqlite(db):
    from app.ontologies.sentinels import cdc

    event = SentinelCdcOutbox(
        id="claim-event",
        chain_id="claim-chain",
        ontology_id="claim-ontology",
        object_type_id="claim-type",
        changed_keys=[],
        cascade_depth=0,
        status="pending",
        attempts=0,
        available_at=cdc._now(),
    )
    db.add(event)
    db.commit()
    factory = sessionmaker(bind=db.get_bind())
    first = factory()
    second = factory()
    try:
        now = cdc._now()
        stale_before = now
        first_token = cdc._claim_one(
            first, event.id, now, stale_before)
        second_token = cdc._claim_one(
            second, event.id, now, stale_before)
    finally:
        first.close()
        second.close()

    assert first_token
    assert second_token is None


def test_chain_barrier_surfaces_downstream_failure(db, monkeypatch):
    from app.ontologies.sentinels import cdc
    from app.services.sentinel import engine as sentinel_engine

    root = SentinelCdcOutbox(
        id="barrier-root",
        chain_id="barrier-chain",
        ontology_id="barrier-ontology",
        object_type_id="root-type",
        changed_keys=["state"],
        cascade_depth=0,
        status="pending",
        attempts=0,
        available_at=cdc._now(),
    )
    db.add(root)
    db.commit()

    def fake_run_for_change(
            run_db, ontology_id, object_type_id, changed_keys):
        if object_type_id == "root-type":
            run_db.add(SentinelCdcOutbox(
                id="barrier-downstream",
                chain_id=cdc._cascade_chain_id.get(),
                ontology_id=ontology_id,
                object_type_id="downstream-type",
                changed_keys=["alert"],
                cascade_depth=cdc._cascade_depth.get() + 1,
                status="pending",
                attempts=0,
                available_at=cdc._now(),
            ))
            run_db.commit()
            return {
                "evaluated": 1, "fired": 1,
                "errors": 0, "firings": [],
            }
        return {
            "evaluated": 1, "fired": 0,
            "errors": 1,
            "firings": [{
                "sentinelId": "downstream-broken",
                "status": "error",
            }],
        }

    monkeypatch.setattr(
        sentinel_engine, "run_for_change", fake_run_for_change)
    factory = sessionmaker(bind=db.get_bind())

    barrier = cdc._drain_chain_barrier(
        {root.chain_id}, session_factory=factory)

    db.expire_all()
    downstream = db.query(SentinelCdcOutbox).filter_by(
        id="barrier-downstream").one()
    assert barrier["completed"] is False
    assert barrier["errors"]
    assert downstream.status == "retry"
    assert downstream.result_json["errors"] == 1


def test_restart_recovers_held_event_only_after_its_mapping_is_applied(db):
    project = OntologyProject(
        id="restart-held-ontology", name="Restart held", domain="test",
        status="published", created_by="tester",
    )
    mapping = OntologyMapping(
        id="restart-held-mapping", ontology_id=project.id,
        curated_dataset_id=None, entity_class="Record",
        field_mapping={}, status="projecting",
    )
    event = SentinelCdcOutbox(
        id="restart-held-event",
        chain_id="restart-held-chain",
        ontology_id=project.id,
        object_type_id="restart-held-type",
        changed_keys=["state"],
        cascade_depth=0,
        mapping_ids=[mapping.id],
        status="held",
        attempts=0,
        available_at=datetime.now(timezone.utc),
    )
    db.add_all([project, mapping, event])
    db.commit()
    factory = sessionmaker(bind=db.get_bind(), expire_on_commit=False)

    waiting = recover_held_outbox(session_factory=factory)
    db.expire_all()
    row = db.query(SentinelCdcOutbox).filter_by(id=event.id).one()
    assert waiting == {
        "examined": 1, "activated": 0, "waiting": 1, "errors": [],
    }
    assert row.status == "held"
    assert "projecting" in row.last_error

    mapping.status = "applied"
    row.available_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.commit()
    recovered = recover_held_outbox(session_factory=factory)
    db.expire_all()
    row = db.query(SentinelCdcOutbox).filter_by(id=event.id).one()
    assert recovered == {
        "examined": 1, "activated": 1, "waiting": 0, "errors": [],
    }
    assert row.status == "pending"
    assert row.last_error is None


def test_held_recovery_rotates_past_blocked_prefix(db):
    project = OntologyProject(
        id="held-rotation-ontology", name="Held rotation", domain="test",
        status="published", created_by="tester",
    )
    blocked_mapping = OntologyMapping(
        id="held-blocked-mapping", ontology_id=project.id,
        curated_dataset_id=None, entity_class="Blocked",
        field_mapping={}, status="failed",
    )
    ready_mapping = OntologyMapping(
        id="held-ready-mapping", ontology_id=project.id,
        curated_dataset_id=None, entity_class="Ready",
        field_mapping={}, status="applied",
    )
    now = datetime.now(timezone.utc)
    blocked = SentinelCdcOutbox(
        id="held-blocked-event", chain_id="held-blocked-chain",
        ontology_id=project.id, object_type_id="blocked-type",
        changed_keys=[], cascade_depth=0,
        mapping_ids=[blocked_mapping.id], status="held", attempts=0,
        available_at=now,
        created_at=now - timedelta(seconds=2),
        updated_at=now - timedelta(seconds=2),
    )
    ready = SentinelCdcOutbox(
        id="held-ready-event", chain_id="held-ready-chain",
        ontology_id=project.id, object_type_id="ready-type",
        changed_keys=[], cascade_depth=0,
        mapping_ids=[ready_mapping.id], status="held", attempts=0,
        available_at=now,
        created_at=now - timedelta(seconds=1),
        updated_at=now - timedelta(seconds=1),
    )
    db.add_all([
        project, blocked_mapping, ready_mapping, blocked, ready])
    db.commit()
    factory = sessionmaker(bind=db.get_bind(), expire_on_commit=False)

    first = recover_held_outbox(limit=1, session_factory=factory)
    second = recover_held_outbox(limit=1, session_factory=factory)
    db.expire_all()

    assert first["waiting"] == 1
    assert second["activated"] == 1
    assert db.query(SentinelCdcOutbox).filter_by(
        id=blocked.id).one().status == "held"
    assert db.query(SentinelCdcOutbox).filter_by(
        id=ready.id).one().status == "pending"


def test_cdc_status_is_ontology_scoped_and_exposes_retry_errors(db):
    now = datetime.now(timezone.utc)
    project_a = OntologyProject(
        id="status-ontology-a", name="Status A", domain="test",
        status="published", created_by="tester",
    )
    project_b = OntologyProject(
        id="status-ontology-b", name="Status B", domain="test",
        status="published", created_by="tester",
    )
    db.add_all([project_a, project_b])
    db.commit()
    release_a = _set_current_release(
        db, project_a, "status-release-a")
    release_b = _set_current_release(
        db, project_b, "status-release-b")
    db.add_all([
        SentinelCdcOutbox(
            id="status-retry", chain_id="status-chain-a",
            ontology_id="status-ontology-a", object_type_id="type-a",
            ontology_release_id=release_a.id,
            changed_keys=[], cascade_depth=0, mapping_ids=[],
            status="retry", attempts=2, available_at=now,
            last_error="action parameter missing",
        ),
        SentinelCdcOutbox(
            id="status-dead-other", chain_id="status-chain-b",
            ontology_id="status-ontology-b", object_type_id="type-b",
            ontology_release_id=release_b.id,
            changed_keys=[], cascade_depth=0, mapping_ids=[],
            status="dead", attempts=4, available_at=now,
            last_error="webhook exhausted",
        ),
    ])
    db.commit()
    factory = sessionmaker(bind=db.get_bind(), expire_on_commit=False)

    status = cdc_dispatch_status(
        "status-ontology-a", session_factory=factory)

    assert status["healthy"] is False
    assert status["quiescent"] is False
    assert status["durable"]["retry"] == 1
    assert status["durable"]["dead"] == 0
    assert [item["eventId"] for item in status["last_errors"]] == [
        "status-retry"]


def test_cdc_status_defaults_to_current_release_and_history_is_explicit(db):
    now = datetime.now(timezone.utc)
    project = OntologyProject(
        id="status-release-scope", name="Status release scope",
        domain="test", status="published", created_by="tester",
    )
    db.add(project)
    db.commit()
    old_release = _set_current_release(
        db, project, "status-scope-v1", "v1")
    current_release = _set_current_release(
        db, project, "status-scope-v2", "v2")
    db.add_all([
        SentinelCdcOutbox(
            id="status-old-dead", chain_id="status-old-chain",
            ontology_id=project.id,
            ontology_release_id=old_release.id,
            object_type_id="type-old", changed_keys=[],
            cascade_depth=0, mapping_ids=[], status="dead",
            attempts=4, available_at=now,
            last_error="old release failure",
        ),
        SentinelCdcOutbox(
            id="status-current-retry", chain_id="status-current-chain",
            ontology_id=project.id,
            ontology_release_id=current_release.id,
            object_type_id="type-current", changed_keys=[],
            cascade_depth=0, mapping_ids=[], status="retry",
            attempts=2, available_at=now,
            last_error="current release failure",
        ),
    ])
    db.commit()
    factory = sessionmaker(bind=db.get_bind(), expire_on_commit=False)

    current = cdc_dispatch_status(
        project.id, session_factory=factory)
    history = cdc_dispatch_status(
        project.id, include_history=True, session_factory=factory)

    assert current["scope"] == "current_release"
    assert current["ontology_release_id"] == current_release.id
    assert current["durable"]["retry"] == 1
    assert current["durable"]["dead"] == 0
    assert [item["eventId"] for item in current["last_errors"]] == [
        "status-current-retry"]
    assert history["scope"] == "history"
    assert history["durable"]["retry"] == 1
    assert history["durable"]["dead"] == 1
    assert {
        item["eventId"] for item in history["recent_events"]
    } == {"status-current-retry", "status-old-dead"}


def test_cdc_status_fails_health_when_enabled_worker_is_dead(
        db, monkeypatch):
    from app.ontologies.sentinels import cdc

    monkeypatch.setattr(cdc, "AUTO_DISPATCH", True)
    monkeypatch.setattr(cdc, "_dispatch_worker", None)
    factory = sessionmaker(bind=db.get_bind(), expire_on_commit=False)

    status = cdc.cdc_dispatch_status(
        "worker-health-ontology", session_factory=factory)

    assert status["worker_alive"] is False
    assert status["healthy"] is False


def test_authenticated_cdc_status_endpoint(
        client, auth_headers, ontology, db):
    project = db.query(OntologyProject).filter_by(
        id=ontology["id"]).one()
    event = SentinelCdcOutbox(
        id="http-status-dead", chain_id="http-status-chain",
        ontology_id=ontology["id"], object_type_id="type-http",
        ontology_release_id=project.current_release_id,
        changed_keys=[], cascade_depth=0, mapping_ids=[],
        status="dead", attempts=4,
        available_at=datetime.now(timezone.utc),
        last_error="downstream action failed",
    )
    db.add(event)
    db.commit()

    unauthenticated = client.get(
        f"/api/v1/ontologies/{ontology['id']}/sentinels/cdc-status")
    assert unauthenticated.status_code in (401, 403)

    response = client.get(
        f"/api/v1/ontologies/{ontology['id']}/sentinels/cdc-status",
        headers=auth_headers,
    )
    assert response.status_code == 200, response.text
    payload = response.json()["data"]
    assert payload["healthy"] is False
    assert payload["quiescent"] is True
    assert payload["durable"]["dead"] == 1
    assert payload["dead_letters"][0]["eventId"] == event.id
