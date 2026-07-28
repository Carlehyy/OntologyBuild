"""Fail-closed production dependency configuration regression tests."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess

from app.shared.config import (
    Settings,
    production_config_errors,
    required_dependency_config_errors,
)


ROOT = Path(__file__).resolve().parents[4]


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


def test_committed_manifest_enables_required_mode_without_empty_values():
    manifest = _read_env(ROOT / "production.dependencies.env")
    required = {
        "ENVIRONMENT",
        "STRICT_PRODUCTION_CONFIG",
        "REQUIRE_EXTERNAL_DEPENDENCIES",
        "DATABASE_URL",
        "REDIS_URL",
        "DATASET_IMPORT_USE_CELERY",
        "NEO4J_URI",
        "NEO4J_USER",
        "NEO4J_PASSWORD",
        "MINIO_ENDPOINT",
        "MINIO_ACCESS_KEY",
        "MINIO_SECRET_KEY",
        "STORAGE_LOCAL_FALLBACK",
        "N8N_API_URL",
        "N8N_EMAIL",
        "N8N_PASSWORD",
        "N8N_API_KEY",
    }

    assert required <= manifest.keys()
    assert all(manifest[key] for key in required)
    assert manifest["ENVIRONMENT"] == "production"
    assert manifest["STRICT_PRODUCTION_CONFIG"] == "false"
    assert manifest["REQUIRE_EXTERNAL_DEPENDENCIES"] == "true"
    assert manifest["DATASET_IMPORT_USE_CELERY"] == "true"
    assert manifest["STORAGE_LOCAL_FALLBACK"] == "false"
    assert not manifest["MINIO_ENDPOINT"].endswith(":9001")


def test_deploy_merges_manifest_and_disables_local_fallback(tmp_path):
    shutil.copy(ROOT / ".env.example", tmp_path / ".env.example")
    shutil.copy(
        ROOT / "production.dependencies.env",
        tmp_path / "production.dependencies.env",
    )
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
    manifest = _read_env(tmp_path / "production.dependencies.env")
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
