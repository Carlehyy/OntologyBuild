"""
事件登记 API Schemas

复用 formal 的 CamelModel（alias_generator + populate_by_name）：
  - 前端发 camelCase（eventType/occurredAt）→ 走 alias 解析
  - 第三方发 snake_case（event_type/occurred_at）→ 走 populate_by_name 解析
两边都收，对外输出统一 camelCase。响应体由 router 的 _event_out 等助手手工组装
（含 sourceLabel / attachmentCount 等派生字段）。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import Field

from app.ontologies.formal_modeling.schemas import CamelModel


# ── 平台录入 / 编辑 ──────────────────────────────────────────────

class EventCreate(CamelModel):
    title: str
    description: Optional[str] = ""
    event_type: Optional[str] = ""
    severity: str = "info"
    tags: list[str] = Field(default_factory=list)
    payload: dict[str, Any] = Field(default_factory=dict)
    occurred_at: Optional[datetime] = None
    # 本体桥接（可选）
    ontology_id: Optional[str] = None
    subject_ref: Optional[str] = None
    confidence: Optional[float] = None
    # 更正链：本事件更正某旧事件（可选）
    supersedes_id: Optional[str] = None


class EventUpdate(CamelModel):
    title: Optional[str] = None
    description: Optional[str] = None
    event_type: Optional[str] = None
    severity: Optional[str] = None
    tags: Optional[list[str]] = None
    payload: Optional[dict[str, Any]] = None
    occurred_at: Optional[datetime] = None
    ontology_id: Optional[str] = None
    subject_ref: Optional[str] = None
    confidence: Optional[float] = None


class StatusChange(CamelModel):
    status: str          # active | archived
    note: Optional[str] = None


# ── 第三方接口上传 ───────────────────────────────────────────────

class IngestEvent(CamelModel):
    """第三方上传的单条事件。字段与平台一致，另可显式带业务出处。"""
    title: str
    description: Optional[str] = ""
    event_type: Optional[str] = ""
    severity: str = "info"
    tags: list[str] = Field(default_factory=list)
    payload: dict[str, Any] = Field(default_factory=dict)
    occurred_at: Optional[datetime] = None
    # 出处（source_system 缺省用密钥名；source_ref 参与幂等）
    source_system: Optional[str] = None
    source_ref: Optional[str] = None
    confidence: Optional[float] = None
    # 本体桥接（可选）
    ontology_id: Optional[str] = None
    subject_ref: Optional[str] = None


# ── 密钥管理 ────────────────────────────────────────────────────

class IngestKeyCreate(CamelModel):
    name: str
    allowed_source_system: Optional[str] = None
