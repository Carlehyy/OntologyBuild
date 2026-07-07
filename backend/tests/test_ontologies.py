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
