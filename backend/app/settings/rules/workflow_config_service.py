"""n8n workflow configuration and connectivity application operations."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

import httpx
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.settings.workflows.models import WorkflowConfig
from app.settings.workflows.n8n_client import (
    N8nApiError,
    enforce_n8n_url_policy,
    test_n8n_connection,
)
from app.settings.workflows.schemas import (
    WorkflowConfigResponse,
    WorkflowConnectionTestRequest,
    WorkflowConnectionTestResponse,
)
from app.shared.encryption import decrypt, encrypt


logger = logging.getLogger(__name__)


def _is_environment_managed(environment: str) -> bool:
    return str(environment or "").strip().lower() != "test"


def _get_workflow_config(db: Session) -> WorkflowConfig:
    """Get or create the single-row workflow/n8n config after successful validation."""
    cfg = (
        db.query(WorkflowConfig)
        .filter(WorkflowConfig.id == "default")
        .first()
    )
    if not cfg:
        cfg = WorkflowConfig(id="default")
        db.add(cfg)
        db.commit()
        db.refresh(cfg)
    return cfg


def get_workflow_config(
    db: Session,
    *,
    environment: str,
    managed_api_url: str = "",
    managed_api_key: str = "",
    managed_timeout_seconds: int = 30,
    enforce_url_policy_fn: Callable[..., str] = enforce_n8n_url_policy,
) -> WorkflowConfigResponse:
    if _is_environment_managed(environment):
        try:
            api_base = enforce_url_policy_fn(
                managed_api_url,
                environment=environment,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=503,
                detail=f"启动环境中的 n8n 配置无效: {exc}",
            ) from exc
        return WorkflowConfigResponse(
            enabled=True,
            api_url=api_base,
            has_api_key=bool(str(managed_api_key or "").strip()),
            timeout_seconds=managed_timeout_seconds,
        )

    cfg = (
        db.query(WorkflowConfig)
        .filter(WorkflowConfig.id == "default")
        .first()
    )
    if not cfg:
        return WorkflowConfigResponse()
    return WorkflowConfigResponse(
        enabled=cfg.enabled,
        api_url=cfg.api_url,
        has_api_key=bool(cfg.api_key_encrypted),
        timeout_seconds=cfg.timeout_seconds,
    )


def reject_direct_update() -> None:
    raise HTTPException(
        status_code=409,
        detail=(
            "n8n 配置由启动环境/配置中心托管，运行时不允许覆盖；"
            "请修改 N8N_* 配置并重启服务"
        ),
    )


def test_workflow_connection(
    body: WorkflowConnectionTestRequest,
    db: Session,
    *,
    environment: str,
    managed_api_url: str = "",
    managed_api_key: str = "",
    managed_timeout_seconds: int = 30,
    get_workflow_config_fn: Callable[[Session], WorkflowConfig] = (
        _get_workflow_config
    ),
    enforce_url_policy_fn: Callable[..., str] = enforce_n8n_url_policy,
    test_connection_fn: Callable[..., Any] = test_n8n_connection,
    decrypt_fn: Callable[[str], str] = decrypt,
    encrypt_fn: Callable[[str], str] = encrypt,
    n8n_api_error_type: type[Exception] = N8nApiError,
    httpx_module: Any = httpx,
    log: logging.Logger = logger,
) -> WorkflowConnectionTestResponse:
    environment_managed = _is_environment_managed(environment)
    candidate_url = (
        managed_api_url if environment_managed else body.api_url
    )
    timeout_seconds = (
        managed_timeout_seconds
        if environment_managed
        else body.timeout_seconds
    )
    try:
        api_base = enforce_url_policy_fn(
            candidate_url,
            environment=environment,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    api_key = (
        str(managed_api_key or "").strip()
        if environment_managed
        else body.api_key.strip()
    )
    if not api_key and not environment_managed:
        cfg = (
            db.query(WorkflowConfig)
            .filter(WorkflowConfig.id == "default")
            .first()
        )
        if cfg and cfg.api_key_encrypted:
            try:
                api_key = decrypt_fn(cfg.api_key_encrypted)
            except Exception as exc:
                log.warning(
                    "Failed to decrypt saved n8n API key: %s",
                    exc,
                )
                return WorkflowConnectionTestResponse(
                    ok=False,
                    message="已保存的 n8n API Key 无法解密，请重新输入并保存",
                    api_base=api_base,
                )
    if not api_key:
        return WorkflowConnectionTestResponse(
            ok=False,
            message="请填写 n8n API Key",
            api_base=api_base,
        )

    try:
        result = test_connection_fn(
            api_url=api_base,
            api_key=api_key,
            timeout_seconds=timeout_seconds,
        )
    except n8n_api_error_type as exc:
        return WorkflowConnectionTestResponse(
            ok=False,
            message=f"n8n API 返回错误: {exc.message}",
            api_base=api_base,
        )
    except httpx_module.ConnectError:
        return WorkflowConnectionTestResponse(
            ok=False,
            message="无法连接到 n8n 服务，请检查地址是否正确",
            api_base=api_base,
        )
    except httpx_module.TimeoutException:
        return WorkflowConnectionTestResponse(
            ok=False,
            message="连接 n8n 超时，请检查网络或调大超时时间",
            api_base=api_base,
        )
    except ValueError as exc:
        return WorkflowConnectionTestResponse(
            ok=False,
            message=str(exc),
            api_base=api_base,
        )
    except Exception as exc:
        return WorkflowConnectionTestResponse(
            ok=False,
            message=f"连接 n8n 失败: {exc}",
            api_base=api_base,
        )

    if not environment_managed:
        cfg = get_workflow_config_fn(db)
        cfg.enabled = body.enabled
        cfg.api_url = result.api_base
        cfg.timeout_seconds = body.timeout_seconds
        if body.api_key:
            cfg.api_key_encrypted = encrypt_fn(body.api_key)
        db.commit()

    return WorkflowConnectionTestResponse(
        ok=result.ok,
        message=(
            "n8n 环境托管配置连接成功"
            if environment_managed
            else "n8n 连接成功"
        ),
        api_base=result.api_base,
    )
