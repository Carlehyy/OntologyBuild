from __future__ import annotations

import json
import sys
import uuid

import pytest

import scripts.steward_file_asset_live_e2e as live_script
from app.data_channel.datasets.models import Dataset, DatasetVersion
from app.data_channel.file_assets.models import PipelineFileAsset
from app.data_channel.pipelines.models import Pipeline, PipelineRun
from app.data_channel.steward.models import N8nPipeline
from app.data_channel.steward.service import validate_managed_workflow_contract
from scripts.steward_file_asset_live_e2e import (
    _OUTPUT_NODE,
    _cleanup_local_state,
    _forbidden_keys,
    _json_object_cell,
    _normalize_public_root,
    _safe_error,
    _workflow_payload,
)


@pytest.mark.parametrize(
    "value",
    [
        "",
        "platform.example.com",
        "ftp://platform.example.com",
        "https://user:secret@platform.example.com",
        "https://platform.example.com/subpath",
        "https://platform.example.com?token=secret",
        "https://platform.example.com/#fragment",
    ],
)
def test_live_file_asset_script_rejects_ambiguous_public_roots(value):
    with pytest.raises(ValueError, match="public-root"):
        _normalize_public_root(value)


def test_live_file_asset_script_normalizes_public_origin():
    assert _normalize_public_root("https://platform.example.com:8443/") == (
        "https://platform.example.com:8443"
    )


def test_live_file_asset_script_finds_project_root_after_tmp_copy(
    tmp_path, monkeypatch,
):
    project_root = tmp_path / "backend"
    package = project_root / "app"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    copied_script = tmp_path / "tmp" / "steward_file_asset_live_e2e.py"
    copied_script.parent.mkdir()
    copied_script.write_text("", encoding="utf-8")
    monkeypatch.setattr(live_script, "__file__", str(copied_script))
    monkeypatch.chdir(project_root)
    monkeypatch.setattr(sys, "path", list(sys.path))

    selected = live_script._install_backend_path()

    assert selected == project_root
    assert sys.path[0] == str(project_root)


def test_live_file_asset_workflow_is_managed_and_contains_no_runtime_secret():
    webhook_path = "ob-file-asset-live-e2e-" + ("a" * 32)
    workflow = _workflow_payload(
        name="OB-FILE-ASSET-LIVE-E2E-test",
        webhook_path=webhook_path,
    )

    contract = validate_managed_workflow_contract(workflow)
    assert contract["webhook_path"] == webhook_path
    assert contract["output_node_name"] == _OUTPUT_NODE

    nodes = {node["name"]: node for node in workflow["nodes"]}
    generate_code = nodes["生成唯一附件"]["parameters"]["jsCode"]
    assert "body.run_id" in generate_code
    assert "Buffer.from" in generate_code

    upload = nodes["上传平台附件"]["parameters"]
    assert upload["url"] == (
        '={{ $node["Webhook"].json.body.file_gateway.upload_url }}'
    )
    assert upload["contentType"] == "multipart-form-data"
    parameters = upload["bodyParameters"]["parameters"]
    assert {
        "parameterType": "formBinaryData",
        "name": "file",
        "inputDataFieldName": "data",
    } in parameters
    assert any(item.get("name") == "idempotency_key" for item in parameters)
    serialized = json.dumps(workflow, ensure_ascii=False)
    assert "api_key" not in serialized.lower()
    assert "password" not in serialized.lower()
    assert "storage_uri" not in serialized.lower()
    assert "://" not in serialized


def test_live_file_asset_report_sanitizes_capabilities_and_detects_lake_leaks():
    share_url = (
        "https://platform.example.com/api/public/file-assets/"
        "secret-share-token/download"
    )
    jwt_token = "abcdefghijk.abcdefghijklmnop.qrstuvwxyz123456"
    message = (
        f"GET {share_url} Authorization: Bearer {jwt_token} "
        "path=ob-file-asset-live-e2e-secret"
    )
    safe = _safe_error(
        message,
        "ob-file-asset-live-e2e-secret",
        share_url,
    )
    assert "secret-share-token" not in safe
    assert jwt_token not in safe
    assert "ob-file-asset-live-e2e-secret" not in safe

    assert _forbidden_keys(
        {
            "attachment": {"id": "asset", "storage_uri": "s3://private"},
            "nested": [{"file_gateway": {"token": "secret"}}],
        }
    ) == {"file_gateway", "storage_uri", "token"}


def test_live_file_asset_decodes_only_structured_json_lake_cells():
    file_ref = {"$type": "file_ref", "id": "asset-1"}

    assert _json_object_cell(file_ref) is file_ref
    assert _json_object_cell(json.dumps(file_ref)) == file_ref
    assert _json_object_cell("plain text") is None
    assert _json_object_cell("[1, 2]") is None


def test_live_file_asset_cleanup_uses_product_dataset_and_outbox_paths(
    db, admin_user, monkeypatch,
):
    pipeline_id = str(uuid.uuid4())
    binding_id = str(uuid.uuid4())
    run_id = str(uuid.uuid4())
    dataset_id = str(uuid.uuid4())
    version_id = str(uuid.uuid4())
    asset_id = str(uuid.uuid4())
    storage_uri = f"s3://media/pipeline-files/{asset_id}.txt"

    pipeline = Pipeline(
        id=pipeline_id,
        name=f"file-cleanup-{pipeline_id}",
        definition={"engine": "n8n", "n8n": {"steward_id": binding_id}},
        status="published",
        enabled=True,
        created_by=admin_user.id,
    )
    binding = N8nPipeline(
        id=binding_id,
        name=pipeline.name,
        n8n_workflow_id=f"workflow-{binding_id}",
        pipeline_id=pipeline_id,
        created_by=admin_user.id,
    )
    dataset = Dataset(
        id=dataset_id,
        name=f"CURATED::{pipeline.name}",
        kind="curated",
        schema_json={},
        producer_pipeline_id=pipeline_id,
        output_key="default",
    )
    version = DatasetVersion(
        id=version_id,
        dataset_id=dataset_id,
        version_no=1,
        rowcount=1,
        data_blob=b"payload",
        data_size=7,
    )
    dataset.latest_version_id = version_id
    run = PipelineRun(
        id=run_id,
        pipeline_id=pipeline_id,
        status="success",
        dataset_version_id=version_id,
    )
    asset = PipelineFileAsset(
        id=asset_id,
        pipeline_id=pipeline_id,
        workflow_id=binding.n8n_workflow_id,
        invocation_id=run_id,
        owner_id=admin_user.id,
        dataset_version_id=version_id,
        purpose="run",
        status="committed",
        idempotency_key="record:attachment",
        original_name="fixture.txt",
        object_key=f"pipeline-files/{asset_id}.txt",
        storage_uri=storage_uri,
        size=7,
        content_type="text/plain",
        sha256="a" * 64,
    )
    db.add_all([pipeline, binding, dataset, version, run, asset])
    db.commit()

    class MemoryStorage:
        def __init__(self):
            self.objects = {storage_uri}

        def delete_object(self, uri):
            self.objects.discard(uri)

        def object_exists(self, uri):
            return uri in self.objects

    storage = MemoryStorage()
    monkeypatch.setattr(
        "app.data_channel.datasets.service.get_storage_service",
        lambda: storage,
    )
    monkeypatch.setattr(
        "app.shared.storage.get_storage_service",
        lambda: storage,
    )

    result = _cleanup_local_state(
        db,
        pipeline_id=pipeline_id,
        known_asset_ids={asset_id},
        known_storage_uris={storage_uri},
    )

    assert result == {
        "databaseDeleted": True,
        "storageObjectsDeleted": True,
    }
    assert not storage.objects
