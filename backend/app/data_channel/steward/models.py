"""
数据管家 (Data Steward) — 数据模型

对话式编排 n8n 数据流水线的治理层。与手工画布流水线并行的第二种来源：
LLM 在授权工具边界内创建/编辑 n8n workflow，**平台侧永远保持草稿态
（workflow 不激活、不注册进流水线体系），直到用户在审批面板批准**。

三张表：
  - N8nPipeline          n8n 工作流的平台治理记录（草稿/审批状态机 + 快照）
  - StewardConversation  数据管家对话
  - StewardMessage       消息（含工具调用轨迹，全程可审计）

状态机：
  draft ──submit──▶ pending_approval ──approve──▶ approved
    ▲                     │ reject                    │ revoke / agent 修改
    └─────────────────────┴───────────────────────────┘
  （approved 被修改 → 自动停用 n8n workflow 并降回 draft，需重新审批）
"""
import uuid
from datetime import datetime, timezone
from sqlalchemy import String, DateTime, Text, JSON
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


# 状态常量 — 所有状态迁移都经由 service 层的显式函数，禁止散落赋值
STATUS_DRAFT = "draft"
STATUS_PENDING = "pending_approval"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"
STATUS_ARCHIVED = "archived"


class N8nPipeline(Base):
    """一个受管 n8n 工作流的平台治理记录。

    n8n 侧的 workflow JSON 是真身；本表记录它在平台内的身份与审批状态。
    approved 后注册/更新一条 v2_pipelines 记录（definition.engine="n8n"，
    status=published），从而进入流水线列表并可被任务池调度。
    """
    __tablename__ = "v2_n8n_pipelines"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True, default="")

    n8n_workflow_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(30), default=STATUS_DRAFT, index=True)

    # 最近一次从 n8n 同步/写入的 workflow JSON（nodes/connections/settings）
    workflow_snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # 批准那一刻的 workflow JSON — 审计基线，用于对比"批准后是否被改过"
    approved_snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # 批准后注册的 v2_pipelines.id（engine=n8n 的影子流水线）
    pipeline_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    # 创建它的数据管家会话（血缘：这条流水线是哪段对话产出的）
    conversation_id: Mapped[str | None] = mapped_column(String, nullable=True)

    created_by: Mapped[str | None] = mapped_column(String, nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String, nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    reject_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class StewardConversation(Base):
    __tablename__ = "v2_steward_conversations"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    user_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(200), default="新对话")

    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class StewardMessage(Base):
    """一条消息。assistant 消息附带完整工具轨迹（steps）——审计的最小单元。"""
    __tablename__ = "v2_steward_messages"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    conversation_id: Mapped[str] = mapped_column(String, nullable=False, index=True)

    role: Mapped[str] = mapped_column(String(20), nullable=False)  # user | assistant
    content: Mapped[str] = mapped_column(Text, default="")

    # [{tool, arguments, resultSummary, durationMs, error?}]
    steps: Mapped[list] = mapped_column(JSON, default=list)
    # 本回合触达的 N8nPipeline 记录 id 列表（前端据此刷新审批面板）
    touched_pipeline_ids: Mapped[list] = mapped_column(JSON, default=list)

    model: Mapped[str | None] = mapped_column(String(200), nullable=True)
    token_usage: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
