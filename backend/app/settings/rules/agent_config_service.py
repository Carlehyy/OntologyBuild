"""QwenPaw Agent configuration and connectivity application operations."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any, Optional

import httpx
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.settings.agents.models import AgentConfig
from app.settings.agents.schemas import (
    AgentConfigResponse,
    AgentConfigUpdate,
    FetchAgentsResponse,
    QwenPawAgentInfo,
    TestConnectionRequest,
    TestConnectionResponse,
)
from app.shared.encryption import decrypt, encrypt


logger = logging.getLogger(__name__)


def _get_agent_config(db: Session) -> AgentConfig:
    """Get or create the single-row agent config."""
    cfg = db.query(AgentConfig).filter(AgentConfig.id == "default").first()
    if not cfg:
        cfg = AgentConfig(id="default")
        db.add(cfg)
        db.commit()
        db.refresh(cfg)
    return cfg


def _normalize_base_url(raw: str) -> str:
    """Strip trailing slash and ensure it's a valid-looking URL."""
    url = raw.strip().rstrip("/")
    if url and not url.startswith("http"):
        url = f"http://{url}"
    return url


def _build_qwenpaw_api_base(
    config_base_url: str,
    *,
    normalize_base_url_fn: Callable[[str], str] = _normalize_base_url,
) -> str:
    """Build the QwenPaw API base URL with /api suffix."""
    base = normalize_base_url_fn(config_base_url)
    if not base:
        return ""
    if not base.endswith("/api"):
        base = f"{base}/api"
    return base


def _login_qwenpaw(
    api_base: str,
    username: str,
    password: str,
    *,
    httpx_module: Any = httpx,
    log: logging.Logger = logger,
) -> Optional[str]:
    """Try to log in to QwenPaw and return a JWT token, or None."""
    try:
        with httpx_module.Client(timeout=10.0) as client:
            resp = client.post(
                f"{api_base}/auth/login",
                json={"username": username, "password": password},
            )
            if resp.status_code == 200:
                data = resp.json()
                return data.get("access_token") or data.get("token")
    except Exception as exc:
        log.debug("QwenPaw login failed: %s", exc)
    return None


def _build_api_base(
    raw: str,
    *,
    build_qwenpaw_api_base_fn: Callable[[str], str],
    normalize_base_url_fn: Callable[[str], str],
) -> str:
    if build_qwenpaw_api_base_fn is _build_qwenpaw_api_base:
        return build_qwenpaw_api_base_fn(
            raw,
            normalize_base_url_fn=normalize_base_url_fn,
        )
    return build_qwenpaw_api_base_fn(raw)


def _login(
    api_base: str,
    username: str,
    password: str,
    *,
    login_qwenpaw_fn: Callable[[str, str, str], Optional[str]],
    httpx_module: Any,
    log: logging.Logger,
) -> Optional[str]:
    if login_qwenpaw_fn is _login_qwenpaw:
        return login_qwenpaw_fn(
            api_base,
            username,
            password,
            httpx_module=httpx_module,
            log=log,
        )
    return login_qwenpaw_fn(api_base, username, password)


def get_agent_config(
    db: Session,
    *,
    get_agent_config_fn: Callable[[Session], AgentConfig] = (
        _get_agent_config
    ),
) -> AgentConfigResponse:
    cfg = get_agent_config_fn(db)
    return AgentConfigResponse(
        base_url=cfg.base_url,
        auth_enabled=cfg.auth_enabled,
        username=cfg.username,
        has_password=bool(cfg.password_encrypted),
        target_agent_id=cfg.target_agent_id,
        target_agent_name=cfg.target_agent_name,
    )


def update_agent_config(
    body: AgentConfigUpdate,
    db: Session,
    *,
    get_agent_config_fn: Callable[[Session], AgentConfig] = (
        _get_agent_config
    ),
    normalize_base_url_fn: Callable[[str], str] = _normalize_base_url,
    encrypt_fn: Callable[[str], str] = encrypt,
) -> dict[str, str]:
    cfg = get_agent_config_fn(db)
    cfg.base_url = normalize_base_url_fn(body.base_url)
    cfg.auth_enabled = body.auth_enabled
    cfg.username = body.username.strip()
    if body.password:
        cfg.password_encrypted = encrypt_fn(body.password)
        # Clear cached token when password changes
        cfg.token = ""
    elif body.password == "" and not body.auth_enabled:
        cfg.password_encrypted = ""
        cfg.token = ""
    cfg.target_agent_id = body.target_agent_id.strip()
    cfg.target_agent_name = body.target_agent_name.strip()
    db.commit()
    return {"message": "Agent config updated"}


def test_agent_connection(
    body: TestConnectionRequest,
    db: Session,
    *,
    get_agent_config_fn: Callable[[Session], AgentConfig] = (
        _get_agent_config
    ),
    normalize_base_url_fn: Callable[[str], str] = _normalize_base_url,
    build_qwenpaw_api_base_fn: Callable[[str], str] = (
        _build_qwenpaw_api_base
    ),
    login_qwenpaw_fn: Callable[[str, str, str], Optional[str]] = (
        _login_qwenpaw
    ),
    encrypt_fn: Callable[[str], str] = encrypt,
    httpx_module: Any = httpx,
    log: logging.Logger = logger,
) -> TestConnectionResponse:
    api_base = _build_api_base(
        body.base_url,
        build_qwenpaw_api_base_fn=build_qwenpaw_api_base_fn,
        normalize_base_url_fn=normalize_base_url_fn,
    )
    if not api_base:
        raise HTTPException(status_code=400, detail="Invalid base URL")

    # Step 1: test basic connectivity via /api/auth/status
    try:
        with httpx_module.Client(timeout=10.0) as client:
            status_resp = client.get(f"{api_base}/auth/status")
    except httpx_module.ConnectError:
        return TestConnectionResponse(
            ok=False,
            message="无法连接到 QwenPaw 服务，请检查地址是否正确",
        )
    except httpx_module.TimeoutException:
        return TestConnectionResponse(
            ok=False,
            message="连接 QwenPaw 超时，请检查网络",
        )
    except Exception as exc:
        return TestConnectionResponse(
            ok=False,
            message=f"连接失败: {exc}",
        )

    # Parse auth status
    has_auth = False
    try:
        status_data = status_resp.json()
        has_auth = status_data.get(
            "enabled",
            False,
        ) or status_data.get("has_users", False)
    except Exception:
        # If status endpoint is unreachable, try the agents endpoint
        try:
            with httpx_module.Client(timeout=10.0) as client:
                agents_resp = client.get(f"{api_base}/agents")
                has_auth = agents_resp.status_code == 401
        except Exception:
            pass

    # Step 2: test authentication if credentials provided
    token_valid = False
    if body.auth_enabled and body.username and body.password:
        token = _login(
            api_base,
            body.username,
            body.password,
            login_qwenpaw_fn=login_qwenpaw_fn,
            httpx_module=httpx_module,
            log=log,
        )
        token_valid = token is not None
        if not token_valid:
            return TestConnectionResponse(
                ok=True,
                message="QwenPaw 服务可连通，但认证失败：用户名或密码错误",
                has_auth=True,
                token_valid=False,
            )

    if has_auth and not body.auth_enabled:
        return TestConnectionResponse(
            ok=True,
            message="QwenPaw 服务已开启认证，但本次未提供凭据。建议开启「启用认证」并填写用户名密码。",
            has_auth=True,
            token_valid=False,
        )

    # ── 连接成功：自动入库 ──
    cfg = get_agent_config_fn(db)
    cfg.base_url = normalize_base_url_fn(body.base_url)
    cfg.auth_enabled = body.auth_enabled
    cfg.username = body.username.strip()
    if body.password:
        cfg.password_encrypted = encrypt_fn(body.password)
        cfg.token = ""
    elif not body.auth_enabled:
        cfg.password_encrypted = ""
        cfg.token = ""
    db.commit()

    return TestConnectionResponse(
        ok=True,
        message="连接成功" + ("，认证通过" if token_valid else ""),
        has_auth=has_auth,
        token_valid=token_valid,
    )


def fetch_qwenpaw_agents(
    body: TestConnectionRequest,
    db: Session,
    *,
    get_agent_config_fn: Callable[[Session], AgentConfig] = (
        _get_agent_config
    ),
    normalize_base_url_fn: Callable[[str], str] = _normalize_base_url,
    build_qwenpaw_api_base_fn: Callable[[str], str] = (
        _build_qwenpaw_api_base
    ),
    login_qwenpaw_fn: Callable[[str, str, str], Optional[str]] = (
        _login_qwenpaw
    ),
    decrypt_fn: Callable[[str], str] = decrypt,
    httpx_module: Any = httpx,
    log: logging.Logger = logger,
) -> FetchAgentsResponse:
    api_base = _build_api_base(
        body.base_url,
        build_qwenpaw_api_base_fn=build_qwenpaw_api_base_fn,
        normalize_base_url_fn=normalize_base_url_fn,
    )
    if not api_base:
        raise HTTPException(status_code=400, detail="Invalid base URL")

    headers = {}
    if body.auth_enabled:
        token = None
        cfg = get_agent_config_fn(db)
        # 优先用数据库已保存的密码
        if cfg.password_encrypted:
            try:
                saved_pw = decrypt_fn(cfg.password_encrypted)
                token = _login(
                    api_base,
                    cfg.username or body.username,
                    saved_pw,
                    login_qwenpaw_fn=login_qwenpaw_fn,
                    httpx_module=httpx_module,
                    log=log,
                )
            except Exception:
                pass
        # 回退到用户本次输入的密码
        if not token and body.password:
            token = _login(
                api_base,
                body.username,
                body.password,
                login_qwenpaw_fn=login_qwenpaw_fn,
                httpx_module=httpx_module,
                log=log,
            )
        if token:
            headers["Authorization"] = f"Bearer {token}"
        else:
            raise HTTPException(
                status_code=502,
                detail="QwenPaw 认证失败，请重新测试连接",
            )

    try:
        with httpx_module.Client(timeout=10.0) as client:
            resp = client.get(f"{api_base}/agents", headers=headers)
            if resp.status_code == 401:
                raise HTTPException(
                    status_code=502,
                    detail="QwenPaw 认证失败，请检查用户名和密码",
                )
            resp.raise_for_status()
            data = resp.json()
            agents = data.get("agents", [])
            return FetchAgentsResponse(
                agents=[
                    QwenPawAgentInfo(
                        id=agent.get("id", ""),
                        name=agent.get(
                            "name",
                            agent.get("id", ""),
                        ),
                        description=agent.get("description", ""),
                    )
                    for agent in agents
                ],
            )
    except httpx_module.ConnectError:
        raise HTTPException(
            status_code=502,
            detail="无法连接到 QwenPaw 服务",
        )
    except httpx_module.TimeoutException:
        raise HTTPException(
            status_code=504,
            detail="连接 QwenPaw 超时",
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"获取智能体列表失败: {exc}",
        )
