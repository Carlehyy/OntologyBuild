from __future__ import annotations

import io
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

import pytest
import jwt

from app.config import settings
from app.data_channel.file_assets.models import PipelineFileAsset
from app.data_channel.file_assets.service import (
    FileAssetError,
    abandon_invocation,
    authenticated_download_url,
    canonical_file_ref,
    cleanup_expired_assets,
    commit_invocation,
    create_upload_token,
    decode_upload_token,
    gateway_context,
    public_share_url,
    reconcile_invocation,
    sanitize_original_name,
    store_upload,
    validate_and_canonicalize_refs,
)
from app.data_channel.pipelines.models import Pipeline
from app.data_channel.steward.models import N8nPipeline
from app.models.user import User
from app.services.auth_service import create_access_token, hash_password
from app.shared.storage import StorageService


class MemoryStorage:
    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.metadata: dict[str, dict] = {}
        self.deleted: list[str] = []

    def put_object(self, bucket, key, data, content_type="application/octet-stream",
                   length=-1, metadata=None):
        uri = f"s3://{bucket}/{key}"
        self.objects[uri] = data.read()
        self.metadata[uri] = dict(metadata or {})
        return uri

    def delete_object(self, uri):
        self.objects.pop(uri, None)
        self.deleted.append(uri)

    def get_stream(self, uri):
        return io.BytesIO(self.objects[uri])


@pytest.fixture
def managed_pipeline(db, admin_user):
    pipeline = Pipeline(
        name="带附件流水线",
        definition={"engine": "n8n"},
        created_by=admin_user.id,
    )
    db.add(pipeline)
    db.flush()
    binding = N8nPipeline(
        name="带附件流水线",
        n8n_workflow_id="wf-files-1",
        pipeline_id=pipeline.id,
        created_by=admin_user.id,
    )
    db.add(binding)
    db.commit()
    return pipeline, binding


def _claims(pipeline, binding, owner_id, *, invocation="inv-1", purpose="preview"):
    token = create_upload_token(
        pipeline_id=pipeline.id,
        workflow_id=binding.n8n_workflow_id,
        invocation_id=invocation,
        purpose=purpose,
        owner_id=owner_id,
    )
    return token, decode_upload_token(token)


def test_gateway_context_requires_absolute_credential_free_url(monkeypatch):
    monkeypatch.setattr(settings, "pipeline_file_gateway_base_url", "backend:8000/files")
    with pytest.raises(FileAssetError, match="绝对 HTTP"):
        gateway_context(
            pipeline_id="pipeline-1", workflow_id="workflow-1",
            invocation_id="invocation-1", purpose="preview", owner_id="user-1")

    monkeypatch.setattr(
        settings, "pipeline_file_gateway_base_url",
        "https://user:secret@example.com/api/v2/file-transfer")
    with pytest.raises(FileAssetError, match="不能包含账号"):
        gateway_context(
            pipeline_id="pipeline-1", workflow_id="workflow-1",
            invocation_id="invocation-1", purpose="preview", owner_id="user-1")


def test_gateway_context_uses_configured_n8n_reachable_url(monkeypatch):
    monkeypatch.setattr(
        settings, "pipeline_file_gateway_base_url",
        "https://platform.example.com/api/v2/file-transfer/")
    context = gateway_context(
        pipeline_id="pipeline-1", workflow_id="workflow-1",
        invocation_id="invocation-1", purpose="preview", owner_id="user-1")
    assert context["upload_url"] == (
        "https://platform.example.com/api/v2/file-transfer/upload")


def test_file_links_use_browser_public_origins_not_the_n8n_gateway(monkeypatch):
    monkeypatch.setattr(
        settings,
        "pipeline_file_gateway_base_url",
        "http://backend:8000/api/v2/file-transfer",
    )
    monkeypatch.setattr(
        settings,
        "pipeline_file_public_app_base_url",
        "https://app.example.com",
    )
    monkeypatch.setattr(
        settings,
        "pipeline_file_public_api_base_url",
        "https://files.example.com/",
    )

    assert authenticated_download_url("asset 1") == (
        "https://app.example.com/#/file-assets/asset%201/download")
    assert public_share_url("token_1") == (
        "https://files.example.com/api/public/file-assets/token_1/download")


def test_file_link_public_origins_reject_paths_and_credentials(monkeypatch):
    monkeypatch.setattr(
        settings,
        "pipeline_file_public_app_base_url",
        "https://app.example.com/subpath",
    )
    with pytest.raises(FileAssetError, match="PUBLIC_APP_BASE_URL"):
        authenticated_download_url("asset-1")

    monkeypatch.setattr(
        settings,
        "pipeline_file_public_api_base_url",
        "https://user:secret@files.example.com",
    )
    with pytest.raises(FileAssetError, match="PUBLIC_API_BASE_URL"):
        public_share_url("token-1")


def test_filename_is_metadata_not_object_path(db, admin_user, managed_pipeline):
    pipeline, binding = managed_pipeline
    storage = MemoryStorage()
    _token, claims = _claims(pipeline, binding, admin_user.id)

    asset = store_upload(
        db,
        claims=claims,
        stream=io.BytesIO(b"hello"),
        filename="../../报告 2026\r\nX-Evil.PDF",
        content_type="application/pdf",
        idempotency_key="source-record-42:attachment",
        storage=storage,
    )

    assert asset.original_name == "报告 2026X-Evil.PDF"
    assert asset.original_name not in asset.object_key
    assert asset.object_key.startswith(f"pipeline-files/{pipeline.id}/preview/")
    assert asset.object_key.endswith(f"/{asset.id}.pdf")
    assert storage.objects[asset.storage_uri] == b"hello"
    assert storage.metadata[asset.storage_uri]["sha256"] == asset.sha256
    assert asset.share_token_hash is None
    assert canonical_file_ref(asset)["share_url"] is None


def test_untrusted_content_type_cannot_become_response_header(
    db, admin_user, managed_pipeline,
):
    pipeline, binding = managed_pipeline
    storage = MemoryStorage()
    _token, claims = _claims(pipeline, binding, admin_user.id)
    asset = store_upload(
        db, claims=claims, stream=io.BytesIO(b"payload"), filename="safe.pdf",
        content_type="text/plain\r\nX-Evil: yes", idempotency_key="mime",
        storage=storage,
    )
    assert asset.content_type == "application/octet-stream"


def test_idempotency_returns_same_ref_and_rejects_different_content(
    db, admin_user, managed_pipeline,
):
    pipeline, binding = managed_pipeline
    storage = MemoryStorage()
    _token, claims = _claims(pipeline, binding, admin_user.id)
    first = store_upload(
        db, claims=claims, stream=io.BytesIO(b"same"), filename="a.pdf",
        content_type="application/pdf", idempotency_key="idem", storage=storage)
    second = store_upload(
        db, claims=claims, stream=io.BytesIO(b"same"), filename="renamed.pdf",
        content_type="application/pdf", idempotency_key="idem", storage=storage)
    assert second.id == first.id
    assert len(storage.objects) == 1

    with pytest.raises(FileAssetError, match="不同文件"):
        store_upload(
            db, claims=claims, stream=io.BytesIO(b"different"), filename="a.pdf",
            content_type="application/pdf", idempotency_key="idem", storage=storage)


def test_file_refs_are_scope_checked_and_canonicalized(
    db, admin_user, managed_pipeline,
):
    pipeline, binding = managed_pipeline
    storage = MemoryStorage()
    _token, claims = _claims(pipeline, binding, admin_user.id)
    asset = store_upload(
        db, claims=claims, stream=io.BytesIO(b"payload"), filename="报告.pdf",
        content_type="application/pdf", idempotency_key="idem", storage=storage)

    spoofed = [{"id": 1, "attachment": {
        "$type": "file_ref", "id": asset.id, "name": "spoof.exe", "size": 1,
    }}]
    rows, ids = validate_and_canonicalize_refs(
        db, spoofed, pipeline_id=pipeline.id, invocation_id="inv-1")
    assert ids == [asset.id]
    assert rows[0]["attachment"] == canonical_file_ref(asset)
    assert rows[0]["attachment"]["authenticated_url"].endswith(
        f"/#/file-assets/{asset.id}/download")
    assert rows[0]["attachment"]["share_url"].endswith("/download")
    assert asset.expires_at is not None
    assert asset.share_token_hash
    assert asset.share_token_encrypted
    token = urlsplit(rows[0]["attachment"]["share_url"]).path.split("/")[-2]
    assert token not in asset.share_token_encrypted

    with pytest.raises(FileAssetError, match="本次执行"):
        validate_and_canonicalize_refs(
            db, spoofed, pipeline_id=pipeline.id, invocation_id="other-run")


def test_commit_and_abandon_lifecycle(db, admin_user, managed_pipeline):
    pipeline, binding = managed_pipeline
    storage = MemoryStorage()
    _token, claims = _claims(
        pipeline, binding, admin_user.id, invocation="formal-1", purpose="run")
    kept = store_upload(
        db, claims=claims, stream=io.BytesIO(b"kept"), filename="kept.pdf",
        content_type="application/pdf", idempotency_key="kept", storage=storage)
    orphan = store_upload(
        db, claims=claims, stream=io.BytesIO(b"orphan"), filename="orphan.pdf",
        content_type="application/pdf", idempotency_key="orphan", storage=storage)

    commit_invocation(
        db, pipeline_id=pipeline.id, invocation_id="formal-1",
        referenced_ids=[kept.id], dataset_version_id=None)
    db.commit()
    db.refresh(kept)
    db.refresh(orphan)
    assert kept.status == "committed" and kept.expires_at is None
    assert orphan.status == "deleted" and orphan.storage_uri is None

    assert abandon_invocation(
        db, pipeline_id=pipeline.id, invocation_id="formal-1") == 0
    db.refresh(kept)
    assert kept.status == "committed"


def test_successful_preview_pins_referenced_share_but_deletes_orphan(
    db, admin_user, managed_pipeline,
):
    pipeline, binding = managed_pipeline
    storage = MemoryStorage()
    _token, claims = _claims(pipeline, binding, admin_user.id)
    kept = store_upload(
        db, claims=claims, stream=io.BytesIO(b"kept-preview"),
        filename="kept.pdf", content_type="application/pdf",
        idempotency_key="kept-preview", storage=storage)
    orphan = store_upload(
        db, claims=claims, stream=io.BytesIO(b"orphan-preview"),
        filename="orphan.pdf", content_type="application/pdf",
        idempotency_key="orphan-preview", storage=storage)

    _rows, ids = validate_and_canonicalize_refs(
        db,
        [{"attachment": {"$type": "file_ref", "id": kept.id}}],
        pipeline_id=pipeline.id,
        invocation_id="inv-1",
    )
    assert reconcile_invocation(
        db,
        pipeline_id=pipeline.id,
        invocation_id="inv-1",
        referenced_ids=ids,
        commit=False,
        pin_referenced=True,
    ) == 1
    db.commit()
    db.refresh(kept)
    db.refresh(orphan)
    assert kept.status == "ready"
    assert kept.expires_at is None
    assert kept.share_revoked_at is None
    assert orphan.status == "deleted"
    assert orphan.storage_uri is None


def test_validated_share_token_is_not_persisted_before_preview_finalize(
    db, admin_user, managed_pipeline,
):
    pipeline, binding = managed_pipeline
    storage = MemoryStorage()
    _token, claims = _claims(pipeline, binding, admin_user.id)
    asset = store_upload(
        db, claims=claims, stream=io.BytesIO(b"pending"),
        filename="pending.txt", content_type="text/plain",
        idempotency_key="pending-token", storage=storage)

    rows, _ids = validate_and_canonicalize_refs(
        db,
        [{"attachment": {"$type": "file_ref", "id": asset.id}}],
        pipeline_id=pipeline.id,
        invocation_id="inv-1",
    )
    share_url = rows[0]["attachment"]["share_url"]
    assert share_url
    share_token = urlsplit(share_url).path.split("/")[-2]
    assert asset.share_token_hash

    db.rollback()
    db.refresh(asset)
    assert asset.share_token_hash is None
    assert asset.share_token_encrypted is None

    commit_invocation(
        db,
        pipeline_id=pipeline.id,
        invocation_id="inv-1",
        referenced_ids=[asset.id],
        dataset_version_id=None,
        share_tokens={asset.id: share_token},
    )
    db.commit()
    db.refresh(asset)
    assert canonical_file_ref(asset)["share_url"] == share_url


def test_expired_preview_is_tombstoned(db, admin_user, managed_pipeline, monkeypatch):
    pipeline, binding = managed_pipeline
    storage = MemoryStorage()
    monkeypatch.setattr(
        "app.data_channel.file_assets.service.get_storage_service", lambda: storage)
    _token, claims = _claims(pipeline, binding, admin_user.id)
    asset = store_upload(
        db, claims=claims, stream=io.BytesIO(b"old"), filename="old.pdf",
        content_type="application/pdf", idempotency_key="old", storage=storage)
    asset.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.commit()

    assert cleanup_expired_assets(db, storage=storage) == 1
    db.refresh(asset)
    assert asset.status == "deleted"
    assert asset.storage_uri is None
    assert storage.deleted


def test_upload_endpoint_requires_scoped_token_and_download_auth(
    client, db, admin_user, auth_headers, managed_pipeline, monkeypatch,
):
    pipeline, binding = managed_pipeline
    storage = MemoryStorage()
    monkeypatch.setattr(
        "app.data_channel.file_assets.service.get_storage_service", lambda: storage)
    monkeypatch.setattr(
        "app.data_channel.file_assets.router.get_storage_service", lambda: storage)
    token, _claims_data = _claims(pipeline, binding, admin_user.id)

    ordinary = create_access_token({"sub": admin_user.id})
    rejected = client.post(
        "/api/v2/file-transfer/upload",
        headers={"Authorization": f"Bearer {ordinary}"},
        data={"idempotency_key": "ordinary"},
        files={"file": ("a.pdf", b"data", "application/pdf")},
    )
    assert rejected.status_code == 400

    response = client.post(
        "/api/v2/file-transfer/upload",
        headers={"Authorization": f"Bearer {token}"},
        data={"idempotency_key": "request-1:file"},
        files={"file": ("../财务报表.pdf", b"real-pdf", "application/pdf")},
    )
    assert response.status_code == 201, response.text
    ref = response.json()["file_ref"]
    assert ref["name"] == "财务报表.pdf"
    assert "storage_uri" not in ref
    assert ref["share_url"] is None

    metadata_url = f"/api/v2/file-assets/{ref['id']}"
    assert client.get(metadata_url).status_code in {401, 403}
    metadata = client.get(metadata_url, headers=auth_headers)
    assert metadata.status_code == 200
    assert metadata.json() == ref

    assert client.get(ref["download_url"]).status_code in {401, 403}
    downloaded = client.get(ref["download_url"], headers=auth_headers)
    assert downloaded.status_code == 200
    assert downloaded.content == b"real-pdf"
    assert "UTF-8''" in downloaded.headers["content-disposition"]
    assert downloaded.headers["x-content-type-options"] == "nosniff"


def test_permanent_public_share_can_be_revoked_and_rotated(
    client, db, admin_user, auth_headers, managed_pipeline, monkeypatch,
):
    pipeline, binding = managed_pipeline
    storage = MemoryStorage()
    monkeypatch.setattr(
        "app.data_channel.file_assets.service.get_storage_service", lambda: storage)
    monkeypatch.setattr(
        "app.data_channel.file_assets.router.get_storage_service", lambda: storage)
    _token, claims = _claims(pipeline, binding, admin_user.id)
    asset = store_upload(
        db, claims=claims, stream=io.BytesIO(b"shared-payload"),
        filename="长期报告.pdf", content_type="application/pdf",
        idempotency_key="permanent-share", storage=storage,
    )
    rows, _ids = validate_and_canonicalize_refs(
        db,
        [{"attachment": {"$type": "file_ref", "id": asset.id}}],
        pipeline_id=pipeline.id,
        invocation_id="inv-1",
    )
    reconcile_invocation(
        db,
        pipeline_id=pipeline.id,
        invocation_id="inv-1",
        referenced_ids=[asset.id],
        pin_referenced=True,
    )
    assert asset.expires_at is None
    first_url = rows[0]["attachment"]["share_url"]
    first_path = urlsplit(first_url).path

    public_download = client.get(first_path)
    assert public_download.status_code == 200
    assert public_download.content == b"shared-payload"
    assert public_download.headers["cache-control"] == "no-store"
    assert public_download.headers["x-content-type-options"] == "nosniff"
    assert "UTF-8''" in public_download.headers["content-disposition"]

    assert client.get(f"/api/v2/file-assets/{asset.id}/share").status_code in {
        401, 403,
    }
    managed = client.get(
        f"/api/v2/file-assets/{asset.id}/share", headers=auth_headers)
    assert managed.status_code == 200
    assert managed.json()["share_url"] == first_url
    assert managed.json()["expires_at"] is None

    revoked = client.delete(
        f"/api/v2/file-assets/{asset.id}/share", headers=auth_headers)
    assert revoked.status_code == 200
    assert revoked.json()["enabled"] is False
    assert client.get(first_path).status_code == 404

    rotated = client.post(
        f"/api/v2/file-assets/{asset.id}/share", headers=auth_headers)
    assert rotated.status_code == 200
    second_url = rotated.json()["share_url"]
    assert second_url and second_url != first_url
    assert client.get(first_path).status_code == 404
    assert client.get(urlsplit(second_url).path).content == b"shared-payload"

    authenticated = client.get(
        f"/api/v2/file-assets/{asset.id}/download", headers=auth_headers)
    assert authenticated.status_code == 200
    assert authenticated.content == b"shared-payload"


def test_public_gateway_upload_fails_without_minio_and_writes_no_local_asset(
    client, db, admin_user, managed_pipeline, monkeypatch, tmp_path,
):
    pipeline, binding = managed_pipeline
    local_root = tmp_path / "objects"
    monkeypatch.setattr(settings, "storage_local_dir", str(local_root))
    storage = StorageService.unavailable()
    monkeypatch.setattr(
        "app.data_channel.file_assets.service.get_storage_service", lambda: storage)
    monkeypatch.setattr(
        "app.data_channel.file_assets.router.get_storage_service", lambda: storage)
    token, _claims_data = _claims(pipeline, binding, admin_user.id)

    with pytest.raises(RuntimeError, match="MinIO 对象存储不可用"):
        client.post(
            "/api/v2/file-transfer/upload",
            headers={"Authorization": f"Bearer {token}"},
            files={"file": ("no-minio.txt", b"must-not-persist", "text/plain")},
            data={"idempotency_key": "no-minio"},
        )

    assert db.query(PipelineFileAsset).filter(
        PipelineFileAsset.pipeline_id == pipeline.id,
    ).count() == 0
    assert not local_root.exists()


def test_legacy_asset_without_token_gets_share_on_demand(
    client, db, admin_user, auth_headers, managed_pipeline, monkeypatch,
):
    pipeline, binding = managed_pipeline
    storage = MemoryStorage()
    monkeypatch.setattr(
        "app.data_channel.file_assets.service.get_storage_service", lambda: storage)
    monkeypatch.setattr(
        "app.data_channel.file_assets.router.get_storage_service", lambda: storage)
    _token, claims = _claims(pipeline, binding, admin_user.id)
    asset = store_upload(
        db, claims=claims, stream=io.BytesIO(b"legacy"),
        filename="legacy.txt", content_type="text/plain",
        idempotency_key="legacy-share", storage=storage,
    )
    asset.share_token_hash = None
    asset.share_token_encrypted = None
    asset.share_created_at = None
    db.commit()

    before = client.get(
        f"/api/v2/file-assets/{asset.id}/share", headers=auth_headers)
    assert before.status_code == 200
    assert before.json()["enabled"] is False
    assert before.json()["permanent"] is False
    assert before.json()["expires_at"] is not None

    created = client.post(
        f"/api/v2/file-assets/{asset.id}/share", headers=auth_headers)
    assert created.status_code == 200
    assert created.json()["enabled"] is True
    assert created.json()["permanent"] is True
    assert created.json()["expires_at"] is None
    db.refresh(asset)
    assert asset.expires_at is None
    assert client.get(urlsplit(created.json()["share_url"]).path).content == b"legacy"


def test_share_mutation_requires_owner_or_admin(
    client, db, admin_user, editor_user, auth_headers, managed_pipeline,
):
    pipeline, binding = managed_pipeline
    storage = MemoryStorage()
    _token, claims = _claims(pipeline, binding, editor_user.id)
    asset = store_upload(
        db, claims=claims, stream=io.BytesIO(b"owned"),
        filename="owned.txt", content_type="text/plain",
        idempotency_key="owned-share", storage=storage,
    )
    owner_headers = {
        "Authorization": f"Bearer {create_access_token({'sub': editor_user.id})}",
    }
    other_editor = User(
        id=str(uuid.uuid4()),
        username="other-editor",
        email="other-editor@test.com",
        password_hash=hash_password("editor123"),
        role="editor",
    )
    db.add(other_editor)
    db.commit()
    other_headers = {
        "Authorization": f"Bearer {create_access_token({'sub': other_editor.id})}",
    }
    base = f"/api/v2/file-assets/{asset.id}/share"

    # Reading share state follows the ordinary authenticated asset-lake policy.
    assert client.get(base, headers=other_headers).status_code == 200
    assert client.post(base, headers=other_headers).status_code == 403
    assert client.post(f"{base}/regenerate", headers=other_headers).status_code == 403
    assert client.delete(base, headers=other_headers).status_code == 403

    assert client.post(base, headers=owner_headers).status_code == 200
    assert client.post(f"{base}/regenerate", headers=auth_headers).status_code == 200
    assert client.delete(base, headers=auth_headers).status_code == 200

    # Legacy records with no owner are administrator-only.
    asset.owner_id = None
    db.commit()
    assert client.post(base, headers=owner_headers).status_code == 403
    assert client.post(base, headers=auth_headers).status_code == 200


def test_original_name_normalization():
    assert sanitize_original_name("C:\\temp\\..\\合同.pdf") == "合同.pdf"
    assert sanitize_original_name("\x00\r\n") == "attachment"


def test_upload_token_rejects_forged_invalid_scope_fields():
    common = {
        "typ": "pipeline-file-upload",
        "pipeline_id": "pipeline-1",
        "workflow_id": "workflow-1",
        "invocation_id": "invocation-1",
        "purpose": "preview",
        "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
    }
    invalid_purpose = jwt.encode(
        {**common, "purpose": "permanent"}, settings.secret_key, algorithm="HS256")
    with pytest.raises(FileAssetError, match="用途"):
        decode_upload_token(invalid_purpose)

    oversized_invocation = jwt.encode(
        {**common, "invocation_id": "x" * 101}, settings.secret_key, algorithm="HS256")
    with pytest.raises(FileAssetError, match="invocation_id"):
        decode_upload_token(oversized_invocation)

    traversal_invocation = jwt.encode(
        {**common, "invocation_id": "../escape"}, settings.secret_key, algorithm="HS256")
    with pytest.raises(FileAssetError, match="不安全字符"):
        decode_upload_token(traversal_invocation)
