def test_create_model(client, auth_headers):
    r = client.post("/api/v1/models",
                    json={"name": "GPT-4o", "provider": "openai", "api_key": "sk-test",
                          "models": ["gpt-4o", "gpt-4o-mini"]},
                    headers=auth_headers)
    assert r.status_code == 201
    d = r.json()["data"]
    assert d["name"] == "GPT-4o"
    assert d["enabled"] is True
    assert d["is_default"] is True
    assert "api_key" not in d  # key should not be returned


def test_list_models(client, auth_headers):
    client.post("/api/v1/models", json={"name": "M1", "provider": "openai", "models": []}, headers=auth_headers)
    r = client.get("/api/v1/models", headers=auth_headers)
    assert len(r.json()["data"]) >= 1


def test_update_model(client, auth_headers):
    r = client.post("/api/v1/models", json={"name": "Old", "provider": "openai", "models": []}, headers=auth_headers)
    mid = r.json()["data"]["id"]
    r2 = client.put(f"/api/v1/models/{mid}", json={"name": "New"}, headers=auth_headers)
    assert r2.json()["data"]["name"] == "New"


def test_delete_model(client, auth_headers):
    r = client.post("/api/v1/models", json={"name": "Del", "provider": "anthropic", "models": []}, headers=auth_headers)
    mid = r.json()["data"]["id"]
    r2 = client.delete(f"/api/v1/models/{mid}", headers=auth_headers)
    assert r2.status_code == 204


def test_create_easyocr_model_config(client, auth_headers):
    r = client.post("/api/v1/models", json={
        "name": "Local EasyOCR",
        "config_type": "ocr",
        "provider": "easyocr",
        "models": [],
        "options": {"enabled": False, "lang": "ch_sim,en", "device": "cpu"},
    }, headers=auth_headers)

    assert r.status_code == 201
    data = r.json()["data"]
    assert data["config_type"] == "ocr"
    assert data["provider"] == "easyocr"


def test_easyocr_model_test_reports_disabled(client, auth_headers):
    r = client.post("/api/v1/models", json={
        "name": "Local EasyOCR",
        "config_type": "ocr",
        "provider": "easyocr",
        "models": [],
        "options": {"enabled": False},
    }, headers=auth_headers)
    mid = r.json()["data"]["id"]

    test = client.post(f"/api/v1/models/{mid}/test", headers=auth_headers)

    assert test.status_code == 200
    assert test.json()["data"]["ok"] is False
    assert "EasyOCR" in test.json()["data"]["response"]


def test_model_state_fields_and_default_switch(client, auth_headers):
    first = client.post("/api/v1/models", json={
        "name": "DeepSeek Flash",
        "provider": "compatible",
        "api_base": "https://api.deepseek.com",
        "models": ["deepseek-v4-flash"],
    }, headers=auth_headers).json()["data"]
    assert first["enabled"] is True
    assert first["is_default"] is True

    second = client.post("/api/v1/models", json={
        "name": "DeepSeek Pro",
        "provider": "compatible",
        "api_base": "https://api.deepseek.com",
        "models": ["deepseek-v4-pro"],
        "is_default": True,
    }, headers=auth_headers).json()["data"]
    assert second["is_default"] is True

    listed = client.get("/api/v1/models", headers=auth_headers).json()["data"]
    defaults = [item for item in listed if item["is_default"]]
    assert len(defaults) == 1
    assert defaults[0]["id"] == second["id"]

    disabled = client.post(f"/api/v1/models/{second['id']}/enabled", json={"enabled": False}, headers=auth_headers)
    assert disabled.status_code == 200
    assert disabled.json()["data"]["enabled"] is False
    assert disabled.json()["data"]["is_default"] is False

    listed = client.get("/api/v1/models", headers=auth_headers).json()["data"]
    defaults = [item for item in listed if item["is_default"]]
    assert len(defaults) == 1
    assert defaults[0]["id"] == first["id"]


def test_disabled_model_test_reports_disabled(client, auth_headers):
    r = client.post("/api/v1/models", json={
        "name": "Disabled LLM",
        "provider": "compatible",
        "models": ["deepseek-v4-flash"],
        "enabled": False,
    }, headers=auth_headers)
    mid = r.json()["data"]["id"]

    test = client.post(f"/api/v1/models/{mid}/test", headers=auth_headers)

    assert test.status_code == 200
    assert test.json()["data"]["ok"] is False
    assert "disabled" in test.json()["data"]["response"]
