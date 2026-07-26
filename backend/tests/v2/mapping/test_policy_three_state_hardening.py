"""Three-state boundaries for versioned Mapping automation policies."""
from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException

from app.models.ontology import OntologyProject
from app.models.ontology_version import OntologyVersion
from app.models.v2.dataset import Dataset
from app.models.v2.mapping import OntologyMapping
from app.ontologies.mappings.router import UpdateMappingRequest, update_mapping
from app.ontologies.versions.evolution_service import (
    complete_snapshot,
    snapshot_hash,
)
from app.ontologies.versions.router import save_draft_mappings


def _mapping_source(db, admin_user, *, project_id: str):
    project = OntologyProject(
        id=project_id,
        name=f"policy-{project_id}",
        domain="test",
        created_by=admin_user.id,
    )
    dataset = Dataset(
        id=f"dataset-{project_id}",
        name=f"dataset-{project_id}",
        kind="curated",
        schema_json={"primary_key": "id"},
    )
    mapping = OntologyMapping(
        id=f"mapping-{project_id}",
        ontology_id=project.id,
        curated_dataset_id=dataset.id,
        entity_class="BusinessRow",
        field_mapping={"id": "id", "__primary_key__": "id"},
        status="applied",
    )
    db.add_all([project, dataset, mapping])
    db.commit()
    return project, mapping


@pytest.mark.parametrize(
    ("policy_field", "stored_key"),
    [
        ("auto_apply_on_review", "__auto_apply_on_review__"),
        ("auto_apply_on_version", "__auto_apply_on_version__"),
    ],
)
def test_live_policy_switch_is_blocked_when_current_release_exists(
        db, admin_user, policy_field, stored_key):
    project, mapping = _mapping_source(
        db, admin_user, project_id=f"published-{policy_field}")
    release = OntologyVersion(
        id=str(uuid.uuid4()),
        ontology_id=project.id,
        version_number="v1",
        node_kind="release",
        lifecycle_status="released",
        revision=0,
        snapshot_formal=complete_snapshot({
            "mappings": [{
                "id": mapping.id,
                "curatedDatasetId": mapping.curated_dataset_id,
                "entityClass": mapping.entity_class,
                "fieldMapping": dict(mapping.field_mapping),
            }],
        }),
        created_by=admin_user.id,
    )
    db.add(release)
    db.flush()
    project.current_release_id = release.id
    project.status = "published"
    db.commit()

    with pytest.raises(HTTPException) as blocked:
        update_mapping(
            project.id,
            mapping.id,
            UpdateMappingRequest(**{policy_field: True}),
            db,
        )

    assert blocked.value.status_code == 409
    assert blocked.value.detail["code"] == (
        "mapping_policy_requires_versioned_draft")
    assert "草稿" in blocked.value.detail["message"]
    assert "试跑" in blocked.value.detail["message"]
    assert "发布" in blocked.value.detail["message"]
    assert blocked.value.detail["fields"] == [policy_field]
    assert stored_key not in mapping.field_mapping

    # Repeating the already-released value is read-only/idempotent and must
    # not add client metadata that could drift from the release snapshot.
    replay = update_mapping(
        project.id,
        mapping.id,
        UpdateMappingRequest(**{policy_field: False}),
        db,
    )
    assert replay["idempotent_replay"] is True
    assert "__client_definition__" not in mapping.field_mapping


def test_legacy_project_without_release_pointer_keeps_policy_api(
        db, admin_user):
    project, mapping = _mapping_source(
        db, admin_user, project_id="legacy-policy")

    result = update_mapping(
        project.id,
        mapping.id,
        UpdateMappingRequest(auto_apply_on_review=True),
        db,
    )

    assert result["auto_apply_on_review"] is True
    assert mapping.field_mapping["__auto_apply_on_review__"] is True


def _draft(db, admin_user, *, suffix: str):
    project = OntologyProject(
        id=f"workspace-project-{suffix}",
        name=f"workspace-project-{suffix}",
        domain="test",
        created_by=admin_user.id,
    )
    snapshot = complete_snapshot(None)
    draft = OntologyVersion(
        id=f"workspace-draft-{suffix}",
        ontology_id=project.id,
        version_number="v0.1",
        node_kind="draft",
        lifecycle_status="editing",
        revision=0,
        snapshot_formal=snapshot,
        snapshot_hash=snapshot_hash(snapshot),
        created_by=admin_user.id,
    )
    db.add_all([project, draft])
    db.commit()
    return project, draft


@pytest.mark.parametrize(
    ("collection", "flag", "value"),
    [
        ("mappings", "__auto_apply_on_review__", "false"),
        ("mappings", "__auto_apply_on_version__", 1),
        ("linkMappings", "__auto_apply_on_review__", None),
        ("linkMappings", "__auto_apply_on_version__", 0),
    ],
)
def test_workspace_rejects_non_boolean_mapping_automation_policy(
        db, admin_user, collection, flag, value):
    suffix = f"{collection}-{flag}-{type(value).__name__}"
    project, draft = _draft(db, admin_user, suffix=suffix)
    body = {
        collection: [{
            "id": f"item-{suffix}",
            "fieldMapping": {flag: value},
        }],
    }

    with pytest.raises(HTTPException) as invalid:
        save_draft_mappings(
            project.id, draft.id, body, db, admin_user)

    assert invalid.value.status_code == 422
    assert invalid.value.detail["code"] == (
        "invalid_mapping_automation_policy_type")
    assert invalid.value.detail["errors"][0]["field"] == (
        f"fieldMapping.{flag}")
    assert draft.revision == 0
    assert draft.snapshot_formal == complete_snapshot(None)


def test_workspace_accepts_literal_boolean_mapping_automation_policies(
        db, admin_user):
    project, draft = _draft(db, admin_user, suffix="literal-bools")
    body = {
        "mappings": [{
            "id": "object-policy",
            "fieldMapping": {
                "__auto_apply_on_review__": True,
                "__auto_apply_on_version__": False,
            },
        }],
        "linkMappings": [{
            "id": "link-policy",
            "fieldMapping": {
                "__auto_apply_on_review__": False,
                "__auto_apply_on_version__": True,
            },
        }],
    }

    result = save_draft_mappings(
        project.id, draft.id, body, db, admin_user)

    assert result["data"]["revision"].startswith("1:")
    saved = draft.snapshot_formal
    assert saved["mappings"][0]["fieldMapping"] == body[
        "mappings"][0]["fieldMapping"]
    assert saved["linkMappings"][0]["fieldMapping"] == body[
        "linkMappings"][0]["fieldMapping"]
