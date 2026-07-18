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


def test_model_call_logs_support_pagination_and_filters(client, auth_headers, db):
    import datetime
    from app.model_configs.models import ModelCallLog

    cfg = client.post("/api/v1/models", json={
        "name": "Logs", "provider": "compatible", "models": ["model-a"],
    }, headers=auth_headers).json()["data"]
    base = datetime.datetime(2026, 7, 1, 0, 0, 0)
    statuses = ("success", "error", "timeout")
    for index in range(25):
        status = statuses[index % len(statuses)]
        db.add(ModelCallLog(
            id=f"log-{index:02d}",
            model_config_id=cfg["id"],
            model_name="model-a",
            provider="compatible",
            status=status,
            latency_ms=100 + index,
            error_message="401 api_key=sk-super-secret-value" if status == "error" else None,
            created_at=base + datetime.timedelta(minutes=index),
        ))
    db.commit()

    second_page = client.get(
        f"/api/v1/models/{cfg['id']}/calls?page=2&page_size=10",
        headers=auth_headers,
    )
    assert second_page.status_code == 200
    page_data = second_page.json()["data"]
    assert page_data["total"] == 25
    assert page_data["page"] == 2
    assert page_data["page_size"] == 10
    assert len(page_data["items"]) == 10
    assert page_data["items"][0]["id"] == "log-14"

    failed = client.get(
        f"/api/v1/models/{cfg['id']}/calls?status=error&page_size=100",
        headers=auth_headers,
    ).json()["data"]
    assert failed["total"] == 8
    assert all(item["status"] == "error" for item in failed["items"])
    assert all("sk-super" not in (item["error_summary"] or "") for item in failed["items"])
    assert all("[已隐藏]" in (item["error_summary"] or "") for item in failed["items"])

    ranged = client.get(
        f"/api/v1/models/{cfg['id']}/calls"
        "?start=2026-07-01T00:10:00Z&end=2026-07-01T00:12:00Z",
        headers=auth_headers,
    ).json()["data"]
    assert ranged["total"] == 3
    assert [item["id"] for item in ranged["items"]] == ["log-12", "log-11", "log-10"]


def test_model_call_log_filters_validate_status_and_time_range(client, auth_headers):
    cfg = client.post("/api/v1/models", json={
        "name": "Log validation", "provider": "compatible", "models": ["model-a"],
    }, headers=auth_headers).json()["data"]

    invalid_status = client.get(
        f"/api/v1/models/{cfg['id']}/calls?status=pending",
        headers=auth_headers,
    )
    invalid_range = client.get(
        f"/api/v1/models/{cfg['id']}/calls"
        "?start=2026-07-02T00:00:00Z&end=2026-07-01T00:00:00Z",
        headers=auth_headers,
    )
    assert invalid_status.status_code == 400
    assert invalid_range.status_code == 400


def test_model_call_timeout_errors_use_timeout_status():
    from app.ontologies.agent_runtime.llm_bridge import LLMError, _failure_status

    assert _failure_status(LLMError("request timed out after 30 seconds")) == "timeout"
    assert _failure_status(LLMError("authentication failed")) == "error"


def test_create_model_is_staged_disabled(client, auth_headers):
    r = client.post("/api/v1/models", json={
        "name": "GPT-4o", "provider": "openai", "api_key": "sk-test",
        "models": ["gpt-4o", "gpt-4o-mini"],
    }, headers=auth_headers)
    assert r.status_code == 201
    data = r.json()["data"]
    assert data["name"] == "GPT-4o"
    assert data["models"] == ["gpt-4o"]
    assert data["enabled"] is False
    assert data["is_default"] is False
    assert data["last_test_status"] is None
    assert data["created_at"].endswith("+00:00")
    assert data["updated_at"].endswith("+00:00")
    assert "api_key" not in data


def test_create_llm_requires_model_and_valid_api_base(client, auth_headers):
    missing_model = client.post("/api/v1/models", json={
        "name": "Broken", "provider": "compatible", "models": [],
    }, headers=auth_headers)
    assert missing_model.status_code == 422

    invalid_url = client.post("/api/v1/models", json={
        "name": "Broken URL", "provider": "compatible", "models": ["m"],
        "api_base": "not-a-url",
    }, headers=auth_headers)
    assert invalid_url.status_code == 422


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
    client.post("/api/v1/models", json={"name": "M1", "provider": "openai", "models": ["m1"]}, headers=auth_headers)
    r = client.get("/api/v1/models", headers=auth_headers)
    assert len(r.json()["data"]) >= 1


def test_update_model(client, auth_headers):
    r = client.post("/api/v1/models", json={"name": "Old", "provider": "openai", "models": ["m"]}, headers=auth_headers)
    mid = r.json()["data"]["id"]
    r2 = client.put(f"/api/v1/models/{mid}", json={"name": "New"}, headers=auth_headers)
    assert r2.json()["data"]["name"] == "New"


def test_delete_model(client, auth_headers):
    r = client.post("/api/v1/models", json={"name": "Del", "provider": "anthropic", "models": ["claude"]}, headers=auth_headers)
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
    from unittest.mock import patch

    first = client.post("/api/v1/models", json={
        "name": "DeepSeek Flash",
        "provider": "compatible",
        "api_base": "https://api.deepseek.com",
        "models": ["deepseek-v4-flash"],
    }, headers=auth_headers).json()["data"]
    assert first["enabled"] is False
    assert first["is_default"] is False

    second = client.post("/api/v1/models", json={
        "name": "DeepSeek Pro",
        "provider": "compatible",
        "api_base": "https://api.deepseek.com",
        "models": ["deepseek-v4-pro"],
    }, headers=auth_headers).json()["data"]

    for config in (first, second):
        with patch("app.ontologies.agent_runtime.llm_bridge.chat", return_value={"content": "PONG"}):
            tested = client.post(f"/api/v1/models/{config['id']}/test", headers=auth_headers)
        assert tested.json()["data"]["ok"] is True
        enabled = client.post(
            f"/api/v1/models/{config['id']}/enabled",
            json={"enabled": True}, headers=auth_headers,
        )
        assert enabled.status_code == 200

    defaulted = client.post(f"/api/v1/models/{second['id']}/default", headers=auth_headers)
    assert defaulted.status_code == 200
    assert defaulted.json()["data"]["is_default"] is True

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


def test_untested_model_cannot_be_enabled_or_defaulted(client, auth_headers):
    r = client.post("/api/v1/models", json={
        "name": "Untested LLM",
        "provider": "compatible",
        "models": ["deepseek-v4-flash"],
    }, headers=auth_headers)
    mid = r.json()["data"]["id"]

    enabled = client.post(f"/api/v1/models/{mid}/enabled", json={"enabled": True}, headers=auth_headers)
    defaulted = client.post(f"/api/v1/models/{mid}/default", headers=auth_headers)
    assert enabled.status_code == 409
    assert defaulted.status_code == 409


def test_disabled_model_can_be_tested_without_polluting_usage_stats(client, auth_headers, db):
    from unittest.mock import patch
    from app.model_configs.models import ModelCallLog, ModelConfig

    created = client.post("/api/v1/models", json={
        "name": "Staged LLM", "provider": "compatible", "api_key": "sk-test",
        "api_base": "https://example.com/v1", "models": ["model-a"],
    }, headers=auth_headers).json()["data"]

    with patch("app.ontologies.agent_runtime.llm_bridge.chat", return_value={"content": "PONG"}) as mocked_chat:
        tested = client.post(f"/api/v1/models/{created['id']}/test", headers=auth_headers)

    assert tested.status_code == 200
    assert tested.json()["data"]["ok"] is True
    call_kwargs = mocked_chat.call_args.args[0]
    assert "model_config_id" not in call_kwargs
    assert call_kwargs["max_output_tokens"] == 16
    assert call_kwargs["timeout_seconds"] == 30
    assert db.query(ModelCallLog).count() == 0
    db.expire_all()
    stored = db.query(ModelConfig).filter(ModelConfig.id == created["id"]).one()
    assert stored.last_test_status == "success"
    refreshed = client.get(f"/api/v1/models/{created['id']}", headers=auth_headers).json()["data"]
    assert refreshed["last_tested_at"].endswith("+00:00")


def test_failed_test_is_sanitized_and_persisted(client, auth_headers, db):
    from unittest.mock import patch
    from app.model_configs.models import ModelConfig
    from app.ontologies.agent_runtime.llm_bridge import LLMError

    created = client.post("/api/v1/models", json={
        "name": "Bad Key", "provider": "compatible", "models": ["model-a"],
    }, headers=auth_headers).json()["data"]
    with patch(
        "app.ontologies.agent_runtime.llm_bridge.chat",
        side_effect=LLMError("401 invalid api key sk-super-secret-value"),
    ):
        tested = client.post(f"/api/v1/models/{created['id']}/test", headers=auth_headers)

    result = tested.json()["data"]
    assert tested.status_code == 200
    assert result["ok"] is False
    assert result["code"] == "AUTH_FAILED"
    assert "sk-super" not in result["response"]
    db.expire_all()
    stored = db.query(ModelConfig).filter(ModelConfig.id == created["id"]).one()
    assert stored.last_test_status == "error"


def test_connection_edit_disables_model_and_requires_retest(client, auth_headers):
    from unittest.mock import patch

    created = client.post("/api/v1/models", json={
        "name": "Editable", "provider": "compatible", "models": ["model-a"],
    }, headers=auth_headers).json()["data"]
    with patch("app.ontologies.agent_runtime.llm_bridge.chat", return_value={"content": "PONG"}):
        client.post(f"/api/v1/models/{created['id']}/test", headers=auth_headers)
    client.post(f"/api/v1/models/{created['id']}/enabled", json={"enabled": True}, headers=auth_headers)

    updated = client.put(
        f"/api/v1/models/{created['id']}", json={"models": ["model-b"]}, headers=auth_headers,
    )
    data = updated.json()["data"]
    assert data["enabled"] is False
    assert data["last_test_status"] is None
    assert "重新测试" in data["last_test_message"]


def test_import_is_atomic_and_always_staged(client, auth_headers):
    result = client.post("/api/v1/models/import", json={"configs": [{
        "name": "Imported", "config_type": "llm", "provider": "compatible",
        "api_base": "https://example.com/v1", "models": ["model-a"],
        "enabled": True, "is_default": True,
    }]}, headers=auth_headers)
    assert result.status_code == 201
    imported = result.json()["data"]["configs"][0]
    assert imported["enabled"] is False
    assert imported["is_default"] is False
    assert imported["has_api_key"] is False

    duplicate_batch = client.post("/api/v1/models/import", json={"configs": [
        {"name": "Dup", "provider": "compatible", "models": ["a"]},
        {"name": "dup", "provider": "compatible", "models": ["b"]},
    ]}, headers=auth_headers)
    assert duplicate_batch.status_code == 409


def test_non_llm_cannot_be_default(client, auth_headers, db):
    from app.model_configs.models import ModelConfig

    ocr = client.post("/api/v1/models", json={
        "name": "OCR", "config_type": "ocr", "provider": "external_api",
        "api_base": "https://ocr.example.com",
    }, headers=auth_headers).json()["data"]
    stored = db.query(ModelConfig).filter(ModelConfig.id == ocr["id"]).one()
    stored.enabled = True
    stored.last_test_status = "success"
    db.commit()

    defaulted = client.post(f"/api/v1/models/{ocr['id']}/default", headers=auth_headers)
    assert defaulted.status_code == 409
