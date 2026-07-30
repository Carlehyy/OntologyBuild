"""Derived projections must never expose a stale value to Sentinel."""

from __future__ import annotations

from datetime import datetime

import pytest

from app.models.ontology import OntologyProject
from app.models.ontology_formal import (
    ObjectInstance,
    ObjectType,
    OntologyFunction,
    PropertyFact,
)
from app.ontologies.formal_modeling.derived import (
    DerivedComputationError,
    recompute_instance_derived,
)
from app.ontologies.formal_modeling.facts import record_object_presence
from app.ontologies.sentinels.evaluator import _instance_values
from app.ontologies.formal_modeling.validation import validate_model


def _seed(
    db,
    suffix: str,
    *,
    function_present: bool = True,
    enabled: bool = True,
    language: str = "expression",
    body: str = 'object["amount"] * 2',
    function_type: str = "object",
    target_object_type_id: str | None = None,
    parameters: list | None = None,
    return_type: str = "number",
) -> tuple[str, ObjectInstance]:
    ontology_id = f"derived-fail-closed-{suffix}"
    object_type_id = f"derived-type-{suffix}"
    function_id = f"derived-function-{suffix}"
    db.add(OntologyProject(
        id=ontology_id,
        name=ontology_id,
        domain="derived-tests",
        created_by="tests",
        status="draft",
        version="v1.0.0",
    ))
    db.add(ObjectType(
        id=object_type_id,
        ontology_id=ontology_id,
        name=object_type_id,
        display_name=object_type_id,
        properties=[
            {
                "id": f"stored-amount-{suffix}",
                "name": "amount",
                "displayName": "Amount",
                "type": "number",
                "source": "stored",
            },
            {
                "id": f"derived-property-{suffix}",
                "name": "score",
                "displayName": "Score",
                "type": "number",
                "source": "computed",
                "functionId": function_id,
            },
        ],
    ))
    if function_present:
        db.add(OntologyFunction(
            id=function_id,
            ontology_id=ontology_id,
            name=function_id,
            display_name=function_id,
            function_type=function_type,
            language=language,
            target_object_type_id=(
                target_object_type_id or object_type_id),
            parameters=parameters or [],
            return_type=return_type,
            body=body,
            enabled=enabled,
        ))
    instance = ObjectInstance(
        id=f"derived-instance-{suffix}",
        ontology_id=ontology_id,
        object_type_id=object_type_id,
        properties={"amount": 5},
        computed={"score": 10, "unrelated": "kept"},
        source="pipeline",
    )
    db.add(instance)
    db.commit()
    db.refresh(instance)
    return ontology_id, instance


@pytest.mark.parametrize(
    ("suffix", "seed_kwargs", "message"),
    [
        ("missing", {"function_present": False}, "函数不存在"),
        ("disabled", {"enabled": False}, "已禁用"),
        (
            "execution-error",
            {"body": 'object["amount"] / 0'},
            "重算失败",
        ),
    ],
)
def test_expression_derived_failure_aborts_input_update_and_preserves_consistency(
        db, suffix, seed_kwargs, message):
    ontology_id, instance = _seed(db, suffix, **seed_kwargs)

    instance.properties = {"amount": 9}
    with pytest.raises(DerivedComputationError, match=message):
        recompute_instance_derived(
            db, ontology_id=ontology_id, instance=instance)
    db.rollback()

    persisted = db.query(ObjectInstance).filter_by(id=instance.id).one()
    assert persisted.properties == {"amount": 5}
    assert persisted.computed == {"score": 10, "unrelated": "kept"}


def test_non_expression_derived_value_is_invalidated_before_sentinel_can_read_it(
        db):
    ontology_id, instance = _seed(
        db, "typescript", language="typescript", body="return object.amount * 2")

    changed = recompute_instance_derived(
        db, ontology_id=ontology_id, instance=instance)
    db.flush()

    assert changed == 0
    assert instance.computed == {"unrelated": "kept"}
    assert "score" not in _instance_values(instance)


def test_computed_property_without_function_binding_is_invalidated(db):
    ontology_id, instance = _seed(db, "missing-binding")
    object_type = db.query(ObjectType).filter_by(
        id=instance.object_type_id).one()
    object_type.properties = [{
        "id": "unbound-score",
        "name": "score",
        "displayName": "Score",
        "type": "number",
        "source": "computed",
    }]
    db.flush()

    recompute_instance_derived(
        db, ontology_id=ontology_id, instance=instance)

    assert instance.computed == {"unrelated": "kept"}
    assert "score" not in _instance_values(instance)


def test_object_presence_fact_does_not_mask_derived_property_named_exists(db):
    ontology_id, instance = _seed(
        db,
        "derived-exists",
        body="True",
        return_type="boolean",
    )
    object_type = db.query(ObjectType).filter_by(
        id=instance.object_type_id).one()
    function_id = f"derived-function-derived-exists"
    object_type.properties = [
        {
            "id": "stored-amount-derived-exists",
            "name": "amount",
            "displayName": "Amount",
            "type": "number",
            "source": "stored",
        },
        {
            "id": "derived-property-exists",
            "name": "exists",
            "displayName": "Exists",
            "type": "boolean",
            "source": "computed",
            "functionId": function_id,
        },
    ]
    instance.computed = {"exists": True}
    record_object_presence(
        db,
        ontology_id=ontology_id,
        instance_id=instance.id,
        object_type_id=instance.object_type_id,
        source="action://create",
    )
    db.flush()

    changed = recompute_instance_derived(
        db, ontology_id=ontology_id, instance=instance)
    db.flush()

    assert changed == 1
    derived = db.query(PropertyFact).filter_by(
        ontology_id=ontology_id,
        instance_id=instance.id,
        property_name="exists",
        kind="derived",
    ).one()
    assert derived.value == {"v": True, "present": True}


def test_expression_result_type_mismatch_aborts_projection_update(db):
    ontology_id, instance = _seed(
        db, "wrong-result-type", body='"not-a-number"')

    instance.properties = {"amount": 9}
    with pytest.raises(DerivedComputationError, match="结果类型不匹配"):
        recompute_instance_derived(
            db, ontology_id=ontology_id, instance=instance)
    db.rollback()

    persisted = db.query(ObjectInstance).filter_by(id=instance.id).one()
    assert persisted.properties == {"amount": 5}
    assert persisted.computed == {"score": 10, "unrelated": "kept"}


@pytest.mark.parametrize(
    ("suffix", "seed_kwargs", "message"),
    [
        (
            "object-set-binding",
            {"function_type": "object_set"},
            "类型必须为 object",
        ),
        (
            "wrong-target-binding",
            {"target_object_type_id": "another-object-type"},
            "绑定了其他对象类型",
        ),
    ],
)
def test_computed_property_rejects_incompatible_function_contract(
        db, suffix, seed_kwargs, message):
    ontology_id, instance = _seed(db, suffix, **seed_kwargs)

    with pytest.raises(DerivedComputationError, match=message):
        recompute_instance_derived(
            db, ontology_id=ontology_id, instance=instance)


@pytest.mark.parametrize(
    ("suffix", "seed_kwargs", "error_code", "message"),
    [
        (
            "declared-parameter",
            {
                "parameters": [{
                    "name": "multiplier", "type": "number",
                }],
            },
            "derived_function_parameters_unsupported",
            "不能声明参数",
        ),
        (
            "params-scope",
            {"body": 'object["amount"] * params.multiplier'},
            "derived_function_params_scope_unsupported",
            "不能引用 params",
        ),
        (
            "objects-scope",
            {"body": "len(objects)"},
            "derived_function_objects_scope_unsupported",
            "不能引用 objects",
        ),
        (
            "computed-dependency",
            {"body": 'object["score"] + 1'},
            "derived_function_dependency_unsupported",
            "不能依赖其他派生属性",
        ),
        (
            "unknown-property",
            {"body": 'object["ammount"] * 2'},
            "derived_function_unknown_property",
            "未声明的存储属性",
        ),
        (
            "return-type",
            {"return_type": "string"},
            "derived_function_return_type_mismatch",
            "返回类型",
        ),
    ],
)
def test_derived_function_scope_contract_fails_static_and_runtime(
        db, suffix, seed_kwargs, error_code, message):
    ontology_id, instance = _seed(db, suffix, **seed_kwargs)
    object_type = db.query(ObjectType).filter_by(
        id=instance.object_type_id).one()
    function = db.query(OntologyFunction).filter_by(
        ontology_id=ontology_id).one()

    static_errors = validate_model(
        [object_type], [], [], [function], [instance], [])
    assert error_code in {
        item["code"] for item in static_errors}
    with pytest.raises(DerivedComputationError, match=message):
        recompute_instance_derived(
            db, ontology_id=ontology_id, instance=instance)


def test_derived_latest_fact_uses_seq_when_timestamps_are_equal(db):
    ontology_id, instance = _seed(db, "same-timestamp-order")
    fixed = datetime(2026, 7, 24, 12, 0, 0)
    older = PropertyFact(
        id="z-older-derived",
        ontology_id=ontology_id,
        instance_id=instance.id,
        object_type_id=instance.object_type_id,
        property_name="score",
        value={"v": 18},
        kind="derived",
        source="fn:test",
        seq=1,
        recorded_at=fixed,
    )
    newer = PropertyFact(
        id="a-newer-derived",
        ontology_id=ontology_id,
        instance_id=instance.id,
        object_type_id=instance.object_type_id,
        property_name="score",
        value={"v": 10},
        kind="derived",
        source="fn:test",
        seq=2,
        supersedes_id=older.id,
        recorded_at=fixed,
    )
    instance.properties = {"amount": 9}
    instance.computed = {"score": 10}
    db.add_all([older, newer])
    db.commit()

    changed = recompute_instance_derived(
        db, ontology_id=ontology_id, instance=instance)
    db.flush()

    facts = db.query(PropertyFact).filter_by(
        ontology_id=ontology_id,
        instance_id=instance.id,
        property_name="score",
    ).order_by(PropertyFact.seq.asc()).all()
    assert changed == 1
    assert [fact.value["v"] for fact in facts] == [18, 10, 18]
    assert facts[-1].seq == 3
    assert facts[-1].supersedes_id == newer.id
