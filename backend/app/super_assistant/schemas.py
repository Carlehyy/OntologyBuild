from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ConversationCreate(BaseModel):
    title: str = Field(default="新会话", max_length=200)
    model_config_id: str | None = None


class ConversationUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    model_config_id: str | None = None


class ConversationOut(ORMModel):
    id: str
    title: str
    model_config_id: str | None
    status: str
    created_at: datetime
    updated_at: datetime


class MessageOut(ORMModel):
    id: str
    conversation_id: str
    role: str
    content: str
    status: str
    steps: list[Any]
    token_usage: dict[str, Any]
    created_at: datetime


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=100_000)
    model_config_id: str | None = None


class ApprovalRequest(BaseModel):
    decision: Literal["approve", "deny"]


class SkillCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$")
    display_name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4000)
    triggers: list[str] = Field(default_factory=list)
    instructions: str = Field(default="", max_length=500_000)
    enabled: bool = True


class SkillUpdate(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    triggers: list[str] | None = None
    enabled: bool | None = None


class SkillOut(ORMModel):
    id: str
    name: str
    display_name: str
    description: str
    triggers: list[str]
    manifest: list[dict[str, Any]]
    enabled: bool
    revision: int
    created_at: datetime
    updated_at: datetime


class SkillFileOut(BaseModel):
    path: str
    size: int
    editable: bool


class SkillFileContent(BaseModel):
    content: str = Field(max_length=5_000_000)


class McpServerCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$")
    url: str = Field(min_length=1, max_length=1000)
    headers: dict[str, str] = Field(default_factory=dict)
    enabled: bool = True
    require_confirmation: bool = True

    @field_validator("headers")
    @classmethod
    def validate_headers(cls, value: dict[str, str]) -> dict[str, str]:
        if len(value) > 30:
            raise ValueError("请求头不能超过 30 个")
        for key, item in value.items():
            if not key or len(key) > 200 or "\n" in key or "\r" in key:
                raise ValueError("请求头名称无效")
            if len(item) > 8000 or "\n" in item or "\r" in item:
                raise ValueError(f"请求头 {key} 的值无效")
        return value


class McpServerUpdate(BaseModel):
    url: str | None = Field(default=None, min_length=1, max_length=1000)
    headers: dict[str, str] | None = None
    enabled: bool | None = None
    require_confirmation: bool | None = None

    @field_validator("headers")
    @classmethod
    def validate_headers(cls, value: dict[str, str] | None) -> dict[str, str] | None:
        if value is None:
            return None
        return McpServerCreate.validate_headers(value)


class McpServerOut(ORMModel):
    id: str
    name: str
    transport: str
    url: str
    header_names: list[str]
    enabled: bool
    require_confirmation: bool
    tool_manifest: list[dict[str, Any]]
    last_test_status: str | None
    last_test_message: str | None
    last_tested_at: datetime | None
    created_at: datetime
    updated_at: datetime


class McpTestOut(BaseModel):
    ok: bool
    message: str
    tools: list[dict[str, Any]] = Field(default_factory=list)
