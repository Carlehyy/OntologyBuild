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

def test_delete_ontology(client, auth_headers, monkeypatch):
    class FakeNeo4j:
        available = True

        def delete_by_ontology(self, ontology_id):
            self.deleted = ontology_id
            return 0

        def close(self):
            return None

    monkeypatch.setattr(
        "app.services.v2.graph.neo4j_service.Neo4jService",
        FakeNeo4j,
    )
    r = client.post("/api/v1/ontologies", json={"name": "Del", "domain": "财务"}, headers=auth_headers)
    oid = r.json()["data"]["id"]
    r2 = client.delete(f"/api/v1/ontologies/{oid}", headers=auth_headers)
    assert r2.status_code == 204


def test_delete_ontology_keeps_sql_truth_when_neo4j_is_unavailable(
    client,
    auth_headers,
    db,
    monkeypatch,
):
    from app.models.ontology import OntologyProject

    class UnavailableNeo4j:
        available = False

        def close(self):
            return None

    monkeypatch.setattr(
        "app.services.v2.graph.neo4j_service.Neo4jService",
        UnavailableNeo4j,
    )
    created = client.post(
        "/api/v1/ontologies",
        json={"name": "Delete fenced", "domain": "财务"},
        headers=auth_headers,
    ).json()["data"]

    response = client.delete(
        f"/api/v1/ontologies/{created['id']}",
        headers=auth_headers,
    )

    assert response.status_code == 503
    db.expire_all()
    persisted = db.get(OntologyProject, created["id"])
    assert persisted is not None
    assert persisted.projection_status == "failed"
    assert "Neo4j ontology deletion failed" in (
        persisted.projection_error or ""
    )


def test_delete_ontology_fences_sql_commit_failure_after_graph_delete(
    client,
    auth_headers,
    db,
    monkeypatch,
):
    from app.models.ontology import OntologyProject

    deleted: list[str] = []

    class FakeNeo4j:
        available = True

        def delete_by_ontology(self, ontology_id):
            deleted.append(ontology_id)
            return 0

        def close(self):
            return None

    monkeypatch.setattr(
        "app.services.v2.graph.neo4j_service.Neo4jService",
        FakeNeo4j,
    )
    created = client.post(
        "/api/v1/ontologies",
        json={"name": "Delete SQL failure", "domain": "财务"},
        headers=auth_headers,
    ).json()["data"]

    original_commit = db.commit
    commit_calls = 0

    def fail_delete_commit():
        nonlocal commit_calls
        commit_calls += 1
        if commit_calls == 2:
            raise RuntimeError("injected SQL delete commit failure")
        return original_commit()

    monkeypatch.setattr(db, "commit", fail_delete_commit)

    response = client.delete(
        f"/api/v1/ontologies/{created['id']}",
        headers=auth_headers,
    )

    assert response.status_code == 503
    assert deleted == [created["id"]]
    db.expire_all()
    persisted = db.get(OntologyProject, created["id"])
    assert persisted is not None
    assert persisted.projection_status == "failed"
    assert "SQL ontology deletion failed" in (
        persisted.projection_error or ""
    )


def test_delete_ontology_accepts_ambiguous_commit_when_sql_row_is_absent(
    client,
    auth_headers,
    db,
    monkeypatch,
):
    from app.models.ontology import OntologyProject

    deleted: list[str] = []

    class FakeNeo4j:
        available = True

        def delete_by_ontology(self, ontology_id):
            deleted.append(ontology_id)
            return 0

        def close(self):
            return None

    monkeypatch.setattr(
        "app.services.v2.graph.neo4j_service.Neo4jService",
        FakeNeo4j,
    )
    created = client.post(
        "/api/v1/ontologies",
        json={"name": "Delete committed ambiguity", "domain": "财务"},
        headers=auth_headers,
    ).json()["data"]

    original_commit = db.commit
    commit_calls = 0

    def commit_then_report_failure():
        nonlocal commit_calls
        commit_calls += 1
        if commit_calls == 2:
            original_commit()
            raise RuntimeError("injected post-commit acknowledgement failure")
        return original_commit()

    monkeypatch.setattr(db, "commit", commit_then_report_failure)

    response = client.delete(
        f"/api/v1/ontologies/{created['id']}",
        headers=auth_headers,
    )

    assert response.status_code == 204
    assert deleted == [created["id"]]
    db.expire_all()
    assert db.get(OntologyProject, created["id"]) is None

def test_update_ontology(client, auth_headers):
    r = client.post("/api/v1/ontologies", json={"name": "Update", "domain": "医疗"}, headers=auth_headers)
    oid = r.json()["data"]["id"]
    r2 = client.put(f"/api/v1/ontologies/{oid}", json={"description": "updated desc"}, headers=auth_headers)
    assert r2.status_code == 200
    assert r2.json()["data"]["description"] == "updated desc"


def test_retired_simple_llm_build_mode_normalizes_on_create_and_update(
    client,
    auth_headers,
):
    created = client.post(
        "/api/v1/ontologies",
        json={
            "name": "旧客户端兼容本体",
            "domain": "供应链",
            "build_mode": "simple_llm",
        },
        headers=auth_headers,
    )
    assert created.status_code == 201, created.text
    ontology = created.json()["data"]
    assert ontology["build_mode"] == "manual"

    updated = client.put(
        f"/api/v1/ontologies/{ontology['id']}",
        json={"build_mode": " simple_llm "},
        headers=auth_headers,
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["data"]["build_mode"] == "manual"


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


def test_assistant_card_click_increments_and_lists(client, auth_headers):
    created = client.post(
        "/api/v1/ontologies",
        json={"name": "助手卡片计数", "domain": "供应链"},
        headers=auth_headers,
    ).json()["data"]

    listed = client.get("/api/v1/ontologies", headers=auth_headers)
    card = next(
        item for item in listed.json()["data"]["items"]
        if item["id"] == created["id"]
    )
    assert card["assistant_card_clicks"] == 0

    first = client.post(
        f"/api/v1/ontologies/{created['id']}/assistant-card-clicks",
        headers=auth_headers,
    )
    assert first.status_code == 200, first.text
    assert first.json()["data"] == {"id": created["id"], "assistant_card_clicks": 1}

    second = client.post(
        f"/api/v1/ontologies/{created['id']}/assistant-card-clicks",
        headers=auth_headers,
    )
    assert second.json()["data"]["assistant_card_clicks"] == 2

    listed = client.get("/api/v1/ontologies", headers=auth_headers)
    card = next(
        item for item in listed.json()["data"]["items"]
        if item["id"] == created["id"]
    )
    assert card["assistant_card_clicks"] == 2


def test_assistant_card_click_unknown_ontology_returns_404(client, auth_headers):
    r = client.post(
        "/api/v1/ontologies/not-exist/assistant-card-clicks",
        headers=auth_headers,
    )
    assert r.status_code == 404


def test_assistant_card_click_requires_auth(client):
    r = client.post("/api/v1/ontologies/not-exist/assistant-card-clicks")
    assert r.status_code == 403
