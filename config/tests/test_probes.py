from __future__ import annotations

from app.models import ConfigProfile, default_profile
from app.probes import PROBES, run_probe


def _profile_with_secrets() -> ConfigProfile:
    payload = default_profile().model_dump()
    payload["postgres"]["password"] = "postgres-super-secret"
    payload["redis"]["password"] = "redis-super-secret"
    payload["neo4j"]["password"] = "neo4j-super-secret"
    payload["minio"]["access_key"] = "minio-secret-access"
    payload["minio"]["secret_key"] = "minio-super-secret"
    payload["n8n"]["api_key"] = "n8n-super-secret"
    payload["llm"]["api_key"] = "llm-super-secret"
    return ConfigProfile.model_validate(payload)


def test_probe_errors_redact_every_known_secret(monkeypatch) -> None:
    profile = _profile_with_secrets()

    def fail(current: ConfigProfile):
        raise RuntimeError(
            "bad "
            + " ".join(
                [
                    current.postgres.password,
                    current.redis.password,
                    current.neo4j.password,
                    current.minio.access_key,
                    current.minio.secret_key,
                    current.n8n.api_key,
                    current.llm.api_key,
                ]
            )
        )

    monkeypatch.setitem(PROBES, "postgres", fail)
    result = run_probe("postgres", profile)

    assert result.ok is False
    assert "***" in result.detail
    assert "super-secret" not in result.detail
    assert profile.minio.access_key not in result.detail


def test_unknown_probe_fails_without_network() -> None:
    result = run_probe("missing", _profile_with_secrets())

    assert result.ok is False
    assert result.duration_ms == 0
