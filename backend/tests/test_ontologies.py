def test_create_ontology(client, auth_headers):
    r = client.post("/api/v1/ontologies",
                    json={"name": "供应链测试", "domain": "供应链"},
                    headers=auth_headers)
    assert r.status_code == 201
    assert r.json()["data"]["name"] == "供应链测试"

def test_duplicate_name_returns_409(client, auth_headers):
    client.post("/api/v1/ontologies", json={"name": "供应链测试", "domain": "供应链"}, headers=auth_headers)
    r = client.post("/api/v1/ontologies", json={"name": "供应链测试", "domain": "供应链"}, headers=auth_headers)
    assert r.status_code == 409

def test_invalid_domain_returns_422(client, auth_headers):
    r = client.post("/api/v1/ontologies", json={"name": "Test", "domain": "invalid"}, headers=auth_headers)
    assert r.status_code == 422

def test_list_ontologies(client, auth_headers):
    client.post("/api/v1/ontologies", json={"name": "A", "domain": "供应链"}, headers=auth_headers)
    client.post("/api/v1/ontologies", json={"name": "B", "domain": "采购"}, headers=auth_headers)
    r = client.get("/api/v1/ontologies", headers=auth_headers)
    assert r.json()["data"]["total"] == 2

def test_list_ontologies_requires_auth(client):
    r = client.get("/api/v1/ontologies")
    assert r.status_code == 403

def test_get_ontology(client, auth_headers):
    r = client.post("/api/v1/ontologies", json={"name": "GetTest", "domain": "财务"}, headers=auth_headers)
    oid = r.json()["data"]["id"]
    r2 = client.get(f"/api/v1/ontologies/{oid}", headers=auth_headers)
    assert r2.status_code == 200
    assert r2.json()["data"]["name"] == "GetTest"

def test_delete_ontology(client, auth_headers):
    r = client.post("/api/v1/ontologies", json={"name": "Del", "domain": "财务"}, headers=auth_headers)
    oid = r.json()["data"]["id"]
    r2 = client.delete(f"/api/v1/ontologies/{oid}", headers=auth_headers)
    assert r2.status_code == 204

def test_update_ontology(client, auth_headers):
    r = client.post("/api/v1/ontologies", json={"name": "Update", "domain": "医疗"}, headers=auth_headers)
    oid = r.json()["data"]["id"]
    r2 = client.put(f"/api/v1/ontologies/{oid}", json={"description": "updated desc"}, headers=auth_headers)
    assert r2.status_code == 200
    assert r2.json()["data"]["description"] == "updated desc"


def test_ontology_card_metadata_uses_configured_domain(client, auth_headers):
    domain_response = client.post(
        "/api/v1/domains",
        json={"name": "客户运营", "description": "客户全生命周期"},
        headers=auth_headers,
    )
    assert domain_response.status_code == 201

    created = client.post(
        "/api/v1/ontologies",
        json={
            "name": "客户运营本体",
            "domain": "客户运营",
            "description": "统一客户、触点和服务知识。",
            "icon": "users",
        },
        headers=auth_headers,
    )
    assert created.status_code == 201, created.text
    payload = created.json()["data"]
    assert payload["icon"] == "users"
    assert payload["build_mode"] == "manual"

    listed = client.get("/api/v1/ontologies", headers=auth_headers)
    assert listed.status_code == 200
    card = next(item for item in listed.json()["data"]["items"] if item["id"] == payload["id"])
    assert card["description"] == "统一客户、触点和服务知识。"
    assert card["icon"] == "users"
    assert card["sentinel_count"] == 0


def test_ontology_card_counts_come_from_current_release_snapshot(client, auth_headers, db):
    created = client.post(
        "/api/v1/ontologies",
        json={"name": "发布快照计数本体", "domain": "供应链"},
        headers=auth_headers,
    ).json()["data"]

    from app.models.ontology_formal import ActionType
    from app.models.ontology_version import OntologyVersion
    from app.ontologies.versions.evolution_service import complete_snapshot, snapshot_hash

    # Mutable workspace rows must not leak into the management card before a
    # release. The card reads only the immutable current-release snapshot.
    db.add_all([
        ActionType(
            ontology_id=created["id"],
            name=f"draft_action_{index}",
            display_name=f"草稿动作 {index}",
        )
        for index in range(3)
    ])
    release = db.query(OntologyVersion).filter_by(
        id=created["current_release_id"],
    ).one()
    release.snapshot_formal = complete_snapshot({
        "objectTypes": [{"id": "object-1"}, {"id": "object-2"}],
        "linkTypes": [{"id": "link-1"}],
        "actions": [{"id": "action-1"}],
        "sentinels": [{"id": "sentinel-1"}, {"id": "sentinel-2"}],
    })
    release.snapshot_hash = snapshot_hash(release.snapshot_formal)
    db.commit()

    listed = client.get("/api/v1/ontologies", headers=auth_headers)
    assert listed.status_code == 200
    card = next(
        item for item in listed.json()["data"]["items"]
        if item["id"] == created["id"]
    )
    assert {
        key: card[key]
        for key in ("entity_count", "relation_count", "action_count", "sentinel_count")
    } == {
        "entity_count": 2,
        "relation_count": 1,
        "action_count": 1,
        "sentinel_count": 2,
    }


def test_update_ontology_card_metadata(client, auth_headers):
    created = client.post(
        "/api/v1/ontologies",
        json={"name": "待编辑本体", "domain": "财务"},
        headers=auth_headers,
    ).json()["data"]

    updated = client.put(
        f"/api/v1/ontologies/{created['id']}",
        json={"name": "财务管理本体", "description": "财务知识模型", "icon": "landmark"},
        headers=auth_headers,
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["data"]["name"] == "财务管理本体"
    assert updated.json()["data"]["icon"] == "landmark"
