from types import SimpleNamespace

from app.data_channel.steward import query_service


def _service_stub():
    return SimpleNamespace(
        n8n_config_status=lambda _db: {
            "configured": False,
            "enabled": False,
            "api_url": "",
        },
    )


def test_steward_status_exposes_only_python_gateway_readiness(db, monkeypatch):
    monkeypatch.setattr(
        query_service.settings,
        "python_kernel_gateway_url",
        "http://python-kernel-gateway.internal:8088",
    )

    payload = query_service.steward_status(
        db,
        service_module=_service_stub(),
        select_llm_model_config_fn=lambda _db: None,
    )["data"]

    assert payload["python"] == {"configured": True}
    assert "url" not in payload["python"]


def test_steward_status_marks_blank_python_gateway_unconfigured(db, monkeypatch):
    monkeypatch.setattr(
        query_service.settings,
        "python_kernel_gateway_url",
        "   ",
    )

    payload = query_service.steward_status(
        db,
        service_module=_service_stub(),
        select_llm_model_config_fn=lambda _db: None,
    )["data"]

    assert payload["python"] == {"configured": False}
