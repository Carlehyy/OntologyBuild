"""Backward-compatible production configuration gates."""

import os
from pathlib import Path
import shutil
import stat
import subprocess

import pytest
import yaml

from app.shared.config import Settings, production_config_errors
from app.settings.workflows.n8n_client import enforce_n8n_url_policy


ROOT = Path(__file__).resolve().parents[4]


def _production_settings(**updates):
    values = {
        "environment": "production",
        "database_url": "postgresql://app:strong-password@db:5432/app",
        "secret_key": "0123456789abcdef0123456789abcdef",
        "encryption_key": "",
        "cors_allowed_origins": "",
        "first_admin_password": "strong-admin-password",
        "neo4j_password": "strong-neo4j-password",
        "minio_access_key": "ontology-minio",
        "minio_secret_key": "strong-minio-password",
        "storage_local_fallback": False,
        "pipeline_file_public_app_base_url": "https://platform.example.com",
        "pipeline_file_public_api_base_url": "https://api.example.com",
        "allow_public_registration": False,
    }
    values.update(updates)
    return Settings(**values)


def test_existing_production_can_keep_secret_key_derived_encryption():
    assert production_config_errors(_production_settings()) == []


def test_production_strict_mode_is_opt_in_for_existing_installations():
    assert Settings().strict_production_config is False


def test_explicit_encryption_key_must_still_be_valid_fernet():
    errors = production_config_errors(
        _production_settings(encryption_key="not-a-fernet-key"))
    assert "ENCRYPTION_KEY must be a valid Fernet key" in errors


def test_wildcard_cors_remains_blocked_but_empty_is_same_origin():
    errors = production_config_errors(
        _production_settings(cors_allowed_origins="*"))
    assert "CORS_ALLOWED_ORIGINS" in errors


def test_production_rejects_non_public_or_malformed_file_link_origins():
    defaults = production_config_errors(_production_settings(
        pipeline_file_public_app_base_url="http://localhost:5173",
        pipeline_file_public_api_base_url="http://127.0.0.1:8000",
    ))
    malformed = production_config_errors(_production_settings(
        pipeline_file_public_app_base_url="https://user@example.com",
        pipeline_file_public_api_base_url="https://api.example.com/files?x=1",
    ))
    with_path = production_config_errors(_production_settings(
        pipeline_file_public_app_base_url="https://platform.example.com/app",
    ))

    assert sum("browser-reachable public host" in error
               for error in defaults) == 2
    assert any("APP_BASE_URL must be an absolute" in error
               for error in malformed)
    assert any("API_BASE_URL must be an absolute" in error
               for error in malformed)
    assert any("APP_BASE_URL must be an absolute" in error
               for error in with_path)


def test_production_allows_fallback_on_absolute_persistent_volume():
    errors = production_config_errors(_production_settings(
        storage_local_fallback=True,
        storage_local_dir="/uploads/object-storage",
    ))
    assert not any("STORAGE_LOCAL" in error for error in errors)


def test_production_rejects_relative_or_temporary_fallback_paths():
    relative = production_config_errors(_production_settings(
        storage_local_fallback=True,
        storage_local_dir="storage",
    ))
    temporary = production_config_errors(_production_settings(
        storage_local_fallback=True,
        storage_local_dir="/tmp/object-storage",
    ))
    assert any("absolute persistent path" in error for error in relative)
    assert any("persistent non-temporary volume" in error for error in temporary)


def test_production_compose_shares_fallback_without_minio_startup_dependency():
    compose = yaml.safe_load((ROOT / "docker-compose.prod.yml").read_text())
    backend = compose["services"]["backend"]
    worker = compose["services"]["celery_worker"]

    assert "minio" not in backend["depends_on"]
    assert backend["environment"]["STORAGE_LOCAL_FALLBACK"] == (
        "${STORAGE_LOCAL_FALLBACK:-true}")
    assert worker["environment"]["STORAGE_LOCAL_FALLBACK"] == (
        "${STORAGE_LOCAL_FALLBACK:-true}")
    assert backend["environment"]["STORAGE_LOCAL_DIR"] == "/uploads/object-storage"
    assert worker["environment"]["STORAGE_LOCAL_DIR"] == "/uploads/object-storage"
    assert "PIPELINE_FILE_PUBLIC_APP_BASE_URL" in backend["environment"]
    assert "PIPELINE_FILE_PUBLIC_API_BASE_URL" in backend["environment"]
    assert "PIPELINE_FILE_PUBLIC_APP_BASE_URL" in worker["environment"]
    assert "PIPELINE_FILE_PUBLIC_API_BASE_URL" in worker["environment"]
    assert "uploads:/uploads" in backend["volumes"]
    assert "uploads:/uploads" in worker["volumes"]


def test_public_plain_http_n8n_is_rejected_in_production():
    with pytest.raises(ValueError, match="必须使用 HTTPS"):
        enforce_n8n_url_policy(
            "http://n8n.example.com:5678/api/v1", environment="production")


def test_private_http_and_public_https_n8n_are_allowed_in_production():
    assert enforce_n8n_url_policy(
        "http://10.0.0.8:5678", environment="production"
    ) == "http://10.0.0.8:5678/api/v1"
    assert enforce_n8n_url_policy(
        "https://n8n.example.com/api/v1", environment="production"
    ) == "https://n8n.example.com/api/v1"


def test_n8n_global_config_test_requires_admin(client, editor_user):
    login = client.post(
        "/api/v1/auth/login",
        json={"username": "editor", "password": "editor123"},
    )
    token = login.json()["data"]["access_token"]
    response = client.post(
        "/api/v1/settings/workflow-config/test",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "enabled": True,
            "api_url": "http://127.0.0.1:5678/api/v1",
            "api_key": "not-sent-because-authz-runs-first",
            "timeout_seconds": 1,
        },
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "Admin required"


def test_secret_key_derived_encryption_remains_decryptable(monkeypatch):
    from app.shared import encryption

    monkeypatch.setattr(encryption.settings, "encryption_key", "")
    monkeypatch.setattr(
        encryption.settings, "secret_key",
        "0123456789abcdef0123456789abcdef")
    ciphertext = encryption.encrypt("existing-connection-password")
    assert encryption.decrypt(ciphertext) == "existing-connection-password"


def _run_deploy_validation(app_dir: Path, *, health_url: str | None = None):
    env = os.environ.copy()
    env.update({
        "APP_DIR": str(app_dir),
        "SKIP_GIT": "1",
        "DEPLOY_VALIDATE_ONLY": "1",
        "STRICT_PRODUCTION_CONFIG": "0",
    })
    if health_url is not None:
        env["HEALTH_URL"] = health_url
    return subprocess.run(
        ["bash", str(ROOT / "scripts" / "deploy-prod.sh")],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=20,
    )


def _read_env(path: Path) -> dict[str, str]:
    values = {}
    for line in path.read_text().splitlines():
        if line and not line.lstrip().startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value
    return values


def test_deploy_bootstraps_server_env_without_more_github_secrets(tmp_path):
    shutil.copy(ROOT / ".env.example", tmp_path / ".env.example")

    result = _run_deploy_validation(tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    generated_path = tmp_path / ".env"
    generated = _read_env(generated_path)
    assert generated["ENVIRONMENT"] == "production"
    assert len(generated["SECRET_KEY"]) == 64
    assert len(generated["FIRST_ADMIN_PASSWORD"]) == 48
    assert generated["POSTGRES_PASSWORD"] in generated["DATABASE_URL"]
    assert generated["NEO4J_AUTH"] == f"neo4j/{generated['NEO4J_PASSWORD']}"
    assert generated["MINIO_ACCESS_KEY"] != "minioadmin"
    assert generated["MINIO_SECRET_KEY"] != "minioadmin"
    assert generated["STORAGE_LOCAL_FALLBACK"] == "true"
    assert generated["STORAGE_LOCAL_DIR"] == "/uploads/object-storage"
    assert generated["STRICT_PRODUCTION_CONFIG"] == "false"
    assert generated["PIPELINE_FILE_GATEWAY_BASE_URL"] == (
        "http://127.0.0.1:80/api/v2/file-transfer")
    assert generated["PIPELINE_FILE_PUBLIC_APP_BASE_URL"] == (
        "http://127.0.0.1:80")
    assert generated["PIPELINE_FILE_PUBLIC_API_BASE_URL"] == (
        "http://127.0.0.1:80")
    assert generated["SECRET_KEY"] not in result.stdout
    assert stat.S_IMODE(generated_path.stat().st_mode) == 0o600


def test_existing_example_env_warns_but_does_not_block_deploy(tmp_path):
    shutil.copy(ROOT / ".env.example", tmp_path / ".env.example")
    shutil.copy(ROOT / ".env.example", tmp_path / ".env")

    result = _run_deploy_validation(tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "warning: SECRET_KEY" in result.stdout
    assert "production environment validation succeeded" in result.stdout
    generated = _read_env(tmp_path / ".env")
    assert generated["STORAGE_LOCAL_FALLBACK"] == "true"
    assert generated["STORAGE_LOCAL_DIR"] == "/uploads/object-storage"
    assert generated["PIPELINE_FILE_GATEWAY_BASE_URL"] == (
        "http://127.0.0.1:80/api/v2/file-transfer")
    assert generated["PIPELINE_FILE_PUBLIC_APP_BASE_URL"] == (
        "http://127.0.0.1:80")
    assert generated["PIPELINE_FILE_PUBLIC_API_BASE_URL"] == (
        "http://127.0.0.1:80")


def test_deploy_derives_pipeline_file_gateway_from_external_health_url(tmp_path):
    shutil.copy(ROOT / ".env.example", tmp_path / ".env.example")
    shutil.copy(ROOT / ".env.example", tmp_path / ".env")

    result = _run_deploy_validation(
        tmp_path, health_url="https://platform.example.com/")

    assert result.returncode == 0, result.stdout + result.stderr
    generated = _read_env(tmp_path / ".env")
    assert generated["PIPELINE_FILE_GATEWAY_BASE_URL"] == (
        "https://platform.example.com/api/v2/file-transfer")
    assert generated["PIPELINE_FILE_PUBLIC_APP_BASE_URL"] == (
        "https://platform.example.com")
    assert generated["PIPELINE_FILE_PUBLIC_API_BASE_URL"] == (
        "https://platform.example.com")


def test_deploy_preserves_explicit_pipeline_file_gateway(tmp_path):
    content = (ROOT / ".env.example").read_text().replace(
        "PIPELINE_FILE_GATEWAY_BASE_URL=http://backend:8000/api/v2/file-transfer",
        "PIPELINE_FILE_GATEWAY_BASE_URL=https://platform.example.com/api/v2/file-transfer",
    )
    (tmp_path / ".env.example").write_text(content)
    (tmp_path / ".env").write_text(content)

    result = _run_deploy_validation(tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    generated = _read_env(tmp_path / ".env")
    assert generated["PIPELINE_FILE_GATEWAY_BASE_URL"] == (
        "https://platform.example.com/api/v2/file-transfer")


def test_deploy_preserves_explicit_public_file_origins(tmp_path):
    content = (ROOT / ".env.example").read_text()
    content = content.replace(
        "PIPELINE_FILE_PUBLIC_APP_BASE_URL=http://localhost:5173",
        "PIPELINE_FILE_PUBLIC_APP_BASE_URL=https://app.example.com",
    ).replace(
        "PIPELINE_FILE_PUBLIC_API_BASE_URL=http://localhost:8000",
        "PIPELINE_FILE_PUBLIC_API_BASE_URL=https://files.example.com",
    )
    (tmp_path / ".env.example").write_text(content)
    (tmp_path / ".env").write_text(content)

    result = _run_deploy_validation(
        tmp_path, health_url="https://platform.example.com/")

    assert result.returncode == 0, result.stdout + result.stderr
    generated = _read_env(tmp_path / ".env")
    assert generated["PIPELINE_FILE_PUBLIC_APP_BASE_URL"] == (
        "https://app.example.com")
    assert generated["PIPELINE_FILE_PUBLIC_API_BASE_URL"] == (
        "https://files.example.com")


def test_deploy_rejects_malformed_public_file_origin(tmp_path):
    content = (ROOT / ".env.example").read_text().replace(
        "PIPELINE_FILE_PUBLIC_API_BASE_URL=http://localhost:8000",
        "PIPELINE_FILE_PUBLIC_API_BASE_URL=https://user@example.com/files?x=1",
    )
    (tmp_path / ".env.example").write_text(content)
    (tmp_path / ".env").write_text(content)

    result = _run_deploy_validation(tmp_path)

    assert result.returncode != 0
    assert "PIPELINE_FILE_PUBLIC_API_BASE_URL must be an absolute" in result.stdout


def test_deploy_rejects_custom_relative_storage_fallback_path(tmp_path):
    content = (ROOT / ".env.example").read_text().replace(
        "STORAGE_LOCAL_DIR=storage",
        "STORAGE_LOCAL_DIR=relative/object-storage",
    )
    (tmp_path / ".env.example").write_text(content)
    (tmp_path / ".env").write_text(content)

    result = _run_deploy_validation(tmp_path)

    assert result.returncode != 0
    assert "STORAGE_LOCAL_DIR must be an absolute persistent path" in result.stdout


def test_deploy_normalizes_custom_absolute_storage_path_to_shared_volume(tmp_path):
    content = (ROOT / ".env.example").read_text().replace(
        "STORAGE_LOCAL_DIR=storage",
        "STORAGE_LOCAL_DIR=/var/lib/private-object-storage",
    )
    (tmp_path / ".env.example").write_text(content)
    (tmp_path / ".env").write_text(content)

    result = _run_deploy_validation(tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    assert _read_env(tmp_path / ".env")["STORAGE_LOCAL_DIR"] == (
        "/uploads/object-storage")
