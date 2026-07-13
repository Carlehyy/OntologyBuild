"""Production guardrails for formal actions, sentinels, and Celery workers."""
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from sqlalchemy.exc import IntegrityError

from app.models.ontology_formal import (
    ActionExecutionLog,
    ActionType,
    ObjectInstance,
    ObjectType,
)
from app.models.sentinel import Notification, Sentinel, SentinelMatchState
from app.models.ontology import OntologyProject
from app.models.v2.mapping import OntologyMapping
from app.services.formal.action_engine import execute_action
from app.ontologies.sentinels.evaluator import _sentinel_execution_lock
from app.services.sentinel.evaluator import (
    evaluate_sentinel,
    reject_sentinel_match_claim,
    resume_sentinel_match_claim,
)
from app.ontologies.formal_modeling.router import decide_pending_action
from app.ontologies.formal_modeling.schemas import DecisionRequest
from fastapi import HTTPException


def test_postgres_sentinel_lock_uses_one_dedicated_connection_across_commits():
    calls = []

    class FakeConnection:
        def __init__(self):
            self.closed = False

        def execute(self, statement, params):
            calls.append((self, str(statement), params))

        def close(self):
            self.closed = True

    connection = FakeConnection()

    class FakeEngine:
        dialect = SimpleNamespace(name="postgresql")

        def connect(self):
            return connection

    class FakeSession:
        commits = 0

        def get_bind(self):
            return FakeEngine()

        def execute(self, *_args, **_kwargs):
            raise AssertionError("advisory lock must not use the business Session")

        def commit(self):
            self.commits += 1

    session = FakeSession()
    with _sentinel_execution_lock(session, "dedicated-lock"):
        # This represents action-engine commits that can release/swap the
        # business Session's pooled connection while evaluation is in flight.
        session.commit()
        session.commit()

    assert session.commits == 2
    assert len(calls) == 2
    assert calls[0][0] is connection and calls[1][0] is connection
    assert "pg_advisory_lock" in calls[0][1]
    assert "pg_advisory_unlock" in calls[1][1]
    assert calls[0][2] == calls[1][2] == {"key": "sentinel:dedicated-lock"}
    assert connection.closed is True


def _seed_object(db, ontology_id="runtime-hardening", *, suffix=""):
    if db.query(OntologyProject).filter_by(id=ontology_id).first() is None:
        db.add(OntologyProject(
            id=ontology_id, name=ontology_id, domain="test",
            created_by="runtime-tests", status="published", version="v1.0.0"))
        db.flush()
    object_type = ObjectType(
        id=f"ot-order{suffix}", ontology_id=ontology_id,
        name=f"Order{suffix}", display_name=f"Order{suffix}",
        primary_key="id", properties=[],
    )
    instance = ObjectInstance(
        id=f"order-1{suffix}", ontology_id=ontology_id,
        object_type_id=object_type.id,
        properties={"active": True, "count": 0, "status": "new"},
    )
    db.add_all([object_type, instance])
    db.commit()
    return object_type, instance


def _body(action, instance, parameters=None, **extra):
    return SimpleNamespace(
        action_id=action.id,
        target_instance_id=instance.id if instance is not None else None,
        parameters=parameters or {}, dry_run=False, **extra,
    )


def _update_rule(property_name: str, *, source="constant", value="\"done\""):
    return {
        "id": f"update-{property_name}", "type": "update_property",
        "name": f"update {property_name}", "enabled": True, "order": 0,
        "config": {
            "targetProperty": property_name,
            "valueSource": source,
            "value": value,
        },
    }


def test_action_parameter_contract_applies_defaults_and_rejects_bad_input(db):
    ontology_id = "action-contract"
    object_type, instance = _seed_object(db, ontology_id)
    action = ActionType(
        id="action-contract", ontology_id=ontology_id,
        name="set_status", display_name="Set status",
        object_type_id=object_type.id,
        parameters=[
            {
                "name": "status", "type": "string", "required": True,
                "defaultValue": "ready",
                "options": [
                    {"label": "Ready", "value": "ready"},
                    {"label": "Done", "value": "done"},
                ],
            },
            {"name": "score", "type": "number", "min": 1, "max": 10},
        ],
        rules=[_update_rule("status", source="parameter", value="status")],
    )
    db.add(action)
    db.commit()

    result = execute_action(db, ontology_id, _body(action, instance))
    db.refresh(instance)
    assert result["status"] == "success"
    assert result["parameters"] == {"status": "ready"}
    assert instance.properties["status"] == "ready"

    wrong_type = execute_action(
        db, ontology_id,
        _body(action, instance, {"status": "ready", "score": "9"}),
    )
    unknown = execute_action(
        db, ontology_id,
        _body(action, instance, {"status": "ready", "extra": True}),
    )
    out_of_range = execute_action(
        db, ontology_id,
        _body(action, instance, {"status": "ready", "score": 11}),
    )
    assert wrong_type["status"] == "failed"
    assert any("类型错误" in item for item in wrong_type["validationErrors"])
    assert unknown["status"] == "failed"
    assert any("未声明参数" in item for item in unknown["validationErrors"])
    assert out_of_range["status"] == "failed"
    assert any("不得大于" in item for item in out_of_range["validationErrors"])


def test_production_action_blocks_during_incomplete_mapping_projection(
        db, monkeypatch):
    from app.config import settings

    ontology_id = "action-projection-fence"
    object_type, instance = _seed_object(db, ontology_id)
    action = ActionType(
        id="projection-fenced-action", ontology_id=ontology_id,
        name="projection_fenced", display_name="Projection fenced",
        object_type_id=object_type.id, parameters=[], rules=[])
    mapping = OntologyMapping(
        id="projection-in-progress", ontology_id=ontology_id,
        entity_class="Order", field_mapping={}, status="projecting")
    db.add_all([action, mapping])
    db.commit()
    monkeypatch.setattr(settings, "environment", "production")

    result = execute_action(db, ontology_id, _body(action, instance))

    assert result["status"] == "failed"
    assert result["validationErrors"] == ["ontology_projection_not_ready"]


def test_action_rejects_bad_target_missing_validation_and_unknown_rule(db):
    ontology_id = "action-fail-closed"
    object_type, instance = _seed_object(db, ontology_id)
    other_type, other = _seed_object(db, ontology_id, suffix="-other")
    action = ActionType(
        id="action-guarded", ontology_id=ontology_id,
        name="guarded", display_name="Guarded",
        object_type_id=object_type.id, parameters=[], rules=[],
        validation_function_id="missing-validation-function",
    )
    db.add(action)
    db.commit()

    missing_validation = execute_action(db, ontology_id, _body(action, instance))
    wrong_target = execute_action(db, ontology_id, _body(action, other))
    assert missing_validation["status"] == "failed"
    assert any("校验函数不存在" in item for item in missing_validation["validationErrors"])
    assert wrong_target["status"] == "failed"
    assert any("类型不匹配" in item for item in wrong_target["validationErrors"])

    action.validation_function_id = None
    action.rules = [{
        "id": "mystery", "type": "silently_do_something",
        "name": "mystery", "enabled": True, "order": 0, "config": {},
    }]
    db.commit()
    unknown_rule = execute_action(db, ontology_id, _body(action, instance))
    assert unknown_rule["status"] == "failed"
    assert any("不支持的动作规则类型" in item
               for item in unknown_rule["validationErrors"])


@pytest.mark.parametrize("rule", [
    {
        "id": "email", "type": "notification", "name": "email",
        "enabled": True, "order": 0,
        "config": {
            "channel": "email", "recipientSource": "constant",
            "recipient": "ops@example.com", "messageTemplate": "hello",
        },
    },
    {
        "id": "webhook", "type": "webhook", "name": "webhook",
        "enabled": True, "order": 0,
        "config": {"url": "https://example.invalid/hook", "method": "POST"},
    },
])
def test_external_delivery_never_reports_fake_success(db, rule):
    ontology_id = f"delivery-{rule['id']}"
    object_type, instance = _seed_object(db, ontology_id)
    action = ActionType(
        id=f"action-{rule['id']}", ontology_id=ontology_id,
        name=rule["id"], display_name=rule["id"],
        object_type_id=object_type.id, parameters=[], rules=[rule],
    )
    db.add(action)
    db.commit()

    result = execute_action(db, ontology_id, _body(action, instance))
    assert result["status"] == "failed"
    assert "可靠投递器" in result["errorMessage"]
    assert db.query(Notification).filter_by(ontology_id=ontology_id).count() == 0


def test_internal_notification_has_a_real_queryable_sink(db):
    ontology_id = "delivery-internal"
    object_type, instance = _seed_object(db, ontology_id)
    action = ActionType(
        id="action-internal", ontology_id=ontology_id,
        name="internal", display_name="Internal",
        object_type_id=object_type.id, parameters=[], rules=[{
            "id": "internal", "type": "notification", "name": "internal",
            "enabled": True, "order": 0,
            "config": {
                "channel": "internal", "recipientSource": "constant",
                "recipient": "ops", "messageTemplate": "object={{object.status}}",
            },
        }],
    )
    db.add(action)
    db.commit()

    result = execute_action(db, ontology_id, _body(action, instance))
    notification = db.query(Notification).filter_by(ontology_id=ontology_id).one()
    assert result["status"] == "success"
    assert notification.status == "delivered"
    assert notification.body == "object=new"


def test_action_idempotency_replays_success_and_pending(db):
    ontology_id = "action-idempotency"
    object_type, instance = _seed_object(db, ontology_id)
    action = ActionType(
        id="increment", ontology_id=ontology_id,
        name="increment", display_name="Increment",
        object_type_id=object_type.id, parameters=[],
        rules=[_update_rule("count", source="expression", value="object.count + 1")],
    )
    pending_action = ActionType(
        id="approval", ontology_id=ontology_id,
        name="approval", display_name="Approval",
        object_type_id=object_type.id, parameters=[], rules=[],
        requires_approval=True,
    )
    db.add_all([action, pending_action])
    db.commit()

    first = execute_action(
        db, ontology_id,
        _body(action, instance, idempotency_key="idem-success",
              sentinel_match_state_id="state-1"),
    )
    second = execute_action(
        db, ontology_id,
        _body(action, instance, idempotency_key="idem-success",
              sentinel_match_state_id="state-1"),
    )
    db.refresh(instance)
    assert first["status"] == "success"
    assert second["status"] == "success" and second["idempotentReplay"] is True
    assert second["id"] == first["id"]
    assert instance.properties["count"] == 1

    pending_one = execute_action(
        db, ontology_id,
        _body(pending_action, instance, idempotency_key="idem-pending",
              sentinel_match_state_id="state-2"),
    )
    pending_two = execute_action(
        db, ontology_id,
        _body(pending_action, instance, idempotency_key="idem-pending",
              sentinel_match_state_id="state-2"),
    )
    assert pending_one["status"] == "pending"
    assert pending_two["id"] == pending_one["id"]
    assert pending_two["idempotentReplay"] is True
    assert db.query(ActionExecutionLog).filter_by(
        ontology_id=ontology_id, idempotency_key="idem-pending").count() == 1


def test_sentinel_multi_action_retry_does_not_repeat_successful_step(db):
    ontology_id = "sentinel-chain"
    object_type, instance = _seed_object(db, ontology_id)
    increment = ActionType(
        id="chain-increment", ontology_id=ontology_id,
        name="increment", display_name="Increment",
        object_type_id=object_type.id, parameters=[],
        rules=[_update_rule("count", source="expression", value="object.count + 1")],
    )
    downstream = ActionType(
        id="chain-downstream", ontology_id=ontology_id,
        name="downstream", display_name="Downstream",
        object_type_id=object_type.id, parameters=[], rules=[{
            "id": "unavailable", "type": "webhook", "name": "unavailable",
            "enabled": True, "order": 0,
            "config": {"url": "https://example.invalid"},
        }],
    )
    sentinel = Sentinel(
        id="sentinel-chain", ontology_id=ontology_id,
        name="chain", display_name="Chain",
        bindings=[{"alias": "a", "objectTypeId": object_type.id}], links=[],
        condition="a.active == True", primary_alias="a",
        action_ids=[increment.id, downstream.id], action_parameters={},
        trigger_mode="on_enter", muted=False, enabled=True, status="published",
    )
    db.add_all([increment, downstream, sentinel])
    db.commit()

    first = evaluate_sentinel(db, ontology_id, sentinel, "manual")
    state = db.query(SentinelMatchState).filter_by(sentinel_id=sentinel.id).one()
    first_state_id = state.id
    db.refresh(instance)
    assert first.status == "error"
    assert state.runtime_status == "failed_enter"
    assert instance.properties["count"] == 1

    downstream.rules = [_update_rule("status")]
    db.commit()
    second = evaluate_sentinel(db, ontology_id, sentinel, "manual")
    state = db.query(SentinelMatchState).filter_by(sentinel_id=sentinel.id).one()
    db.refresh(instance)
    assert second.status == "fired"
    assert state.id == first_state_id and state.runtime_status == "completed"
    assert instance.properties["count"] == 1
    assert instance.properties["status"] == "done"
    assert second.action_results[0]["idempotentReplay"] is True


def test_sentinel_pending_reuses_approval_and_rejection_releases_claim(db):
    ontology_id = "sentinel-pending"
    object_type, instance = _seed_object(db, ontology_id)
    action = ActionType(
        id="pending-action", ontology_id=ontology_id,
        name="pending", display_name="Pending",
        object_type_id=object_type.id, parameters=[], rules=[],
        requires_approval=True,
    )
    sentinel = Sentinel(
        id="sentinel-pending", ontology_id=ontology_id,
        name="pending", display_name="Pending",
        bindings=[{"alias": "a", "objectTypeId": object_type.id}], links=[],
        condition="a.active == True", primary_alias="a",
        action_ids=[action.id], action_parameters={}, trigger_mode="on_enter",
        muted=False, enabled=True, status="published",
    )
    db.add_all([action, sentinel])
    db.commit()

    first = evaluate_sentinel(db, ontology_id, sentinel, "manual")
    second = evaluate_sentinel(db, ontology_id, sentinel, "manual")
    state = db.query(SentinelMatchState).filter_by(sentinel_id=sentinel.id).one()
    pending_logs = db.query(ActionExecutionLog).filter_by(
        ontology_id=ontology_id, status="pending").all()
    assert first.status == "pending" and second.status == "pending"
    assert state.runtime_status == "pending_enter"
    assert len(pending_logs) == 1
    assert second.action_results[0]["idempotentReplay"] is True

    pending_logs[0].status = "rejected"
    pending_logs[0].idempotency_key = None
    db.commit()
    released = reject_sentinel_match_claim(db, ontology_id, state.id)
    assert released["status"] == "released"
    assert db.query(SentinelMatchState).filter_by(sentinel_id=sentinel.id).count() == 0


def test_approved_sentinel_step_resumes_remaining_chain_once(db):
    ontology_id = "sentinel-approval-resume"
    object_type, instance = _seed_object(db, ontology_id)
    increment = ActionType(
        id="approval-increment", ontology_id=ontology_id,
        name="increment", display_name="Increment",
        object_type_id=object_type.id, parameters=[],
        rules=[_update_rule("count", source="expression", value="object.count + 1")],
    )
    approval = ActionType(
        id="approval-gate", ontology_id=ontology_id,
        name="approval", display_name="Approval",
        object_type_id=object_type.id, parameters=[],
        rules=[_update_rule("status", value="\"approved\"")],
        requires_approval=True,
    )
    finish = ActionType(
        id="approval-finish", ontology_id=ontology_id,
        name="finish", display_name="Finish",
        object_type_id=object_type.id, parameters=[],
        rules=[_update_rule("status")],
    )
    sentinel = Sentinel(
        id="sentinel-approval-resume", ontology_id=ontology_id,
        name="approval_resume", display_name="Approval resume",
        bindings=[{"alias": "a", "objectTypeId": object_type.id}], links=[],
        condition="a.active == True", primary_alias="a",
        action_ids=[increment.id, approval.id, finish.id], action_parameters={},
        trigger_mode="on_enter", muted=False, enabled=True, status="published",
    )
    db.add_all([increment, approval, finish, sentinel])
    db.commit()

    firing = evaluate_sentinel(db, ontology_id, sentinel, "manual")
    state = db.query(SentinelMatchState).filter_by(sentinel_id=sentinel.id).one()
    pending = db.query(ActionExecutionLog).filter_by(
        ontology_id=ontology_id, action_id=approval.id, status="pending").one()
    db.refresh(instance)
    assert firing.status == "pending"
    # Approval is a gate for the whole chain: no earlier automatic side effect
    # is committed while a later step is still awaiting a human decision.
    assert instance.properties["count"] == 0
    assert instance.properties["status"] == "new"

    actual = execute_action(
        db, ontology_id,
        _body(
            approval, instance,
            sentinel_match_state_id=state.id,
        ),
        skip_approval=True,
    )
    assert actual["status"] == "success"
    pending.status = "approved"
    pending.related_log_id = actual["id"]
    db.commit()

    resumed = resume_sentinel_match_claim(db, ontology_id, state.id)
    db.refresh(instance)
    state = db.query(SentinelMatchState).filter_by(sentinel_id=sentinel.id).one()
    assert resumed["status"] == "fired"
    assert state.runtime_status == "completed"
    assert instance.properties["count"] == 1
    assert instance.properties["status"] == "done"
    assert [item["idempotentReplay"] for item in resumed["actionResults"][:2]] \
        == [False, True]


def test_match_state_has_database_uniqueness_guard(db):
    one = SentinelMatchState(
        id="state-one", ontology_id="unique", sentinel_id="sentinel",
        match_key="same", match_detail={}, runtime_status="completed",
    )
    two = SentinelMatchState(
        id="state-two", ontology_id="unique", sentinel_id="sentinel",
        match_key="same", match_detail={}, runtime_status="completed",
    )
    db.add(one)
    db.commit()
    db.add(two)
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_pending_approval_cannot_cross_ontology_version(db):
    ontology_id = "approval-release-binding"
    object_type, instance = _seed_object(db, ontology_id)
    action = ActionType(
        id="approval-release-action", ontology_id=ontology_id,
        name="approve_me", display_name="Approve me",
        object_type_id=object_type.id, parameters=[], rules=[],
        requires_approval=True,
    )
    db.add(action)
    db.commit()

    pending = execute_action(db, ontology_id, _body(action, instance))
    assert pending["status"] == "pending"
    assert pending["ontologyVersion"] == "v1.0.0"

    project = db.query(OntologyProject).filter_by(id=ontology_id).one()
    project.version = "v2.0.0"
    db.commit()
    admin = SimpleNamespace(id="admin", username="admin", role="admin")

    with pytest.raises(HTTPException) as stale:
        decide_pending_action(
            ontology_id, pending["id"],
            DecisionRequest(decision="approved"), db, admin)
    assert stale.value.status_code == 409
    # Rejection remains possible so stale work can be closed without effects.
    rejected = decide_pending_action(
        ontology_id, pending["id"],
        DecisionRequest(decision="rejected", reason="stale release"), db, admin)
    assert rejected["data"]["status"] == "rejected"


def test_celery_delayed_tasks_and_compose_workers_use_the_same_app():
    from app.tasks.celery_app import celery_app

    celery_app.loader.import_default_modules()
    assert "app.tasks.v2.pipeline_run.pipeline_run_task" in celery_app.tasks
    assert "app.tasks.v2.mapping_apply.mapping_apply_task" in celery_app.tasks

    root = Path(__file__).resolve().parents[2]
    for filename in ("docker-compose.yml", "docker-compose.v2.yml", "docker-compose.prod.yml"):
        compose = yaml.safe_load((root / filename).read_text(encoding="utf-8"))
        assert compose["services"]["celery_worker"]["command"] == (
            "celery -A app.tasks.celery_app:celery_app worker --loglevel=info"
        )
