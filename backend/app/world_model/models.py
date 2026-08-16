"""
世界模型 (World Model) — 数据模型

四张表：
  - WorldModelProject        推演模型项目（脚本 + 引擎类型 + 状态）
  - WorldModelScriptVersion  每次「保存」冻结的脚本版本（开发态历史，可恢复）
  - WorldModelService        推演服务（一等实体：端点 + 状态 + 本体语义注册，二期发布落点）
  - WorldModelCallRecord     推演服务调用记录（二期发布运行时写入，本期只读）

状态机（本期仅 draft；published 为二期「发布为推演服务」预留）：
  draft --发布(二期)--> published
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


# —— 引擎类型：推演模型的规律来源（决定外推能力与行动输入支持方式） ——
ENGINE_TYPES = ("statistical", "mechanistic", "state_machine", "learned")

# —— 状态 ——
STATUS_DRAFT = "draft"
STATUS_PUBLISHED = "published"  # 二期「发布为推演服务」启用

# —— 推演服务状态（二期发布流程驱动；一期建表即落位） ——
SERVICE_STATUS_DRAFT = "draft"      # 已登记未上线
SERVICE_STATUS_ONLINE = "online"    # 在线可调用
SERVICE_STATUS_OFFLINE = "offline"  # 已下线

# 每个项目保留的脚本历史版数上限（对齐 Python 脚本流水线的 20 版策略）
SCRIPT_VERSION_KEEP = 20


class WorldModelProject(Base):
    """一个推演模型项目：以代码承载演化规律的最小管理单元。"""

    __tablename__ = "world_model_projects"
    __table_args__ = (
        Index("ix_world_model_projects_status", "status"),
        Index("ix_world_model_projects_engine_type", "engine_type"),
        Index("ix_world_model_projects_updated_at", "updated_at"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True, default="")
    engine_type: Mapped[str] = mapped_column(String(32), nullable=False,
                                             default="statistical")
    script: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(20), nullable=False,
                                        default=STATUS_DRAFT)

    created_by: Mapped[str | None] = mapped_column(
        String, ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now)


class WorldModelScriptVersion(Base):
    """保存时冻结的脚本版本（草稿期编辑历史，供查看与恢复）。"""

    __tablename__ = "world_model_script_versions"
    __table_args__ = (
        UniqueConstraint("project_id", "version_no",
                         name="uq_world_model_script_versions_project_version"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(
        String, ForeignKey("world_model_projects.id", ondelete="CASCADE"),
        nullable=False, index=True)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    script: Mapped[str] = mapped_column(Text, nullable=False)
    # 保存时用于验证的测试入参（context/actions/horizon），便于复盘当时语境
    test_input: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_by: Mapped[str | None] = mapped_column(
        String, ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now)


class WorldModelService(Base):
    """推演服务（一等实体）：推演模型发布后的在线调用单元。

    发布动作在推演模型开发页完成（冻结版本 + 本体语义注册 + 生成调用
    端点）；独立的「推演服务」页承担跨项目注册表、上线/下线与试调用。
    数据模型保持独立——调用记录与语义注册都挂在服务上。
    """

    __tablename__ = "world_model_services"
    __table_args__ = (
        Index("ix_world_model_services_project", "project_id"),
        Index("ix_world_model_services_status", "status"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(
        String, ForeignKey("world_model_projects.id", ondelete="CASCADE"),
        nullable=False)
    # 发布时冻结的脚本版本（重新发布 = 更新到新的冻结版本）
    version_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("world_model_script_versions.id", ondelete="SET NULL"),
        nullable=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True, default="")
    status: Mapped[str] = mapped_column(String(20), nullable=False,
                                        default=SERVICE_STATUS_DRAFT)
    # 对外调用路径（二期注册端点时生成）
    endpoint_path: Mapped[str | None] = mapped_column(String(200), nullable=True)

    # —— 本体语义注册（发布时必填，二期）：值引用本体概念而非自由文本 ——
    # 适用对象类型（本体类 id 列表，如 ["ot_resident_user"]）
    applicable_object_types: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # 前置条件（结构化表达，如 {"fact": "月度消费记录", "min_count": 12}）
    preconditions: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # 入参 / 出参与本体属性的绑定映射
    input_mapping: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    output_mapping: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    created_by: Mapped[str | None] = mapped_column(
        String, ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now)


class WorldModelCallRecord(Base):
    """一次推演服务调用的完整快照（二期发布运行时写入，本期只读）。

    字段设计对齐「调用记录需要支撑回测与审计」的要求：模型版本、调用方、
    输入/输出快照、置信度元数据都留在记录里。
    """

    __tablename__ = "world_model_call_records"
    __table_args__ = (
        Index("ix_world_model_call_records_project", "project_id"),
        Index("ix_world_model_call_records_created_at", "created_at"),
        Index("ix_world_model_call_records_ok", "ok"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    project_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("world_model_projects.id", ondelete="SET NULL"),
        nullable=True)
    # 一期建表落位：调用记录挂在服务（一等实体）上，二期埋点写入
    service_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("world_model_services.id", ondelete="SET NULL"),
        nullable=True)
    service_name: Mapped[str] = mapped_column(String(200), nullable=False,
                                              default="")
    # 调用方标识（Agent 会话 / 工具调用 ID / API 消费者）
    caller: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    ok: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    request_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    response_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now)
