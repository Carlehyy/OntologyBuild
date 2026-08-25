from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class AssistantEvalTask(Base):
    """一次助手评估任务：对某个助手的若干历史会话跑一组评分维度。"""

    __tablename__ = "assistant_eval_tasks"
    __table_args__ = (
        Index("ix_ae_tasks_assistant_created", "assistant_key", "created_at"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    assistant_key: Mapped[str] = mapped_column(String(50), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    # queued → running → success | error（对象生命周期状态，前端按此渲染）
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued")
    # {mode: manual|sample, sample_size, sample_days, dimension_keys: [...]}
    params: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    judge_model_config_id: Mapped[str | None] = mapped_column(String, nullable=True)
    judge_model_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    conversation_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completed_conversations: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # {overall, dimensions:{key:{avg,min,max,count}}, llm_calls, engine}
    summary: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str | None] = mapped_column(
        String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)


class AssistantEvalItem(Base):
    """任务内单条会话的评估结果明细。"""

    __tablename__ = "assistant_eval_items"
    __table_args__ = (
        Index("ix_ae_items_task", "task_id"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    task_id: Mapped[str] = mapped_column(
        String, ForeignKey("assistant_eval_tasks.id", ondelete="CASCADE"), nullable=False
    )
    conversation_id: Mapped[str] = mapped_column(String, nullable=False)
    conversation_title: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    overall_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    # {dim_key: 0-100 分}；仅包含成功评分的维度
    scores: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    # {dim_key: {"score": 原始分, "reason": 评判理由}}
    reasons: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    # {loop_detected, tool_error_count, low_dims: [...], engine_error}
    flags: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    root_cause: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_now)


class AssistantEvalRubric(Base):
    """自定义评分标准（rubric）：由 judge 模型按任务描述生成，供评估任务选用。

    任务创建时会把 rubric 快照进 task.params，删除本记录不影响历史报告。
    """

    __tablename__ = "assistant_eval_rubrics"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    task_description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # "1. …\n\n2. …" 编号列表文本（OpenJudge rubric 格式）
    rubrics: Mapped[str] = mapped_column(Text, nullable=False, default="")
    min_score: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    max_score: Mapped[float] = mapped_column(Float, nullable=False, default=5)
    judge_model_config_id: Mapped[str | None] = mapped_column(String, nullable=True)
    judge_model_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    created_by: Mapped[str | None] = mapped_column(
        String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_now)
