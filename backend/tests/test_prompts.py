def test_create_prompt(client, auth_headers):
    r = client.post("/api/v1/prompts",
                    json={"name": "测试提示词", "domain": "供应链", "content": "提取实体..."},
                    headers=auth_headers)
    assert r.status_code == 201
    assert r.json()["data"]["name"] == "测试提示词"

def test_list_prompts(client, auth_headers):
    client.post("/api/v1/prompts", json={"name": "P1", "domain": "供应链", "content": "content"}, headers=auth_headers)
    r = client.get("/api/v1/prompts", headers=auth_headers)
    assert r.status_code == 200
    assert len(r.json()["data"]) >= 1

def test_list_prompts_by_domain(client, auth_headers):
    client.post("/api/v1/prompts", json={"name": "SC", "domain": "供应链", "content": "c"}, headers=auth_headers)
    client.post("/api/v1/prompts", json={"name": "FIN", "domain": "财务", "content": "c"}, headers=auth_headers)
    r = client.get("/api/v1/prompts?domain=供应链", headers=auth_headers)
    assert all(p["domain"] == "供应链" for p in r.json()["data"])

def test_update_prompt(client, auth_headers):
    r = client.post("/api/v1/prompts", json={"name": "P", "domain": "医疗", "content": "old"}, headers=auth_headers)
    pid = r.json()["data"]["id"]
    r2 = client.put(f"/api/v1/prompts/{pid}", json={"content": "new content"}, headers=auth_headers)
    assert r2.json()["data"]["content"] == "new content"

def test_delete_prompt(client, auth_headers):
    r = client.post("/api/v1/prompts", json={"name": "Del", "domain": "其他", "content": "c"}, headers=auth_headers)
    pid = r.json()["data"]["id"]
    r2 = client.delete(f"/api/v1/prompts/{pid}", headers=auth_headers)
    assert r2.status_code == 204
