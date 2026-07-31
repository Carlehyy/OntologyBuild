from __future__ import annotations

from pathlib import Path

import pytest

from app.config import Settings
from app.shared.config import (
    production_config_errors,
    required_dependency_config_errors,
)
from app.models.model_config import ModelConfig
from app.models.workflow_config import WorkflowConfig
from app.services.encryption_service import decrypt
from app.services.local_config_sync import (
    MANAGED_LLM_ID,
    sync_local_managed_runtime_config,
)
from app.shared import env_files


def test_settings_central_env_overrides_legacy_but_not_process_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    legacy = tmp_path / "legacy.env"
    central = tmp_path / "central.env"
    legacy.write_text(
        "DATABASE_URL=postgresql://legacy:legacy@legacy-db/legacy\n"
        "REDIS_URL=redis://legacy-redis:6379/0\n"
        "LOCAL_BACKEND_PORT=8100\n",
        encoding="utf-8",
    )
    central.write_text(
        "# 集中配置文件允许中文注释\n"
        "DATABASE_URL=postgresql://central:central@central-db/central\n"
        "REDIS_URL=redis://central-redis:6379/0\n"
        "LOCAL_BACKEND_PORT=8200\n"
        "API_HUB_SYSTEM_MCP_TOKEN=extra-fields-must-not-break-settings\n",
        encoding="utf-8",
    )
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://process:process@process-db/process",
    )

    configured = Settings(_env_file=(legacy, central))

    assert configured.database_url == (
        "postgresql://process:process@process-db/process"
    )
    assert configured.redis_url == "redis://central-redis:6379/0"
    assert configured.local_backend_port == 8200
    assert "database_url" in configured.model_fields_set
    assert "redis_url" in configured.model_fields_set


def test_clean_checkout_production_uses_process_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    missing_legacy = tmp_path / "missing-legacy.env"
    missing_central = tmp_path / "missing-central.env"
    process_values = {
        "ENVIRONMENT": "production",
        "DATABASE_URL": (
            "postgresql://ontology:strong-password@postgres.example.test:5432/"
            "ontology"
        ),
        "REDIS_URL": (
            "rediss://:strong-password@redis.example.test:6380/0"
        ),
        "DATASET_IMPORT_USE_CELERY": "true",
        "SECRET_KEY": "a-secure-production-secret-key-over-32-characters",
        "FIRST_ADMIN_PASSWORD": "a-secure-admin-password",
        "CORS_ALLOWED_ORIGINS": "",
        "REQUIRE_EXTERNAL_DEPENDENCIES": "true",
        "NEO4J_URI": "neo4j+s://neo4j.example.test:7687",
        "NEO4J_USER": "neo4j-app",
        "NEO4J_PASSWORD": "a-secure-neo4j-password",
        "MINIO_ENDPOINT": "minio.example.test:9000",
        "MINIO_ACCESS_KEY": "ontology-minio",
        "MINIO_SECRET_KEY": "a-secure-minio-password",
        "STORAGE_LOCAL_FALLBACK": "false",
        "PIPELINE_FILE_PUBLIC_APP_BASE_URL": "https://app.example.test",
        "PIPELINE_FILE_PUBLIC_API_BASE_URL": "https://api.example.test",
        "ALLOW_PUBLIC_REGISTRATION": "false",
    }
    for key, value in process_values.items():
        monkeypatch.setenv(key, value)

    configured = Settings(
        _env_file=(missing_legacy, missing_central),
    )

    assert configured.environment == "production"
    assert configured.database_url == process_values["DATABASE_URL"]
    assert "database_url" in configured.model_fields_set
    assert required_dependency_config_errors(configured) == []
    assert production_config_errors(configured) == []


def test_environment_paths_are_repository_anchored():
    backend = Path(__file__).resolve().parents[2]
    repository = backend.parent

    assert env_files.BACKEND_DIR == backend
    assert env_files.LEGACY_BACKEND_ENV_FILE == backend / ".env"
    assert env_files.LOCAL_CONFIG_ENV_FILE == (
        repository / "config" / "generated" / "local" / ".env"
    )


def test_api_hub_dotenv_loader_preserves_process_priority(
    monkeypatch: pytest.MonkeyPatch,
):
    calls: list[tuple[Path, str, bool]] = []

    def fake_load(path, *, encoding, override):
        calls.append((path, encoding, override))

    monkeypatch.setattr(env_files, "load_dotenv", fake_load)
    env_files.load_backend_dotenv()

    assert calls == [
        (env_files.LOCAL_CONFIG_ENV_FILE, "utf-8", False),
        (env_files.LEGACY_BACKEND_ENV_FILE, "utf-8", False),
    ]


def test_api_hub_local_backend_port_precedes_deploy_port(
    monkeypatch: pytest.MonkeyPatch,
):
    from app.api_hub import config as api_hub_config

    monkeypatch.setenv("LOCAL_BACKEND_HOST", "127.0.0.1")
    monkeypatch.setenv("LOCAL_BACKEND_PORT", "8123")
    monkeypatch.setenv("DEPLOY_RUN_PORT", "5173")
    monkeypatch.setenv("APP_PORT", "9000")

    assert api_hub_config._app_host_and_port() == ("127.0.0.1", 8123)


def test_api_hub_relative_data_dir_is_anchored_to_backend():
    from app.api_hub import config as api_hub_config

    assert api_hub_config._resolve_data_dir("./runtime/api-hub") == (
        env_files.BACKEND_DIR / "runtime" / "api-hub"
    ).resolve()


def _managed_settings(**overrides) -> Settings:
    values = {
        "environment": "development",
        "local_config_managed": True,
        "local_n8n_api_url": "http://127.0.0.1:5678",
        "local_n8n_api_key": "n8n-secret",
        "local_n8n_timeout_seconds": 12,
        "local_llm_name": "本地默认模型",
        "local_llm_provider": "compatible",
        "local_llm_api_base": "http://127.0.0.1:4000/v1",
        "local_llm_api_key": "llm-secret",
        "local_llm_model": "local-model",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_managed_runtime_config_is_idempotent_and_encrypts_secrets(
    db,
    admin_user,
):
    current = _managed_settings()
    existing_default = ModelConfig(
        id="existing-default",
        name="原默认模型",
        config_type="llm",
        provider="compatible",
        api_base="http://127.0.0.1:4100/v1",
        api_key_encrypted="",
        models=["old-model"],
        options={},
        enabled=True,
        is_default=True,
        created_by=admin_user.id,
    )
    db.add(existing_default)
    db.commit()

    assert sync_local_managed_runtime_config(db, current) is True
    db.commit()

    workflow = db.query(WorkflowConfig).filter_by(id="default").one()
    managed_llm = db.query(ModelConfig).filter_by(id=MANAGED_LLM_ID).one()
    workflow_ciphertext = workflow.api_key_encrypted
    llm_ciphertext = managed_llm.api_key_encrypted

    assert workflow.enabled is True
    assert workflow.api_url == "http://127.0.0.1:5678/api/v1"
    assert workflow.timeout_seconds == 12
    assert workflow.api_key_encrypted != "n8n-secret"
    assert decrypt(workflow.api_key_encrypted) == "n8n-secret"
    assert managed_llm.enabled is True
    assert managed_llm.is_default is True
    assert managed_llm.models == ["local-model"]
    assert managed_llm.api_key_encrypted != "llm-secret"
    assert decrypt(managed_llm.api_key_encrypted) == "llm-secret"
    db.refresh(existing_default)
    assert existing_default.is_default is False

    assert sync_local_managed_runtime_config(db, current) is True
    db.commit()
    db.expire_all()

    assert db.query(WorkflowConfig).count() == 1
    assert db.query(ModelConfig).filter_by(id=MANAGED_LLM_ID).count() == 1
    assert (
        db.query(WorkflowConfig).filter_by(id="default").one().api_key_encrypted
        == workflow_ciphertext
    )
    assert (
        db.query(ModelConfig)
        .filter_by(id=MANAGED_LLM_ID)
        .one()
        .api_key_encrypted
        == llm_ciphertext
    )


def test_managed_runtime_config_never_runs_in_production(db):
    current = _managed_settings(
        environment="production",
        local_n8n_api_key="",
        local_llm_api_key="",
    )

    assert sync_local_managed_runtime_config(db, current) is False
    assert db.query(WorkflowConfig).count() == 0
    assert db.query(ModelConfig).filter_by(id=MANAGED_LLM_ID).count() == 0


def test_managed_runtime_config_allows_optional_llm_to_be_omitted(db):
    current = _managed_settings(local_llm_api_key="")

    assert sync_local_managed_runtime_config(db, current) is True
    db.commit()

    workflow = db.query(WorkflowConfig).filter_by(id="default").one()
    assert workflow.enabled is True
    assert decrypt(workflow.api_key_encrypted) == "n8n-secret"
    assert db.query(ModelConfig).filter_by(id=MANAGED_LLM_ID).count() == 0


def test_managed_runtime_config_fails_closed_when_incomplete(db, admin_user):
    current = _managed_settings(local_llm_model="")

    with pytest.raises(RuntimeError, match="LOCAL_LLM_MODEL"):
        sync_local_managed_runtime_config(db, current)
