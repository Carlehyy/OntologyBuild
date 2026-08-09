from datetime import datetime, timedelta, timezone

from app.models.ontology_formal import (
    LinkInstance, ObjectInstance, ObjectType, PropertyFact,
)
from app.models.ontology_version import OntologyVersion
from app.models.inference import AuditLog
from app.models.v2.mapping import OntologyLinkMapping, OntologyMapping
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
            ontology_release_id=release.id,
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
            ontology_release_id=release.id,
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
            ontology_release_id=release.id,
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
            ontology_release_id=release.id,
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
            ontology_release_id=release.id,
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


def test_instance_browser_object_keyword_matches_values_not_keys(
        client, auth_headers, ontology, db):
    release = db.query(OntologyVersion).filter_by(
        id=ontology["current_release_id"],
    ).one()
    _seed_release_instance_data(ontology["id"], release, db)
    base = (
        f"/api/v2/formal/ontologies/{ontology['id']}"
        "/instance-browser/objects?object_type_id=ot-order"
    )

    # 存储属性值与派生属性值都能命中
    by_value = client.get(f"{base}&keyword=甲方", headers=auth_headers)
    assert by_value.status_code == 200, by_value.text
    assert [item["id"] for item in by_value.json()["data"]["items"]] == ["order-1"]
    by_computed = client.get(f"{base}&keyword=高", headers=auth_headers)
    assert [item["id"] for item in by_computed.json()["data"]["items"]] == ["order-3"]

    # 只出现在键名里的词不再命中(修复键名污染)
    by_key = client.get(f"{base}&keyword=customer", headers=auth_headers)
    assert by_key.json()["data"]["total"] == 0

    # id、external_id、source 维持可搜
    by_id = client.get(f"{base}&keyword=order-1", headers=auth_headers)
    assert [item["id"] for item in by_id.json()["data"]["items"]] == ["order-1"]
    by_source = client.get(f"{base}&keyword=pipeline", headers=auth_headers)
    assert [item["id"] for item in by_source.json()["data"]["items"]] == ["order-1"]


def test_instance_browser_link_keyword_matches_endpoint_labels(
        client, auth_headers, ontology, db):
    release = db.query(OntologyVersion).filter_by(
        id=ontology["current_release_id"],
    ).one()
    _seed_release_instance_data(ontology["id"], release, db)
    base = (
        f"/api/v2/formal/ontologies/{ontology['id']}"
        "/instance-browser/links?link_type_id=lt-owner"
    )

    # 端点在表格里显示的业务标签(目标端“张三”、源端“A-001”)可搜到关系
    by_target = client.get(f"{base}&keyword=张三", headers=auth_headers)
    assert by_target.status_code == 200, by_target.text
    assert [item["id"] for item in by_target.json()["data"]["items"]] == ["link-1"]
    by_source = client.get(f"{base}&keyword=A-001", headers=auth_headers)
    assert [item["id"] for item in by_source.json()["data"]["items"]] == ["link-1"]

    # 端点 external_id 同样可命中
    by_external = client.get(f"{base}&keyword=source-order-1", headers=auth_headers)
    assert [item["id"] for item in by_external.json()["data"]["items"]] == ["link-1"]

    # 关系自身属性值可搜;只出现在键名里的词不命中
    by_value = client.get(f"{base}&keyword=2026-01-01", headers=auth_headers)
    assert [item["id"] for item in by_value.json()["data"]["items"]] == ["link-1"]
    by_key = client.get(f"{base}&keyword=since", headers=auth_headers)
    assert by_key.json()["data"]["total"] == 0


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


def _seed_legacy_projection_candidate(
        ontology_id, release, db, *, mappings_released: bool):
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    release.published_at = now - timedelta(hours=1)
    object_mappings = [
        {
            "id": "mapping-orders",
            "curatedDatasetId": "dataset-orders",
            "entityClass": "Order",
            "fieldMapping": {},
            "targetObjectTypeId": "ot-order",
            "status": "applied",
            "confidence": None,
        },
        {
            "id": "mapping-owners",
            "curatedDatasetId": "dataset-owners",
            "entityClass": "Owner",
            "fieldMapping": {},
            "targetObjectTypeId": "ot-owner",
            "status": "applied",
            "confidence": None,
        },
    ]
    link_mappings = [{
        "id": "mapping-owner-link",
        "srcDatasetId": "dataset-orders",
        "tgtDatasetId": "dataset-owners",
        "relationType": "owned_by",
        "srcKey": "owner_id",
        "tgtKey": "id",
        "status": "active",
        "linkTypeId": "lt-owner",
        "edgeDatasetId": None,
        "fieldMapping": {},
    }]
    release.snapshot_formal = {
        "objectTypes": [
            {
                "id": "ot-order", "name": "Order", "displayName": "订单",
                "primaryKey": "p-order-id", "properties": [{
                    "id": "p-order-id", "name": "id", "displayName": "订单号",
                    "type": "string", "required": True,
                }],
            },
            {
                "id": "ot-owner", "name": "Owner", "displayName": "负责人",
                "primaryKey": "p-owner-id", "properties": [{
                    "id": "p-owner-id", "name": "id", "displayName": "负责人编号",
                    "type": "string", "required": True,
                }],
            },
        ],
        "linkTypes": [{
            "id": "lt-owner", "name": "owned_by", "displayName": "负责人关系",
            "sourceObjectTypeId": "ot-order", "targetObjectTypeId": "ot-owner",
            "cardinality": "many-to-one", "properties": [],
        }],
        "actions": [], "functions": [], "sentinels": [],
        "mappings": object_mappings if mappings_released else [],
        "linkMappings": link_mappings if mappings_released else [],
    }
    db.add_all([
        Dataset(id="dataset-orders", name="订单数据", kind="structured"),
        Dataset(id="dataset-owners", name="负责人数据", kind="structured"),
        OntologyMapping(
            id="mapping-orders", ontology_id=ontology_id,
            curated_dataset_id="dataset-orders", entity_class="Order",
            field_mapping={}, target_object_type_id="ot-order",
            status="applied", confidence=None,
        ),
        OntologyMapping(
            id="mapping-owners", ontology_id=ontology_id,
            curated_dataset_id="dataset-owners", entity_class="Owner",
            field_mapping={}, target_object_type_id="ot-owner",
            status="applied", confidence=None,
        ),
        OntologyLinkMapping(
            id="mapping-owner-link", ontology_id=ontology_id,
            src_dataset_id="dataset-orders", tgt_dataset_id="dataset-owners",
            relation_type="owned_by", src_key="owner_id", tgt_key="id",
            status="active", link_type_id="lt-owner", edge_dataset_id=None,
            field_mapping={},
        ),
        ObjectInstance(
            id="legacy-order", ontology_id=ontology_id,
            ontology_release_id=None, object_type_id="ot-order",
            properties={"id": "O-1"}, source="pipeline",
            external_id="source-order-1", created_at=now, updated_at=now,
        ),
        ObjectInstance(
            id="legacy-owner", ontology_id=ontology_id,
            ontology_release_id=None, object_type_id="ot-owner",
            properties={"id": "U-1"}, source="pipeline",
            external_id="source-owner-1", created_at=now, updated_at=now,
        ),
        LinkInstance(
            id="legacy-link", ontology_id=ontology_id,
            ontology_release_id=None, link_type_id="lt-owner",
            source_object_id="legacy-order", target_object_id="legacy-owner",
            properties={}, source_relation_id="source-link-1", created_at=now,
        ),
        PropertyFact(
            id="legacy-order-fact", ontology_id=ontology_id,
            instance_id="legacy-order", object_type_id="ot-order",
            property_name="id", value={"v": "O-1"}, kind="property",
            source="pipeline", ontology_release_id=None,
            ontology_version=None, recorded_at=now,
        ),
        PropertyFact(
            id="legacy-link-fact", ontology_id=ontology_id,
            instance_id="legacy-link", object_type_id="lt-owner",
            property_name="__exists__", value={"v": True}, kind="link",
            source="pipeline", ontology_release_id=None,
            ontology_version=None, recorded_at=now,
        ),
    ])
    db.commit()


def test_instance_browser_adopts_only_proven_legacy_projection(
        client, auth_headers, ontology, db):
    ontology_id = ontology["id"]
    release = db.query(OntologyVersion).filter_by(
        id=ontology["current_release_id"],
    ).one()
    _seed_legacy_projection_candidate(
        ontology_id, release, db, mappings_released=True)

    catalog = client.get(
        f"/api/v2/formal/ontologies/{ontology_id}/instance-browser/catalog",
        headers=auth_headers,
    )
    assert catalog.status_code == 200, catalog.text
    legacy = catalog.json()["data"]["legacyProjection"]
    assert legacy == {
        "objectInstances": 2,
        "linkInstances": 1,
        "total": 3,
        "canAdopt": True,
        "recommendedAction": "adopt_legacy",
        "blockingReasons": [],
    }

    adopted = client.post(
        f"/api/v2/formal/ontologies/{ontology_id}/instance-browser/adopt-legacy",
        headers=auth_headers,
        json={
            "expectedReleaseId": release.id,
            "expectedObjectInstances": 2,
            "expectedLinkInstances": 1,
        },
    )
    assert adopted.status_code == 200, adopted.text
    assert adopted.json()["data"]["adopted"] == {
        "objectInstances": 2,
        "linkInstances": 1,
        "propertyFacts": 2,
    }

    db.expire_all()
    assert {
        item.ontology_release_id
        for item in db.query(ObjectInstance).filter_by(
            ontology_id=ontology_id).all()
    } == {release.id}
    assert db.query(LinkInstance).filter_by(
        ontology_id=ontology_id).one().ontology_release_id == release.id
    assert {
        (item.ontology_release_id, item.ontology_version)
        for item in db.query(PropertyFact).filter_by(
            ontology_id=ontology_id).all()
    } == {(release.id, release.version_number)}
    audit = db.query(AuditLog).filter_by(
        ontology_id=ontology_id,
        event_subtype="legacy_projection_adopted",
    ).one()
    assert audit.object_id == release.id
    assert audit.meta["object_instances"] == 2

    refreshed = client.get(
        f"/api/v2/formal/ontologies/{ontology_id}/instance-browser/catalog",
        headers=auth_headers,
    ).json()["data"]
    assert refreshed["legacyProjection"]["total"] == 0
    assert [item["instanceCount"] for item in refreshed["objectTypes"]] == [1, 1]
    assert refreshed["linkTypes"][0]["instanceCount"] == 1


def test_instance_browser_rejects_adoption_when_mappings_are_unreleased(
        client, auth_headers, ontology, db):
    ontology_id = ontology["id"]
    release = db.query(OntologyVersion).filter_by(
        id=ontology["current_release_id"],
    ).one()
    _seed_legacy_projection_candidate(
        ontology_id, release, db, mappings_released=False)

    catalog = client.get(
        f"/api/v2/formal/ontologies/{ontology_id}/instance-browser/catalog",
        headers=auth_headers,
    ).json()["data"]
    legacy = catalog["legacyProjection"]
    assert legacy["objectInstances"] == 2
    assert legacy["linkInstances"] == 1
    assert legacy["canAdopt"] is False
    assert legacy["recommendedAction"] == "publish_draft"
    assert {
        item["code"] for item in legacy["blockingReasons"]
    } >= {"release_mapping_coverage_missing", "release_mapping_mismatch"}

    rejected = client.post(
        f"/api/v2/formal/ontologies/{ontology_id}/instance-browser/adopt-legacy",
        headers=auth_headers,
        json={
            "expectedReleaseId": release.id,
            "expectedObjectInstances": 2,
            "expectedLinkInstances": 1,
        },
    )
    assert rejected.status_code == 409, rejected.text
    assert rejected.json()["detail"]["code"] == "legacy_projection_not_adoptable"
    db.expire_all()
    assert db.query(ObjectInstance).filter_by(
        ontology_id=ontology_id,
        ontology_release_id=None,
    ).count() == 2
    assert db.query(AuditLog).filter_by(
        ontology_id=ontology_id,
        event_subtype="legacy_projection_adopted",
    ).count() == 0
