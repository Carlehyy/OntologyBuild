def test_stats_last_call_is_utc_aware(client, auth_headers, db):
    """最近调用时间必须带 UTC 时区，避免前端把 naive 时间戳当本地时间(东八区差 8 小时)。"""
    import datetime
    from app.model_configs.models import ModelCallLog

    cfg = client.post("/api/v1/models", json={
        "name": "TZ", "provider": "compatible", "models": ["m"],
    }, headers=auth_headers).json()["data"]

    # 模拟 SQLite 读回的 naive UTC 时间戳
    db.add(ModelCallLog(
        id="tz-log-1", model_config_id=cfg["id"], model_name="m", provider="compatible",
        status="success", latency_ms=1200,
        created_at=datetime.datetime(2026, 7, 9, 17, 20, 13),
    ))
    db.commit()

    stats = client.get(f"/api/v1/models/{cfg['id']}/stats", headers=auth_headers).json()["data"]
    assert stats["lastCall"] == "2026-07-09T17:20:13+00:00"


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


def test_model_config_limits_to_single_model(client, auth_headers):
    # 创建时多填模型 —— 仅保留第一个，并去除首尾空白
    created = client.post("/api/v1/models", json={
        "name": "Multi", "provider": "compatible",
        "models": ["  deepseek-v4-flash  ", "deepseek-v4-pro", ""],
    }, headers=auth_headers).json()["data"]
    assert created["models"] == ["deepseek-v4-flash"]

    # 更新时多填模型 —— 同样仅保留第一个
    updated = client.put(f"/api/v1/models/{created['id']}", json={
        "models": ["a-model", "b-model"],
    }, headers=auth_headers).json()["data"]
    assert updated["models"] == ["a-model"]


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
