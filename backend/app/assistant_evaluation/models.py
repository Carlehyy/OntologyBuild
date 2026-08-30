from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, JSON, String, Text, \
    UniqueConstraint
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
    # structured_root_cause 输出：{category, dim_key, dim_score, severity, levers, summary}
    attribution: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
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


class AssistantEvalBenchmarkSet(Base):
    """基准集：从真实会话沉淀出的固定复评集合（数据飞轮的回归基线资产）。

    条目引用活会话（不快照轨迹），按 conversation_id 稳定哈希切分
    train / heldout：train 供优化迭代参考，heldout 只作投产前门禁，
    两者互不流动。源会话被删除时条目在复评时自然失效。
    """

    __tablename__ = "assistant_eval_benchmark_sets"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    assistant_key: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # 由评估任务坏例沉淀时记录来源任务，保证可追溯
    source_task_id: Mapped[str | None] = mapped_column(String, nullable=True)
    created_by: Mapped[str | None] = mapped_column(
        String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_now, onupdate=_now
    )


class AssistantEvalBenchmarkItem(Base):
    """基准集内单条会话条目（一个会话在一个集合内只出现一次）。"""

    __tablename__ = "assistant_eval_benchmark_items"
    __table_args__ = (
        UniqueConstraint("set_id", "conversation_id", name="uq_ae_bench_set_conversation"),
        Index("ix_ae_bench_items_set", "set_id"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    set_id: Mapped[str] = mapped_column(
        String, ForeignKey("assistant_eval_benchmark_sets.id", ondelete="CASCADE"),
        nullable=False,
    )
    conversation_id: Mapped[str] = mapped_column(String, nullable=False)
    conversation_title: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    # train | heldout（缺省按稳定哈希切分，重建可复现）
    split: Mapped[str] = mapped_column(String(10), nullable=False, default="train")
    # manual | badcase | task：条目来源
    origin: Mapped[str] = mapped_column(String(20), nullable=False, default="manual")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_now)


class AssistantEvalCalibration(Base):
    """噪声地板校准：同一批会话重复评分，度量 judge 分数方差。

    方差是自动投产阈值的地基——两配置臂的分数差只有显著大于噪声
    地板，"优化生效"才可归因。与评估任务共用全局串行闸门和 judge
    解析通道，本表只存聚合结果，不落逐次明细。
    """

    __tablename__ = "assistant_eval_calibrations"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    assistant_key: Mapped[str] = mapped_column(String(50), nullable=False)
    # queued → running → success | error
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued")
    # {conversation_ids, dimension_keys, repeats, engine, benchmark_set_id}
    params: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    judge_model_config_id: Mapped[str | None] = mapped_column(String, nullable=True)
    judge_model_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    # {repeats, per_dim: {dim: {noise, conversations, samples}}, overall_noise, scored_conversations}
    result: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str | None] = mapped_column(
        String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)


class AssistantEvalTimelineEvent(Base):
    """审计时间线：飞轮每一步（分析/提案/验证/投产/回退）的事件留痕。

    M1 记录任务、基准集、校准三类事件；M2/M3 的提案与投产事件复用
    同一张表，actor 区分 admin（人工）与 system（后台线程）。
    """

    __tablename__ = "assistant_eval_timeline_events"
    __table_args__ = (
        Index("ix_ae_timeline_assistant_created", "assistant_key", "created_at"),
        Index("ix_ae_timeline_ref", "ref_type", "ref_id"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    assistant_key: Mapped[str | None] = mapped_column(String(50), nullable=True)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    # admin | system（M3 增加 autopilot）
    actor: Mapped[str] = mapped_column(String(20), nullable=False, default="admin")
    actor_user_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    # task | benchmark_set | calibration | proposal（M2）
    ref_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    ref_id: Mapped[str | None] = mapped_column(String, nullable=True)
    detail: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_now)
