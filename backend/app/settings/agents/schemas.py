from typing import Optional
from pydantic import BaseModel, Field


class AgentConfigUpdate(BaseModel):
    """Request to save agent configuration."""

    base_url: str = Field(default="", description="QwenPaw API base URL, e.g. http://127.0.0.1:8088")
    auth_enabled: bool = Field(default=False)
    username: str = Field(default="")
    password: str = Field(default="", description="Plaintext password; encrypted on save")
    target_agent_id: str = Field(default="")
    target_agent_name: str = Field(default="")


class AgentConfigResponse(BaseModel):
    """Current agent configuration (password never returned)."""

    base_url: str = ""
    auth_enabled: bool = False
    username: str = ""
    has_password: bool = False
    target_agent_id: str = ""
    target_agent_name: str = ""


class TestConnectionRequest(BaseModel):
    """Request to test connectivity to QwenPaw."""

    base_url: str
    auth_enabled: bool = False
    username: str = ""
    password: str = ""


class TestConnectionResponse(BaseModel):
    """Result of connectivity test."""

    ok: bool
    message: str
    has_auth: bool = False
    token_valid: bool = False


class QwenPawAgentInfo(BaseModel):
    """Summary of a QwenPaw agent."""

    id: str
    name: str
    description: str = ""


class FetchAgentsResponse(BaseModel):
    """List of agents fetched from QwenPaw."""

    agents: list[QwenPawAgentInfo] = []
