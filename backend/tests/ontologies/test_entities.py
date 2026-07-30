def test_create_entity(client, auth_headers, ontology):
    oid = ontology["id"]
    r = client.post(f"/api/v1/ontologies/{oid}/entities",
                    json={"name_cn": "供应商", "name_en": "Supplier", "type": "组织"},
                    headers=auth_headers)
    assert r.status_code == 201
    assert r.json()["data"]["name_cn"] == "供应商"

def test_list_entities(client, auth_headers, ontology):
    oid = ontology["id"]
    client.post(f"/api/v1/ontologies/{oid}/entities",
                json={"name_cn": "供应商", "name_en": "Supplier"}, headers=auth_headers)
    r = client.get(f"/api/v1/ontologies/{oid}/entities", headers=auth_headers)
    assert r.status_code == 200
    assert len(r.json()["data"]) == 1

def test_update_entity(client, auth_headers, ontology):
    oid = ontology["id"]
    r = client.post(f"/api/v1/ontologies/{oid}/entities",
                    json={"name_cn": "原材料"}, headers=auth_headers)
    eid = r.json()["data"]["id"]
    r2 = client.put(f"/api/v1/ontologies/{oid}/entities/{eid}",
                    json={"description": "原材料实体"}, headers=auth_headers)
    assert r2.json()["data"]["description"] == "原材料实体"

def test_delete_entity(client, auth_headers, ontology):
    oid = ontology["id"]
    r = client.post(f"/api/v1/ontologies/{oid}/entities", json={"name_cn": "临时"}, headers=auth_headers)
    eid = r.json()["data"]["id"]
    r2 = client.delete(f"/api/v1/ontologies/{oid}/entities/{eid}", headers=auth_headers)
    assert r2.status_code == 204
