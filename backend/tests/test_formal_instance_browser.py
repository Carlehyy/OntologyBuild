from datetime import datetime, timedelta, timezone

from app.models.ontology_formal import (
    LinkInstance, ObjectInstance, ObjectType,
)
from app.models.ontology_version import OntologyVersion
from app.models.v2.dataset import Dataset


def _seed_release_instance_data(ontology_id, release, db):
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    release.published_at = now - timedelta(days=1)
    release.snapshot_formal = {
        "objectTypes": [
            {
                "id": "ot-order",
                "name": "Order",
                "displayName": "订单",
                "primaryKey": "prop-order-no",
                "properties": [
                    {
                        "id": "prop-order-no",
                        "name": "order_no",
                        "displayName": "订单号",
                        "type": "string",
                        "required": True,
                    },
                    {
                        "id": "prop-customer",
                        "name": "customer",
                        "displayName": "客户",
                        "type": "string",
                        "required": True,
                    },
                    {
                        "id": "prop-risk",
                        "name": "risk",
                        "displayName": "风险等级",
                        "type": "string",
                        "source": "computed",
                    },
                ],
            },
            {
                "id": "ot-owner",
                "name": "Owner",
                "displayName": "负责人",
                "primaryKey": "prop-owner-name",
                "properties": [{
                    "id": "prop-owner-name",
                    "name": "name",
                    "displayName": "姓名",
                    "type": "string",
                    "required": True,
                }],
            },
        ],
        "linkTypes": [{
            "id": "lt-owner",
            "name": "owned_by",
            "displayName": "负责人关系",
            "sourceObjectTypeId": "ot-order",
            "targetObjectTypeId": "ot-owner",
            "cardinality": "many-to-one",
            "properties": [{
                "id": "prop-since",
                "name": "since",
                "displayName": "负责时间",
                "type": "datetime",
                "required": True,
            }],
        }],
        "actions": [],
        "functions": [],
        "sentinels": [],
        "mappings": [],
        "linkMappings": [],
    }
    db.add(ObjectType(
        id="ot-rogue",
        ontology_id=ontology_id,
        name="Rogue",
        display_name="未发布对象",
        properties=[],
    ))
    db.add_all([
        ObjectInstance(
            id="order-1",
            ontology_id=ontology_id,
            object_type_id="ot-order",
            properties={"order_no": "A-001", "customer": "甲方"},
            computed={"risk": "低"},
            source="pipeline",
            external_id="source-order-1",
            created_at=now - timedelta(hours=5),
            updated_at=now - timedelta(hours=3),
        ),
        ObjectInstance(
            id="order-2",
            ontology_id=ontology_id,
            object_type_id="ot-order",
            properties={"order_no": "A-002", "customer": "乙方"},
            computed={"risk": "中"},
            source="collector",
            external_id="source-order-2",
            created_at=now - timedelta(hours=4),
            updated_at=now - timedelta(hours=2),
        ),
        ObjectInstance(
            id="order-3",
            ontology_id=ontology_id,
            object_type_id="ot-order",
            properties={
                "order_no": "A-003",
                "customer": "丙方",
                "runtime_extra": {"complete": True},
            },
            computed={"risk": "高"},
            source="manual",
            external_id="source-order-3",
            created_at=now - timedelta(hours=3),
            updated_at=now - timedelta(hours=1),
        ),
        ObjectInstance(
            id="owner-1",
            ontology_id=ontology_id,
            object_type_id="ot-owner",
            properties={"name": "张三"},
            source="manual",
            created_at=now - timedelta(hours=2),
            updated_at=now - timedelta(hours=1),
        ),
        ObjectInstance(
            id="rogue-1",
            ontology_id=ontology_id,
            object_type_id="ot-rogue",
            properties={"id": "X-001"},
            source="manual",
            created_at=now,
            updated_at=now,
        ),
        LinkInstance(
            id="link-1",
            ontology_id=ontology_id,
            link_type_id="lt-owner",
            source_object_id="order-1",
            target_object_id="owner-1",
            properties={"since": "2026-01-01T00:00:00Z"},
            source_relation_id="source-link-1",
            created_at=now - timedelta(minutes=2),
        ),
        LinkInstance(
            id="link-rogue",
            ontology_id=ontology_id,
            link_type_id="lt-rogue",
            source_object_id="order-2",
            target_object_id="owner-1",
            properties={"secret": "draft"},
            created_at=now,
        ),
    ])
    db.commit()


def test_instance_browser_catalog_uses_only_current_release(
        client, auth_headers, ontology, db):
    release = db.query(OntologyVersion).filter_by(
        id=ontology["current_release_id"],
    ).one()
    _seed_release_instance_data(ontology["id"], release, db)
    snapshot = dict(release.snapshot_formal)
    snapshot["mappings"] = [{
        "id": "mapping-orders",
        "curatedDatasetId": "dataset-orders",
        "targetObjectTypeId": "ot-order",
        "entityClass": "Order",
    }]
    snapshot["linkMappings"] = [{
        "id": "mapping-owner-link",
        "linkTypeId": "lt-owner",
        "relationType": "owned_by",
        "srcDatasetId": "dataset-orders",
        "tgtDatasetId": "dataset-owners",
        "edgeDatasetId": "dataset-ownerships",
    }]
    release.snapshot_formal = snapshot
    db.add_all([
        Dataset(id="dataset-orders", name="订单数据", kind="curated"),
        Dataset(id="dataset-owners", name="负责人数据", kind="structured"),
        Dataset(id="dataset-ownerships", name="订单负责人关系", kind="structured"),
    ])
    db.commit()

    response = client.get(
        f"/api/v2/formal/ontologies/{ontology['id']}/instance-browser/catalog",
        headers=auth_headers,
    )

    assert response.status_code == 200, response.text
    body = response.json()["data"]
    assert body["release"]["id"] == release.id
    assert body["release"]["version"] == release.version_number
    assert [item["id"] for item in body["objectTypes"]] == [
        "ot-order", "ot-owner",
    ]
    assert [item["instanceCount"] for item in body["objectTypes"]] == [3, 1]
    assert [(item["id"], item["instanceCount"]) for item in body["linkTypes"]] == [
        ("lt-owner", 1),
    ]
    assert body["objectTypes"][0]["associatedDatasets"] == [{
        "id": "dataset-orders",
        "name": "订单数据",
        "kind": "curated",
        "roles": ["实体数据"],
        "available": True,
    }]
    assert body["objectTypes"][1]["associatedDatasets"] == []
    assert {
        item["id"]: item["roles"]
        for item in body["linkTypes"][0]["associatedDatasets"]
    } == {
        "dataset-orders": ["源实体数据"],
        "dataset-owners": ["目标实体数据"],
        "dataset-ownerships": ["关系数据"],
    }


def test_instance_browser_pages_objects_and_rejects_unpublished_type(
        client, auth_headers, ontology, db):
    release = db.query(OntologyVersion).filter_by(
        id=ontology["current_release_id"],
    ).one()
    _seed_release_instance_data(ontology["id"], release, db)
    base = f"/api/v2/formal/ontologies/{ontology['id']}/instance-browser/objects"

    response = client.get(
        f"{base}?object_type_id=ot-order&page=1&page_size=2",
        headers=auth_headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()["data"]
    assert body["total"] == 3
    assert body["page"] == 1
    assert body["pageSize"] == 2
    assert [item["id"] for item in body["items"]] == ["order-3", "order-2"]
    assert body["items"][0]["properties"]["runtime_extra"] == {"complete": True}
    assert body["items"][0]["computed"] == {"risk": "高"}

    search = client.get(
        f"{base}?object_type_id=ot-order&keyword=source-order-2",
        headers=auth_headers,
    )
    assert search.status_code == 200, search.text
    assert [item["id"] for item in search.json()["data"]["items"]] == ["order-2"]

    rogue = client.get(
        f"{base}?object_type_id=ot-rogue",
        headers=auth_headers,
    )
    assert rogue.status_code == 404
    assert rogue.json()["detail"]["code"] == "release_type_not_found"


def test_instance_browser_pages_links_with_readable_endpoints(
        client, auth_headers, ontology, db):
    release = db.query(OntologyVersion).filter_by(
        id=ontology["current_release_id"],
    ).one()
    _seed_release_instance_data(ontology["id"], release, db)

    response = client.get(
        f"/api/v2/formal/ontologies/{ontology['id']}/instance-browser/links"
        "?link_type_id=lt-owner&page=1&page_size=20",
        headers=auth_headers,
    )

    assert response.status_code == 200, response.text
    body = response.json()["data"]
    assert body["total"] == 1
    item = body["items"][0]
    assert item["id"] == "link-1"
    assert item["properties"] == {"since": "2026-01-01T00:00:00Z"}
    assert item["sourceRelationId"] == "source-link-1"
    assert item["sourceObject"] == {
        "id": "order-1",
        "objectTypeId": "ot-order",
        "label": "A-001",
        "externalId": "source-order-1",
    }
    assert item["targetObject"]["label"] == "张三"


def test_instance_browser_requires_authentication(client, ontology):
    response = client.get(
        f"/api/v2/formal/ontologies/{ontology['id']}/instance-browser/catalog",
    )
    assert response.status_code in {401, 403}
