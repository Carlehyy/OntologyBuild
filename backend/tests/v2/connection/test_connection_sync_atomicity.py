import pytest
from fastapi import HTTPException

from app.data_channel.connections.router import SyncBody, trigger_sync
from app.models.v2.connection import Connection
from app.models.v2.dataset import Dataset, DatasetVersion
from app.tasks.v2.connection_sync import sync_connection


class _Connector:
    def list_resources(self):
        return ["orders"]

    def pull_full(self, _resource):
        return [{"order_id": "SO-1"}]


class _FailingStorage:
    def put_bytes(self, *_args, **_kwargs):
        raise ConnectionError("object store unavailable")


class _ResourceConnector:
    def list_resources(self):
        return ["orders", "customers"]

    def pull_full(self, resource):
        return [{"resource": resource}]


class _NoResourceConnector:
    def list_resources(self):
        return []


class _PullFailureConnector:
    def list_resources(self):
        return ["orders"]

    def pull_full(self, _resource):
        raise RuntimeError("upstream unavailable")


class _Storage:
    def __init__(self):
        self.objects = {}

    def put_bytes(self, bucket, key, data, *_args, **_kwargs):
        uri = f"s3://{bucket}/{key}"
        self.objects[uri] = data
        return uri

    def delete_object(self, uri):
        self.objects.pop(uri, None)


def test_connection_sync_uses_database_when_managed_minio_is_unavailable(
    db, monkeypatch,
):
    connection = Connection(
        id="conn-atomic", name="订单库", kind="rest", config={}, status="inactive",
    )
    db.add(connection)
    db.commit()
    monkeypatch.setattr(
        "app.services.connection.registry.get_connector",
        lambda _kind, _config: _Connector(),
    )
    monkeypatch.setattr(
        "app.data_channel.datasets.service.get_storage_service",
        lambda: _FailingStorage(),
    )

    result = sync_connection(connection.id, db=db)

    assert result["status"] == "ok"
    dataset = db.query(Dataset).filter(
        Dataset.source_connection_id == connection.id
    ).one()
    version = db.query(DatasetVersion).filter(
        DatasetVersion.dataset_id == dataset.id).one()
    assert version.storage_uri is None
    assert version.data_size == len(version.data_blob)


def test_connection_resources_have_independent_dataset_identity_and_retry_is_idempotent(
    db, monkeypatch,
):
    connection = Connection(
        id="conn-resources", name="业务库", kind="rest", config={}, status="inactive",
    )
    db.add(connection)
    db.commit()
    connector = _ResourceConnector()
    storage = _Storage()
    monkeypatch.setattr(
        "app.services.connection.registry.get_connector",
        lambda _kind, _config: connector,
    )
    monkeypatch.setattr(
        "app.data_channel.datasets.service.get_storage_service",
        lambda: storage,
    )

    orders_first = sync_connection(connection.id, resource="orders", db=db)
    customers = sync_connection(connection.id, resource="customers", db=db)
    orders_retry = sync_connection(connection.id, resource="orders", db=db)

    assert orders_first["status"] == customers["status"] == orders_retry["status"] == "ok"
    assert orders_first["dataset_id"] != customers["dataset_id"]
    assert orders_retry["dataset_id"] == orders_first["dataset_id"]
    assert orders_first["version_no"] == 1
    assert customers["version_no"] == 1
    assert orders_retry["version_no"] == 2

    datasets = db.query(Dataset).filter(
        Dataset.source_connection_id == connection.id
    ).order_by(Dataset.source_resource).all()
    assert [(dataset.source_resource, dataset.id) for dataset in datasets] == [
        ("customers", customers["dataset_id"]),
        ("orders", orders_first["dataset_id"]),
    ]
    assert db.query(DatasetVersion).filter(
        DatasetVersion.dataset_id == orders_first["dataset_id"]
    ).count() == 2
    assert db.query(DatasetVersion).filter(
        DatasetVersion.dataset_id == customers["dataset_id"]
    ).count() == 1


@pytest.mark.parametrize(
    ("connector", "expected_error"),
    [
        (_NoResourceConnector(), "did not provide a resource"),
        (_PullFailureConnector(), "upstream unavailable"),
    ],
)
def test_failed_connection_sync_never_creates_empty_success_dataset(
    db, monkeypatch, connector, expected_error,
):
    connection = Connection(
        id=f"conn-failure-{connector.__class__.__name__}",
        name="失败连接",
        kind="rest",
        config={},
        status="inactive",
    )
    db.add(connection)
    db.commit()
    monkeypatch.setattr(
        "app.services.connection.registry.get_connector",
        lambda _kind, _config: connector,
    )

    result = sync_connection(connection.id, db=db)

    assert result["status"] == "error"
    assert expected_error in result["error"]
    db.refresh(connection)
    assert connection.status == "error"
    assert db.query(Dataset).filter(
        Dataset.source_connection_id == connection.id
    ).count() == 0


def test_sync_endpoint_surfaces_connector_failure(db, monkeypatch):
    connection = Connection(
        id="conn-route-failure",
        name="路由失败连接",
        kind="rest",
        config={},
        status="inactive",
    )
    db.add(connection)
    db.commit()
    monkeypatch.setattr(
        "app.tasks.v2.connection_sync.sync_connection",
        lambda *_args, **_kwargs: {
            "status": "error",
            "error": "upstream unavailable",
        },
    )

    with pytest.raises(HTTPException) as exc_info:
        trigger_sync(connection.id, SyncBody(), db)

    assert exc_info.value.status_code == 502
    assert "upstream unavailable" in str(exc_info.value.detail)


def test_async_dispatch_does_not_mark_connection_active_before_completion(
    db, monkeypatch,
):
    connection = Connection(
        id="conn-async-dispatch",
        name="异步连接",
        kind="rest",
        config={},
        status="inactive",
    )
    db.add(connection)
    db.commit()
    dispatched = []
    monkeypatch.setattr(
        sync_connection,
        "delay",
        lambda *args: dispatched.append(args),
        raising=False,
    )

    result = trigger_sync(
        connection.id,
        SyncBody(async_mode=True, resource="/orders"),
        db,
    )

    db.refresh(connection)
    assert result["status"] == "sync_triggered"
    assert connection.status == "inactive"
    assert dispatched == [(connection.id, "full", "/orders")]
