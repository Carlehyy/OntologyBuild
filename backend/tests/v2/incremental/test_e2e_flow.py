"""增量更新编排器端到端流程测试（全 Mock）"""
import pytest
from unittest.mock import MagicMock, patch, call
from app.services.v2.incremental.orchestrator import IncrementalOrchestrator
from app.models.ontology import OntologyProject
from app.models.v2.pipeline import Pipeline, PipelineRun
from app.models.v2.curated import CuratedDataset, CuratedReview
from app.models.v2.dataset import Dataset
from app.models.v2.mapping import OntologyLinkMapping, OntologyMapping
from datetime import datetime, timezone


# ── 测试辅助 ──────────────────────────────────────────────────────────

def make_pipeline(pipeline_id="pl-1", dataset_id="ds-1", auto_trigger=True, target_ids=None):
    pl = Pipeline(
        id=pipeline_id,
        name="test_pipeline",
        source_dataset_id=dataset_id,
        route="A",
        spec={"trigger": {"on_dataset_version": auto_trigger}},
        target_curated_ids=target_ids or ["curated-1"],
        status="active",
    )
    return pl


def make_curated_ds(ds_id="curated-1", status="approved"):
    return CuratedDataset(id=ds_id, name="test_curated", status=status, pipeline_id="pl-1")


def make_review(review_id="rev-1", ds_id="curated-1", status="approved"):
    return CuratedReview(id=review_id, curated_dataset_id=ds_id, status=status)


def make_mapping(mapping_id="map-1", ds_id="curated-1", auto_apply=True):
    return OntologyMapping(
        id=mapping_id,
        ontology_id="ont-1",
        curated_dataset_id=ds_id,
        entity_class="Order",
        field_mapping={"order_id": "id", "__auto_apply_on_review__": auto_apply},
        status="applied",
    )


def make_run(run_id="run-1", pipeline_id="pl-1", status="success"):
    return PipelineRun(id=run_id, pipeline_id=pipeline_id, status=status)


# ── on_connection_sync 测试 ───────────────────────────────────────────

def test_on_connection_sync_triggers_pipeline():
    db = MagicMock()
    pipeline = make_pipeline(auto_trigger=True)

    db.query.return_value.filter.return_value.filter.return_value.all.return_value = [pipeline]
    db.add = MagicMock()
    db.commit = MagicMock()
    db.refresh = MagicMock(side_effect=lambda obj: setattr(obj, 'id', 'new-run-1'))

    orch = IncrementalOrchestrator(db)
    with patch("app.services.v2.incremental.orchestrator.IncrementalOrchestrator._trigger_pipeline",
               return_value="new-run-1") as mock_trigger:
        result = orch.on_connection_sync("conn-1", "ds-1")

    assert len(result["triggered_pipelines"]) == 1
    mock_trigger.assert_called_once_with("pl-1", mode="incremental")


def test_on_connection_sync_no_auto_trigger():
    db = MagicMock()
    pipeline = make_pipeline(auto_trigger=False)
    db.query.return_value.filter.return_value.filter.return_value.all.return_value = [pipeline]

    orch = IncrementalOrchestrator(db)
    result = orch.on_connection_sync("conn-1", "ds-1")
    assert result["triggered_pipelines"] == []


def test_on_connection_sync_no_pipelines():
    db = MagicMock()
    db.query.return_value.filter.return_value.filter.return_value.all.return_value = []

    orch = IncrementalOrchestrator(db)
    result = orch.on_connection_sync("conn-1", "ds-1")
    assert result["triggered_pipelines"] == []


# ── on_pipeline_success 测试 ─────────────────────────────────────────

def test_on_pipeline_success_resets_curated_status():
    db = MagicMock()
    run = make_run(status="success")
    pipeline = make_pipeline(target_ids=["curated-1"])
    curated = make_curated_ds(status="approved")

    def query_side(model):
        q = MagicMock()
        if model == PipelineRun:
            q.filter.return_value.first.return_value = run
        elif model == Pipeline:
            q.filter.return_value.first.return_value = pipeline
        elif model == CuratedDataset:
            q.filter.return_value.first.return_value = curated
        return q

    db.query.side_effect = query_side
    db.commit = MagicMock()

    orch = IncrementalOrchestrator(db)
    result = orch.on_pipeline_success("run-1")

    assert "curated-1" in result["updated_datasets"]
    assert curated.status == "pending_review"


def test_on_pipeline_success_skips_if_run_not_found():
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None

    orch = IncrementalOrchestrator(db)
    result = orch.on_pipeline_success("bad-run")
    assert result["status"] == "skipped"


# ── on_review_approved 测试 ───────────────────────────────────────────

def test_on_review_approved_triggers_mapping():
    db = MagicMock()
    review = make_review(status="approved")
    mapping = make_mapping(auto_apply=True)

    def query_side(model):
        q = MagicMock()
        if model == CuratedReview:
            q.filter.return_value.first.return_value = review
        elif model == OntologyMapping:
            q.filter.return_value.filter.return_value.all.return_value = [mapping]
        return q

    db.query.side_effect = query_side

    orch = IncrementalOrchestrator(db)
    with patch.object(orch, '_trigger_mapping_apply', return_value="task-1") as mock_trigger:
        result = orch.on_review_approved("rev-1")

    assert len(result["triggered_mappings"]) == 1
    mock_trigger.assert_called_once_with("map-1", "ont-1")


def test_on_review_approved_no_auto_apply():
    db = MagicMock()
    review = make_review(status="approved")
    mapping = make_mapping(auto_apply=False)

    def query_side(model):
        q = MagicMock()
        if model == CuratedReview:
            q.filter.return_value.first.return_value = review
        elif model == OntologyMapping:
            q.filter.return_value.filter.return_value.all.return_value = [mapping]
        return q

    db.query.side_effect = query_side

    orch = IncrementalOrchestrator(db)
    result = orch.on_review_approved("rev-1")
    assert result["triggered_mappings"] == []


def test_on_review_approved_skips_non_approved():
    db = MagicMock()
    review = make_review(status="pending")
    db.query.return_value.filter.return_value.first.return_value = review

    orch = IncrementalOrchestrator(db)
    result = orch.on_review_approved("rev-1")
    assert result["status"] == "skipped"


def test_review_approved_link_only_subscription_dispatches_real_query(
        db, admin_user):
    """An approved edge dataset can be consumed only by a LinkMapping.

    This uses real ORM rows so the OR predicate over source/target/edge roles
    and the link-only dispatch anchor are both exercised.
    """
    dataset = Dataset(
        id="curated-edge-ds",
        name="curated-edge-ds",
        kind="curated",
        schema_json={"primary_key": "edge_id"},
    )
    ontology = OntologyProject(
        id="ont-link-only",
        name="Link-only review subscription",
        domain="test",
        created_by=admin_user.id,
    )
    review = CuratedReview(
        id="rev-link-only",
        curated_dataset_id=dataset.id,
        status="approved",
    )
    # Endpoint object mappings are deliberately not review subscribers. The
    # reviewed dataset is used only as the relationship's edge table.
    endpoint_mapping = OntologyMapping(
        id="endpoint-object-map",
        ontology_id=ontology.id,
        curated_dataset_id=dataset.id,
        entity_class="Endpoint",
        field_mapping={"edge_id": "id"},
        status="applied",
    )
    link_mapping = OntologyLinkMapping(
        id="review-link-map",
        ontology_id=ontology.id,
        src_dataset_id=dataset.id,
        tgt_dataset_id=dataset.id,
        edge_dataset_id=dataset.id,
        relation_type="connected_to",
        src_key="source_id",
        tgt_key="target_id",
        field_mapping={"__auto_apply_on_review__": True},
        status="active",
    )
    db.add_all([
        dataset, ontology, review, endpoint_mapping, link_mapping,
    ])
    db.commit()

    orch = IncrementalOrchestrator(db)
    with patch.object(
        orch, "_trigger_mapping_apply", return_value="task-link",
    ) as dispatch:
        result = orch.on_review_approved(review.id)

    dispatch.assert_called_once_with(link_mapping.id, ontology.id)
    assert result["triggered_mappings"] == [{
        "ontology_id": ontology.id,
        "mapping_id": link_mapping.id,
        "mapping_ids": [],
        "link_mapping_ids": [link_mapping.id],
        "trigger_mapping_kind": "link",
        "task_id": "task-link",
    }]


def test_review_approval_does_not_treat_version_flag_as_subscription(
        db, admin_user):
    """Manual-version automation must never silently opt into human review."""
    dataset = Dataset(
        id="curated-no-review-subscription",
        name="curated-no-review-subscription",
        kind="curated",
        schema_json={"primary_key": "id"},
    )
    ontology = OntologyProject(
        id="ont-no-review-subscription",
        name="No review subscription",
        domain="test",
        created_by=admin_user.id,
    )
    review = CuratedReview(
        id="rev-no-review-subscription",
        curated_dataset_id=dataset.id,
        status="approved",
    )
    mapping = OntologyMapping(
        id="version-only-object-map",
        ontology_id=ontology.id,
        curated_dataset_id=dataset.id,
        entity_class="BusinessRow",
        field_mapping={"__auto_apply_on_version__": True},
        status="applied",
    )
    link_mapping = OntologyLinkMapping(
        id="version-only-link-map",
        ontology_id=ontology.id,
        src_dataset_id=dataset.id,
        tgt_dataset_id=dataset.id,
        relation_type="related_to",
        src_key="id",
        tgt_key="id",
        field_mapping={"__auto_apply_on_version__": True},
        status="active",
    )
    db.add_all([dataset, ontology, review, mapping, link_mapping])
    db.commit()

    orch = IncrementalOrchestrator(db)
    with patch.object(orch, "_trigger_mapping_apply") as dispatch:
        result = orch.on_review_approved(review.id)

    dispatch.assert_not_called()
    assert result["triggered_mappings"] == []


# ── E2E 完整链路 ──────────────────────────────────────────────────────

def test_full_incremental_chain():
    """
    模拟完整增量链路：
    Connection 同步 → Pipeline 触发 → 审核通过 → Mapping 自动应用
    """
    db = MagicMock()

    pipeline = make_pipeline(auto_trigger=True, target_ids=["curated-1"])
    run = make_run(status="success")
    curated = make_curated_ds(status="approved")
    review = make_review(status="approved")
    mapping = make_mapping(auto_apply=True)

    call_count = {"query": 0}

    def query_side(model):
        q = MagicMock()
        if model == Pipeline:
            q.filter.return_value.filter.return_value.all.return_value = [pipeline]
            q.filter.return_value.first.return_value = pipeline
        elif model == PipelineRun:
            q.filter.return_value.first.return_value = run
        elif model == CuratedDataset:
            q.filter.return_value.first.return_value = curated
        elif model == CuratedReview:
            q.filter.return_value.first.return_value = review
        elif model == OntologyMapping:
            q.filter.return_value.filter.return_value.all.return_value = [mapping]
        return q

    db.query.side_effect = query_side
    db.add = MagicMock()
    db.commit = MagicMock()
    db.refresh = MagicMock(side_effect=lambda obj: None)

    orch = IncrementalOrchestrator(db)

    # Step 1: Connection 同步完成
    with patch.object(orch, '_trigger_pipeline', return_value="run-new"):
        sync_result = orch.on_connection_sync("conn-1", "ds-1")
    assert len(sync_result["triggered_pipelines"]) == 1

    # Step 2: Pipeline 成功 → Curated 变为 pending_review
    pipeline_result = orch.on_pipeline_success("run-1")
    assert curated.status == "pending_review"

    # Step 3: 审核通过 → Mapping 自动触发
    review.status = "approved"  # 重新设为 approved
    with patch.object(
        orch, '_trigger_mapping_apply', return_value="task-1",
    ) as trigger_mapping:
        approve_result = orch.on_review_approved(
            "rev-1", synchronous=True)
    assert len(approve_result["triggered_mappings"]) == 1
    trigger_mapping.assert_called_once_with(
        "map-1", "ont-1", synchronous=True)
    assert approve_result["triggered_mappings"][0]["dispatch_mode"] == "synchronous"


def test_pipeline_dispatch_fails_closed_in_compatibility_mode(monkeypatch):
    db = MagicMock()
    db.refresh.side_effect = lambda row: setattr(row, "id", "run-strict")
    orch = IncrementalOrchestrator(db)

    with patch(
        "app.tasks.v2.pipeline_run.pipeline_run_task.delay",
        side_effect=ConnectionError("broker secret"),
    ), patch(
        "app.tasks.v2.pipeline_run.pipeline_run_task.run",
    ) as synchronous_fallback, pytest.raises(
        RuntimeError, match="Pipeline 未执行"
    ) as exc_info:
        orch._trigger_pipeline("pipeline-strict")

    run = db.add.call_args.args[0]
    assert run.status == "failed"
    assert "Pipeline 未执行" in run.error_log
    assert "broker secret" not in run.error_log
    assert "broker secret" not in str(exc_info.value)
    synchronous_fallback.assert_not_called()


def test_mapping_dispatch_fails_closed_in_compatibility_mode(monkeypatch):
    db = MagicMock()
    orch = IncrementalOrchestrator(db)

    with patch(
        "app.tasks.v2.mapping_apply.mapping_apply_task.delay",
        side_effect=ConnectionError("broker secret"),
    ), patch(
        "app.tasks.v2.mapping_apply.mapping_apply_task.run",
    ) as synchronous_fallback, pytest.raises(
        RuntimeError, match="Mapping 未执行"
    ) as exc_info:
        orch._trigger_mapping_apply("mapping-strict", "ontology-strict")

    assert "broker secret" not in str(exc_info.value)
    synchronous_fallback.assert_not_called()


def test_synchronous_mapping_replay_requires_edge_safe_sentinel_barrier():
    db = MagicMock()
    orch = IncrementalOrchestrator(db)
    projection = {"sentinel_dispatch": {"runs": [], "errors": []}}
    manual_result = {"errors": 0, "firings": []}

    with patch(
        "app.tasks.v2.mapping_apply.mapping_apply_task",
    ) as mapping_task, patch(
        "app.services.sentinel.engine.run_manual",
        return_value=manual_result,
    ) as manual:
        mapping_task.run.return_value = projection
        result = orch._trigger_mapping_apply(
            "mapping-durable", "ontology-durable", synchronous=True)

    assert result == "sync:mapping-durable"
    mapping_task.run.assert_called_once_with(
        "mapping-durable", "ontology-durable")
    manual.assert_called_once_with(db, "ontology-durable")
