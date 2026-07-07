"""
本体智能体 (Ontology Agent) — 数据模型

参考 Palantir AIP 的 agent×ontology 交互机制：agent 不直接访问底层数据库、
不自由扫描 schema，只能在 AgentProfile 声明的授权边界内，通过本体暴露的
对象 / 链接 / 事实 / 动作完成业务交互。

三张表：
  - AgentProfile       每个本体一份的授权边界配置（能看什么、能提什么动作、配额）
  - AgentConversation  对话（按本体 × 用户组织）
  - AgentMessage       消息（含工具调用轨迹 / 引用 / 动作提案，全程可审计）
"""
import uuid
from datetime import datetime, timezone
from sqlalchemy import String, DateTime, Text, JSON, Boolean, Integer
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class AgentProfile(Base):
    """智能体授权边界 — 每个本体一份。

    白名单语义：None = 全部允许；[] = 全部拒绝；[id...] = 仅列表内允许。
    动作默认 []（默认拒绝）：读默认开放，写必须显式授权 —— 治理优先。
    """
    __tablename__ = "fo_agent_profiles"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    ontology_id: Mapped[str] = mapped_column(String, nullable=False, unique=True, index=True)

    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    allowed_object_type_ids: Mapped[list] = mapped_column(JSON, nullable=True)   # None=全部
    allowed_link_type_ids: Mapped[list] = mapped_column(JSON, nullable=True)     # None=全部
    allowed_action_ids: Mapped[list] = mapped_column(JSON, nullable=True, default=list)  # 默认拒绝

    # 是否允许 agent 提出动作提案（dry-run 预演）。真实执行永远走用户确认 + HITL 闸门。
    allow_action_proposals: Mapped[bool] = mapped_column(Boolean, default=True)

    max_rows_per_query: Mapped[int] = mapped_column(Integer, default=50)
    max_steps: Mapped[int] = mapped_column(Integer, default=8)

    system_prompt_extra: Mapped[str] = mapped_column(Text, default="")
    default_model_id: Mapped[str] = mapped_column(String, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class AgentConversation(Base):
    __tablename__ = "fo_agent_conversations"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    ontology_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String, nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(200), default="新对话")

    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class AgentMessage(Base):
    """一条消息。assistant 消息附带完整工具轨迹（steps）——审计的最小单元。"""
    __tablename__ = "fo_agent_messages"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    conversation_id: Mapped[str] = mapped_column(String, nullable=False, index=True)

    role: Mapped[str] = mapped_column(String(20), nullable=False)   # user | assistant
    content: Mapped[str] = mapped_column(Text, default="")

    # [{tool, arguments, summary, durationMs, result, error?}]
    steps: Mapped[list] = mapped_column(JSON, default=list)
    # [{instanceId, objectType, label}]
    citations: Mapped[list] = mapped_column(JSON, default=list)
    # [{proposalId, actionId, actionName, parameters, targetInstanceId, validationErrors, effects}]
    proposals: Mapped[list] = mapped_column(JSON, default=list)

    model: Mapped[str] = mapped_column(String(200), nullable=True)
    token_usage: Mapped[dict] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
