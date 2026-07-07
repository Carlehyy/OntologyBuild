from datetime import datetime
from typing import Any, Literal
from pydantic import BaseModel, Field


class McpParameter(BaseModel):
    name: str
    location: str
    required: bool = False
    schema_: dict[str, Any] = Field(default_factory=dict, alias="schema")


class McpInterfaceOut(BaseModel):
    operation_id: str
    method: str
    path: str
    summary: str = ""
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    parameters: list[McpParameter] = Field(default_factory=list)
    request_body: dict[str, Any] | None = None
    enabled: bool = False
    supported: bool = True
    unsupported_reason: str | None = None
    excluded: bool = False
    exclude_reason: str | None = None
    display_name: str | None = None
    config_description: str | None = None


class McpInterfaceListOut(BaseModel):
    items: list[McpInterfaceOut]
    total: int
    enabled_count: int


class McpOpenBody(BaseModel):
    open: bool
    display_name: str | None = None
    description: str | None = None


class McpInterfaceConfigOut(BaseModel):
    id: str
    operation_id: str
    method: str
    path: str
    enabled: bool
    display_name: str | None = None
    description: str | None = None
    created_by: str | None = None
    updated_by: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class McpInfoOut(BaseModel):
    endpoint: str
    transport: Literal["streamable-http"] = "streamable-http"
    server_name: str
    token_required: bool = True
    auth: str
    published_count: int
