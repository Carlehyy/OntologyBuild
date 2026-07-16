import json
import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.data_channel.pipelines.models import Pipeline
from app.data_channel.pipelines.router import (
    PipelineUpdate,
    PublishBody,
    ValidateDefinitionsBody,
    _dry_run_uri,
    publish_pipeline,
    update_pipeline,
    validate_pipeline,
    validate_column_definitions,
)
from app.data_channel.datasets.service import DatasetService
from app.data_channel.steward.service import canonical_json_hash


COLUMNS = [{
    "source_key": "id",
    "field_key": "id",
    "field_name": "标识",
    "field_type": "string",
    "is_primary_key": True,
    "nullable": False,
}]


class _Storage:
    def __init__(self):
        self.objects: dict[str, bytes] = {}

    def get_object(self, uri: str) -> bytes:
        return self.objects[uri]

    def put_bytes(
        self, bucket: str, key: str, data: bytes, content_type: str = "",
    ) -> str:
        uri = f"s3://{bucket}/{key}"
        self.objects[uri] = data
        return uri

    def delete_object(self, uri: str) -> None:
        self.objects.pop(uri, None)


def _pipeline(db) -> Pipeline:
    pipeline = Pipeline(
        id="canvas-attestation",
        name="发布凭证测试",
        status="draft",
        definition={
            "engine": "canvas",
            "nodes": [],
            "edges": [],
        },
        spec={},
        column_definitions=[],
    )
    db.add(pipeline)
    db.commit()
    return pipeline


def _stage(storage: _Storage, pipeline_id: str, rows: list[dict]) -> str:
    dry_run_id = str(uuid.uuid4())
    outputs = [{"rows": rows}]
    payload = {
        "pipeline_id": pipeline_id,
        "dry_run_id": dry_run_id,
        "created_at": "2026-07-17T00:00:00Z",
        "truncated": False,
        "outputs": outputs,
        "output_checksum": canonical_json_hash(outputs),
    }
    storage.objects[_dry_run_uri(pipeline_id, dry_run_id)] = json.dumps(
        payload
    ).encode()
    return dry_run_id


def test_canvas_validation_persists_publish_attestation_and_matching_save_keeps_it(
    db, monkeypatch,
):
    pipeline = _pipeline(db)
    storage = _Storage()
    dry_run_id = _stage(storage, pipeline.id, [{"id": "A-1"}])
    monkeypatch.setattr(
        "app.services.storage_service.get_storage_service",
        lambda: storage,
    )

    result = validate_column_definitions(
        pipeline.id,
        ValidateDefinitionsBody(column_definitions=COLUMNS),
        dry_run_id,
        db,
    )

    assert result.valid is True
    assert pipeline.validation_attestation["dry_run_id"] == dry_run_id
    update_pipeline(
        pipeline.id,
        PipelineUpdate(column_definitions=COLUMNS),
        db,
    )
    assert pipeline.validation_attestation is not None


def test_canvas_execution_or_contract_change_invalidates_attestation(
    db, monkeypatch,
):
    pipeline = _pipeline(db)
    storage = _Storage()
    dry_run_id = _stage(storage, pipeline.id, [{"id": "A-1"}])
    monkeypatch.setattr(
        "app.services.storage_service.get_storage_service",
        lambda: storage,
    )
    validate_column_definitions(
        pipeline.id,
        ValidateDefinitionsBody(column_definitions=COLUMNS),
        dry_run_id,
        db,
    )

    update_pipeline(
        pipeline.id,
        PipelineUpdate(spec={"cleansing": {"deduplicate": True}}),
        db,
    )

    assert pipeline.validation_attestation is None


def test_canvas_publish_endpoint_rejects_missing_validation_attestation(
    db, monkeypatch,
):
    pipeline = _pipeline(db)
    pipeline.column_definitions = COLUMNS
    db.commit()
    monkeypatch.setattr(
        "app.data_channel.pipelines.router.validate_pipeline",
        lambda _pipeline_id, _db: SimpleNamespace(valid=True, errors=[]),
    )

    with pytest.raises(HTTPException, match="执行预览与字段定义"):
        publish_pipeline(
            pipeline.id,
            PublishBody(enable=False),
            db,
            SimpleNamespace(id="admin"),
        )


def test_canvas_publish_accepts_current_verified_attestation(
    db, monkeypatch,
):
    pipeline = _pipeline(db)
    storage = _Storage()
    dry_run_id = _stage(storage, pipeline.id, [{"id": "A-1"}])
    monkeypatch.setattr(
        "app.services.storage_service.get_storage_service",
        lambda: storage,
    )
    validate_column_definitions(
        pipeline.id,
        ValidateDefinitionsBody(column_definitions=COLUMNS),
        dry_run_id,
        db,
    )
    update_pipeline(
        pipeline.id,
        PipelineUpdate(column_definitions=COLUMNS),
        db,
    )
    monkeypatch.setattr(
        "app.data_channel.pipelines.router.validate_pipeline",
        lambda _pipeline_id, _db: SimpleNamespace(valid=True, errors=[]),
    )

    result = publish_pipeline(
        pipeline.id,
        PublishBody(enable=False),
        db,
        SimpleNamespace(id="admin"),
    )

    assert result["status"] == "published"
    assert result["enabled"] is False


def test_pipeline_validation_reads_historical_json_when_rowcount_is_missing(
    db, monkeypatch,
):
    storage = _Storage()
    monkeypatch.setattr(
        "app.data_channel.datasets.service.get_storage_service",
        lambda: storage,
    )
    dataset_service = DatasetService(db, storage=storage)
    dataset = dataset_service.create_dataset("历史 JSON", "semi")
    dataset_service.create_version(
        dataset.id,
        b'[{"id": "A-1"}]',
        rowcount=None,
    )
    pipeline = Pipeline(
        id="historical-json-pipeline",
        name="历史 JSON 流水线",
        status="draft",
        definition={
            "nodes": [
                {
                    "id": "connector",
                    "type": "connector",
                    "config": {"files": [{"dataset_id": dataset.id}]},
                },
                {"id": "storage", "type": "storage", "config": {}},
                {"id": "transform", "type": "transform", "config": {}},
                {"id": "output", "type": "output", "config": {}},
            ],
            "edges": [
                {"source": "connector", "target": "storage"},
                {"source": "storage", "target": "transform"},
                {"source": "transform", "target": "output"},
            ],
        },
        spec={},
    )
    db.add(pipeline)
    db.commit()

    result = validate_pipeline(pipeline.id, db)

    assert result.valid is True
    assert not any("所有 Connector" in item["message"] for item in result.errors)

    pipeline.definition = {
        "nodes": [
            {"id": "storage", "type": "storage", "config": {}},
            {"id": "transform", "type": "transform", "config": {}},
            {"id": "output", "type": "output", "config": {}},
        ],
        "edges": [
            {"source": "storage", "target": "transform"},
            {"source": "transform", "target": "output"},
        ],
    }
    pipeline.source_dataset_id = dataset.id
    db.commit()

    fallback_result = validate_pipeline(pipeline.id, db)

    assert not any("暂无数据版本" in item["message"]
                   for item in fallback_result.warnings)
