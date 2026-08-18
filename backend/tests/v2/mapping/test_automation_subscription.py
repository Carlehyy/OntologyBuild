"""断点2：自动化订阅总览与一键订阅（数据更新默认流入本体）。"""
from __future__ import annotations

import uuid

from app.models.ontology import OntologyProject
from app.models.v2.dataset import Dataset, DatasetVersion
from app.models.v2.mapping import OntologyMapping
from app.ontologies.mappings.automation_subscription import (
    automation_overview,
    subscribe_automation,
)


def _curated(db, ontology_id: str, name: str) -> Dataset:
    ds = Dataset(
        id=str(uuid.uuid4()), name=name, kind="curated",
        schema_json={"primary_key": "id", "columns": ["id"]},
    )
    db.add(ds)
    db.flush()
    version = DatasetVersion(
        dataset_id=ds.id, version_no=1, rowcount=1,
        storage_uri=f"s3://test/{ds.id}/v1.csv", checksum="1" * 64,
    )
    ds.latest_version_id = version.id
    db.add(version)
    db.flush()
    db.add(OntologyMapping(
        ontology_id=ontology_id, curated_dataset_id=ds.id,
        entity_class=name, field_mapping={"__primary_key__": "id"},
        status="draft", confidence=0.9,
    ))
    return ds


def _structured(db, ontology_id: str, name: str, *, with_version: bool) -> Dataset:
    ds = Dataset(
        id=str(uuid.uuid4()), name=name, kind="structured",
        schema_json={
            "origin": "manual", "columns": ["id"], "columns_typed": [
                {"name": "id", "type": "string", "nullable": False}],
            "types_source": "declared", "primary_key": "id",
            "pk_source": "manual",
        },
    )
    db.add(ds)
    db.flush()
    if with_version:
        version = DatasetVersion(
            dataset_id=ds.id, version_no=1, rowcount=1,
            checksum="a" * 64,
        )
        ds.latest_version_id = version.id
        db.add(version)
    db.flush()
    db.add(OntologyMapping(
        ontology_id=ontology_id, curated_dataset_id=ds.id,
        entity_class=name, field_mapping={"__primary_key__": "id"},
        status="draft", confidence=0.9,
    ))
    return ds


def _project(db):
    project = OntologyProject(
        name=f"订阅总览-{uuid.uuid4().hex[:8]}", domain="测试",
        build_mode="pipeline_mapping",
        created_by="admin",
    )
    db.add(project)
    db.commit()
    return project


def test_overview_reports_unsubscribed_bound_mappings(db):
    project = _project(db)
    _curated(db, project.id, "Order")
    _structured(db, project.id, "Ledger", with_version=True)
    db.commit()

    view = automation_overview(db, project.id)
    assert view["total"] == 2
    assert view["unsubscribed"] == 2
    assert all(item["needs_subscription"] for item in view["items"])
    by_class = {item["entity_class"]: item for item in view["items"]}
    assert by_class["Order"]["dataset"]["dataset_kind"] == "curated"
    assert by_class["Ledger"]["version_eligible"] is True
    # 未绑定数据集的映射不是订阅对象
    db.add(OntologyMapping(
        ontology_id=project.id, curated_dataset_id=None,
        entity_class="Unbound", field_mapping={}, status="draft",
    ))
    db.commit()
    view = automation_overview(db, project.id)
    assert view["total"] == 3
    unbound = next(
        item for item in view["items"]
        if item["entity_class"] == "Unbound")
    assert unbound["needs_subscription"] is False
    assert unbound["dataset"] is None


def test_subscribe_all_sets_flags_with_per_mapping_eligibility(db):
    project = _project(db)
    _curated(db, project.id, "Order")
    _structured(db, project.id, "Ledger", with_version=True)
    # 无版本的人工数据集：不具备版本自动灌入资格
    _structured(db, project.id, "Broken", with_version=False)
    db.commit()

    result = subscribe_automation(
        db, project.id, on_version=True, on_review=True)
    assert result["subscribed_count"] == 2
    db.expire_all()
    mappings = db.query(OntologyMapping).filter(
        OntologyMapping.ontology_id == project.id,
        OntologyMapping.curated_dataset_id.isnot(None),
    ).all()
    by_class = {m.entity_class: m.field_mapping or {} for m in mappings}
    assert by_class["Order"]["__auto_apply_on_review__"] is True
    assert by_class["Order"].get("__auto_apply_on_version__") is None
    assert by_class["Ledger"]["__auto_apply_on_version__"] is True
    assert by_class["Broken"].get("__auto_apply_on_version__") is None
    skipped_reasons = {
        item["entity_class"]: item["reason"] for item in result["skipped"]}
    assert skipped_reasons["Broken"].startswith(
        "version_automation_not_eligible")
    assert "Order" not in skipped_reasons


def test_overview_after_subscribe_has_no_unsubscribed(db):
    project = _project(db)
    _curated(db, project.id, "Order")
    db.commit()

    subscribe_automation(db, project.id, on_version=False, on_review=True)
    db.commit()
    view = automation_overview(db, project.id)
    assert view["unsubscribed"] == 0
    assert view["items"][0]["subscribed_review"] is True
