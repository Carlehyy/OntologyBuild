import httpx
import logging
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from typing import List
from pydantic import BaseModel
from app.deps import get_db, get_current_user, require_admin
from app.config import settings
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
    enforce_n8n_url_policy,
    normalize_n8n_api_base,
    test_n8n_connection,
)
from app.settings.rules import (
    agent_config_service,
    rules_service,
    workflow_config_service,
)

logger = logging.getLogger(__name__)
router = APIRouter()

# Historical private imports remain patchable. Handlers inject the current
# router-level objects into services at request time.
_get_agent_config = agent_config_service._get_agent_config
_normalize_base_url = agent_config_service._normalize_base_url
_build_qwenpaw_api_base = agent_config_service._build_qwenpaw_api_base
_login_qwenpaw = agent_config_service._login_qwenpaw
_get_workflow_config = workflow_config_service._get_workflow_config


class RuleUpdate(BaseModel):
    rule_key: str
    rule_value: str


@router.get("/rules")
def get_rules(
    db: Session = Depends(get_db),
    _=Depends(get_current_user),
):
    return rules_service.get_rules(db)


@router.put("/rules")
def update_rules(
    body: List[RuleUpdate],
    db: Session = Depends(get_db),
    _=Depends(get_current_user),
):
    return rules_service.update_rules(body, db)


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


@router.get(
    "/workflow-config",
    response_model=WorkflowConfigResponse,
)
def get_workflow_config(
    db: Session = Depends(get_db),
    _=Depends(get_current_user),
):
    return workflow_config_service.get_workflow_config(
        db,
        environment=settings.environment,
        managed_api_url=settings.n8n_api_url,
        managed_api_key=settings.n8n_api_key,
        managed_timeout_seconds=settings.n8n_timeout_seconds,
        enforce_url_policy_fn=enforce_n8n_url_policy,
    )


@router.put("/workflow-config")
def update_workflow_config(
    _body: WorkflowConfigUpdate,
    _db: Session = Depends(get_db),
    _=Depends(require_admin),
):
    return workflow_config_service.reject_direct_update()


@router.post(
    "/workflow-config/test",
    response_model=WorkflowConnectionTestResponse,
)
def test_workflow_connection(
    body: WorkflowConnectionTestRequest,
    db: Session = Depends(get_db),
    _=Depends(require_admin),
):
    return workflow_config_service.test_workflow_connection(
        body,
        db,
        environment=settings.environment,
        managed_api_url=settings.n8n_api_url,
        managed_api_key=settings.n8n_api_key,
        managed_timeout_seconds=settings.n8n_timeout_seconds,
        get_workflow_config_fn=_get_workflow_config,
        enforce_url_policy_fn=enforce_n8n_url_policy,
        test_connection_fn=test_n8n_connection,
        decrypt_fn=decrypt,
        encrypt_fn=encrypt,
        n8n_api_error_type=N8nApiError,
        httpx_module=httpx,
        log=logger,
    )
