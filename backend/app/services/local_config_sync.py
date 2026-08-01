"""Provision the required env-backed n8n integration into the database."""

from __future__ import annotations

from urllib.parse import urlsplit

from sqlalchemy.orm import Session

from app.config import Settings, settings
from app.models.workflow_config import WorkflowConfig
from app.services.encryption_service import decrypt, encrypt
from app.settings.workflows.n8n_client import enforce_n8n_url_policy


def _required(value: str, variable_name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise RuntimeError(
            f"平台启动必须配置 {variable_name}"
        )
    return normalized


def _encrypted_value(current: str | None, plaintext: str) -> str:
    """Keep an existing Fernet token when its plaintext has not changed."""

    if current:
        try:
            if decrypt(current) == plaintext:
                return current
        except Exception:  # noqa: BLE001 - replace ciphertext from an old key
            pass
    return encrypt(plaintext)


def _validated_http_url(raw: str, variable_name: str) -> str:
    url = _required(raw, variable_name).rstrip("/")
    parsed = urlsplit(url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise RuntimeError(
            f"{variable_name} 必须是无内嵌账号、查询参数或片段的 HTTP(S) 地址"
        )
    return url


def sync_local_managed_runtime_config(
    db: Session,
    current: Settings = settings,
) -> bool:
    """Apply the required n8n record; tests deliberately skip provisioning."""

    if current.environment.strip().lower() == "test":
        return False

    n8n_url = _validated_http_url(
        current.n8n_api_url,
        "N8N_API_URL",
    )
    try:
        n8n_api_base = enforce_n8n_url_policy(
            n8n_url,
            environment=current.environment,
        )
    except ValueError as exc:
        raise RuntimeError(f"N8N_API_URL 无效: {exc}") from exc
    n8n_key = _required(current.n8n_api_key, "N8N_API_KEY")

    workflow = (
        db.query(WorkflowConfig)
        .filter(WorkflowConfig.id == "default")
        .first()
    )
    if workflow is None:
        workflow = WorkflowConfig(id="default")
        db.add(workflow)
    workflow.enabled = True
    workflow.api_url = n8n_api_base
    workflow.timeout_seconds = current.n8n_timeout_seconds
    workflow.api_key_encrypted = _encrypted_value(
        workflow.api_key_encrypted,
        n8n_key,
    )

    return True
