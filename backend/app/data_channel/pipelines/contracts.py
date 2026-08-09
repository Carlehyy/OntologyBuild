"""HTTP request and response contracts for the pipeline API."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class PipelineCreate(BaseModel):
    name: str
    domain: str = "通用"
    description: str = ""
    source_dataset_id: Optional[str] = None
    route: Optional[str] = None
    spec: Optional[dict] = None
    definition: Optional[dict] = None


class PipelineUpdate(BaseModel):
    # Lifecycle status and enabled state have dedicated endpoints.
    name: Optional[str] = None
    domain: Optional[str] = None
    description: Optional[str] = None
    source_dataset_id: Optional[str] = None
    route: Optional[str] = None
    spec: Optional[dict] = None
    definition: Optional[dict] = None
    column_definitions: Optional[list] = None


class PipelineResponse(BaseModel):
    id: str
    name: str
    domain: Optional[str] = "通用"
    description: Optional[str] = ""
    source_dataset_id: Optional[str] = None
    route: Optional[str] = None
    spec: Optional[dict] = None
    definition: Optional[dict] = None
    status: str = "draft"
    engine: Optional[str] = None
    enabled: Optional[bool] = None
    column_definitions: Optional[list] = None
    branch: Optional[str] = "main"
    version: int = 1
    target_curated_ids: Optional[list] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    class Config:
        from_attributes = True


class ValidateResult(BaseModel):
    valid: bool
    errors: list[dict] = []
    warnings: list[dict] = []


class ValidateDefinitionsBody(BaseModel):
    column_definitions: list


class ValidateDefinitionsError(BaseModel):
    field_key: str
    message: str
    severity: str


class ValidateDefinitionsResult(BaseModel):
    valid: bool
    errors: list[dict] = []


class PublishBody(BaseModel):
    enable: bool = False


class ScriptBody(BaseModel):
    """Python 脚本流水线的执行/保存请求体。"""
    script: str


class EnabledBody(BaseModel):
    enabled: bool
