"""决策推演持久化模型。

推演只读取锁定发布版本的本体投影，并把输入快照、多个视角和确定性评估保存到
本表。它不写 ObjectInstance、PropertyFact、Sentinel 或 ActionExecutionLog。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Index, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class DecisionSimulationRun(Base):
    """一次不可变的、可复核的决策推演运行。"""

    __tablename__ = "fo_decision_simulation_runs"
    __table_args__ = (
        Index(
            "ix_decision_simulation_owner_started",
            "ontology_id", "created_by", "started_at",
        ),
        Index(
            "ix_decision_simulation_conversation_started",
            "conversation_id", "started_at",
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    ontology_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("ontology_projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    ontology_release_id: Mapped[str | None] = mapped_column(
        String, nullable=True, index=True)
    conversation_id: Mapped[str | None] = mapped_column(
        String,
        ForeignKey("fo_agent_conversations.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    created_by: Mapped[str] = mapped_column(
        String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    model_config_id: Mapped[str | None] = mapped_column(String, nullable=True)
    model_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="running")

    # 下面所有 JSON 都是本次运行的冻结产物；运行结束后不再随真实世界变化。
    specification: Mapped[dict] = mapped_column(JSON, default=dict)
    snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    perspectives: Mapped[list] = mapped_column(JSON, default=list)
    evaluation: Mapped[dict] = mapped_column(JSON, default=dict)
    recommendation: Mapped[dict] = mapped_column(JSON, default=dict)
    diagnostics: Mapped[dict] = mapped_column(JSON, default=dict)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    started_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
