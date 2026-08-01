from contextlib import contextmanager

import pytest
from fastapi import HTTPException

from app.models.entity import Entity
from app.models.action import Action
from app.models.logic import LogicRule
from app.models.ontology import OntologyProject
from app.models.v2.logic import OntologyLogicRule
from app.models.v2.action import OntologyActionRun, OntologyActionType
from app.routers.actions import publish_actions
from app.routers.logic import publish_logic_rules
from app.routers.v2.logic_actions import (
    ActionReviewRequest,
    ActionRunRequest,
    LogicReviewRequest,
    LogicTestRequest,
    review_action_type,
    review_logic_rule,
    run_action_type,
    test_logic_rule as run_logic_rule_test,
)


def _project(ontology_id: str) -> OntologyProject:
    return OntologyProject(
        id=ontology_id,
        name=ontology_id,
        domain="logic-action-tests",
        created_by="tests",
    )


def test_run_published_set_property_action(db, monkeypatch):
    from app.ontologies import projection_state, runtime_fence

    ontology_id = "ont-runtime-1"
    events: list[str] = []
    lock_held = {"value": False}
    original_rebuild = projection_state.rebuild_after_commit

    @contextmanager
    def observed_lock(_db, target_ontology_id):
        assert target_ontology_id == ontology_id
        lock_held["value"] = True
        events.append("lock-enter")
        try:
            yield
        finally:
            events.append("lock-exit")
            lock_held["value"] = False

    def observed_rebuild(session, target_ontology_id):
        assert lock_held["value"] is True
        events.append("rebuild")
        return original_rebuild(session, target_ontology_id)

    monkeypatch.setattr(
        runtime_fence,
        "_ontology_build_lock",
        observed_lock,
    )
    monkeypatch.setattr(
        projection_state,
        "rebuild_after_commit",
        observed_rebuild,
    )
    entity = Entity(
        id="entity-1",
        ontology_id=ontology_id,
        name_cn="Order 1",
        name_en="Order",
        type="Order",
        properties={"status": "pending"},
    )
    action = OntologyActionType(
        id="action-1",
        ontology_id=ontology_id,
        name="Change Order status",
        action_category="state_transition",
        target_entity_type="Order",
        parameters=[{"name": "status", "type": "string", "required": True}],
        effects=[{"action": "set_property", "property": "status"}],
        status="published",
        enabled=True,
    )
    db.add(_project(ontology_id))
    db.add(entity)
    db.add(action)
    db.commit()

    result = run_action_type(
        ontology_id,
        action.id,
        ActionRunRequest(target_object_id=entity.id, parameters={"status": "approved"}),
        db,
    )

    db.refresh(entity)
    run = db.query(OntologyActionRun).filter(OntologyActionRun.id == result["run_id"]).first()
    assert result["status"] == "completed"
    assert entity.properties["status"] == "approved"
    assert run.status == "completed"
    assert run.before_snapshot[entity.id]["status"] == "pending"
    assert run.after_snapshot[entity.id]["status"] == "approved"
    project = db.query(OntologyProject).filter_by(id=ontology_id).one()
    assert project.projection_status == "ready"
    assert events == ["lock-enter", "rebuild", "lock-exit"]


def test_legacy_action_keeps_committed_truth_when_projection_rebuild_fails(
    db, monkeypatch,
):
    from app.ontologies import projection_state

    ontology_id = "ont-runtime-projection-failure"
    entity = Entity(
        id="entity-projection-failure",
        ontology_id=ontology_id,
        name_cn="Order",
        name_en="Order",
        type="Order",
        properties={"status": "pending"},
    )
    action = OntologyActionType(
        id="action-projection-failure",
        ontology_id=ontology_id,
        name="Change status",
        action_category="state_transition",
        target_entity_type="Order",
        parameters=[{"name": "status", "type": "string", "required": True}],
        effects=[{"action": "set_property", "property": "status"}],
        status="published",
        enabled=True,
    )
    db.add_all([_project(ontology_id), entity, action])
    db.commit()

    original_rebuild = projection_state.rebuild_after_commit

    def fail_rebuild(session, target_ontology_id):
        return original_rebuild(
            session,
            target_ontology_id,
            rebuild=lambda _ontology_id: False,
            run_in_test=True,
        )

    monkeypatch.setattr(
        projection_state,
        "rebuild_after_commit",
        fail_rebuild,
    )
    with pytest.raises(HTTPException) as exc_info:
        run_action_type(
            ontology_id,
            action.id,
            ActionRunRequest(
                target_object_id=entity.id,
                parameters={"status": "approved"},
            ),
            db,
        )

    assert getattr(exc_info.value, "status_code", None) == 503
    assert exc_info.value.detail["code"] == "ontology_projection_failed"
    db.expire_all()
    assert db.query(Entity).filter_by(id=entity.id).one().properties == {
        "status": "approved",
    }
    run = db.query(OntologyActionRun).filter_by(
        ontology_id=ontology_id,
    ).one()
    assert run.status == "completed"
    project = db.query(OntologyProject).filter_by(id=ontology_id).one()
    assert project.projection_status == "failed"
    assert "incomplete" in (project.projection_error or "")


def test_logic_review_and_executable_test(db):
    rule = OntologyLogicRule(
        id="logic-1",
        ontology_id="ont-runtime-2",
        name="Amount positive",
        logic_type="validation",
        target_entity_type="Order",
        expression={"operator": "gt", "field": "amount", "value": 0},
        status="draft",
        enabled=True,
    )
    db.add(rule)
    db.commit()

    review = review_logic_rule(
        "ont-runtime-2",
        "logic-1",
        LogicReviewRequest(status="reviewed", enabled=True, notes="approved"),
        db,
    )
    result = run_logic_rule_test(
        "ont-runtime-2",
        "logic-1",
        LogicTestRequest(row={"amount": "12.5"}),
        db,
    )

    assert review["status"] == "reviewed"
    assert result["status"] == "completed"
    assert result["passed"] is True
    assert db.query(OntologyLogicRule).filter_by(id="logic-1").first().source_ref["review_notes"] == "approved"


def test_action_review_updates_submission_criteria(db):
    action = OntologyActionType(
        id="action-review-1",
        ontology_id="ont-runtime-3",
        name="Reviewable action",
        action_category="crud",
        effects=[],
        status="draft",
        enabled=True,
    )
    db.add(action)
    db.commit()

    result = review_action_type(
        "ont-runtime-3",
        "action-review-1",
        ActionReviewRequest(
            status="reviewed",
            submission_criteria=[{"type": "required_param", "name": "reason"}],
            notes="needs reason",
        ),
        db,
    )

    db.refresh(action)
    assert result["status"] == "reviewed"
    assert action.submission_criteria[0]["name"] == "reason"
    assert action.side_effects[0]["type"] == "review_note"


def test_action_runtime_rejects_missing_required_parameter(db):
    db.add(_project("ont-runtime-4"))
    action = OntologyActionType(
        id="action-criteria-1",
        ontology_id="ont-runtime-4",
        name="Create Order",
        action_category="crud",
        target_entity_type="Order",
        parameters=[{"name": "data", "type": "object", "required": True}],
        effects=[{"action": "create_object", "entity_type": "Order"}],
        status="published",
        enabled=True,
    )
    db.add(action)
    db.commit()

    try:
        run_action_type("ont-runtime-4", action.id, ActionRunRequest(parameters={}), db)
    except Exception as exc:
        assert "missing_parameter" in str(exc)
    else:
        raise AssertionError("Expected action submission to fail")


def test_v1_logic_publish_syncs_v2_status(db):
    ontology_id = "ont-runtime-5"
    db.add(LogicRule(
        id="logic-v1",
        ontology_id=ontology_id,
        name_cn="Mapping Rule: Supplier",
        name_en="mapping_supplier",
        formula="mapping",
        enabled=True,
        status="draft",
    ))
    db.add(OntologyLogicRule(
        id="logic-v2",
        ontology_id=ontology_id,
        name="Mapping Rule: Supplier",
        logic_type="mapping",
        expression={},
        enabled=True,
        status="draft",
    ))
    db.commit()

    publish_logic_rules(ontology_id, db)

    assert db.query(LogicRule).filter_by(id="logic-v1").first().status == "published"
    assert db.query(OntologyLogicRule).filter_by(id="logic-v2").first().status == "published"


def test_v1_action_publish_syncs_v2_status(db):
    ontology_id = "ont-runtime-6"
    db.add(Action(
        id="action-v1",
        ontology_id=ontology_id,
        name_cn="Create Supplier",
        name_en="create_supplier",
        enabled=True,
        status="draft",
    ))
    db.add(OntologyActionType(
        id="action-v2",
        ontology_id=ontology_id,
        name="Create Supplier",
        action_category="crud",
        effects=[],
        enabled=True,
        status="draft",
    ))
    db.commit()

    publish_actions(ontology_id, db)

    assert db.query(Action).filter_by(id="action-v1").first().status == "published"
    assert db.query(OntologyActionType).filter_by(id="action-v2").first().status == "published"
