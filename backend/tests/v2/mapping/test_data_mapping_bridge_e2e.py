"""真实 API 级数据映射桥梁测试。

链路覆盖：人工数据资产 -> 正规本体对象/关系定义 -> 映射配置 -> 全量执行 ->
对象/关系实例 -> 配置删除与当前态撤销。对象存储使用内存实现以保持测试隔离，
映射、数据库事务、投影与 HTTP 路由均使用生产代码。
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from app.main import app
from app.routers.v2 import datasets as datasets_module
from app.routers.v2 import mappings as mappings_module
from app.services.v2.mapping.mapping_service import MappingService


class IsolatedStorage:
    def __init__(self):
        self.objects: dict[str, bytes] = {}

    def put_bytes(self, bucket, key, data, content_type=""):
        uri = f"s3://{bucket}/{key}"
        self.objects[uri] = data
        return uri

    def get_object(self, uri):
        if uri not in self.objects:
            raise FileNotFoundError(uri)
        return self.objects[uri]

    def delete_object(self, uri):
        self.objects.pop(uri, None)


@pytest.fixture
def bridge_api(client, db, monkeypatch):
    from app.data_channel.datasets import service as dataset_service

    storage = IsolatedStorage()
    monkeypatch.setattr(dataset_service, "get_storage_service", lambda: storage)

    def override_db():
        yield db

    app.dependency_overrides[datasets_module.get_db] = override_db
    app.dependency_overrides[mappings_module.get_db] = override_db
    yield client
    app.dependency_overrides.pop(datasets_module.get_db, None)
    app.dependency_overrides.pop(mappings_module.get_db, None)


def _formal(ontology_id: str) -> str:
    return f"/api/v2/formal/ontologies/{ontology_id}"


def _create_table(api, headers, name: str, columns: list[tuple[str, str]], pk: str,
                  rows: list[dict]) -> str:
    response = api.post("/api/v2/datasets/create-table", headers=headers, json={
        "name": name,
        "columns": [{"name": column, "type": field_type}
                    for column, field_type in columns],
        "primary_key": pk,
    })
    assert response.status_code == 201, response.text
    dataset_id = response.json()["data"]["id"]
    response = api.post(f"/api/v2/datasets/{dataset_id}/rows/edit", headers=headers, json={
        "base_version_no": 1,
        "inserts": [{"values": row} for row in rows],
    })
    assert response.status_code == 200, response.text
    return dataset_id


def _create_object_type(api, headers, ontology_id: str, name: str,
                        properties: list[dict], primary_key: str) -> str:
    response = api.post(f"{_formal(ontology_id)}/object-types", headers=headers, json={
        "name": name,
        "displayName": name,
        "primaryKey": primary_key,
        "properties": properties,
    })
    assert response.status_code == 201, response.text
    return response.json()["data"]["id"]


def _create_object_mapping(api, headers, ontology_id: str, dataset_id: str,
                           object_type_id: str, entity_class: str,
                           field_mapping: dict[str, str]):
    return api.post(f"/api/v2/ontologies/{ontology_id}/mappings", headers=headers, json={
        "curated_dataset_id": dataset_id,
        "entity_class": entity_class,
        "target_object_type_id": object_type_id,
        "field_mapping": field_mapping,
        "ignored_fields": [],
        "confidence": 1,
    })


def test_mapping_api_rejects_incompatible_object_and_relation_types(
        bridge_api, auth_headers, ontology):
    api, headers, ontology_id = bridge_api, auth_headers, ontology["id"]
    suppliers = _create_table(api, headers, "供应商", [
        ("supplier_id", "string"), ("name", "string"), ("rating", "integer"),
    ], "supplier_id", [{"supplier_id": "S-1", "name": "甲", "rating": "5"}])
    orders = _create_table(api, headers, "订单", [
        ("order_id", "string"), ("amount", "float"),
    ], "order_id", [{"order_id": "O-1", "amount": "12.5"}])
    assignments = _create_table(api, headers, "订单供应商关系", [
        ("line_id", "string"), ("supplier_id", "string"),
        ("order_id", "string"), ("score", "float"), ("note", "string"),
    ], "line_id", [{
        "line_id": "L-1", "supplier_id": "S-1", "order_id": "O-1",
        "score": "0.9", "note": "优先",
    }])

    supplier_type = _create_object_type(api, headers, ontology_id, "Supplier", [
        {"id": "supplier-id", "name": "supplier_id", "type": "string", "required": True},
        {"id": "supplier-name", "name": "name", "type": "string", "required": False},
        {"id": "supplier-rating", "name": "rating", "type": "number", "required": False},
    ], "supplier_id")
    order_type = _create_object_type(api, headers, ontology_id, "Order", [
        {"id": "order-id", "name": "order_id", "type": "string", "required": True},
        {"id": "order-amount", "name": "amount", "type": "number", "required": False},
    ], "order_id")

    bad_object = _create_object_mapping(
        api, headers, ontology_id, suppliers, supplier_type, "Supplier",
        {"supplier_id": "supplier_id", "name": "rating"})
    assert bad_object.status_code == 422
    assert bad_object.json()["detail"]["code"] == "mapping_type_mismatch"

    assert _create_object_mapping(
        api, headers, ontology_id, suppliers, supplier_type, "Supplier",
        {"supplier_id": "supplier_id", "name": "name", "rating": "rating"},
    ).status_code == 200
    assert _create_object_mapping(
        api, headers, ontology_id, orders, order_type, "Order",
        {"order_id": "order_id", "amount": "amount"},
    ).status_code == 200

    link = api.post(f"{_formal(ontology_id)}/link-types", headers=headers, json={
        "name": "SUPPLIES", "displayName": "供应",
        "sourceObjectTypeId": supplier_type, "targetObjectTypeId": order_type,
        "cardinality": "many-to-many",
        "properties": [{
            "id": "supply-score", "name": "score", "type": "number", "required": False,
        }],
    })
    assert link.status_code == 201, link.text
    link_type_id = link.json()["data"]["id"]

    bad_link = api.post(f"/api/v2/ontologies/{ontology_id}/link-mappings",
                        headers=headers, json={
        "src_dataset_id": suppliers, "tgt_dataset_id": orders,
        "edge_dataset_id": assignments, "link_type_id": link_type_id,
        "relation_type": "SUPPLIES", "src_key": "supplier_id", "tgt_key": "order_id",
        "field_mapping": {"score": "note"},
    })
    assert bad_link.status_code == 422
    assert bad_link.json()["detail"]["code"] == "link_mapping_type_mismatch"


def test_asset_lake_to_ontology_mapping_bridge_and_reconciliation(
        bridge_api, auth_headers, ontology):
    api, headers, ontology_id = bridge_api, auth_headers, ontology["id"]
    suppliers = _create_table(api, headers, "供应商", [
        ("supplier_id", "string"), ("name", "string"),
    ], "supplier_id", [
        {"supplier_id": "S-1", "name": "甲供应商"},
        {"supplier_id": "S-2", "name": "乙供应商"},
    ])
    orders = _create_table(api, headers, "订单", [
        ("order_id", "string"), ("title", "string"),
    ], "order_id", [
        {"order_id": "O-1", "title": "首单"},
        {"order_id": "O-2", "title": "次单"},
    ])
    assignments = _create_table(api, headers, "订单供应商关系", [
        ("line_id", "string"), ("supplier_id", "string"),
        ("order_id", "string"), ("memo", "string"),
    ], "line_id", [
        {"line_id": "L-1", "supplier_id": "S-1", "order_id": "O-1", "memo": "主供"},
        {"line_id": "L-2", "supplier_id": "S-2", "order_id": "O-2", "memo": "备供"},
    ])

    supplier_type = _create_object_type(api, headers, ontology_id, "Supplier", [
        {"id": "supplier-id", "name": "supplier_id", "type": "string", "required": True},
        {"id": "supplier-name", "name": "name", "type": "string", "required": False},
    ], "supplier_id")
    order_type = _create_object_type(api, headers, ontology_id, "Order", [
        {"id": "order-id", "name": "order_id", "type": "string", "required": True},
        {"id": "order-title", "name": "title", "type": "string", "required": False},
    ], "order_id")
    link_response = api.post(f"{_formal(ontology_id)}/link-types", headers=headers, json={
        "name": "SUPPLIES", "displayName": "供应",
        "sourceObjectTypeId": supplier_type, "targetObjectTypeId": order_type,
        "cardinality": "many-to-many",
        "properties": [{
            "id": "supply-memo", "name": "memo", "type": "string", "required": False,
        }],
    })
    assert link_response.status_code == 201, link_response.text
    link_type_id = link_response.json()["data"]["id"]

    supplier_mapping = _create_object_mapping(
        api, headers, ontology_id, suppliers, supplier_type, "Supplier",
        {"supplier_id": "supplier_id", "name": "name"})
    order_mapping = _create_object_mapping(
        api, headers, ontology_id, orders, order_type, "Order",
        {"order_id": "order_id", "title": "title"})
    assert supplier_mapping.status_code == 200, supplier_mapping.text
    assert order_mapping.status_code == 200, order_mapping.text
    supplier_mapping_id = supplier_mapping.json()["mapping_id"]

    link_mapping = api.post(f"/api/v2/ontologies/{ontology_id}/link-mappings",
                            headers=headers, json={
        "src_dataset_id": suppliers, "tgt_dataset_id": orders,
        "edge_dataset_id": assignments, "link_type_id": link_type_id,
        "relation_type": "SUPPLIES", "src_key": "supplier_id", "tgt_key": "order_id",
        "field_mapping": {"memo": "memo"},
    })
    assert link_mapping.status_code == 200, link_mapping.text
    link_mapping_id = link_mapping.json()["link_mapping_id"]

    with patch.object(MappingService, "_rebuild_neo4j_projection", return_value=True), \
         patch.object(MappingService, "_rebuild_chroma_projection", return_value=4):
        applied = api.post(
            f"/api/v2/ontologies/{ontology_id}/mappings/{supplier_mapping_id}/apply-from-dataset",
            headers=headers)
    assert applied.status_code == 200, applied.text
    assert applied.json()["total_entities"] == 4

    instances = api.get(f"{_formal(ontology_id)}/instances", headers=headers).json()["data"]
    assert len(instances) == 4
    assert {item["properties"].get("name") for item in instances
            if item["objectTypeId"] == supplier_type} == {"甲供应商", "乙供应商"}
    links = api.get(f"{_formal(ontology_id)}/link-instances", headers=headers).json()["data"]
    assert len(links) == 2
    assert {item["properties"]["memo"] for item in links} == {"主供", "备供"}

    removed_link = api.delete(
        f"/api/v2/ontologies/{ontology_id}/link-mappings/{link_mapping_id}", headers=headers)
    assert removed_link.status_code == 204, removed_link.text
    assert api.get(f"{_formal(ontology_id)}/link-instances", headers=headers).json()["data"] == []

    removed_objects = api.delete(
        f"/api/v2/ontologies/{ontology_id}/mappings/{supplier_mapping_id}", headers=headers)
    assert removed_objects.status_code == 204, removed_objects.text
    remaining = api.get(f"{_formal(ontology_id)}/instances", headers=headers).json()["data"]
    assert len(remaining) == 2
    assert {item["objectTypeId"] for item in remaining} == {order_type}

