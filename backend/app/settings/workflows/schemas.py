from pydantic import BaseModel, Field


class WorkflowConfigUpdate(BaseModel):
    """Legacy update shape; the runtime endpoint rejects persisted overrides."""

    enabled: bool = Field(default=False)
    api_url: str = Field(
        default="",
        description="n8n service root URL, e.g. http://127.0.0.1:5678",
    )
    api_key: str = Field(default="", description="Plaintext n8n API key; encrypted on save")
    timeout_seconds: int = Field(default=30, ge=1, le=120)


class WorkflowConfigResponse(BaseModel):
    """Current workflow/n8n configuration (API key never returned)."""

    enabled: bool = False
    api_url: str = ""
    has_api_key: bool = False
    timeout_seconds: int = 30


class WorkflowConnectionTestRequest(BaseModel):
    """Request shape retained for the n8n connectivity endpoint.

    Normal runtimes ignore these candidate values and test the environment-
    managed configuration.  ``ENVIRONMENT=test`` retains the historical
    injectable behavior for deterministic tests.
    """

    enabled: bool = Field(default=False)
    api_url: str
    api_key: str = ""
    timeout_seconds: int = Field(default=30, ge=1, le=120)


class WorkflowConnectionTestResponse(BaseModel):
    """Result of n8n connectivity test."""

    ok: bool
    message: str
    api_base: str = ""
