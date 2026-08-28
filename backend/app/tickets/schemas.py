"""
工单 API Schemas

复用 formal 的 CamelModel（alias_generator + populate_by_name）：
前端发 camelCase，对外输出统一 camelCase。响应体由 service 层的
ticket_out 等助手手工组装（含 attachmentCount 等派生字段）。
"""
from __future__ import annotations

from typing import Optional

from pydantic import Field

from app.ontologies.formal_modeling.schemas import CamelModel


class TicketCreate(CamelModel):
    title: str
    content: str = Field(default="", description="反馈正文")
    category: Optional[str] = Field(
        default=None, description="分类：system_fault|experience|feature|other；缺省 other")
    page_url: Optional[str] = Field(
        default=None, description="提交时所在页面完整地址（含 hash 路由），供审查")


class ProgressUpdate(CamelModel):
    """管理员处理工单：调整进度状态并留下必填评论。"""
    status: str
    comment: str = Field(default="", description="处理评论（必填）")
