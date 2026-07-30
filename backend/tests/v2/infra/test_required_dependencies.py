"""Fail-closed production dependency configuration regression tests."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import stat
import subprocess

import pytest

from app.shared.config import (
    Settings,
    production_config_errors,
    required_dependency_config_errors,
)


ROOT = Path(__file__).resolve().parents[4]
MATERIALIZER = ROOT / "scripts" / "ci" / "materialize-production-dependencies.sh"
MATERIALIZED_KEYS = {
    "ENVIRONMENT",
    "PUBLIC_PORT",
    "STRICT_PRODUCTION_CONFIG",
    "REQUIRE_EXTERNAL_DEPENDENCIES",
    "POSTGRES_HOST",
    "POSTGRES_PORT",
    "POSTGRES_DB",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "DATABASE_URL",
    "REDIS_URL",
    "DATASET_IMPORT_USE_CELERY",
    "NEO4J_URI",
    "NEO4J_USER",
    "NEO4J_PASSWORD",
    "NEO4J_AUTH",
    "MINIO_CONSOLE_URL",
    "MINIO_ENDPOINT",
    "MINIO_ACCESS_KEY",
    "MINIO_SECRET_KEY",
    "MINIO_USE_SSL",
    "STORAGE_LOCAL_FALLBACK",
    "N8N_API_URL",
    "N8N_EMAIL",
    "N8N_PASSWORD",
    "N8N_API_KEY",
}
MATERIALIZER_SOURCE_VALUES = {
    "PROD_PUBLIC_PORT": "8080",
    "PROD_POSTGRES_HOST": "pg.example.com",
    "PROD_POSTGRES_PORT": "5432",
    "PROD_POSTGRES_DB": "ontology",
    "PROD_POSTGRES_USER": "ontology",
    "PROD_POSTGRES_PASSWORD": "synthetic-postgres-password",
    "PROD_DATABASE_URL": (
        "postgresql://ontology:synthetic-postgres-password"
        "@pg.example.com:5432/ontology"
    ),
    "PROD_REDIS_URL": (
        "redis://:synthetic-redis-password@redis.example.com:6379/0"
    ),
    "PROD_NEO4J_URI": "bolt+s://graph.example.com:7687",
    "PROD_NEO4J_USER": "neo4j",
    "PROD_NEO4J_PASSWORD": "synthetic-neo4j-password",
    "PROD_NEO4J_AUTH": "neo4j/synthetic-neo4j-password",
    "PROD_MINIO_CONSOLE_URL": "https://objects.example.com",
    "PROD_MINIO_ENDPOINT": "objects.example.com:9000",
    "PROD_MINIO_ACCESS_KEY": "synthetic-minio-access",
    "PROD_MINIO_SECRET_KEY": "synthetic-minio-secret",
    "PROD_MINIO_USE_SSL": "true",
    "PROD_N8N_API_URL": "https://n8n.example.com/api/v1",
    "PROD_N8N_EMAIL": "automation@example.com",
    "PROD_N8N_PASSWORD": "synthetic-n8n-password",
    "PROD_N8N_API_KEY": "synthetic-n8n-api-key",
}


def _required_settings(**updates) -> Settings:
    values = {
        "environment": "production",
        "strict_production_config": True,
        "require_external_dependencies": True,
        "database_url": (
            "postgresql://ontology:strong-password@pg.example.com:5432/ontology"
        ),
        "redis_url": "redis://:strong-password@redis.example.com:6379/0",
        "dataset_import_use_celery": True,
        "secret_key": "0123456789abcdef0123456789abcdef",
        "cors_allowed_origins": "",
        "first_admin_password": "strong-admin-password",
        "neo4j_uri": "bolt://graph.example.com:7687",
        "neo4j_user": "neo4j",
        "neo4j_password": "strong-neo4j-password",
        "minio_endpoint": "objects.example.com:9000",
        "minio_access_key": "ontology-minio",
        "minio_secret_key": "strong-minio-password",
        "storage_local_fallback": False,
        "pipeline_file_public_app_base_url": "https://platform.example.com",
        "pipeline_file_public_api_base_url": "https://api.example.com",
        "allow_public_registration": False,
    }
    values.update(updates)
    return Settings(**values)


def _read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text().splitlines():
        if line and not line.lstrip().startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value
    return values


def _run_materializer(
    target: Path,
    overrides: dict[str, str | None] | None = None,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.update(MATERIALIZER_SOURCE_VALUES)
    # These values must never be caller-controlled, even if they are present.
    env.update({
        "PROD_STRICT_PRODUCTION_CONFIG": "false",
        "PROD_REQUIRE_EXTERNAL_DEPENDENCIES": "false",
        "PROD_DATASET_IMPORT_USE_CELERY": "false",
        "PROD_STORAGE_LOCAL_FALLBACK": "true",
    })
    for key, value in (overrides or {}).items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    return subprocess.run(
        ["bash", str(MATERIALIZER), str(target)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
    )


def test_required_external_dependency_mode_accepts_complete_configuration():
    assert production_config_errors(_required_settings()) == []


def test_required_dependency_gate_is_independent_from_legacy_app_secrets():
    current = _required_settings(
        strict_production_config=False,
        secret_key="dev-secret-key",
        first_admin_password="admin123",
    )

    assert required_dependency_config_errors(current) == []
    assert "SECRET_KEY" in production_config_errors(current)
    assert "FIRST_ADMIN_PASSWORD" in production_config_errors(current)


def test_required_mode_rejects_every_degraded_dependency_path():
    errors = production_config_errors(_required_settings(
        database_url="postgresql://app:password@db:5432/app",
        redis_url="redis://redis:6379/0",
        dataset_import_use_celery=False,
        neo4j_uri="bolt://neo4j:7687",
        minio_endpoint="minio:9001",
        storage_local_fallback=True,
        storage_local_dir="/uploads/object-storage",
    ))

    assert any("STORAGE_LOCAL_FALLBACK=false" in item for item in errors)
    assert any("DATASET_IMPORT_USE_CELERY=true" in item for item in errors)
    assert any("authenticated external PostgreSQL" in item for item in errors)
    assert any("authenticated external Redis" in item for item in errors)
    assert any("external Neo4j" in item for item in errors)
    assert any("S3 API endpoint" in item for item in errors)


def test_committed_manifest_template_declares_contract_without_secrets():
    template = ROOT / "production.dependencies.example.env"
    manifest = _read_env(template)

    assert set(manifest) == MATERIALIZED_KEYS
    assert manifest["ENVIRONMENT"] == "production"
    assert manifest["STRICT_PRODUCTION_CONFIG"] == "true"
    assert manifest["REQUIRE_EXTERNAL_DEPENDENCIES"] == "true"
    assert manifest["DATASET_IMPORT_USE_CELERY"] == "true"
    assert manifest["STORAGE_LOCAL_FALLBACK"] == "false"
    assert not manifest["MINIO_ENDPOINT"].endswith(":9001")
    secret_keys = {
        "POSTGRES_PASSWORD",
        "DATABASE_URL",
        "REDIS_URL",
        "NEO4J_PASSWORD",
        "NEO4J_AUTH",
        "MINIO_ACCESS_KEY",
        "MINIO_SECRET_KEY",
        "N8N_EMAIL",
        "N8N_PASSWORD",
        "N8N_API_KEY",
    }
    assert all(manifest[key] == "" for key in secret_keys)
    assert ".internal" not in template.read_text(encoding="utf-8")


def test_materializer_writes_exact_fail_closed_contract(tmp_path):
    target = tmp_path / "production.dependencies.env"

    result = _run_materializer(target)

    assert result.returncode == 0, result.stdout + result.stderr
    manifest = _read_env(target)
    assert set(manifest) == MATERIALIZED_KEYS
    assert manifest["ENVIRONMENT"] == "production"
    assert manifest["STRICT_PRODUCTION_CONFIG"] == "true"
    assert manifest["REQUIRE_EXTERNAL_DEPENDENCIES"] == "true"
    assert manifest["DATASET_IMPORT_USE_CELERY"] == "true"
    assert manifest["STORAGE_LOCAL_FALLBACK"] == "false"
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    output = result.stdout + result.stderr
    assert "synthetic-" not in output
    assert "automation@example.com" not in output


def test_materializer_rejects_each_missing_value_without_overwrite(tmp_path):
    for source_name in MATERIALIZER_SOURCE_VALUES:
        target = tmp_path / f"{source_name}.env"
        target.write_text("sentinel=unchanged\n", encoding="utf-8")

        result = _run_materializer(target, {source_name: None})

        assert result.returncode != 0
        assert target.read_text(encoding="utf-8") == "sentinel=unchanged\n"
        assert "synthetic-" not in result.stdout + result.stderr


def test_materializer_rejects_multiline_values_without_overwrite(tmp_path):
    target = tmp_path / "production.dependencies.env"
    target.write_text("sentinel=unchanged\n", encoding="utf-8")

    result = _run_materializer(
        target,
        {"PROD_POSTGRES_PASSWORD": "first-line\nsecond-line"},
    )

    assert result.returncode != 0
    assert target.read_text(encoding="utf-8") == "sentinel=unchanged\n"
    assert "first-line" not in result.stdout + result.stderr


@pytest.mark.parametrize(
    ("source_name", "value"),
    [
        ("PROD_PUBLIC_PORT", "0"),
        ("PROD_PUBLIC_PORT", "65536"),
        ("PROD_PUBLIC_PORT", "not-a-port"),
        ("PROD_POSTGRES_PORT", "0"),
        ("PROD_POSTGRES_PORT", "65536"),
        ("PROD_POSTGRES_PORT", "5432/tcp"),
        ("PROD_MINIO_USE_SSL", "TRUE"),
        ("PROD_MINIO_USE_SSL", "1"),
    ],
)
def test_materializer_validates_typed_values(
    tmp_path,
    source_name,
    value,
):
    target = tmp_path / "production.dependencies.env"

    result = _run_materializer(target, {source_name: value})

    assert result.returncode != 0
    assert not target.exists()


def test_deploy_merges_manifest_and_disables_local_fallback(tmp_path):
    shutil.copy(ROOT / ".env.example", tmp_path / ".env.example")
    manifest_path = tmp_path / "production.dependencies.env"
    materialized = _run_materializer(manifest_path)
    assert materialized.returncode == 0, materialized.stdout + materialized.stderr
    env = os.environ.copy()
    env.update({
        "APP_DIR": str(tmp_path),
        "SKIP_GIT": "1",
        "DEPLOY_VALIDATE_ONLY": "1",
        "HEALTH_URL": "https://platform.example.com/",
    })

    result = subprocess.run(
        ["bash", str(ROOT / "scripts" / "deploy-prod.sh")],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    generated = _read_env(tmp_path / ".env")
    manifest = _read_env(manifest_path)
    for key, value in manifest.items():
        assert generated[key] == value
    assert "applied" in result.stdout
    assert manifest["POSTGRES_PASSWORD"] not in result.stdout
    assert manifest["MINIO_SECRET_KEY"] not in result.stdout


def test_dependency_probe_never_logs_driver_error_details(monkeypatch, capsys):
    from app.shared import dependency_probe

    def fail_with_secret():
        raise RuntimeError("driver exposed should-never-appear")

    monkeypatch.setattr(
        dependency_probe.settings,
        "require_external_dependencies",
        True,
    )
    monkeypatch.setattr(
        dependency_probe,
        "PROBES",
        (("PostgreSQL", fail_with_secret),),
    )

    assert dependency_probe.main() == 1
    captured = capsys.readouterr()
    assert "PostgreSQL: unavailable (RuntimeError)" in captured.err
    assert "should-never-appear" not in captured.err


def test_required_mode_makes_environment_minio_authoritative(monkeypatch):
    from app.shared import storage

    sentinel = object()
    monkeypatch.setattr(
        storage.settings,
        "require_external_dependencies",
        True,
    )
    monkeypatch.setattr(
        storage,
        "get_environment_storage_service",
        lambda: sentinel,
    )
    monkeypatch.setattr(storage, "_storage_service", None)

    assert storage.get_storage_service() is sentinel
