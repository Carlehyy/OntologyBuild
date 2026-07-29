from __future__ import annotations

import os
import stat
from pathlib import Path

from dotenv import dotenv_values

from app.env_file import LocalEnvStore
from app.models import ConfigProfile, default_profile


def complete_profile() -> ConfigProfile:
    profile = default_profile()
    payload = profile.model_dump()
    payload["postgres"]["password"] = "Pg@pass:word/#%"
    payload["redis"]["username"] = "ontology"
    payload["redis"]["password"] = "Redis@pass:word/#%"
    payload["neo4j"]["password"] = "Neo4j@pass:word/#%"
    payload["minio"]["access_key"] = "ontology-access"
    payload["minio"]["secret_key"] = "Minio@pass:word/#%"
    payload["n8n"]["api_key"] = "n8n-local-key"
    payload["llm"]["api_key"] = "llm-local-key"
    return ConfigProfile.model_validate(payload)


def test_env_round_trip_keeps_complete_mode_and_special_credentials(
    tmp_path: Path,
) -> None:
    env_path = tmp_path / "config" / "generated" / "local" / ".env"
    store = LocalEnvStore(env_path)
    original = complete_profile()

    result = store.write(original)
    loaded = store.load_profile()
    parsed = dotenv_values(env_path, interpolate=False)

    assert result.path == env_path
    assert result.backup_path is None
    assert loaded == original
    assert parsed["ENVIRONMENT"] == "development"
    assert parsed["REQUIRE_EXTERNAL_DEPENDENCIES"] == "true"
    assert parsed["DATASET_IMPORT_USE_CELERY"] == "true"
    assert parsed["STORAGE_LOCAL_FALLBACK"] == "false"
    assert parsed["LOCAL_CONFIG_MANAGED"] == "true"
    assert parsed["UPLOADS_DIR"] == "./runtime/uploads"
    assert "sqlite" not in parsed["DATABASE_URL"].lower()
    assert parsed["DATABASE_URL"].startswith("postgresql+psycopg2://")
    assert "Pg@pass:word" not in parsed["DATABASE_URL"]
    assert env_path.read_text(encoding="utf-8").startswith(
        "# 由 OpenOntology 本地配置中心生成"
    )


def test_public_profile_masks_saved_secrets_and_blank_preserves_them(
    tmp_path: Path,
) -> None:
    env_path = tmp_path / "generated" / ".env"
    store = LocalEnvStore(env_path)
    original = complete_profile()
    store.write(original)

    public, present, warning = store.public_profile()
    assert warning is None
    assert public.postgres.password == ""
    assert public.llm.api_key == ""
    assert present["postgres.password"] is True
    assert present["llm.api_key"] is True

    payload = public.model_dump()
    payload["platform"]["frontend_port"] = 5199
    submitted = ConfigProfile.model_validate(payload)
    resolved = store.resolve_secrets(submitted)

    assert resolved.platform.frontend_port == 5199
    assert resolved.postgres.password == original.postgres.password
    assert resolved.llm.api_key == original.llm.api_key


def test_rewrite_creates_single_backup_and_uses_restricted_permissions(
    tmp_path: Path,
) -> None:
    env_path = tmp_path / "generated" / ".env"
    store = LocalEnvStore(env_path)
    first = complete_profile()
    store.write(first)
    first_bytes = env_path.read_bytes()

    payload = first.model_dump()
    payload["platform"]["frontend_port"] = 5199
    second = ConfigProfile.model_validate(payload)
    result = store.write(second)

    assert result.backup_path == env_path.with_name(".env.bak")
    assert result.backup_path.read_bytes() == first_bytes
    assert not list(env_path.parent.glob("*.tmp"))
    if os.name != "nt":
        assert stat.S_IMODE(env_path.stat().st_mode) == 0o600
        assert stat.S_IMODE(result.backup_path.stat().st_mode) == 0o600


def test_missing_required_external_secret_fails_closed(tmp_path: Path) -> None:
    store = LocalEnvStore(tmp_path / ".env")
    profile = default_profile()

    try:
        store.resolve_secrets(profile)
    except ValueError as exc:
        message = str(exc)
    else:
        raise AssertionError("完整模式缺少第三方凭据时必须拒绝生成")

    assert "postgres.password" in message
    assert "redis.password" in message
    assert "llm.api_key" in message


def test_individual_probe_only_requires_its_own_credentials(
    tmp_path: Path,
) -> None:
    store = LocalEnvStore(tmp_path / ".env")
    payload = default_profile().model_dump()
    payload["postgres"]["password"] = "postgres-only-password"
    profile = ConfigProfile.model_validate(payload)

    postgres = store.resolve_service_secrets(profile, "postgres")
    browser = store.resolve_service_secrets(profile, "browser")

    assert postgres.postgres.password == "postgres-only-password"
    assert postgres.redis.password == ""
    assert browser.llm.api_key == ""

    try:
        store.resolve_service_secrets(profile, "redis")
    except ValueError as exc:
        assert "redis.password" in str(exc)
    else:
        raise AssertionError("Redis 单项测试缺少自身密码时必须拒绝")
