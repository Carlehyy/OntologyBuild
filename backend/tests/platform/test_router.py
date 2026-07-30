def test_stats_requires_auth(client):
    r = client.get("/api/v1/overview/stats")
    assert r.status_code == 403

def test_stats_returns_counts(client, auth_headers):
    r = client.get("/api/v1/overview/stats", headers=auth_headers)
    assert r.status_code == 200
    d = r.json()["data"]
    assert "ontology_count" in d
    assert "entity_count" in d
    assert "logic_count" in d
    assert "action_count" in d

def test_stats_counts_ontologies(client, auth_headers):
    client.post("/api/v1/ontologies", json={"name": "测试1", "domain": "供应链"}, headers=auth_headers)
    r = client.get("/api/v1/overview/stats", headers=auth_headers)
    assert r.json()["data"]["ontology_count"] == 1


def test_legacy_overview_router_matches_canonical_router():
    from app.platform.router import router as canonical_router
    from app.routers.overview import router as legacy_router

    assert legacy_router is canonical_router


def test_overview_openapi_contract_is_stable():
    from app.main import app

    operation = app.openapi()["paths"]["/api/v1/overview/stats"]["get"]
    assert operation["operationId"] == "get_stats_api_v1_overview_stats_get"
    assert operation["tags"] == ["overview"]
