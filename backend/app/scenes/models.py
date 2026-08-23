"""三维场景 (3D Scenes) — 数据模型

平台「三维场景」一级业务域：以白模风格 Three.js 场景承载业务的
空间化表达。产物与引擎分离——场景定义是可 diff、可校验的声明式
JSON（对象/关系/数据绑定三件套，见 validation.py），渲染由前端
共享引擎完成。

三张表：
  - Scene            场景主体（基本信息 + 草稿/发布状态机指针）
  - SceneVersion     版本冻结（保存即冻结，definition 为不可变 JSON 快照）
  - SceneRuntimeLog  运行日志（前端引擎批量上报的规则命中/恢复记录）

状态机（与 world_model 的 draft|published 同构）：
  draft     --publish--> published（published_version_no 冻结为当前版本）
  published --保存新版本--> draft（已发布版本保留，可随时重新发布）
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    String, Text, DateTime, Integer, JSON, ForeignKey,
    UniqueConstraint, Index,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


# —— 常量：所有取值集中在此，service 层做归一 ——
STATUS_DRAFT = "draft"
STATUS_PUBLISHED = "published"
SCENE_STATUSES = (STATUS_DRAFT, STATUS_PUBLISHED)

VERSION_SOURCE_MANUAL = "manual"          # 手工/API 保存
VERSION_SOURCE_ASSISTANT = "assistant"    # 场景助手对话产出（阶段二）
VERSION_SOURCE_CLONE = "clone"            # 克隆快照落地的首个版本

LOG_LEVELS = ("info", "normal", "warning", "alarm")

# 版本保留上限：与 world_model.SCRIPT_VERSION_KEEP 同一纪律
CHAT_HISTORY_KEEP = 20  # 送入 LLM 的历史消息条数上限
DEFINITION_VERSION_KEEP = 20


class Scene(Base):
    """一个三维场景。

    current_version_no 指向最新草稿版本；published_version_no 指向
    最近一次发布时冻结的版本（未发布过为 NULL）。两者都是版本号
    而非外键，避免 scenes 与 scene_versions 的循环外键。
    """

    __tablename__ = "scenes"
    __table_args__ = (
        Index("ix_scenes_status", "status"),
        Index("ix_scenes_updated_at", "updated_at"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text, default="")
    icon: Mapped[str] = mapped_column(String(40), default="boxes")
    status: Mapped[str] = mapped_column(String(20), default=STATUS_DRAFT)
    current_version_no: Mapped[int] = mapped_column(Integer, default=0)
    published_version_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class SceneVersion(Base):
    """一次保存即一条不可变版本快照（定义内容不再原地修改）。"""

    __tablename__ = "scene_versions"
    __table_args__ = (
        UniqueConstraint("scene_id", "version_no", name="uq_scene_versions_scene_version"),
        Index("ix_scene_versions_scene", "scene_id"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    scene_id: Mapped[str] = mapped_column(String, ForeignKey("scenes.id", ondelete="CASCADE"))
    version_no: Mapped[int] = mapped_column(Integer)
    definition: Mapped[dict] = mapped_column(JSON)
    source: Mapped[str] = mapped_column(String(20), default=VERSION_SOURCE_MANUAL)
    note: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=_now)


class SceneRuntimeLog(Base):
    """发布态场景运行时由前端引擎批量上报的规则命中/恢复日志。

    引擎跑在浏览器端且规则求值是其内置能力（status 三态），后端只
    负责接收、落库与查询，不重复实现求值器。
    """

    __tablename__ = "scene_runtime_logs"
    __table_args__ = (
        Index("ix_scene_runtime_logs_scene_occurred", "scene_id", "occurred_at"),
        Index("ix_scene_runtime_logs_level", "level"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    scene_id: Mapped[str] = mapped_column(String, ForeignKey("scenes.id", ondelete="CASCADE"))
    level: Mapped[str] = mapped_column(String(10), default="info")
    object_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    event_key: Mapped[str] = mapped_column(String(80), default="")
    message: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=_now)
    recorded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=_now)



class SceneConversation(Base):
    """场景建模助手的会话。

    scene_id 允许为空：「从零新建」模式下对话先于场景存在，
    首次成功应用定义时创建草稿场景并绑定；场景被删除后置空，
    会话与历史保留。
    """

    __tablename__ = "scene_conversations"
    __table_args__ = (
        Index("ix_scene_conversations_scene", "scene_id"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    scene_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("scenes.id", ondelete="SET NULL"), nullable=True,
    )
    title: Mapped[str] = mapped_column(String(200), default="")
    model_config_id: Mapped[str | None] = mapped_column(String, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class SceneMessage(Base):
    """会话消息（user/assistant）。assistant 消息记录其应用的版本号。"""

    __tablename__ = "scene_messages"
    __table_args__ = (
        Index("ix_scene_messages_conversation", "conversation_id"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    conversation_id: Mapped[str] = mapped_column(
        String, ForeignKey("scene_conversations.id", ondelete="CASCADE"),
    )
    role: Mapped[str] = mapped_column(String(10))  # user | assistant
    content: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(12), default="complete")  # complete | error
    version_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=_now)
