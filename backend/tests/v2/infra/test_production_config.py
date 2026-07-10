"""Backward-compatible production configuration gates."""

import os
from pathlib import Path
import shutil
import stat
import subprocess

from app.shared.config import Settings, production_config_errors


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


def test_secret_key_derived_encryption_remains_decryptable(monkeypatch):
    from app.shared import encryption

    monkeypatch.setattr(encryption.settings, "encryption_key", "")
    monkeypatch.setattr(
        encryption.settings, "secret_key",
        "0123456789abcdef0123456789abcdef")
    ciphertext = encryption.encrypt("existing-connection-password")
    assert encryption.decrypt(ciphertext) == "existing-connection-password"


def _run_deploy_validation(app_dir: Path):
    env = os.environ.copy()
    env.update({
        "APP_DIR": str(app_dir),
        "SKIP_GIT": "1",
        "DEPLOY_VALIDATE_ONLY": "1",
        "STRICT_PRODUCTION_CONFIG": "0",
    })
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
    assert generated["STRICT_PRODUCTION_CONFIG"] == "false"
    assert generated["SECRET_KEY"] not in result.stdout
    assert stat.S_IMODE(generated_path.stat().st_mode) == 0o600


def test_existing_example_env_warns_but_does_not_block_deploy(tmp_path):
    shutil.copy(ROOT / ".env.example", tmp_path / ".env.example")
    shutil.copy(ROOT / ".env.example", tmp_path / ".env")

    result = _run_deploy_validation(tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "warning: SECRET_KEY" in result.stdout
    assert "production environment validation succeeded" in result.stdout
