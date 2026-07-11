"""
数据管家 (Data Steward) — 数据模型

对话式编排 n8n 数据流水线的编排辅助层。与手工画布并行的第二种流水线来源：
LLM 在授权工具边界内创建/编辑 n8n workflow。**数据管家只负责创建/录入与
编排（n8n 侧不激活）**；一条流水线能否发布，唯一入口在流水线列表的编辑
向导——发布时激活 workflow 并封版，与画布流水线同一生命周期。

三张表：
  - N8nPipeline          n8n 工作流的平台绑定记录（workflow 身份 + 快照 + 试跑样本）
  - StewardConversation  数据管家对话
  - StewardMessage       消息（含工具调用轨迹，全程可审计）

生命周期：发布状态的唯一真源是影子流水线 v2_pipelines.status（draft/published/archived），
本表 status 只区分「在管 draft / 已归档 archived」——归档即从流水线列表移除，
记录本身留档可审计。
"""
import uuid
from datetime import datetime, timezone
from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


# 状态常量 — 迁移都经由 service 层的显式函数，禁止散落赋值
STATUS_DRAFT = "draft"
STATUS_ARCHIVED = "archived"
BROWSER_SOURCE_REMOTE_CDP = "remote_cdp"
BROWSER_SOURCE_COMPANION = "companion"


class N8nPipeline(Base):
    """一个受管 n8n 工作流的平台绑定记录。

    n8n 侧的 workflow JSON 是真身；本表记录它在平台内的身份。创建/纳管即
    注册一条 v2_pipelines 影子行（definition.engine="n8n"，status=draft），
    发布与否由影子行状态决定（编辑向导 publish 时激活 workflow）。
    """
    __tablename__ = "v2_n8n_pipelines"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True, default="")

    n8n_workflow_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(30), default=STATUS_DRAFT, index=True)

    # 最近一次从 n8n 同步/写入的 workflow JSON（nodes/connections/settings）
    workflow_snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # 最近一次试跑结果 + workflow evidence；validate-definitions 成功后追加
    # validation_attestation（字段契约/版本快照/dry-run 输出校验和）。发布时
    # 其 columns 固化为影子流水线期望列，attestation 固化进发布版本审计。
    last_test_result: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # 对应的 v2_pipelines.id（engine=n8n 的影子流水线，创建即登记）
    pipeline_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("v2_pipelines.id", ondelete="SET NULL"), nullable=True, index=True)
    # 创建它的数据管家会话（血缘：这条流水线是哪段对话产出的）
    conversation_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("v2_steward_conversations.id", ondelete="SET NULL"), nullable=True)

    created_by: Mapped[str | None] = mapped_column(
        String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)

    __table_args__ = (
        Index("uq_n8n_pipelines_workflow", "n8n_workflow_id", unique=True),
        Index("uq_n8n_pipelines_shadow_pipeline", "pipeline_id", unique=True),
        CheckConstraint("status IN ('draft','archived')", name="ck_n8n_pipelines_status"),
    )


class StewardBrowserSource(Base):
    """A user-owned browser provider; secrets are always encrypted or hashed."""
    __tablename__ = "v2_steward_browser_sources"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    user_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    source_type: Mapped[str] = mapped_column(String(30), nullable=False)
    endpoint_url_encrypted: Mapped[str] = mapped_column(Text, default="", nullable=False)
    headers_encrypted: Mapped[str] = mapped_column(Text, default="", nullable=False)
    device_token_hash: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)

    __table_args__ = (
        CheckConstraint(
            "source_type IN ('remote_cdp','companion')",
            name="ck_steward_browser_sources_type"),
        Index("ix_steward_browser_sources_user_type", "user_id", "source_type"),
    )


class StewardConversation(Base):
    __tablename__ = "v2_steward_conversations"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    user_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(200), default="新对话")
    browser_source_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("v2_steward_browser_sources.id", ondelete="SET NULL"),
        nullable=True, index=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class StewardMessage(Base):
    """一条消息。assistant 消息附带完整工具轨迹（steps）——审计的最小单元。"""
    __tablename__ = "v2_steward_messages"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    conversation_id: Mapped[str] = mapped_column(
        String, ForeignKey("v2_steward_conversations.id", ondelete="CASCADE"),
        nullable=False, index=True)

    role: Mapped[str] = mapped_column(String(20), nullable=False)  # user | assistant
    content: Mapped[str] = mapped_column(Text, default="")

    # [{tool, arguments, resultSummary, durationMs, error?}]
    steps: Mapped[list] = mapped_column(JSON, default=list)
    # 本回合触达的 N8nPipeline 记录 id 列表（前端据此刷新受管流水线面板）
    touched_pipeline_ids: Mapped[list] = mapped_column(JSON, default=list)

    model: Mapped[str | None] = mapped_column(String(200), nullable=True)
    token_usage: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
