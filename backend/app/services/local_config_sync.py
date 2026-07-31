"""Synchronize local configuration-center integrations into the dev database.

Most platform startup settings are environment based.  n8n and LLM settings
are intentionally database backed, so a local configuration center needs a
narrow bridge for those two records.  This module is fail-closed, idempotent
and permanently disabled outside the development environment.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from sqlalchemy.orm import Session

from app.config import Settings, settings
from app.models.model_config import ModelConfig
from app.models.user import User
from app.models.workflow_config import WorkflowConfig
from app.services.encryption_service import decrypt, encrypt
from app.settings.workflows.n8n_client import enforce_n8n_url_policy


MANAGED_LLM_ID = "local-config-managed-llm"
SUPPORTED_LLM_PROVIDERS = {"openai", "anthropic", "compatible"}


def _required(value: str, variable_name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise RuntimeError(
            f"LOCAL_CONFIG_MANAGED=true 时必须配置 {variable_name}"
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
    """Apply the required local n8n record and an optional default LLM.

    Returns ``True`` only when local management is active.  The caller owns the
    transaction so startup can commit the selected records atomically.  A blank
    ``LOCAL_LLM_API_KEY`` means that no managed default model was requested.
    """

    if current.environment.strip().lower() != "development":
        return False
    if not current.local_config_managed:
        return False

    n8n_url = _validated_http_url(
        current.local_n8n_api_url,
        "LOCAL_N8N_API_URL",
    )
    try:
        n8n_api_base = enforce_n8n_url_policy(
            n8n_url,
            environment=current.environment,
        )
    except ValueError as exc:
        raise RuntimeError(f"LOCAL_N8N_API_URL 无效: {exc}") from exc
    n8n_key = _required(current.local_n8n_api_key, "LOCAL_N8N_API_KEY")

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
    workflow.timeout_seconds = current.local_n8n_timeout_seconds
    workflow.api_key_encrypted = _encrypted_value(
        workflow.api_key_encrypted,
        n8n_key,
    )

    llm_key = str(current.local_llm_api_key or "").strip()
    if not llm_key:
        return True

    provider = _required(
        current.local_llm_provider,
        "LOCAL_LLM_PROVIDER",
    ).lower()
    if provider not in SUPPORTED_LLM_PROVIDERS:
        supported = ", ".join(sorted(SUPPORTED_LLM_PROVIDERS))
        raise RuntimeError(
            f"LOCAL_LLM_PROVIDER 仅支持: {supported}"
        )
    llm_name = _required(current.local_llm_name, "LOCAL_LLM_NAME")
    llm_api_base = _validated_http_url(
        current.local_llm_api_base,
        "LOCAL_LLM_API_BASE",
    )
    llm_model = _required(current.local_llm_model, "LOCAL_LLM_MODEL")

    admin = (
        db.query(User)
        .filter(User.role == "admin", User.is_active.is_(True))
        .order_by(User.created_at.asc())
        .first()
    )
    if admin is None:
        raise RuntimeError("本地托管配置需要至少一个已启用的管理员账号")

    managed_llm = (
        db.query(ModelConfig)
        .filter(ModelConfig.id == MANAGED_LLM_ID)
        .first()
    )
    if managed_llm is None:
        managed_llm = ModelConfig(
            id=MANAGED_LLM_ID,
            name=llm_name,
            config_type="llm",
            provider=provider,
            models=[llm_model],
            options={"managed_by": "local_config_center"},
            enabled=True,
            is_default=False,
            created_by=admin.id,
        )
        db.add(managed_llm)

    # Clear the current default before enabling the reserved managed row.  The
    # partial unique index makes ordering important on PostgreSQL.
    db.query(ModelConfig).filter(
        ModelConfig.config_type == "llm",
        ModelConfig.id != MANAGED_LLM_ID,
        ModelConfig.is_default.is_(True),
    ).update(
        {ModelConfig.is_default: False},
        synchronize_session=False,
    )
    managed_llm.name = llm_name
    managed_llm.config_type = "llm"
    managed_llm.provider = provider
    managed_llm.api_base = llm_api_base
    managed_llm.api_key_encrypted = _encrypted_value(
        managed_llm.api_key_encrypted,
        llm_key,
    )
    managed_llm.models = [llm_model]
    managed_llm.enabled = True
    managed_llm.is_default = True
    managed_llm.created_by = admin.id
    return True
