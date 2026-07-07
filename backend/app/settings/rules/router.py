import httpx
import logging
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional
from pydantic import BaseModel
from app.deps import get_db, get_current_user
from app.models.rules_config import RulesConfig
from app.models.agent_config import AgentConfig
from app.models.workflow_config import WorkflowConfig
from app.services.encryption_service import encrypt, decrypt
from app.schemas.agent_config import (
    AgentConfigUpdate,
    AgentConfigResponse,
    TestConnectionRequest,
    TestConnectionResponse,
    FetchAgentsResponse,
    QwenPawAgentInfo,
)
from app.schemas.workflow_config import (
    WorkflowConfigUpdate,
    WorkflowConfigResponse,
    WorkflowConnectionTestRequest,
    WorkflowConnectionTestResponse,
)
from app.services.workflow.n8n_client import (
    N8nApiError,
    normalize_n8n_api_base,
    test_n8n_connection,
)

logger = logging.getLogger(__name__)
router = APIRouter()

# ── existing rules endpoints ──────────────────────────────────────────────

class RuleUpdate(BaseModel):
    rule_key: str
    rule_value: str

@router.get("/rules")
def get_rules(db: Session = Depends(get_db), _=Depends(get_current_user)):
    rules = db.query(RulesConfig).order_by(RulesConfig.rule_key).all()
    return {"data": [
        {"id": r.id, "rule_key": r.rule_key, "rule_value": r.rule_value,
         "rule_label_cn": r.rule_label_cn, "rule_label_en": r.rule_label_en, "editable": r.editable}
        for r in rules
    ]}

@router.put("/rules")
def update_rules(body: List[RuleUpdate], db: Session = Depends(get_db), _=Depends(get_current_user)):
    for update in body:
        rule = db.query(RulesConfig).filter(RulesConfig.rule_key == update.rule_key, RulesConfig.editable == True).first()
        if rule:
            rule.rule_value = update.rule_value
    db.commit()
    return {"message": "Rules updated"}

# ── agent config endpoints ────────────────────────────────────────────────

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


def _build_qwenpaw_api_base(config_base_url: str) -> str:
    """Build the QwenPaw API base URL with /api suffix."""
    base = _normalize_base_url(config_base_url)
    if not base:
        return ""
    if not base.endswith("/api"):
        base = f"{base}/api"
    return base


def _login_qwenpaw(api_base: str, username: str, password: str) -> Optional[str]:
    """Try to log in to QwenPaw and return a JWT token, or None."""
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(
                f"{api_base}/auth/login",
                json={"username": username, "password": password},
            )
            if resp.status_code == 200:
                data = resp.json()
                return data.get("access_token") or data.get("token")
    except Exception as exc:
        logger.debug("QwenPaw login failed: %s", exc)
    return None


@router.get("/agent-config", response_model=AgentConfigResponse)
def get_agent_config(
    db: Session = Depends(get_db),
    _=Depends(get_current_user),
):
    cfg = _get_agent_config(db)
    return AgentConfigResponse(
        base_url=cfg.base_url,
        auth_enabled=cfg.auth_enabled,
        username=cfg.username,
        has_password=bool(cfg.password_encrypted),
        target_agent_id=cfg.target_agent_id,
        target_agent_name=cfg.target_agent_name,
    )


@router.put("/agent-config")
def update_agent_config(
    body: AgentConfigUpdate,
    db: Session = Depends(get_db),
    _=Depends(get_current_user),
):
    cfg = _get_agent_config(db)
    cfg.base_url = _normalize_base_url(body.base_url)
    cfg.auth_enabled = body.auth_enabled
    cfg.username = body.username.strip()
    if body.password:
        cfg.password_encrypted = encrypt(body.password)
        # Clear cached token when password changes
        cfg.token = ""
    elif body.password == "" and not body.auth_enabled:
        cfg.password_encrypted = ""
        cfg.token = ""
    cfg.target_agent_id = body.target_agent_id.strip()
    cfg.target_agent_name = body.target_agent_name.strip()
    db.commit()
    return {"message": "Agent config updated"}


@router.post("/agent-config/test", response_model=TestConnectionResponse)
def test_agent_connection(
    body: TestConnectionRequest,
    db: Session = Depends(get_db),
    _=Depends(get_current_user),
):
    api_base = _build_qwenpaw_api_base(body.base_url)
    if not api_base:
        raise HTTPException(status_code=400, detail="Invalid base URL")

    # Step 1: test basic connectivity via /api/auth/status
    try:
        with httpx.Client(timeout=10.0) as client:
            status_resp = client.get(f"{api_base}/auth/status")
    except httpx.ConnectError:
        return TestConnectionResponse(
            ok=False,
            message="无法连接到 QwenPaw 服务，请检查地址是否正确",
        )
    except httpx.TimeoutException:
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
        has_auth = status_data.get("enabled", False) or status_data.get("has_users", False)
    except Exception:
        # If status endpoint is unreachable, try the agents endpoint
        try:
            with httpx.Client(timeout=10.0) as client:
                agents_resp = client.get(f"{api_base}/agents")
                has_auth = agents_resp.status_code == 401
        except Exception:
            pass

    # Step 2: test authentication if credentials provided
    token_valid = False
    if body.auth_enabled and body.username and body.password:
        token = _login_qwenpaw(api_base, body.username, body.password)
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
    cfg = _get_agent_config(db)
    cfg.base_url = _normalize_base_url(body.base_url)
    cfg.auth_enabled = body.auth_enabled
    cfg.username = body.username.strip()
    if body.password:
        cfg.password_encrypted = encrypt(body.password)
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


@router.post("/agent-config/agents", response_model=FetchAgentsResponse)
def fetch_qwenpaw_agents(
    body: TestConnectionRequest,
    db: Session = Depends(get_db),
    _=Depends(get_current_user),
):
    api_base = _build_qwenpaw_api_base(body.base_url)
    if not api_base:
        raise HTTPException(status_code=400, detail="Invalid base URL")

    headers = {}
    if body.auth_enabled:
        token = None
        cfg = _get_agent_config(db)
        # 优先用数据库已保存的密码
        if cfg.password_encrypted:
            try:
                saved_pw = decrypt(cfg.password_encrypted)
                token = _login_qwenpaw(api_base, cfg.username or body.username, saved_pw)
            except Exception:
                pass
        # 回退到用户本次输入的密码
        if not token and body.password:
            token = _login_qwenpaw(api_base, body.username, body.password)
        if token:
            headers["Authorization"] = f"Bearer {token}"
        else:
            raise HTTPException(status_code=502, detail="QwenPaw 认证失败，请重新测试连接")

    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(f"{api_base}/agents", headers=headers)
            if resp.status_code == 401:
                raise HTTPException(status_code=502, detail="QwenPaw 认证失败，请检查用户名和密码")
            resp.raise_for_status()
            data = resp.json()
            agents = data.get("agents", [])
            return FetchAgentsResponse(
                agents=[
                    QwenPawAgentInfo(
                        id=a.get("id", ""),
                        name=a.get("name", a.get("id", "")),
                        description=a.get("description", ""),
                    )
                    for a in agents
                ]
            )
    except httpx.ConnectError:
        raise HTTPException(status_code=502, detail="无法连接到 QwenPaw 服务")
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="连接 QwenPaw 超时")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"获取智能体列表失败: {exc}")


# -- workflow/n8n config endpoints ----------------------------------------

def _get_workflow_config(db: Session) -> WorkflowConfig:
    """Get or create the single-row workflow/n8n config after successful validation."""
    cfg = db.query(WorkflowConfig).filter(WorkflowConfig.id == "default").first()
    if not cfg:
        cfg = WorkflowConfig(id="default")
        db.add(cfg)
        db.commit()
        db.refresh(cfg)
    return cfg


@router.get("/workflow-config", response_model=WorkflowConfigResponse)
def get_workflow_config(
    db: Session = Depends(get_db),
    _=Depends(get_current_user),
):
    cfg = db.query(WorkflowConfig).filter(WorkflowConfig.id == "default").first()
    if not cfg:
        return WorkflowConfigResponse()
    return WorkflowConfigResponse(
        enabled=cfg.enabled,
        api_url=cfg.api_url,
        has_api_key=bool(cfg.api_key_encrypted),
        timeout_seconds=cfg.timeout_seconds,
    )


@router.put("/workflow-config")
def update_workflow_config(
    _body: WorkflowConfigUpdate,
    _db: Session = Depends(get_db),
    _=Depends(get_current_user),
):
    raise HTTPException(
        status_code=400,
        detail="请使用测试连接接口；n8n 配置仅在连接测试成功后保存",
    )


@router.post("/workflow-config/test", response_model=WorkflowConnectionTestResponse)
def test_workflow_connection(
    body: WorkflowConnectionTestRequest,
    db: Session = Depends(get_db),
    _=Depends(get_current_user),
):
    api_base = normalize_n8n_api_base(body.api_url)
    if not api_base:
        raise HTTPException(status_code=400, detail="Invalid n8n API URL")

    api_key = body.api_key.strip()
    if not api_key:
        cfg = db.query(WorkflowConfig).filter(WorkflowConfig.id == "default").first()
        if cfg and cfg.api_key_encrypted:
            try:
                api_key = decrypt(cfg.api_key_encrypted)
            except Exception as exc:
                logger.warning("Failed to decrypt saved n8n API key: %s", exc)
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
        result = test_n8n_connection(
            api_url=api_base,
            api_key=api_key,
            timeout_seconds=body.timeout_seconds,
        )
    except N8nApiError as exc:
        return WorkflowConnectionTestResponse(
            ok=False,
            message=f"n8n API 返回错误: {exc.message}",
            api_base=api_base,
        )
    except httpx.ConnectError:
        return WorkflowConnectionTestResponse(
            ok=False,
            message="无法连接到 n8n 服务，请检查地址是否正确",
            api_base=api_base,
        )
    except httpx.TimeoutException:
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

    cfg = _get_workflow_config(db)
    cfg.enabled = body.enabled
    cfg.api_url = result.api_base
    cfg.timeout_seconds = body.timeout_seconds
    if body.api_key:
        cfg.api_key_encrypted = encrypt(body.api_key)
    db.commit()

    return WorkflowConnectionTestResponse(
        ok=result.ok,
        message="n8n 连接成功",
        api_base=result.api_base,
    )
