from pydantic import BaseModel, Field


class WorkflowConfigUpdate(BaseModel):
    """Request to save workflow/n8n configuration."""

    enabled: bool = Field(default=False)
    api_url: str = Field(default="", description="n8n API URL, e.g. http://127.0.0.1:5678/api/v1")
    api_key: str = Field(default="", description="Plaintext n8n API key; encrypted on save")
    timeout_seconds: int = Field(default=10, ge=1, le=120)


class WorkflowConfigResponse(BaseModel):
    """Current workflow/n8n configuration (API key never returned)."""

    enabled: bool = False
    api_url: str = ""
    has_api_key: bool = False
    timeout_seconds: int = 10


class WorkflowConnectionTestRequest(BaseModel):
    """Request to test connectivity to n8n and save it after success."""

    enabled: bool = Field(default=False)
    api_url: str
    api_key: str = ""
    timeout_seconds: int = Field(default=10, ge=1, le=120)


class WorkflowConnectionTestResponse(BaseModel):
    """Result of n8n connectivity test."""

    ok: bool
    message: str
    api_base: str = ""
