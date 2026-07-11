import pytest

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


class _Storage:
    def __init__(self):
        self.objects = {}

    def put_bytes(self, bucket, key, data, *_args, **_kwargs):
        uri = f"s3://{bucket}/{key}"
        self.objects[uri] = data
        return uri

    def delete_object(self, uri):
        self.objects.pop(uri, None)


def test_first_connection_sync_does_not_leave_dataset_shell_on_storage_failure(
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

    with pytest.raises(ConnectionError, match="object store unavailable"):
        sync_connection(connection.id, db=db)

    assert db.query(Dataset).filter(
        Dataset.source_connection_id == connection.id
    ).count() == 0


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
