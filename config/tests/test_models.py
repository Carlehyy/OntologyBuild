from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models import (
    AdvancedConfig,
    ConfigProfile,
    MinioConfig,
    PlatformConfig,
    default_profile,
)


def test_default_profile_generates_stable_security_material() -> None:
    profile = default_profile()

    assert len(profile.platform.first_admin_password) >= 12
    assert len(profile.platform.secret_key) >= 32
    assert len(profile.platform.encryption_key) == 44
    assert profile.advanced.api_hub_mcp_token
    assert profile.advanced.api_hub_system_mcp_token
    assert profile.platform.backend_host == "127.0.0.1"
    assert profile.platform.frontend_host == "127.0.0.1"
    assert profile.postgres.host == "127.0.0.1"
    assert profile.postgres.port == 5432
    assert profile.postgres.database == "openontology"
    assert profile.postgres.username == "postgres"
    assert profile.redis.host == "localhost"
    assert profile.neo4j.uri == "neo4j://localhost:7687"
    assert profile.minio.endpoint == "localhost:9000"
    assert profile.minio.access_key == "admin"


@pytest.mark.parametrize(
    "updates",
    [
        {"backend_host": "0.0.0.0"},
        {"frontend_host": "192.168.1.10"},
        {"backend_port": 5173, "frontend_port": 5173},
    ],
)
def test_platform_rejects_non_local_or_duplicate_bindings(
    updates: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        PlatformConfig(**updates)


@pytest.mark.parametrize(
    "path",
    [
        "/tmp/openontology",
        "C:\\openontology",
        "../outside",
        "./runtime/../outside",
    ],
)
def test_advanced_paths_must_be_portable_project_relative(path: str) -> None:
    with pytest.raises(ValidationError):
        AdvancedConfig(uploads_dir=path)


def test_minio_rejects_console_port_and_scheme() -> None:
    with pytest.raises(ValidationError, match="9001"):
        MinioConfig(endpoint="127.0.0.1:9001")
    with pytest.raises(ValidationError, match="不要包含"):
        MinioConfig(endpoint="http://127.0.0.1:9000")


def test_profile_rejects_dotenv_interpolation_and_short_admin_password() -> None:
    profile = default_profile()
    payload = profile.model_dump()
    payload["postgres"]["password"] = "${HOME}"

    with pytest.raises(ValidationError, match="环境变量解析"):
        ConfigProfile.model_validate(payload)

    payload = profile.model_dump()
    payload["platform"]["first_admin_password"] = "short"
    with pytest.raises(ValidationError, match="至少需要 12"):
        ConfigProfile.model_validate(payload)

    payload = profile.model_dump()
    payload["platform"]["encryption_key"] = "not-a-fernet-key"
    with pytest.raises(ValidationError, match="Fernet"):
        ConfigProfile.model_validate(payload)


def test_optional_sections_accept_blank_form_values_and_use_safe_defaults() -> None:
    payload = default_profile().model_dump()
    payload["chroma"] = {"host": "", "port": None}
    payload["llm"].update(
        {
            "name": "",
            "provider": "",
            "api_base": "",
            "api_key": "",
            "model": "",
        }
    )

    profile = ConfigProfile.model_validate(payload)

    assert profile.chroma.host == "127.0.0.1"
    assert profile.chroma.port == 8001
    assert profile.llm.api_key == ""
    assert profile.llm.api_base == "https://api.openai.com/v1"
