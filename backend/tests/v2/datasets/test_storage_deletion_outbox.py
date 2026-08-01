"""Dataset 对象存储删除 outbox：事务边界、重试和媒体 URI 覆盖。"""
from __future__ import annotations

import pytest

from app.main import app
from app.data_channel.curated import router as curated_router
from app.data_channel.datasets import router as datasets_router
from app.data_channel.datasets.service import (
    DatasetService,
    drain_storage_deletion_outbox,
    enqueue_dataset_storage_deletions,
)
from app.data_channel.file_assets.models import PipelineFileAsset
from app.models.v2.dataset import (
    Dataset,
    DatasetVersion,
    MediaItem,
    StorageDeletionOutbox,
)


class FakeStorage:
    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.deleted: list[str] = []
        self.fail_deletes = False

    def put_bytes(self, bucket: str, key: str, data: bytes, content_type: str = "") -> str:
        uri = f"s3://{bucket}/{key}"
        self.objects[uri] = data
        return uri

    def get_object(self, uri: str) -> bytes:
        return self.objects[uri]

    def object_exists(self, uri: str) -> bool:
        if self.fail_deletes:
            raise ConnectionError("object store unavailable")
        return uri in self.objects

    def delete_object(self, uri: str) -> None:
        if self.fail_deletes:
            raise ConnectionError("object store unavailable")
        self.objects.pop(uri, None)
        self.deleted.append(uri)


@pytest.fixture
def fake_storage(monkeypatch):
    from app.data_channel.datasets import service

    storage = FakeStorage()
    monkeypatch.setattr(service, "get_storage_service", lambda: storage)
    return storage


@pytest.fixture
def api(client, db):
    def override_db():
        yield db

    for module in (datasets_router, curated_router):
        app.dependency_overrides[module.get_db] = override_db
    yield client
    for module in (datasets_router, curated_router):
        app.dependency_overrides.pop(module.get_db, None)


def _seed_dataset_with_all_object_kinds(db, storage: FakeStorage, kind: str):
    service = DatasetService(db, storage=storage)
    dataset = service.create_dataset(f"待删-{kind}", kind)
    version = service.create_version(dataset.id, b"id,name\n1,test\n", rowcount=1)
    media_uri = f"s3://media/{dataset.id}/source.pdf"
    ocr_uri = f"s3://media/{dataset.id}/ocr.json"
    storage.objects[media_uri] = b"pdf"
    storage.objects[ocr_uri] = b"{}"
    pipeline_file_uri = f"s3://media/pipeline-files/{dataset.id}/attachment.pdf"
    storage.objects[pipeline_file_uri] = b"attachment"
    db.add(MediaItem(
        dataset_version_id=version.id,
        media_type="pdf",
        storage_uri=media_uri,
        ocr_status="done",
        ocr_result_uri=ocr_uri,
    ))
    db.add(PipelineFileAsset(
        pipeline_id=None,
        workflow_id="wf-delete",
        invocation_id=f"run-{dataset.id}",
        purpose="run",
        status="committed",
        idempotency_key="attachment-1",
        original_name="attachment.pdf",
        object_key=f"pipeline-files/{dataset.id}/attachment.pdf",
        storage_uri=pipeline_file_uri,
        size=len(b"attachment"),
        content_type="application/pdf",
        sha256="b" * 64,
        dataset_version_id=version.id,
    ))
    db.commit()
    return dataset, version, {
        uri for uri in (
            version.storage_uri, media_uri, ocr_uri, pipeline_file_uri,
        ) if uri
    }


@pytest.mark.parametrize(
    ("kind", "endpoint", "expected_status"),
    [
        ("structured", "/api/v2/datasets/{id}", 200),
        ("curated", "/api/v2/curated/{id}", 204),
    ],
)
def test_dataset_delete_cleans_version_media_and_ocr_objects(
    api, auth_headers, db, fake_storage, kind, endpoint, expected_status,
):
    dataset, _version, uris = _seed_dataset_with_all_object_kinds(
        db, fake_storage, kind)

    response = api.delete(endpoint.format(id=dataset.id), headers=auth_headers)

    assert response.status_code == expected_status, response.text
    assert db.query(Dataset).filter_by(id=dataset.id).first() is None
    assert db.query(StorageDeletionOutbox).count() == 0
    assert uris <= set(fake_storage.deleted)
    assert not (uris & set(fake_storage.objects))


def test_storage_failure_keeps_retryable_outbox_after_metadata_commit(
    api, auth_headers, db, fake_storage,
):
    dataset, _version, uris = _seed_dataset_with_all_object_kinds(
        db, fake_storage, "structured")
    fake_storage.fail_deletes = True

    response = api.delete(
        f"/api/v2/datasets/{dataset.id}", headers=auth_headers)

    assert response.status_code == 200, response.text
    assert db.query(Dataset).filter_by(id=dataset.id).first() is None
    pending = db.query(StorageDeletionOutbox).all()
    assert {entry.storage_uri for entry in pending} == uris
    assert all(entry.attempts == 1 for entry in pending)
    assert all("object store unavailable" in (entry.last_error or "") for entry in pending)
    assert uris <= set(fake_storage.objects)

    fake_storage.fail_deletes = False
    result = drain_storage_deletion_outbox(db, fake_storage)
    assert result == {
        "deleted": len(uris), "failed": 0, "deferred": 0,
    }
    assert db.query(StorageDeletionOutbox).count() == 0
    assert not (uris & set(fake_storage.objects))


def test_enqueue_participates_in_callers_transaction(db, fake_storage):
    dataset, version, _uris = _seed_dataset_with_all_object_kinds(
        db, fake_storage, "structured")

    enqueue_dataset_storage_deletions(db, dataset.id)
    assert db.query(StorageDeletionOutbox).count() == len(_uris)
    # 模拟后续元数据删除失败：调用方 rollback 必须同时撤销 outbox。
    db.rollback()

    assert db.query(StorageDeletionOutbox).count() == 0
    assert db.query(Dataset).filter_by(id=dataset.id).one()
    assert db.query(DatasetVersion).filter_by(id=version.id).one()
    assert db.query(MediaItem).filter_by(dataset_version_id=version.id).one()


def test_drain_defers_uri_still_referenced_by_an_asset(db, fake_storage):
    shared_uri = "s3://raw-datasets/shared/object.bin"
    fake_storage.objects[shared_uri] = b"shared"
    first = Dataset(name="共享对象-A", kind="structured")
    second = Dataset(name="共享对象-B", kind="structured")
    db.add_all([first, second])
    db.flush()
    first_version = DatasetVersion(
        dataset_id=first.id, version_no=1, storage_uri=shared_uri)
    second_version = DatasetVersion(
        dataset_id=second.id, version_no=1, storage_uri=shared_uri)
    db.add_all([first_version, second_version])
    db.commit()

    enqueue_dataset_storage_deletions(db, first.id)
    db.delete(first_version)
    db.delete(first)
    db.commit()

    result = drain_storage_deletion_outbox(db, fake_storage)
    assert result == {"deleted": 0, "failed": 0, "deferred": 1}
    assert shared_uri in fake_storage.objects
    queued = db.query(StorageDeletionOutbox).one()
    assert queued.attempts == 0
    assert "仍被资产元数据引用" in (queued.last_error or "")


def test_environment_object_deletion_does_not_touch_legacy_duplicate(
    db, monkeypatch,
):
    """An authoritative object must hide any same-key regression-era copy."""
    from app.data_channel.datasets import service
    from app.shared import storage as shared_storage

    internal = FakeStorage()
    managed = FakeStorage()
    uri = "s3://raw-datasets/datasets/legacy/object.bin"
    internal.objects[uri] = b"old"
    managed.objects[uri] = b"regression-era-copy"
    legacy_resolutions = 0

    def resolve_legacy():
        nonlocal legacy_resolutions
        legacy_resolutions += 1
        return shared_storage.LegacyManagedStorageAccess(managed)

    platform = shared_storage.PlatformStorageAccess(internal)
    monkeypatch.setattr(service, "get_storage_service", lambda: platform)
    monkeypatch.setattr(
        shared_storage, "get_legacy_managed_storage_access", resolve_legacy)
    db.add(StorageDeletionOutbox(storage_uri=uri))
    db.commit()

    result = drain_storage_deletion_outbox(db)

    assert result == {"deleted": 1, "failed": 0, "deferred": 0}
    assert uri in internal.deleted
    assert uri not in managed.deleted
    assert legacy_resolutions == 0
    assert db.query(StorageDeletionOutbox).count() == 0


def test_legacy_managed_object_deletion_requires_authoritative_miss(
    db, monkeypatch,
):
    from app.data_channel.datasets import service
    from app.shared import storage as shared_storage

    internal = FakeStorage()
    managed = FakeStorage()
    uri = "s3://media/pipeline-files/legacy/object.bin"
    managed.objects[uri] = b"regression-era-file-asset"
    platform = shared_storage.PlatformStorageAccess(internal)
    monkeypatch.setattr(service, "get_storage_service", lambda: platform)
    monkeypatch.setattr(
        shared_storage,
        "get_legacy_managed_storage_access",
        lambda: shared_storage.LegacyManagedStorageAccess(managed),
    )
    db.add(StorageDeletionOutbox(storage_uri=uri))
    db.commit()

    result = drain_storage_deletion_outbox(db)

    assert result == {"deleted": 1, "failed": 0, "deferred": 0}
    assert uri not in internal.deleted
    assert uri in managed.deleted
    assert db.query(StorageDeletionOutbox).count() == 0
