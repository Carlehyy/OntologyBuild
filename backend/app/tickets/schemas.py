"""
工单 API Schemas

复用 formal 的 CamelModel（alias_generator + populate_by_name）：
前端发 camelCase，对外输出统一 camelCase。响应体由 service 层的
ticket_out 等助手手工组装（含 attachmentCount 等派生字段）。
"""
from __future__ import annotations

from pydantic import Field

from app.ontologies.formal_modeling.schemas import CamelModel


class TicketCreate(CamelModel):
    title: str
    content: str = Field(default="", description="反馈正文")


class ProgressUpdate(CamelModel):
    """管理员处理工单：调整进度状态并留下必填评论。"""
    status: str
    comment: str = Field(default="", description="处理评论（必填）")
