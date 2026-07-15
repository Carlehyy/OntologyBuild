"""本体智能体 API Schemas — 对外 camelCase，复用 formal 的 CamelModel 约定。"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import Field

from app.ontologies.formal_modeling.schemas import CamelModel


class AgentProfileOut(CamelModel):
    id: str
    ontology_id: str
    enabled: bool
    allowed_object_type_ids: Optional[list[str]] = None
    allowed_link_type_ids: Optional[list[str]] = None
    allowed_action_ids: Optional[list[str]] = None
    allow_action_proposals: bool = True
    max_rows_per_query: int = 50
    max_steps: int = 8
    system_prompt_extra: str = ""
    default_model_id: Optional[str] = None
    updated_at: datetime


class AgentProfileUpdate(CamelModel):
    enabled: Optional[bool] = None
    allowed_object_type_ids: Optional[list[str]] = None
    allowed_link_type_ids: Optional[list[str]] = None
    allowed_action_ids: Optional[list[str]] = None
    allow_action_proposals: Optional[bool] = None
    max_rows_per_query: Optional[int] = None
    max_steps: Optional[int] = None
    system_prompt_extra: Optional[str] = None
    default_model_id: Optional[str] = None
    # 白名单三态（None=全部允许）无法用 exclude_unset 之外的方式表达清空，
    # 所以显式列出本次要「重置为全部允许」的字段名
    reset_to_all: list[str] = Field(default_factory=list)


class ChatRequest(CamelModel):
    message: str
    conversation_id: Optional[str] = None
    model_id: Optional[str] = None
    stream: bool = True


class ExecuteProposalRequest(CamelModel):
    action_id: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    target_instance_id: Optional[str] = None


class GraphPathRequest(CamelModel):
    source_instance_id: str
    target_instance_id: str
    direction: str = "both"
    max_depth: int = Field(default=5, ge=1, le=6)
    max_paths: int = Field(default=3, ge=1, le=5)


class GraphImpactRequest(CamelModel):
    instance_id: str
    property: str
    proposed_value: Any = None
    direction: str = "both"
    max_depth: int = Field(default=3, ge=1, le=4)


class ConversationOut(CamelModel):
    id: str
    title: str
    created_at: datetime
    updated_at: datetime


class MessageOut(CamelModel):
    id: str
    role: str
    content: str
    steps: list[dict[str, Any]] = Field(default_factory=list)
    citations: list[dict[str, Any]] = Field(default_factory=list)
    proposals: list[dict[str, Any]] = Field(default_factory=list)
    model: Optional[str] = None
    token_usage: Optional[dict[str, Any]] = None
    created_at: datetime
