"""Compatibility checks for the formal-modeling router split."""
from __future__ import annotations

from types import SimpleNamespace

from app.ontologies.formal_modeling import (
    action_workflow_service,
    dashboard_queries,
    instance_service,
    router as formal_router,
    runtime_support,
    schema_authoring_service,
)


def test_formal_router_reexports_runtime_support_aliases():
    assert formal_router._require_ontology is runtime_support._require_ontology
    assert formal_router._ok is runtime_support._ok
    assert formal_router._naive_utc is runtime_support._naive_utc
    assert (
        formal_router._current_release_view
        is runtime_support._current_release_view
    )
    assert (
        formal_router._release_fact_query
        is runtime_support._release_fact_query
    )
    assert (
        formal_router._approval_instance_label
        is runtime_support._approval_instance_label
    )
    assert formal_router._fact_to_dict is runtime_support._fact_to_dict
    assert formal_router._orm_view is runtime_support._orm_view
    assert (
        formal_router._raise_validation_failed
        is runtime_support._raise_validation_failed
    )


def test_formal_router_reexports_instance_service_helpers():
    assert (
        formal_router._reject_direct_runtime_data_write
        is instance_service._reject_direct_runtime_data_write
    )
    assert (
        formal_router._release_catalog_item
        is instance_service._release_catalog_item
    )
    assert (
        formal_router._instance_browser_release
        is instance_service._instance_browser_release
    )
    assert (
        formal_router._instance_summary
        is instance_service._instance_summary
    )
    assert formal_router._mapping_value is instance_service._mapping_value
    assert (
        formal_router._mapping_matches_object_type
        is instance_service._mapping_matches_object_type
    )
    assert (
        formal_router._mapping_matches_link_type
        is instance_service._mapping_matches_link_type
    )
    assert (
        formal_router._release_dataset_associations
        is instance_service._release_dataset_associations
    )


def test_formal_router_reexports_schema_authoring_helpers():
    assert (
        formal_router._require_schema_draft
        is schema_authoring_service._require_schema_draft
    )
    assert (
        formal_router._reject_direct_runtime_data_write
        is schema_authoring_service._reject_direct_runtime_data_write
    )
    assert (
        instance_service._reject_direct_runtime_data_write
        is schema_authoring_service._reject_direct_runtime_data_write
    )
    assert (
        formal_router._runtime_state
        is schema_authoring_service._runtime_state
    )
    assert (
        formal_router._dedup_properties
        is schema_authoring_service._dedup_properties
    )
    assert (
        formal_router._upsert_items
        is schema_authoring_service._upsert_items
    )
    assert (
        formal_router._scrub_orphan_data
        is schema_authoring_service._scrub_orphan_data
    )
    assert formal_router._revision_of is schema_authoring_service._revision_of
    assert (
        formal_router._scrub_dangling_references
        is schema_authoring_service._scrub_dangling_references
    )
    assert formal_router._crud is schema_authoring_service._crud
    assert (
        formal_router.FIELDS_OBJECT_TYPE
        is schema_authoring_service.FIELDS_OBJECT_TYPE
    )
    assert (
        formal_router.FIELDS_LINK_TYPE
        is schema_authoring_service.FIELDS_LINK_TYPE
    )
    assert (
        formal_router.FIELDS_ACTION
        is schema_authoring_service.FIELDS_ACTION
    )
    assert (
        formal_router.FIELDS_FUNCTION
        is schema_authoring_service.FIELDS_FUNCTION
    )
    assert (
        formal_router.FIELDS_INSTANCE
        is schema_authoring_service.FIELDS_INSTANCE
    )
    assert (
        formal_router.FIELDS_LINK_INSTANCE
        is schema_authoring_service.FIELDS_LINK_INSTANCE
    )


def test_full_ontology_public_entry_delegates_with_compatibility_helpers(
    monkeypatch,
):
    database = object()
    expected = {"data": {"id": "ontology-1"}}
    replacement_require = object()
    replacement_revision = object()
    replacement_ok = object()
    received = {}

    def fake_get(ontology_id, db, **kwargs):
        received["args"] = (ontology_id, db)
        received["kwargs"] = kwargs
        return expected

    monkeypatch.setattr(
        formal_router,
        "_require_ontology",
        replacement_require,
    )
    monkeypatch.setattr(
        formal_router,
        "_revision_of",
        replacement_revision,
    )
    monkeypatch.setattr(formal_router, "_ok", replacement_ok)
    monkeypatch.setattr(
        schema_authoring_service,
        "get_full_ontology",
        fake_get,
    )

    result = formal_router.get_full_ontology(
        "ontology-1",
        db=database,
        _=None,
    )

    assert result is expected
    assert received["args"] == ("ontology-1", database)
    assert received["kwargs"] == {
        "require_ontology_fn": replacement_require,
        "revision_of_fn": replacement_revision,
        "ok_fn": replacement_ok,
    }


def test_full_save_wrapper_preserves_router_helper_monkeypatches(
    monkeypatch,
):
    database = object()
    actor = object()
    body = object()
    expected = {"data": {"revision": "revision-2"}}
    replacement_revision = object()
    replacement_upsert = object()
    replacement_scrub = object()
    received = {}

    def fake_save(ontology_id, request, db, current_user, **kwargs):
        received["args"] = (
            ontology_id,
            request,
            db,
            current_user,
        )
        received["kwargs"] = kwargs
        return expected

    monkeypatch.setattr(
        formal_router,
        "_revision_of",
        replacement_revision,
    )
    monkeypatch.setattr(
        formal_router,
        "_upsert_items",
        replacement_upsert,
    )
    monkeypatch.setattr(
        formal_router,
        "_scrub_dangling_references",
        replacement_scrub,
    )
    monkeypatch.setattr(
        schema_authoring_service,
        "save_full_ontology",
        fake_save,
    )

    result = formal_router.save_full_ontology(
        "ontology-1",
        body,
        db=database,
        current_user=actor,
    )

    assert result is expected
    assert received["args"] == (
        "ontology-1",
        body,
        database,
        actor,
    )
    assert received["kwargs"]["revision_of_fn"] is replacement_revision
    assert received["kwargs"]["upsert_items_fn"] is replacement_upsert
    assert (
        received["kwargs"]["scrub_dangling_references_fn"]
        is replacement_scrub
    )
    assert (
        received["kwargs"]["get_full_ontology_fn"]
        is formal_router.get_full_ontology
    )


def test_instance_write_wrapper_preserves_router_guard_monkeypatch(
    monkeypatch,
):
    database = object()
    actor = object()
    body = object()
    expected = {"data": {"id": "instance-1"}}
    replacement_guard = object()
    received = {}

    def fake_create(
        ontology_id,
        request,
        db,
        current_user,
        *,
        reject_runtime_write_fn,
    ):
        received["args"] = (
            ontology_id,
            request,
            db,
            current_user,
        )
        received["guard"] = reject_runtime_write_fn
        return expected

    monkeypatch.setattr(
        formal_router,
        "_reject_direct_runtime_data_write",
        replacement_guard,
    )
    monkeypatch.setattr(instance_service, "create_instance", fake_create)

    result = formal_router.create_instance(
        "ontology-1",
        body,
        db=database,
        current_user=actor,
    )

    assert result is expected
    assert received["args"] == (
        "ontology-1",
        body,
        database,
        actor,
    )
    assert received["guard"] is replacement_guard


def test_instance_browser_public_entry_delegates_to_canonical_service(
    monkeypatch,
):
    database = object()
    expected = {"data": {"items": [], "total": 0}}
    received = {}

    def fake_objects(
        ontology_id,
        object_type_id,
        page,
        page_size,
        keyword,
        db,
    ):
        received["args"] = (
            ontology_id,
            object_type_id,
            page,
            page_size,
            keyword,
            db,
        )
        return expected

    monkeypatch.setattr(
        instance_service,
        "instance_browser_objects",
        fake_objects,
    )

    result = formal_router.instance_browser_objects(
        "ontology-1",
        object_type_id="object-type-1",
        page=2,
        page_size=25,
        keyword="订单",
        db=database,
        _=None,
    )

    assert result is expected
    assert received["args"] == (
        "ontology-1",
        "object-type-1",
        2,
        25,
        "订单",
        database,
    )


def test_pending_action_public_entry_delegates_to_canonical_service(
    monkeypatch,
):
    database = object()
    expected = {"data": [{"id": "pending-1"}]}
    received = {}

    def fake_list(
        ontology_id,
        release_id,
        current_release_only,
        db,
    ):
        received["args"] = (
            ontology_id,
            release_id,
            current_release_only,
            db,
        )
        return expected

    monkeypatch.setattr(
        action_workflow_service,
        "list_pending_actions",
        fake_list,
    )

    result = formal_router.list_pending_actions(
        "ontology-1",
        release_id="release-1",
        current_release_only=True,
        db=database,
        _=None,
    )

    assert result is expected
    assert received["args"] == (
        "ontology-1",
        "release-1",
        True,
        database,
    )


def test_decision_wrapper_preserves_router_execute_action_monkeypatch(
    monkeypatch,
):
    replacement_execute = object()
    received = {}
    expected = {"data": {"status": "approved"}}

    def fake_decide(
        ontology_id,
        log_id,
        body,
        db,
        current_user,
        *,
        execute_action_fn,
    ):
        received["args"] = (
            ontology_id,
            log_id,
            body,
            db,
            current_user,
        )
        received["execute_action_fn"] = execute_action_fn
        return expected

    monkeypatch.setattr(
        formal_router,
        "execute_action",
        replacement_execute,
    )
    monkeypatch.setattr(
        action_workflow_service,
        "decide_pending_action_locked",
        fake_decide,
    )
    body = SimpleNamespace(decision="approved")
    database = object()
    actor = object()

    result = formal_router._decide_pending_action_locked(
        "ontology-1",
        "log-1",
        body,
        database,
        actor,
    )

    assert result is expected
    assert received["args"] == (
        "ontology-1",
        "log-1",
        body,
        database,
        actor,
    )
    assert received["execute_action_fn"] is replacement_execute


def test_dashboard_public_entries_and_thresholds_remain_available(
    monkeypatch,
):
    database = object()
    expected = {"data": {"release": {"id": "release-1"}}}
    monkeypatch.setattr(
        dashboard_queries,
        "ontology_overview",
        lambda ontology_id, db: expected,
    )

    assert (
        formal_router.ontology_overview(
            "ontology-1",
            db=database,
            _=None,
        )
        is expected
    )
    assert (
        formal_router.AUTONOMY_PROMOTE_MIN
        == dashboard_queries.AUTONOMY_PROMOTE_MIN
    )
    assert (
        formal_router.AUTONOMY_PROMOTE_RATE
        == dashboard_queries.AUTONOMY_PROMOTE_RATE
    )
    assert (
        formal_router.AUTONOMY_DEMOTE_FAILRATE
        == dashboard_queries.AUTONOMY_DEMOTE_FAILRATE
    )
