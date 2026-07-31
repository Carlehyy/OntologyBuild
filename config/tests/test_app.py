from __future__ import annotations

from pathlib import Path

import pytest
import uvicorn
from fastapi.testclient import TestClient

import app.main as main_module
from app.main import (
    AVAILABLE_SERVICES,
    DEFAULT_CONFIG_PORT,
    OPTIONAL_SERVICES,
    REQUIRED_SERVICES,
    create_app,
)
from app.models import ConfigProfile
from app.probes import ProbeResult


def _complete_payload(payload: dict) -> dict:
    payload["postgres"]["password"] = "postgres-test-password"
    payload["redis"]["password"] = "redis-test-password"
    payload["neo4j"]["password"] = "neo4j-test-password"
    payload["minio"]["access_key"] = "minio-test-access"
    payload["minio"]["secret_key"] = "minio-test-password"
    payload["n8n"]["api_key"] = "n8n-test-key"
    payload["llm"]["api_key"] = "llm-test-key"
    return payload


def _client(tmp_path: Path) -> tuple[TestClient, Path]:
    static_root = tmp_path / "static"
    static_root.mkdir()
    (static_root / "index.html").write_text("<!doctype html><title>test</title>")
    env_path = tmp_path / "generated" / ".env"
    app = create_app(
        env_path=env_path,
        project_root=tmp_path,
        static_root=static_root,
    )
    return (
        TestClient(
            app,
            base_url="http://127.0.0.1",
            headers={
                "x-config-access-token": (
                    app.state.config_center.access_token
                )
            },
        ),
        env_path,
    )


def test_config_center_default_port_avoids_platform_service_ports() -> None:
    assert DEFAULT_CONFIG_PORT == 8888


def test_run_starts_config_center_on_default_port(
    monkeypatch,
) -> None:
    seen = {}
    monkeypatch.delenv("OPENONTOLOGY_CONFIG_PORT", raising=False)
    monkeypatch.setenv("OPENONTOLOGY_CONFIG_NO_BROWSER", "1")
    monkeypatch.setattr(main_module, "_port_is_free", lambda *_args: True)
    monkeypatch.setattr(
        uvicorn,
        "run",
        lambda _app, **kwargs: seen.update(kwargs),
    )

    main_module.run()

    assert seen["host"] == "127.0.0.1"
    assert seen["port"] == 8888


def test_env_target_cannot_escape_project_root(tmp_path: Path) -> None:
    static_root = tmp_path / "static"
    static_root.mkdir()
    (static_root / "index.html").write_text("<!doctype html>")

    with pytest.raises(RuntimeError, match="项目目录内"):
        create_app(
            env_path=tmp_path.parent / "outside.env",
            project_root=tmp_path,
            static_root=static_root,
        )


def test_local_guard_blocks_bad_host_origin_and_missing_csrf(
    tmp_path: Path,
) -> None:
    client, _ = _client(tmp_path)
    bootstrap = client.get("/api/bootstrap").json()
    profile = _complete_payload(bootstrap["profile"])

    assert client.get("/favicon.ico").status_code == 204
    assert client.get("/", headers={"host": "attacker.example"}).status_code == 400
    assert client.get("/", headers={"host": "testserver"}).status_code == 400
    assert (
        client.get(
            "/api/bootstrap",
            headers={"x-config-access-token": "wrong-token"},
        ).status_code
        == 403
    )
    assert client.post("/api/test/postgres", json=profile).status_code == 403
    assert (
        client.post(
            "/api/test/postgres",
            json=profile,
            headers={
                "x-csrf-token": bootstrap["csrf_token"],
                "origin": "https://attacker.example",
            },
        ).status_code
        == 403
    )


def test_only_required_successful_probes_gate_atomic_generation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client, env_path = _client(tmp_path)
    bootstrap = client.get("/api/bootstrap").json()
    profile = _complete_payload(bootstrap["profile"])
    profile["llm"]["api_key"] = ""
    csrf = bootstrap["csrf_token"]
    assert bootstrap["required_services"] == list(REQUIRED_SERVICES)
    assert bootstrap["optional_services"] == list(OPTIONAL_SERVICES)
    assert set(AVAILABLE_SERVICES) == set(REQUIRED_SERVICES) | set(OPTIONAL_SERVICES)

    monkeypatch.setattr(
        main_module,
        "run_probe",
        lambda service, _profile: ProbeResult(
            True,
            f"{service} ok",
            "mocked",
            1,
        ),
    )

    early = client.post(
        "/api/generate",
        json=profile,
        headers={"x-csrf-token": csrf},
    )
    assert early.status_code == 409
    assert set(early.json()["detail"]["missing_services"]) == set(REQUIRED_SERVICES)

    for service in REQUIRED_SERVICES:
        response = client.post(
            f"/api/test/{service}",
            json=profile,
            headers={"x-csrf-token": csrf},
        )
        assert response.status_code == 200
        assert response.json()["ok"] is True

    generated = client.post(
        "/api/generate",
        json=profile,
        headers={"x-csrf-token": csrf},
    )
    assert generated.status_code == 200
    assert generated.json()["ok"] is True
    assert env_path.is_file()


def test_optional_probes_remain_available_without_gating_generation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client, _ = _client(tmp_path)
    bootstrap = client.get("/api/bootstrap").json()
    profile = _complete_payload(bootstrap["profile"])
    csrf = bootstrap["csrf_token"]
    monkeypatch.setattr(
        main_module,
        "run_probe",
        lambda service, _profile: ProbeResult(True, service, "mocked", 1),
    )

    for service in OPTIONAL_SERVICES:
        response = client.post(
            f"/api/test/{service}",
            json=profile,
            headers={"x-csrf-token": csrf},
        )
        assert response.status_code == 200
        assert response.json()["ok"] is True

    assert (
        client.post(
            "/api/test/not-a-service",
            json=profile,
            headers={"x-csrf-token": csrf},
        ).status_code
        == 404
    )


def test_only_changed_service_invalidates_its_probe_receipt(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client, _ = _client(tmp_path)
    bootstrap = client.get("/api/bootstrap").json()
    profile = _complete_payload(bootstrap["profile"])
    csrf = bootstrap["csrf_token"]
    monkeypatch.setattr(
        main_module,
        "run_probe",
        lambda service, _profile: ProbeResult(True, "ok", "mocked", 1),
    )

    for service in REQUIRED_SERVICES:
        response = client.post(
            f"/api/test/{service}",
            json=profile,
            headers={"x-csrf-token": csrf},
        )
        assert response.status_code == 200

    changed = ConfigProfile.model_validate(profile).model_dump()
    changed["postgres"]["host"] = "127.0.0.2"
    response = client.post(
        "/api/generate",
        json=changed,
        headers={"x-csrf-token": csrf},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["missing_services"] == ["postgres"]


def test_existing_secret_is_not_returned_to_browser(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    first = client.get("/api/bootstrap").json()
    profile = _complete_payload(first["profile"])
    app_state = client.app.state.config_center
    app_state.store.write(ConfigProfile.model_validate(profile))

    second = client.get("/api/bootstrap").json()

    assert second["has_config"] is True
    assert second["profile"]["postgres"]["password"] == ""
    assert second["profile"]["llm"]["api_key"] == ""
    assert second["secrets_present"]["postgres.password"] is True
    assert second["secrets_present"]["llm.api_key"] is True


def test_validation_error_never_echoes_submitted_secrets(
    tmp_path: Path,
) -> None:
    client, _ = _client(tmp_path)
    bootstrap = client.get("/api/bootstrap").json()
    profile = _complete_payload(bootstrap["profile"])
    marker = "must-not-appear-in-validation-response"
    profile["postgres"]["password"] = marker
    profile["platform"]["frontend_port"] = profile["platform"]["backend_port"]

    response = client.post(
        "/api/test/postgres",
        json=profile,
        headers={"x-csrf-token": bootstrap["csrf_token"]},
    )

    assert response.status_code == 422
    assert marker not in response.text
    assert "input" not in response.text


def test_invalid_existing_env_can_be_repaired(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client, env_path = _client(tmp_path)
    env_path.parent.mkdir(parents=True, exist_ok=True)
    env_path.write_text(
        'LOCAL_BACKEND_HOST="0.0.0.0"\n'
        "DATABASE_URL="
        '"postgresql://user:old-secret-must-not-leak@127.0.0.1/db"\n',
        encoding="utf-8",
    )

    bootstrap = client.get("/api/bootstrap")

    assert bootstrap.status_code == 200
    payload = bootstrap.json()
    assert payload["has_config"] is False
    assert payload["config_file_exists"] is True
    assert payload["config_warning"]
    assert "old-secret-must-not-leak" not in bootstrap.text
    assert not any(payload["secrets_present"].values())

    profile = _complete_payload(payload["profile"])
    csrf = payload["csrf_token"]
    monkeypatch.setattr(
        main_module,
        "run_probe",
        lambda service, _profile: ProbeResult(True, service, "mocked", 1),
    )
    for service in REQUIRED_SERVICES:
        response = client.post(
            f"/api/test/{service}",
            json=profile,
            headers={"x-csrf-token": csrf},
        )
        assert response.status_code == 200

    generated = client.post(
        "/api/generate",
        json=profile,
        headers={"x-csrf-token": csrf},
    )
    assert generated.status_code == 200
    assert generated.json()["backup_created"] is True
    assert "old-secret-must-not-leak" not in env_path.read_text(
        encoding="utf-8"
    )


def test_runtime_check_accepts_optional_chroma_outage(monkeypatch) -> None:
    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"status": "degraded", "unavailable": ["chroma"]}

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def get(self, _url: str) -> FakeResponse:
            return FakeResponse()

    monkeypatch.setattr(main_module.httpx, "Client", FakeClient)

    result = main_module._check_backend_runtime(
        "http://127.0.0.1:8000/health/ready"
    )

    assert result["ok"] is True
    assert "chroma" in result["detail"]
