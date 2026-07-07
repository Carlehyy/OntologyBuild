"""业务探索 API Schemas — 对外 camelCase，复用 formal 的 CamelModel 约定。"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import Field

from app.ontologies.formal_modeling.schemas import CamelModel


class SessionCreate(CamelModel):
    title: Optional[str] = None


class SessionOut(CamelModel):
    id: str
    title: str
    canvas_version: int = 0
    status: str = "active"
    created_at: datetime
    updated_at: datetime


class ChatRequest(CamelModel):
    message: str
    model_id: Optional[str] = None
    stream: bool = True


class MessageOut(CamelModel):
    id: str
    role: str
    content: str
    steps: list[dict[str, Any]] = Field(default_factory=list)
    model: Optional[str] = None
    token_usage: Optional[dict[str, Any]] = None
    created_at: datetime


class GenerateDocumentRequest(CamelModel):
    model_id: Optional[str] = None


class DocumentOut(CamelModel):
    id: str
    session_id: str
    title: str
    content_md: str
    version: int
    created_at: datetime


class DocumentListItem(CamelModel):
    id: str
    session_id: str
    title: str
    version: int
    created_at: datetime


class GenerateDraftRequest(CamelModel):
    target_ontology_id: Optional[str] = None   # None = 应用时新建本体
    model_id: Optional[str] = None             # LLM 补缺用的模型；缺省用系统默认


class DraftOut(CamelModel):
    id: str
    session_id: str
    document_id: str
    target_ontology_id: Optional[str] = None
    draft: dict[str, Any] = Field(default_factory=dict)
    report: dict[str, Any] = Field(default_factory=dict)
    status: str = "draft"
    applied_ontology_id: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class AttachmentOut(CamelModel):
    id: str
    session_id: str
    filename: str
    mime_type: Optional[str] = None
    file_size: int = 0
    char_count: int = 0
    status: str = "ready"
    error: Optional[str] = None
    created_at: datetime


class NewOntologySpec(CamelModel):
    name: str
    domain: Optional[str] = None
    description: Optional[str] = None


class ApplyDraftRequest(CamelModel):
    # None = 全部应用；[] = 一个都不选（校验拒绝）
    selected_keys: Optional[list[str]] = None
    new_ontology: Optional[NewOntologySpec] = None
