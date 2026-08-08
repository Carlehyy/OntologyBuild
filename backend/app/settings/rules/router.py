import httpx
import logging
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.deps import get_db, get_current_user
from app.models.agent_config import AgentConfig
from app.services.encryption_service import encrypt, decrypt
from app.schemas.agent_config import (
    AgentConfigUpdate,
    AgentConfigResponse,
    TestConnectionRequest,
    TestConnectionResponse,
    FetchAgentsResponse,
    QwenPawAgentInfo,
)
from app.settings.rules import agent_config_service

logger = logging.getLogger(__name__)
router = APIRouter()

# Historical private imports remain patchable. Handlers inject the current
# router-level objects into services at request time.
_get_agent_config = agent_config_service._get_agent_config
_normalize_base_url = agent_config_service._normalize_base_url
_build_qwenpaw_api_base = agent_config_service._build_qwenpaw_api_base
_login_qwenpaw = agent_config_service._login_qwenpaw


@router.get("/agent-config", response_model=AgentConfigResponse)
def get_agent_config(
    db: Session = Depends(get_db),
    _=Depends(get_current_user),
):
    return agent_config_service.get_agent_config(
        db,
        get_agent_config_fn=_get_agent_config,
    )


@router.put("/agent-config")
def update_agent_config(
    body: AgentConfigUpdate,
    db: Session = Depends(get_db),
    _=Depends(get_current_user),
):
    return agent_config_service.update_agent_config(
        body,
        db,
        get_agent_config_fn=_get_agent_config,
        normalize_base_url_fn=_normalize_base_url,
        encrypt_fn=encrypt,
    )


@router.post(
    "/agent-config/test",
    response_model=TestConnectionResponse,
)
def test_agent_connection(
    body: TestConnectionRequest,
    db: Session = Depends(get_db),
    _=Depends(get_current_user),
):
    return agent_config_service.test_agent_connection(
        body,
        db,
        get_agent_config_fn=_get_agent_config,
        normalize_base_url_fn=_normalize_base_url,
        build_qwenpaw_api_base_fn=_build_qwenpaw_api_base,
        login_qwenpaw_fn=_login_qwenpaw,
        encrypt_fn=encrypt,
        httpx_module=httpx,
        log=logger,
    )


@router.post(
    "/agent-config/agents",
    response_model=FetchAgentsResponse,
)
def fetch_qwenpaw_agents(
    body: TestConnectionRequest,
    db: Session = Depends(get_db),
    _=Depends(get_current_user),
):
    return agent_config_service.fetch_qwenpaw_agents(
        body,
        db,
        get_agent_config_fn=_get_agent_config,
        normalize_base_url_fn=_normalize_base_url,
        build_qwenpaw_api_base_fn=_build_qwenpaw_api_base,
        login_qwenpaw_fn=_login_qwenpaw,
        decrypt_fn=decrypt,
        httpx_module=httpx,
        log=logger,
    )
