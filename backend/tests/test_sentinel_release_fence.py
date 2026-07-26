"""Sentinel execution must be pinned to one immutable release projection."""
from datetime import datetime, timedelta, timezone

import pytest


def test_postgresql_release_lock_modes_are_lease_compatible(db):
    """The cross-connection CDC lease must not block its own runtime fence."""
    from sqlalchemy.dialects import postgresql

    from app.models.ontology import OntologyProject
    from app.ontologies.sentinels.cdc import _release_pointer_statement

    dialect = postgresql.dialect()
    lease_sql = str(_release_pointer_statement(
        "ontology-lock-contract",
        postgresql_lock=True,
    ).compile(dialect=dialect))
    runtime_sql = str(
        db.query(OntologyProject)
        .filter(OntologyProject.id == "ontology-lock-contract")
        .with_for_update(key_share=True)
        .statement.compile(dialect=dialect)
    )

    assert lease_sql.endswith("FOR KEY SHARE")
    assert runtime_sql.endswith("FOR NO KEY UPDATE")


def _sentinel(ontology_id: str, *, sentinel_id: str, enabled: bool = True):
    from app.models.sentinel import Sentinel

    return Sentinel(
        id=sentinel_id,
        ontology_id=ontology_id,
        name=sentinel_id,
        display_name=sentinel_id,
        bindings=[],
        links=[],
        condition=None,
        primary_alias=None,
        action_ids=[],
        action_parameters={},
        enabled=enabled,
        muted=False,
        status="published",
        origin="release_builtin",
    )


def _freeze_live_projection(db, ontology_id: str, release_id: str) -> dict:
    from app.models.ontology_version import OntologyVersion
    from app.ontologies.versions.evolution_service import snapshot_hash
    from app.ontologies.versions.router import _snapshot_formal

    snapshot = _snapshot_formal(db, ontology_id)
    release = db.query(OntologyVersion).filter_by(id=release_id).one()
    release.snapshot_formal = snapshot
    release.snapshot_hash = snapshot_hash(snapshot)
    release.published_at = datetime.now(timezone.utc)
    db.commit()
    return snapshot


def test_runtime_ignores_builtin_definition_absent_from_release(
    ontology, db,
):
    from app.models.sentinel import SentinelFiring
    from app.services.sentinel.engine import run_manual

    db.add(_sentinel(
        ontology["id"], sentinel_id="sentinel-unreleased-ghost"))
    db.commit()

    result = run_manual(db, ontology["id"])

    assert result["evaluated"] == 0
    assert result["errors"] == 0
    assert result["runtimeErrors"] == []
    assert db.query(SentinelFiring).count() == 0


def test_runtime_uses_released_definition_while_live_projection_drifts(
    ontology, db, monkeypatch,
):
    from types import SimpleNamespace

    from app.ontologies.sentinels import engine

    sentinel = _sentinel(
        ontology["id"], sentinel_id="sentinel-released")
    db.add(sentinel)
    db.flush()
    _freeze_live_projection(
        db, ontology["id"], ontology["current_release_id"])

    sentinel.condition = "forged.value == 1"
    sentinel.status = "draft"
    sentinel.enabled = False
    sentinel.muted = True
    db.commit()
    observed = {}

    def capture(
        _db, _ontology_id, released_sentinel, _source,
        expected_release_id=None,
    ):
        observed["condition"] = released_sentinel.condition
        observed["enabled"] = released_sentinel.enabled
        observed["muted"] = released_sentinel.muted
        observed["release_id"] = expected_release_id
        return SimpleNamespace(
            sentinel_id=released_sentinel.id,
            sentinel_name=released_sentinel.display_name,
            status="no_change",
            match_count=0,
            entered=[],
            left=[],
            action_results=[],
            error=None,
        )

    monkeypatch.setattr(engine, "evaluate_sentinel", capture)

    result = engine.run_manual(db, ontology["id"])

    assert result["evaluated"] == 1
    assert result["errors"] == 0
    assert observed == {
        "condition": None,
        "enabled": True,
        "muted": False,
        "release_id": ontology["current_release_id"],
    }


def test_evaluator_uses_snapshot_when_live_builtin_becomes_draft(
    ontology, db,
):
    from app.models.ontology_formal import ObjectInstance, ObjectType
    from app.ontologies.release_context import current_release_context
    from app.ontologies.sentinels.engine import _runtime_sentinels
    from app.ontologies.sentinels.evaluator import evaluate_sentinel

    ontology_id = ontology["id"]
    release_id = ontology["current_release_id"]
    object_type = ObjectType(
        id="draft-drift-type",
        ontology_id=ontology_id,
        name="DraftDriftItem",
        display_name="Draft drift item",
        primary_key="id",
        properties=[{
            "id": "state",
            "name": "state",
            "displayName": "State",
            "type": "string",
            "required": False,
        }],
    )
    instance = ObjectInstance(
        id="draft-drift-object",
        ontology_id=ontology_id,
        ontology_release_id=release_id,
        object_type_id=object_type.id,
        properties={"state": "ready"},
    )
    sentinel = _sentinel(
        ontology_id, sentinel_id="draft-drift-sentinel")
    sentinel.bindings = [{
        "alias": "item",
        "objectTypeId": object_type.id,
    }]
    sentinel.primary_alias = "item"
    sentinel.condition = "item.state == 'ready'"
    db.add_all([object_type, instance, sentinel])
    db.flush()
    _freeze_live_projection(db, ontology_id, release_id)
    context = current_release_context(
        db, ontology_id, expected_release_id=release_id)
    selected = next(
        item for item in _runtime_sentinels(db, context)
        if item.id == sentinel.id
    )

    sentinel.condition = "forged.value == 1"
    sentinel.status = "draft"
    sentinel.enabled = False
    sentinel.muted = True
    db.commit()

    firing = evaluate_sentinel(
        db, ontology_id, selected, "manual",
        expected_release_id=release_id,
    )

    assert firing.status == "skipped"
    assert firing.match_count == 1
    assert firing.error is None


def test_builtin_hitl_resume_survives_deleted_live_projection(ontology, db):
    from types import SimpleNamespace

    from app.models.ontology_formal import (
        ActionExecutionLog,
        ActionType,
        ObjectInstance,
        ObjectType,
    )
    from app.models.sentinel import SentinelMatchState
    from app.services.formal.action_engine import execute_action
    from app.services.sentinel.engine import run_manual
    from app.services.sentinel.evaluator import resume_sentinel_match_claim

    ontology_id = ontology["id"]
    release_id = ontology["current_release_id"]
    object_type = ObjectType(
        id="hitl-deleted-live-type", ontology_id=ontology_id,
        name="HitlItem", display_name="HITL item", primary_key="id",
        properties=[
            {
                "id": "id", "name": "id", "displayName": "ID",
                "type": "string", "required": True,
            },
            {
                "id": "status", "name": "status", "displayName": "Status",
                "type": "string", "required": False,
            },
        ],
    )
    action = ActionType(
        id="hitl-deleted-live-action", ontology_id=ontology_id,
        name="approve_item", display_name="Approve item",
        object_type_id=object_type.id, parameters=[],
        rules=[{
            "id": "approve-status",
            "type": "update_property",
            "name": "Approve status",
            "enabled": True,
            "order": 0,
            "config": {
                "targetProperty": "status",
                "valueSource": "constant",
                "value": "\"approved\"",
            },
        }],
        requires_approval=True,
    )
    instance = ObjectInstance(
        id="hitl-deleted-live-object", ontology_id=ontology_id,
        ontology_release_id=release_id,
        object_type_id=object_type.id,
        properties={"id": "item-1", "status": "waiting"},
    )
    sentinel = _sentinel(
        ontology_id, sentinel_id="hitl-deleted-live-sentinel")
    sentinel.bindings = [{
        "alias": "item", "objectTypeId": object_type.id,
    }]
    sentinel.condition = "item.status == 'waiting'"
    sentinel.primary_alias = "item"
    sentinel.action_ids = [action.id]
    db.add_all([object_type, action, instance, sentinel])
    db.flush()
    _freeze_live_projection(db, ontology_id, release_id)
    db.delete(sentinel)
    db.commit()

    first = run_manual(db, ontology_id)

    assert first["evaluated"] == 1
    assert first["pending"] == 1
    state = db.query(SentinelMatchState).filter_by(
        sentinel_id="hitl-deleted-live-sentinel").one()
    pending = db.query(ActionExecutionLog).filter_by(
        sentinel_match_state_id=state.id, status="pending").one()
    assert state.runtime_status == "pending_enter"
    assert pending.ontology_release_id == release_id
    # Both initial evaluation and HITL recovery are owned by the immutable
    # release snapshot; deletion of the next-editing projection cannot stop it.

    approved_execution = execute_action(
        db,
        ontology_id,
        SimpleNamespace(
            action_id=action.id,
            target_instance_id=instance.id,
            parameters={},
            dry_run=False,
            idempotency_key=None,
            sentinel_match_state_id=state.id,
            sentinel_id=state.sentinel_id,
            expected_release_id=release_id,
            target_snapshot=None,
        ),
        skip_approval=True,
        expected_release_id=release_id,
    )
    assert approved_execution["status"] == "success"
    pending.status = "approved"
    pending.related_log_id = approved_execution["id"]
    db.commit()

    resumed = resume_sentinel_match_claim(db, ontology_id, state.id)

    assert resumed["status"] == "fired"
    db.refresh(instance)
    assert instance.properties["status"] == "approved"


def test_operational_enable_and_mute_overrides_do_not_break_release_contract(
    ontology, db,
):
    from app.services.sentinel.engine import run_manual

    sentinel = _sentinel(
        ontology["id"], sentinel_id="sentinel-operational")
    db.add(sentinel)
    db.flush()
    _freeze_live_projection(
        db, ontology["id"], ontology["current_release_id"])

    sentinel.enabled = False
    sentinel.muted = True
    db.commit()

    result = run_manual(db, ontology["id"])

    assert result["evaluated"] == 0
    assert result["errors"] == 0
    assert result["runtimeErrors"] == []


def test_builtin_operational_api_is_release_cas_idempotent(
    client, auth_headers, ontology, db, monkeypatch,
):
    from app.models.sentinel import (
        SentinelCdcOutbox,
        SentinelMatchState,
    )
    from app.ontologies.sentinels import cdc

    ontology_id = ontology["id"]
    release_id = ontology["current_release_id"]
    sentinel = _sentinel(
        ontology_id,
        sentinel_id="builtin-operational-cas",
        enabled=False,
    )
    sentinel.on_change = True
    db.add(sentinel)
    db.flush()
    _freeze_live_projection(db, ontology_id, release_id)

    # A mutable live-row orphan must never leak into the runtime list.
    orphan = _sentinel(
        ontology_id, sentinel_id="builtin-operational-orphan")
    draft = _sentinel(
        ontology_id, sentinel_id="builtin-draft-toggle")
    draft.status = "draft"
    db.add_all([orphan, draft])
    db.commit()
    monkeypatch.setattr(cdc, "_enqueue_dispatch", lambda _ids: None)

    endpoint = (
        f"/api/v1/ontologies/{ontology_id}/sentinels/"
        f"{sentinel.id}/operational-state"
    )
    listed = client.get(
        f"/api/v1/ontologies/{ontology_id}/sentinels/",
        headers=auth_headers,
    )
    assert listed.status_code == 200, listed.text
    assert [item["id"] for item in listed.json()["data"]] == [sentinel.id]
    assert listed.json()["data"][0]["releaseId"] == release_id
    assert listed.json()["data"][0]["enableGeneration"] == 0

    first = client.patch(
        endpoint,
        headers=auth_headers,
        json={
            "enabled": True,
            "expectedReleaseId": release_id,
            "expectedGeneration": 0,
        },
    )
    assert first.status_code == 200, first.text
    assert first.json()["data"]["enabled"] is True
    assert first.json()["data"]["enableGeneration"] == 1

    repeated = client.patch(
        endpoint,
        headers=auth_headers,
        json={
            "enabled": True,
            "expectedReleaseId": release_id,
            "expectedGeneration": 1,
        },
    )
    assert repeated.status_code == 200, repeated.text
    assert repeated.json()["data"]["enableGeneration"] == 1
    assert db.query(SentinelCdcOutbox).filter_by(
        event_kind=cdc.BUILTIN_ACTIVATION,
        sentinel_id=sentinel.id,
    ).count() == 1

    stale = client.patch(
        endpoint,
        headers=auth_headers,
        json={
            "muted": True,
            "expectedReleaseId": release_id,
            "expectedGeneration": 0,
        },
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == (
        "builtin_sentinel_generation_conflict")

    for method, path, payload in (
        (
            client.put,
            f"/api/v1/ontologies/{ontology_id}/sentinels/{sentinel.id}",
            {"enabled": False},
        ),
        (
            client.post,
            f"/api/v1/ontologies/{ontology_id}/sentinels/"
            f"{sentinel.id}/toggle",
            None,
        ),
    ):
        response = method(path, headers=auth_headers, json=payload)
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == (
            "sentinel_operational_api_required")

    db.add(SentinelMatchState(
        ontology_id=ontology_id,
        sentinel_id=sentinel.id,
        match_key="existing-match",
        match_detail={},
        runtime_status="completed",
    ))
    db.commit()
    disabled = client.patch(
        endpoint,
        headers=auth_headers,
        json={
            "enabled": False,
            "expectedReleaseId": release_id,
            "expectedGeneration": 1,
        },
    )
    assert disabled.status_code == 200, disabled.text
    assert disabled.json()["data"]["enableGeneration"] == 1
    assert db.query(SentinelMatchState).filter_by(
        sentinel_id=sentinel.id).count() == 0

    reenabled = client.patch(
        endpoint,
        headers=auth_headers,
        json={
            "enabled": True,
            "expectedReleaseId": release_id,
            "expectedGeneration": 1,
        },
    )
    assert reenabled.status_code == 200, reenabled.text
    assert reenabled.json()["data"]["enableGeneration"] == 2

    muted = client.patch(
        endpoint,
        headers=auth_headers,
        json={
            "muted": True,
            "expectedReleaseId": release_id,
            "expectedGeneration": 2,
        },
    )
    assert muted.status_code == 200, muted.text
    assert muted.json()["data"]["muted"] is True
    assert muted.json()["data"]["enableGeneration"] == 2
    unmuted = client.patch(
        endpoint,
        headers=auth_headers,
        json={
            "muted": False,
            "expectedReleaseId": release_id,
            "expectedGeneration": 2,
        },
    )
    assert unmuted.status_code == 200, unmuted.text
    assert unmuted.json()["data"]["enableGeneration"] == 3
    assert db.query(SentinelCdcOutbox).filter_by(
        event_kind=cdc.BUILTIN_ACTIVATION,
        sentinel_id=sentinel.id,
    ).count() == 3

    draft_toggle = client.post(
        f"/api/v1/ontologies/{ontology_id}/sentinels/{draft.id}/toggle",
        headers=auth_headers,
    )
    assert draft_toggle.status_code == 200, draft_toggle.text


def test_builtin_operational_activation_is_fail_closed_and_on_change_only(
    client, auth_headers, ontology, db, monkeypatch,
):
    from app.models.sentinel import SentinelCdcOutbox
    from app.ontologies.sentinels import cdc

    ontology_id = ontology["id"]
    release_id = ontology["current_release_id"]
    on_change = _sentinel(
        ontology_id,
        sentinel_id="builtin-capture-failure",
        enabled=False,
    )
    on_change.on_change = True
    schedule_only = _sentinel(
        ontology_id,
        sentinel_id="builtin-schedule-only-enable",
        enabled=False,
    )
    schedule_only.on_change = False
    schedule_only.on_schedule = True
    db.add_all([on_change, schedule_only])
    db.flush()
    _freeze_live_projection(db, ontology_id, release_id)
    monkeypatch.setattr(cdc, "_enqueue_dispatch", lambda _ids: None)
    real_capture = cdc.capture_builtin_activation
    monkeypatch.setattr(
        cdc, "capture_builtin_activation", lambda *_args, **_kwargs: None)

    failed = client.patch(
        f"/api/v1/ontologies/{ontology_id}/sentinels/"
        f"{on_change.id}/operational-state",
        headers=auth_headers,
        json={
            "enabled": True,
            "expectedReleaseId": release_id,
            "expectedGeneration": 0,
        },
    )
    assert failed.status_code == 503, failed.text
    db.expire_all()
    assert db.query(type(on_change)).filter_by(
        id=on_change.id).one().enabled is False
    assert db.query(type(on_change)).filter_by(
        id=on_change.id).one().enable_generation == 0

    monkeypatch.setattr(cdc, "capture_builtin_activation", real_capture)
    enabled = client.patch(
        f"/api/v1/ontologies/{ontology_id}/sentinels/"
        f"{schedule_only.id}/operational-state",
        headers=auth_headers,
        json={
            "enabled": True,
            "expectedReleaseId": f"  {release_id}  ",
            "expectedGeneration": 0,
        },
    )
    assert enabled.status_code == 200, enabled.text
    assert enabled.json()["data"]["enableGeneration"] == 1
    assert db.query(SentinelCdcOutbox).filter_by(
        event_kind=cdc.BUILTIN_ACTIVATION,
        sentinel_id=schedule_only.id,
    ).count() == 0

    invalid_generation = client.patch(
        f"/api/v1/ontologies/{ontology_id}/sentinels/"
        f"{schedule_only.id}/operational-state",
        headers=auth_headers,
        json={
            "muted": True,
            "expectedReleaseId": release_id,
            "expectedGeneration": True,
        },
    )
    assert invalid_generation.status_code == 422


def test_runtime_never_falls_back_without_a_valid_release_pointer(
    ontology, db,
):
    from app.models.ontology import OntologyProject
    from app.models.sentinel import SentinelFiring
    from app.services.sentinel.engine import run_manual

    db.add(_sentinel(
        ontology["id"], sentinel_id="sentinel-no-release"))
    project = db.query(OntologyProject).filter_by(id=ontology["id"]).one()
    project.current_release_id = None
    project.status = "published"
    db.commit()

    result = run_manual(db, ontology["id"])

    assert result["evaluated"] == 0
    assert result["runtimeErrors"][0]["code"] == "current_release_unavailable"
    assert db.query(SentinelFiring).count() == 0


def _fake_firing(sentinel, status: str):
    from types import SimpleNamespace

    return SimpleNamespace(
        sentinel_id=sentinel.id,
        sentinel_name=sentinel.display_name,
        status=status,
        match_count=0,
        entered=[],
        left=[],
        action_results=[],
        error=("synthetic failure" if status == "error" else None),
    )


def test_scheduler_claim_prevents_duplicate_due_execution(
    ontology, db, monkeypatch,
):
    from app.ontologies.sentinels import engine

    sentinel = _sentinel(
        ontology["id"], sentinel_id="sentinel-scheduled-once")
    sentinel.on_schedule = True
    sentinel.on_change = False
    sentinel.scan_interval_seconds = 300
    db.add(sentinel)
    db.flush()
    _freeze_live_projection(
        db, ontology["id"], ontology["current_release_id"])
    calls = []

    def evaluate(_db, _ontology_id, released, _source, **_kwargs):
        calls.append(released.id)
        return _fake_firing(released, "no_change")

    monkeypatch.setattr(engine, "evaluate_sentinel", evaluate)

    first = engine.run_scheduled(db)
    second = engine.run_scheduled(db)

    assert first["evaluated"] == 1
    assert second["evaluated"] == 0
    assert calls == [sentinel.id]
    db.refresh(sentinel)
    assert sentinel.last_scanned_at is not None


def test_failed_schedule_keeps_recoverable_event_and_success_watermark(
    ontology, db, monkeypatch,
):
    from app.models.sentinel import SentinelCdcOutbox
    from app.ontologies.sentinels import engine
    from app.ontologies.sentinels import cdc

    sentinel = _sentinel(
        ontology["id"], sentinel_id="sentinel-scheduled-retry")
    sentinel.on_schedule = True
    sentinel.on_change = False
    sentinel.scan_interval_seconds = 300
    db.add(sentinel)
    db.flush()
    _freeze_live_projection(
        db, ontology["id"], ontology["current_release_id"])
    statuses = iter(("error", "no_change"))

    def evaluate(_db, _ontology_id, released, _source, **_kwargs):
        return _fake_firing(released, next(statuses))

    monkeypatch.setattr(engine, "evaluate_sentinel", evaluate)

    first = engine.run_scheduled(db)
    retry = db.query(SentinelCdcOutbox).filter_by(
        event_kind=cdc.SCHEDULED_SCAN,
        sentinel_id=sentinel.id,
        status="retry",
    ).one()
    assert retry.available_at > retry.updated_at
    db.refresh(sentinel)
    assert sentinel.last_scanned_at is None
    retry.available_at = cdc._now() - timedelta(seconds=1)
    db.commit()
    second = engine.run_scheduled(db)

    assert first["errors"] == 1
    assert second["evaluated"] == 1
    assert second["errors"] == 0


def test_release_control_event_retry_is_idempotent_but_new_activation_runs(
        ontology, db, monkeypatch):
    from app.models.sentinel import Sentinel, SentinelFiring
    from app.ontologies.sentinels import engine

    ontology_id = ontology["id"]
    release_id = ontology["current_release_id"]
    builtin = _sentinel(
        ontology_id, sentinel_id="release-init-builtin")
    builtin.on_change = True
    dynamic = Sentinel(
        id="release-init-dynamic",
        ontology_id=ontology_id,
        name="release_init_dynamic",
        display_name="Release init dynamic",
        bindings=[],
        links=[],
        condition=None,
        primary_alias=None,
        action_ids=[],
        action_parameters={},
        on_change=True,
        enabled=True,
        muted=False,
        status="published",
        origin="assistant_dynamic",
        bound_release_id=release_id,
        definition_revision=1,
        validation_report={"passed": True},
        last_trial_release_id=release_id,
        last_trial_revision=1,
        last_trial_report={"passed": True},
    )
    db.add_all([builtin, dynamic])
    db.flush()
    _freeze_live_projection(db, ontology_id, release_id)
    calls = []

    def evaluate(
            run_db, _ontology_id, sentinel, source,
            expected_release_id=None):
        calls.append((sentinel.id, source))
        firing = SentinelFiring(
            ontology_id=ontology_id,
            sentinel_id=sentinel.id,
            sentinel_name=sentinel.display_name,
            trigger_source=source,
            matches=[],
            match_count=0,
            entered=[],
            left=[],
            action_results=[{"status": "success", "effects": ["once"]}],
            status="fired",
            ontology_release_id=expected_release_id,
        )
        run_db.add(firing)
        run_db.commit()
        run_db.refresh(firing)
        return firing

    monkeypatch.setattr(engine, "evaluate_sentinel", evaluate)

    first = engine.run_release_initialization(
        db, ontology_id, event_id="activation-event-one")
    replay = engine.run_release_initialization(
        db, ontology_id, event_id="activation-event-one", retry=True)
    reactivated = engine.run_release_initialization(
        db, ontology_id, event_id="activation-event-two")

    assert first["fired"] == replay["fired"] == reactivated["fired"] == 1
    assert calls == [
        ("release-init-builtin", "rel:activationevento"),
        ("release-init-builtin", "rel:activationeventt"),
    ]
    assert all(item[0] != dynamic.id for item in calls)


def test_release_activation_event_is_atomic_with_pointer_switch(
        ontology, db, monkeypatch):
    from app.models.ontology import OntologyProject
    from app.models.ontology_version import OntologyVersion
    from app.models.sentinel import SentinelCdcOutbox
    from app.ontologies.sentinels import cdc

    ontology_id = ontology["id"]
    project = db.query(OntologyProject).filter_by(id=ontology_id).one()
    release = OntologyVersion(
        id="atomic-release-activation",
        ontology_id=ontology_id,
        version_number="v-atomic",
        node_kind="release",
        lifecycle_status="released",
        revision=0,
        snapshot_formal={
            "objectTypes": [], "linkTypes": [], "actions": [],
            "functions": [], "mappings": [], "linkMappings": [],
            "sentinels": [{
                "id": "atomic-release-builtin",
                "name": "atomic_release_builtin",
                "displayName": "Atomic release built-in",
                "bindings": [], "links": [], "actionIds": [],
                "onChange": True, "onSchedule": False, "enabled": True,
            }],
        },
        created_by="tests",
    )
    db.add(release)
    db.commit()
    monkeypatch.setattr(cdc, "_enqueue_dispatch", lambda _ids: None)

    project.current_release_id = release.id
    db.flush()
    staged = db.query(SentinelCdcOutbox).filter_by(
        event_kind=cdc.RELEASE_ACTIVATION,
        ontology_release_id=release.id,
    ).one()
    staged_id = staged.id
    db.rollback()
    assert db.query(SentinelCdcOutbox).filter_by(id=staged_id).first() is None

    project = db.query(OntologyProject).filter_by(id=ontology_id).one()
    project.current_release_id = release.id
    db.commit()

    durable = db.query(SentinelCdcOutbox).filter_by(
        event_kind=cdc.RELEASE_ACTIVATION,
        ontology_release_id=release.id,
    ).one()
    assert durable.sentinel_id is None
    assert durable.status == "pending"
    assert durable.dedupe_key.startswith("release_activation:")


def test_pointer_switch_merges_projection_cdc_and_runs_action_once(
        ontology, db, monkeypatch):
    from sqlalchemy.orm import sessionmaker

    from app.models.ontology import OntologyProject
    from app.models.ontology_formal import (
        ActionExecutionLog,
        ActionType,
        LinkInstance,
        LinkType,
        ObjectInstance,
        ObjectType,
    )
    from app.models.ontology_version import OntologyVersion
    from app.models.sentinel import (
        Notification,
        SentinelCdcOutbox,
    )
    from app.ontologies.sentinels import cdc

    ontology_id = ontology["id"]
    release_id = "release-activation-merges-projection"
    object_type_id = "release-activation-object-type"
    link_type_id = "release-activation-link-type"
    action_id = "release-activation-notify"
    sentinel_id = "release-activation-run-on-all"
    object_type = ObjectType(
        id=object_type_id,
        ontology_id=ontology_id,
        name="ActivationObject",
        display_name="Activation object",
        primary_key="id",
        properties=[
            {
                "id": "id", "name": "id", "displayName": "ID",
                "type": "string", "required": True,
            },
            {
                "id": "status", "name": "status", "displayName": "Status",
                "type": "string", "required": False,
            },
        ],
    )
    link_type = LinkType(
        id=link_type_id,
        ontology_id=ontology_id,
        name="ActivationLink",
        display_name="Activation link",
        source_object_type_id=object_type_id,
        target_object_type_id=object_type_id,
        cardinality="many-to-many",
        properties=[],
    )
    notification_rule = {
        "id": "notify-on-activation",
        "type": "notification",
        "name": "Notify on activation",
        "enabled": True,
        "order": 0,
        "config": {
            "channel": "internal",
            "recipientSource": "constant",
            "recipient": "ops",
            "messageTemplate": "release activated",
        },
    }
    action = ActionType(
        id=action_id,
        ontology_id=ontology_id,
        name="release_activation_notify",
        display_name="Release activation notify",
        object_type_id=object_type_id,
        parameters=[],
        rules=[notification_rule],
        requires_approval=False,
    )
    sentinel = _sentinel(ontology_id, sentinel_id=sentinel_id)
    sentinel.bindings = [{
        "alias": "item", "objectTypeId": object_type_id,
    }]
    sentinel.primary_alias = "item"
    sentinel.condition = "item.status == 'ready'"
    sentinel.action_ids = [action_id]
    sentinel.action_parameters = {action_id: {}}
    sentinel.trigger_mode = "run_on_all"
    release = OntologyVersion(
        id=release_id,
        ontology_id=ontology_id,
        version_number="v-activation-merge",
        node_kind="release",
        lifecycle_status="released",
        revision=0,
        snapshot_formal={
            "objectTypes": [{
                "id": object_type_id,
                "name": "ActivationObject",
                "displayName": "Activation object",
                "primaryKey": "id",
                "properties": object_type.properties,
            }],
            "linkTypes": [{
                "id": link_type_id,
                "name": "ActivationLink",
                "displayName": "Activation link",
                "sourceObjectTypeId": object_type_id,
                "targetObjectTypeId": object_type_id,
                "cardinality": "many-to-many",
                "properties": [],
            }],
            "actions": [{
                "id": action_id,
                "name": action.name,
                "displayName": action.display_name,
                "objectTypeId": object_type_id,
                "parameters": [],
                "rules": [notification_rule],
                "requiresApproval": False,
            }],
            "functions": [],
            "mappings": [],
            "linkMappings": [],
            "sentinels": [{
                "id": sentinel_id,
                "name": sentinel.name,
                "displayName": sentinel.display_name,
                "bindings": sentinel.bindings,
                "links": [],
                "condition": sentinel.condition,
                "conditionRows": [],
                "conditionLogic": "and",
                "primaryAlias": "item",
                "actionIds": [action_id],
                "actionParameters": {action_id: {}},
                "triggerMode": "run_on_all",
                "onChange": True,
                "onSchedule": False,
                "enabled": True,
                "muted": False,
            }],
        },
        created_by="tests",
    )
    db.add_all([object_type, link_type, action, sentinel, release])
    db.commit()
    monkeypatch.setattr(cdc, "_enqueue_dispatch", lambda _ids: None)

    target = ObjectInstance(
        id="release-activation-target",
        ontology_id=ontology_id,
        ontology_release_id=release_id,
        object_type_id=object_type_id,
        properties={"id": "target", "status": "ready"},
    )
    relation = LinkInstance(
        id="release-activation-relation",
        ontology_id=ontology_id,
        ontology_release_id=release_id,
        link_type_id=link_type_id,
        source_object_id=target.id,
        target_object_id=target.id,
        properties={},
    )
    db.add_all([target, relation])
    db.flush()
    staged = db.query(SentinelCdcOutbox).filter(
        SentinelCdcOutbox.ontology_release_id == release_id,
        SentinelCdcOutbox.event_kind.in_(
            (cdc.OBJECT_CHANGE, cdc.LINK_CHANGE)),
    ).all()
    assert {row.event_kind for row in staged} == {
        cdc.OBJECT_CHANGE, cdc.LINK_CHANGE,
    }
    assert all(row.status == "pending" for row in staged)

    project = db.query(OntologyProject).filter_by(id=ontology_id).one()
    project.current_release_id = release_id
    project.version = release.version_number
    db.commit()

    rows = db.query(SentinelCdcOutbox).filter(
        SentinelCdcOutbox.ontology_release_id == release_id,
    ).all()
    activation = next(
        row for row in rows if row.event_kind == cdc.RELEASE_ACTIVATION)
    merged = [
        row for row in rows
        if row.event_kind in (cdc.OBJECT_CHANGE, cdc.LINK_CHANGE)
    ]
    assert len(merged) == 2
    assert all(row.status == "completed" for row in merged)
    assert all(
        row.result_json["outcome"] == "merged_into_release_activation"
        and row.result_json["activationEventId"] == activation.id
        for row in merged
    )

    factory = sessionmaker(bind=db.get_bind(), expire_on_commit=False)
    first = cdc.drain_cdc_outbox(
        event_ids={row.id for row in rows},
        session_factory=factory,
    )
    replay = cdc.drain_cdc_outbox(
        event_ids={row.id for row in rows},
        session_factory=factory,
    )

    db.expire_all()
    assert first["processed"] == 1
    assert replay["processed"] == 0
    assert db.query(ActionExecutionLog).filter_by(
        ontology_id=ontology_id,
        action_id=action_id,
    ).count() == 1
    assert db.query(Notification).filter_by(
        ontology_id=ontology_id,
        action_id=action_id,
    ).count() == 1


def test_control_retry_reexecutes_error_then_reuses_success(
        ontology, db, monkeypatch):
    from app.models.sentinel import SentinelFiring
    from app.ontologies.sentinels import engine

    ontology_id = ontology["id"]
    release_id = ontology["current_release_id"]
    sentinel = _sentinel(
        ontology_id, sentinel_id="transient-release-init")
    sentinel.on_change = True
    db.add(sentinel)
    db.flush()
    _freeze_live_projection(db, ontology_id, release_id)
    statuses = iter(("error", "fired"))
    calls = []

    def evaluate(
            run_db, _ontology_id, selected, source,
            expected_release_id=None):
        status = next(statuses)
        calls.append(status)
        firing = SentinelFiring(
            ontology_id=ontology_id,
            sentinel_id=selected.id,
            sentinel_name=selected.display_name,
            trigger_source=source,
            matches=[],
            match_count=0,
            entered=[],
            left=[],
            action_results=[],
            status=status,
            error=("transient" if status == "error" else None),
            ontology_release_id=expected_release_id,
        )
        run_db.add(firing)
        run_db.commit()
        run_db.refresh(firing)
        return firing

    monkeypatch.setattr(engine, "evaluate_sentinel", evaluate)

    failed = engine.run_release_initialization(
        db, ontology_id, event_id="transient-control")
    recovered = engine.run_release_initialization(
        db, ontology_id, event_id="transient-control", retry=True)
    replay = engine.run_release_initialization(
        db, ontology_id, event_id="transient-control", retry=True)

    assert failed["errors"] == 1
    assert recovered["fired"] == replay["fired"] == 1
    assert calls == ["error", "fired"]


def test_schedule_crash_reclaims_event_before_advancing_watermark(
        ontology, db, monkeypatch):
    from sqlalchemy.orm import sessionmaker

    from app.models.sentinel import Sentinel, SentinelCdcOutbox
    from app.ontologies.sentinels import cdc
    from app.services.sentinel import engine as service_engine

    ontology_id = ontology["id"]
    release_id = ontology["current_release_id"]
    sentinel = _sentinel(
        ontology_id, sentinel_id="durable-schedule-crash")
    sentinel.on_change = False
    sentinel.on_schedule = True
    db.add(sentinel)
    db.flush()
    _freeze_live_projection(db, ontology_id, release_id)
    scheduled_at = datetime.now(timezone.utc)
    event_id = cdc.ensure_scheduled_scan_event(
        db,
        ontology_id=ontology_id,
        ontology_release_id=release_id,
        sentinel_id=sentinel.id,
        previous_last_scanned_at=None,
        scheduled_at=scheduled_at,
    )
    assert event_id is not None
    db.refresh(sentinel)
    assert sentinel.last_scanned_at is None

    token = cdc._claim_one(
        db,
        event_id,
        cdc._now(),
        cdc._now() - timedelta(seconds=cdc._CLAIM_TIMEOUT_SECONDS),
    )
    assert token is not None
    crashed = db.query(SentinelCdcOutbox).filter_by(id=event_id).one()
    crashed.claimed_at = cdc._now() - timedelta(
        seconds=cdc._CLAIM_TIMEOUT_SECONDS + 1)
    db.commit()
    calls = []

    def evaluate_after_reclaim(
            run_db, run_ontology_id, sentinel_id, **kwargs):
        live = run_db.query(Sentinel).filter_by(id=sentinel_id).one()
        calls.append((run_ontology_id, sentinel_id, live.last_scanned_at))
        return {
            "evaluated": 1,
            "fired": 0,
            "errors": 0,
            "no_change": 1,
            "no_match": 0,
            "pending": 0,
            "muted": 0,
            "runtimeErrors": [],
            "firings": [],
        }

    monkeypatch.setattr(
        service_engine, "run_scheduled_event", evaluate_after_reclaim)
    factory = sessionmaker(bind=db.get_bind(), expire_on_commit=False)

    recovered = cdc.drain_cdc_outbox(
        event_ids={event_id}, session_factory=factory)
    duplicate = cdc.drain_cdc_outbox(
        event_ids={event_id}, session_factory=factory)

    db.expire_all()
    completed = db.query(SentinelCdcOutbox).filter_by(id=event_id).one()
    live = db.query(Sentinel).filter_by(id=sentinel.id).one()
    assert recovered["processed"] == 1
    assert duplicate["processed"] == 0
    assert completed.status == "completed"
    assert completed.attempts == 2
    assert calls == [(ontology_id, sentinel.id, None)]
    assert live.last_scanned_at is not None


def test_superseded_schedule_does_not_advance_success_watermark(
        ontology, db):
    from sqlalchemy.orm import sessionmaker

    from app.models.sentinel import SentinelCdcOutbox
    from app.ontologies.sentinels import cdc

    ontology_id = ontology["id"]
    release_id = ontology["current_release_id"]
    sentinel = _sentinel(
        ontology_id, sentinel_id="superseded-schedule")
    sentinel.on_change = False
    sentinel.on_schedule = True
    db.add(sentinel)
    db.flush()
    _freeze_live_projection(db, ontology_id, release_id)
    event_id = cdc.ensure_scheduled_scan_event(
        db,
        ontology_id=ontology_id,
        ontology_release_id=release_id,
        sentinel_id=sentinel.id,
        previous_last_scanned_at=None,
        scheduled_at=datetime.now(timezone.utc),
    )
    sentinel.enabled = False
    db.commit()
    factory = sessionmaker(bind=db.get_bind(), expire_on_commit=False)

    result = cdc.drain_cdc_outbox(
        event_ids={event_id}, session_factory=factory)

    db.expire_all()
    live = db.query(type(sentinel)).filter_by(id=sentinel.id).one()
    event = db.query(SentinelCdcOutbox).filter_by(id=event_id).one()
    assert result["processed"] == 1
    assert event.status == "completed"
    assert event.result_json["superseded"] is True
    assert event.result_json["skipped"] == "scheduled_sentinel_inactive"
    assert live.last_scanned_at is None


@pytest.mark.parametrize(
    ("changed_field", "expected_reason"),
    [
        ("definition_revision", "dynamic_sentinel_revision_changed"),
        ("enable_generation", "dynamic_sentinel_enable_changed"),
    ],
)
def test_dynamic_schedule_freezes_definition_and_enable_generation(
        ontology, db, monkeypatch, changed_field, expected_reason):
    from sqlalchemy.orm import sessionmaker

    from app.models.sentinel import Sentinel, SentinelCdcOutbox
    from app.ontologies.sentinels import cdc
    from app.services.sentinel import engine as service_engine

    ontology_id = ontology["id"]
    release_id = ontology["current_release_id"]
    baseline = datetime.now(timezone.utc) - timedelta(hours=1)
    dynamic = Sentinel(
        id=f"dynamic-schedule-{changed_field}",
        ontology_id=ontology_id,
        name=f"dynamic_schedule_{changed_field}",
        display_name=f"Dynamic schedule {changed_field}",
        bindings=[],
        links=[],
        condition=None,
        primary_alias=None,
        action_ids=[],
        action_parameters={},
        on_change=False,
        on_schedule=True,
        scan_interval_seconds=5,
        enabled=True,
        muted=False,
        status="published",
        origin="assistant_dynamic",
        bound_release_id=release_id,
        definition_revision=4,
        enable_generation=2,
        validation_report={"passed": True},
        last_trial_release_id=release_id,
        last_trial_revision=4,
        last_trial_report={"passed": True},
        last_scanned_at=baseline,
    )
    db.add(dynamic)
    db.commit()
    db.refresh(dynamic)
    persisted_baseline = dynamic.last_scanned_at
    event_id = cdc.ensure_scheduled_scan_event(
        db,
        ontology_id=ontology_id,
        ontology_release_id=release_id,
        sentinel_id=dynamic.id,
        previous_last_scanned_at=dynamic.last_scanned_at,
        scheduled_at=datetime.now(timezone.utc),
        sentinel_origin="assistant_dynamic",
        definition_revision=dynamic.definition_revision,
        enable_generation=dynamic.enable_generation,
    )
    event = db.query(SentinelCdcOutbox).filter_by(id=event_id).one()
    assert event.result_json["control"] == {
        "sentinelId": dynamic.id,
        "previousLastScannedAt": cdc._datetime_token(
            dynamic.last_scanned_at),
        "scheduledAt": event.result_json["control"]["scheduledAt"],
        "sentinelOrigin": "assistant_dynamic",
        "definitionRevision": 4,
        "enableGeneration": 2,
    }

    setattr(dynamic, changed_field, getattr(dynamic, changed_field) + 1)
    if changed_field == "definition_revision":
        # Keep the trial current so the frozen revision is the sole stale
        # reason, not an incidental trial mismatch.
        dynamic.last_trial_revision = dynamic.definition_revision
    db.commit()
    calls = []

    def must_not_run(*_args, **_kwargs):
        calls.append(True)
        raise AssertionError("stale dynamic schedule reached execution")

    monkeypatch.setattr(
        service_engine, "run_scheduled_event", must_not_run)
    factory = sessionmaker(bind=db.get_bind(), expire_on_commit=False)

    result = cdc.drain_cdc_outbox(
        event_ids={event_id}, session_factory=factory)

    db.expire_all()
    completed = db.query(SentinelCdcOutbox).filter_by(id=event_id).one()
    current = db.query(Sentinel).filter_by(id=dynamic.id).one()
    assert result["processed"] == 1
    assert result["stale"] == 1
    assert calls == []
    assert completed.status == "completed"
    assert completed.result_json["superseded"] is True
    assert completed.result_json["skipped"] == expected_reason
    assert current.last_scanned_at == persisted_baseline


def test_dead_schedule_is_reported_and_never_looks_successful(
        ontology, db):
    from app.models.sentinel import SentinelCdcOutbox
    from app.ontologies.sentinels import cdc, engine

    ontology_id = ontology["id"]
    release_id = ontology["current_release_id"]
    sentinel = _sentinel(
        ontology_id, sentinel_id="dead-schedule")
    sentinel.on_change = False
    sentinel.on_schedule = True
    db.add(sentinel)
    db.flush()
    _freeze_live_projection(db, ontology_id, release_id)
    event_id = cdc.ensure_scheduled_scan_event(
        db,
        ontology_id=ontology_id,
        ontology_release_id=release_id,
        sentinel_id=sentinel.id,
        previous_last_scanned_at=None,
        scheduled_at=datetime.now(timezone.utc),
    )
    event = db.query(SentinelCdcOutbox).filter_by(id=event_id).one()
    event.status = "dead"
    event.last_error = "synthetic terminal failure"
    event.processed_at = cdc._now()
    db.commit()

    result = engine.run_scheduled(db)

    assert result["evaluated"] == 0
    assert result["errors"] == 1
    assert result["runtimeErrors"][0]["code"] == "scheduled_outbox_dead"
    db.refresh(sentinel)
    assert sentinel.last_scanned_at is None


def test_cdc_status_exposes_control_event_identity(ontology, db):
    from sqlalchemy.orm import sessionmaker

    from app.models.sentinel import SentinelCdcOutbox
    from app.ontologies.sentinels import cdc

    row = SentinelCdcOutbox(
        id="control-status-event",
        chain_id="control-status-chain",
        ontology_id=ontology["id"],
        ontology_release_id=ontology["current_release_id"],
        event_kind=cdc.SCHEDULED_SCAN,
        sentinel_id="control-status-sentinel",
        dedupe_key="control-status-dedupe",
        object_type_id=None,
        changed_keys=[],
        link_change=False,
        cascade_depth=0,
        mapping_ids=[],
        status="dead",
        attempts=4,
        available_at=cdc._now(),
        last_error="terminal",
        result_json={"control": {
            "definitionRevision": 9,
            "enableGeneration": 4,
        }},
    )
    db.add(row)
    db.commit()
    factory = sessionmaker(bind=db.get_bind(), expire_on_commit=False)

    status = cdc.cdc_dispatch_status(
        ontology["id"],
        include_history=True,
        session_factory=factory,
    )

    assert status["last_errors"][0]["eventKind"] == cdc.SCHEDULED_SCAN
    assert status["last_errors"][0]["sentinelId"] == (
        "control-status-sentinel")
    assert status["last_errors"][0]["definitionRevision"] == 9
    assert status["last_errors"][0]["enableGeneration"] == 4
    history = next(
        item for item in status["recent_events"]
        if item["eventId"] == row.id
    )
    assert history["eventKind"] == cdc.SCHEDULED_SCAN
    assert history["sentinelId"] == "control-status-sentinel"
    assert history["definitionRevision"] == 9
    assert history["enableGeneration"] == 4
    assert "dedupeKey" not in history
    assert status["migration_policy"]["legacyActivationBackfill"] == (
        "not_replayed")


def test_dynamic_activation_revalidates_generation_and_deduplicates(
        ontology, db, monkeypatch):
    from sqlalchemy.orm import sessionmaker

    from app.models.sentinel import Sentinel, SentinelCdcOutbox
    from app.ontologies.sentinels import cdc
    from app.services.sentinel import engine as service_engine

    ontology_id = ontology["id"]
    release_id = ontology["current_release_id"]
    monkeypatch.setattr(cdc, "_enqueue_dispatch", lambda _ids: None)
    dynamic = Sentinel(
        id="dynamic-activation-generation",
        ontology_id=ontology_id,
        name="dynamic_activation_generation",
        display_name="Dynamic activation generation",
        bindings=[],
        links=[],
        condition=None,
        primary_alias=None,
        action_ids=[],
        action_parameters={},
        on_change=True,
        enabled=True,
        muted=False,
        status="published",
        origin="assistant_dynamic",
        bound_release_id=release_id,
        definition_revision=4,
        enable_generation=2,
        validation_report={"passed": True},
        last_trial_release_id=release_id,
        last_trial_revision=4,
        last_trial_report={"passed": True},
    )
    db.add(dynamic)
    db.flush()
    stale = cdc.capture_dynamic_activation(
        db,
        ontology_id=ontology_id,
        ontology_release_id=release_id,
        sentinel_id=dynamic.id,
        definition_revision=4,
        enable_generation=1,
    )
    current = cdc.capture_dynamic_activation(
        db,
        ontology_id=ontology_id,
        ontology_release_id=release_id,
        sentinel_id=dynamic.id,
        definition_revision=4,
        enable_generation=2,
    )
    duplicate = cdc.capture_dynamic_activation(
        db,
        ontology_id=ontology_id,
        ontology_release_id=release_id,
        sentinel_id=dynamic.id,
        definition_revision=4,
        enable_generation=2,
    )
    assert stale is not None and current is not None
    assert duplicate is current
    db.commit()
    calls = []

    def initialize(_db, _ontology_id, sentinel_id, **_kwargs):
        calls.append(sentinel_id)
        return {
            "evaluated": 1, "fired": 0, "errors": 0,
            "firings": [], "runtimeErrors": [],
        }

    monkeypatch.setattr(
        service_engine, "run_dynamic_initialization", initialize)
    factory = sessionmaker(bind=db.get_bind(), expire_on_commit=False)
    result = cdc.drain_cdc_outbox(
        event_ids={stale.id, current.id},
        session_factory=factory,
    )

    db.expire_all()
    rows = {
        row.id: row for row in db.query(SentinelCdcOutbox).filter(
            SentinelCdcOutbox.id.in_((stale.id, current.id))).all()
    }
    assert result["processed"] == 2
    assert calls == [dynamic.id]
    assert rows[stale.id].result_json["superseded"] is True
    assert rows[stale.id].result_json["skipped"] == (
        "dynamic_sentinel_enable_changed")
    assert rows[current.id].result_json["evaluated"] == 1


def test_builtin_activation_is_atomic_exact_generation_and_retry_idempotent(
        ontology, db, monkeypatch):
    from sqlalchemy.orm import sessionmaker

    from app.models.sentinel import (
        Sentinel,
        SentinelCdcOutbox,
        SentinelFiring,
    )
    from app.ontologies.sentinels import cdc
    from app.ontologies.sentinels import engine as ontology_engine
    from app.services.sentinel import engine as service_engine

    ontology_id = ontology["id"]
    release_id = ontology["current_release_id"]
    target = _sentinel(
        ontology_id, sentinel_id="builtin-activation-target")
    target.on_change = True
    target.enable_generation = 3
    decoy = _sentinel(
        ontology_id, sentinel_id="builtin-activation-decoy")
    decoy.on_change = True
    decoy.enable_generation = 7
    db.add_all([target, decoy])
    db.flush()
    _freeze_live_projection(db, ontology_id, release_id)
    monkeypatch.setattr(cdc, "_enqueue_dispatch", lambda _ids: None)

    rolled_back = cdc.capture_builtin_activation(
        db,
        ontology_id=ontology_id,
        ontology_release_id=release_id,
        sentinel_id=target.id,
        enable_generation=target.enable_generation,
    )
    assert rolled_back is not None
    rolled_back_id = rolled_back.id
    db.flush()
    db.rollback()
    assert db.query(SentinelCdcOutbox).filter_by(
        id=rolled_back_id).first() is None

    stale = cdc.capture_builtin_activation(
        db,
        ontology_id=ontology_id,
        ontology_release_id=release_id,
        sentinel_id=target.id,
        enable_generation=2,
    )
    current = cdc.capture_builtin_activation(
        db,
        ontology_id=ontology_id,
        ontology_release_id=release_id,
        sentinel_id=target.id,
        enable_generation=3,
    )
    duplicate = cdc.capture_builtin_activation(
        db,
        ontology_id=ontology_id,
        ontology_release_id=release_id,
        sentinel_id=target.id,
        enable_generation=3,
    )
    assert stale is not None and current is not None
    assert duplicate is current
    db.commit()
    calls = []

    def evaluate(
            run_db, _ontology_id, selected, source,
            expected_release_id=None):
        calls.append((selected.id, source, expected_release_id))
        firing = SentinelFiring(
            ontology_id=ontology_id,
            sentinel_id=selected.id,
            sentinel_name=selected.display_name,
            trigger_source=source,
            matches=[],
            match_count=0,
            entered=[],
            left=[],
            action_results=[],
            status="fired",
            ontology_release_id=expected_release_id,
        )
        run_db.add(firing)
        run_db.commit()
        run_db.refresh(firing)
        return firing

    monkeypatch.setattr(ontology_engine, "evaluate_sentinel", evaluate)
    factory = sessionmaker(bind=db.get_bind(), expire_on_commit=False)

    result = cdc.drain_cdc_outbox(
        event_ids={stale.id, current.id}, session_factory=factory)
    direct_retry = service_engine.run_builtin_initialization(
        db,
        ontology_id,
        target.id,
        event_id=current.id,
        retry=True,
    )

    db.expire_all()
    rows = {
        row.id: row for row in db.query(SentinelCdcOutbox).filter(
            SentinelCdcOutbox.id.in_((stale.id, current.id))).all()
    }
    assert result["processed"] == 2
    assert result["stale"] == 1
    assert direct_retry["fired"] == 1
    assert calls == [(
        target.id,
        ontology_engine._control_source("bin", current.id),
        release_id,
    )]
    assert rows[stale.id].result_json["skipped"] == (
        "builtin_sentinel_enable_changed")
    assert rows[current.id].result_json["fired"] == 1
    assert db.query(SentinelFiring).filter_by(
        sentinel_id=decoy.id).count() == 0


@pytest.mark.parametrize(
    ("case", "expected_reason"),
    [
        ("schedule_only", "builtin_sentinel_not_on_change"),
        ("not_in_snapshot", "builtin_sentinel_not_in_release"),
    ],
)
def test_builtin_activation_fails_closed_for_non_initializable_snapshot_member(
        ontology, db, monkeypatch, case, expected_reason):
    from sqlalchemy.orm import sessionmaker

    from app.models.sentinel import SentinelCdcOutbox
    from app.ontologies.sentinels import cdc
    from app.services.sentinel import engine as service_engine

    ontology_id = ontology["id"]
    release_id = ontology["current_release_id"]
    candidate = _sentinel(
        ontology_id, sentinel_id=f"builtin-activation-{case}")
    candidate.on_change = case != "schedule_only"
    candidate.on_schedule = case == "schedule_only"
    candidate.enable_generation = 1
    db.add(candidate)
    db.flush()
    if case == "schedule_only":
        _freeze_live_projection(db, ontology_id, release_id)
    else:
        # Freeze first, then create a published live row that is deliberately
        # absent from the immutable release snapshot.
        db.rollback()
        _freeze_live_projection(db, ontology_id, release_id)
        candidate = _sentinel(
            ontology_id, sentinel_id=f"builtin-activation-{case}")
        candidate.on_change = True
        candidate.enable_generation = 1
        db.add(candidate)
        db.commit()
    event = cdc.capture_builtin_activation(
        db,
        ontology_id=ontology_id,
        ontology_release_id=release_id,
        sentinel_id=candidate.id,
        enable_generation=candidate.enable_generation,
    )
    assert event is not None
    db.commit()
    calls = []

    def must_not_run(*_args, **_kwargs):
        calls.append(True)
        raise AssertionError("non-initializable built-in reached engine")

    monkeypatch.setattr(
        service_engine, "run_builtin_initialization", must_not_run)
    factory = sessionmaker(bind=db.get_bind(), expire_on_commit=False)

    result = cdc.drain_cdc_outbox(
        event_ids={event.id}, session_factory=factory)

    db.expire_all()
    completed = db.query(SentinelCdcOutbox).filter_by(id=event.id).one()
    assert result["processed"] == 1
    assert result["stale"] == 1
    assert calls == []
    assert completed.result_json["superseded"] is True
    assert completed.result_json["skipped"] == expected_reason


@pytest.mark.parametrize(
    ("mutation", "expected_reason"),
    [
        ("draft", "builtin_sentinel_inactive"),
        ("disabled", "builtin_sentinel_inactive"),
        ("muted", "builtin_sentinel_inactive"),
        ("retired", "builtin_sentinel_inactive"),
        ("deleted", "builtin_sentinel_missing"),
    ],
)
def test_builtin_activation_revalidates_live_operational_state(
        ontology, db, monkeypatch, mutation, expected_reason):
    from sqlalchemy.orm import sessionmaker

    from app.models.sentinel import SentinelCdcOutbox
    from app.ontologies.sentinels import cdc
    from app.services.sentinel import engine as service_engine

    ontology_id = ontology["id"]
    release_id = ontology["current_release_id"]
    candidate = _sentinel(
        ontology_id, sentinel_id=f"builtin-live-{mutation}")
    candidate.on_change = True
    candidate.enable_generation = 1
    db.add(candidate)
    db.flush()
    _freeze_live_projection(db, ontology_id, release_id)
    event = cdc.capture_builtin_activation(
        db,
        ontology_id=ontology_id,
        ontology_release_id=release_id,
        sentinel_id=candidate.id,
        enable_generation=1,
    )
    assert event is not None
    event_id = event.id
    db.commit()

    if mutation == "draft":
        candidate.status = "draft"
    elif mutation == "disabled":
        candidate.enabled = False
    elif mutation == "muted":
        candidate.muted = True
    elif mutation == "retired":
        candidate.retired_at = datetime.now(timezone.utc)
    else:
        db.delete(candidate)
    db.commit()
    calls = []

    def must_not_run(*_args, **_kwargs):
        calls.append(True)
        raise AssertionError("stale built-in reached initialization")

    monkeypatch.setattr(
        service_engine, "run_builtin_initialization", must_not_run)
    factory = sessionmaker(bind=db.get_bind(), expire_on_commit=False)

    result = cdc.drain_cdc_outbox(
        event_ids={event_id}, session_factory=factory)

    db.expire_all()
    completed = db.query(SentinelCdcOutbox).filter_by(id=event_id).one()
    assert result["processed"] == 1
    assert result["stale"] == 1
    assert calls == []
    assert completed.result_json["skipped"] == expected_reason


def test_stale_builtin_selection_is_reloaded_and_disabled_before_execution(
    ontology, db,
):
    from sqlalchemy.orm import sessionmaker

    from app.models.sentinel import Sentinel, SentinelMatchState
    from app.ontologies.release_context import current_release_context
    from app.ontologies.sentinels.engine import _runtime_sentinels
    from app.ontologies.sentinels.evaluator import evaluate_sentinel

    ontology_id = ontology["id"]
    release_id = ontology["current_release_id"]
    sentinel = _sentinel(
        ontology_id, sentinel_id="stale-disabled-builtin")
    db.add(sentinel)
    db.flush()
    _freeze_live_projection(db, ontology_id, release_id)

    context = current_release_context(
        db, ontology_id, expected_release_id=release_id)
    selected = next(
        item for item in _runtime_sentinels(db, context)
        if item.id == sentinel.id
    )
    sentinel_id = sentinel.id
    # End the selector transaction while retaining its deliberately stale
    # SimpleNamespace candidate.
    db.rollback()

    WriterSession = sessionmaker(bind=db.get_bind())
    writer = WriterSession()
    try:
        live = writer.query(Sentinel).filter_by(id=sentinel_id).one()
        live.enabled = False
        writer.commit()
    finally:
        writer.close()

    firing = evaluate_sentinel(
        db, ontology_id, selected, "manual",
        expected_release_id=release_id,
    )

    assert firing.status == "error"
    assert firing.action_results[0]["validationErrors"] == [
        "sentinel_disabled",
    ]
    assert db.query(SentinelMatchState).filter_by(
        sentinel_id=sentinel_id).count() == 0


def test_stale_dynamic_selection_cannot_execute_updated_untrialed_definition(
    ontology, db,
):
    from types import SimpleNamespace

    from app.models.sentinel import Sentinel, SentinelMatchState
    from app.ontologies.sentinels.evaluator import evaluate_sentinel

    ontology_id = ontology["id"]
    release_id = ontology["current_release_id"]
    live = Sentinel(
        id="stale-dynamic-definition",
        ontology_id=ontology_id,
        name="stale_dynamic_definition",
        display_name="Stale dynamic definition",
        bindings=[],
        links=[],
        condition=None,
        primary_alias=None,
        action_ids=[],
        action_parameters={},
        enabled=True,
        muted=False,
        status="published",
        origin="assistant_dynamic",
        bound_release_id=release_id,
        definition_revision=1,
        validation_report={"passed": True},
        last_trial_release_id=release_id,
        last_trial_revision=1,
        last_trial_report={"passed": True},
    )
    db.add(live)
    db.commit()
    selected = SimpleNamespace(
        id=live.id,
        name=live.name,
        display_name=live.display_name,
        origin=live.origin,
        definition_revision=1,
    )

    # This models an update that won the shared fence after engine selection:
    # update invalidates the old trial and increments the definition revision.
    live.definition_revision = 2
    live.enabled = True
    live.last_trial_release_id = None
    live.last_trial_revision = None
    live.last_trial_report = None
    db.commit()

    firing = evaluate_sentinel(
        db, ontology_id, selected, "manual",
        expected_release_id=release_id,
    )

    assert firing.status == "error"
    assert firing.action_results[0]["validationErrors"] == [
        "dynamic_sentinel_definition_changed",
    ]
    current_candidate = SimpleNamespace(
        id=live.id,
        name=live.name,
        display_name=live.display_name,
        origin=live.origin,
        definition_revision=2,
    )
    untrialed = evaluate_sentinel(
        db, ontology_id, current_candidate, "manual",
        expected_release_id=release_id,
    )
    assert untrialed.status == "error"
    assert untrialed.action_results[0]["validationErrors"] == [
        "dynamic_sentinel_trial_required",
    ]
    assert db.query(SentinelMatchState).filter_by(
        sentinel_id=live.id).count() == 0


def test_builtin_toggle_waits_for_the_same_fence_as_evaluation(
    ontology, db, monkeypatch,
):
    import threading
    from contextlib import contextmanager

    from sqlalchemy.orm import sessionmaker

    from app.models.sentinel import Sentinel
    from app.ontologies.sentinels import evaluator, router as sentinel_router

    ontology_id = ontology["id"]
    release_id = ontology["current_release_id"]
    sentinel = _sentinel(
        ontology_id, sentinel_id="toggle-shared-fence")
    db.add(sentinel)
    db.flush()
    _freeze_live_projection(db, ontology_id, release_id)

    write_attempted = threading.Event()
    write_completed = threading.Event()
    failures = []
    operation_lock = evaluator._sentinel_execution_lock

    @contextmanager
    def observed_write_fence(session, sentinel_id):
        write_attempted.set()
        with operation_lock(session, sentinel_id):
            yield

    monkeypatch.setattr(
        sentinel_router, "_sentinel_write_fence", observed_write_fence)
    WorkerSession = sessionmaker(bind=db.get_bind())

    def toggle():
        worker = WorkerSession()
        try:
            sentinel_router.update_operational_state(
                ontology_id,
                sentinel.id,
                sentinel_router.SentinelOperationalUpdate(
                    enabled=False,
                    expectedReleaseId=release_id,
                    expectedGeneration=0,
                ),
                db=worker,
                _=None,
            )
        except Exception as exc:  # pragma: no cover - asserted below
            failures.append(exc)
        finally:
            worker.close()
            write_completed.set()

    thread = threading.Thread(target=toggle, daemon=True)
    with operation_lock(db, sentinel.id):
        thread.start()
        assert write_attempted.wait(2)
        assert not write_completed.wait(0.2)

    thread.join(timeout=5)
    assert not thread.is_alive()
    assert failures == []
    db.expire_all()
    assert db.query(Sentinel).filter_by(id=sentinel.id).one().enabled is False
