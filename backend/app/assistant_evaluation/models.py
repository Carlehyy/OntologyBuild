from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, JSON, \
    String, Text, UniqueConstraint
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
    本体助手的基准集必须绑定 ontology_id（回放需要本体上下文）。
    """

    __tablename__ = "assistant_eval_benchmark_sets"
    __table_args__ = (
        # 索引名与迁移 0086 严格一致（初始迁移按 metadata 建表时同名）
        Index("ix_ae_bench_sets_ontology", "ontology_id"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    assistant_key: Mapped[str] = mapped_column(String(50), nullable=False)
    # 本体助手专用：基准会话与沙箱回放所属的本体
    ontology_id: Mapped[str | None] = mapped_column(String, nullable=True)
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
    # task | benchmark_set | calibration | proposal | experiment | profile_version | autopilot_config
    ref_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    ref_id: Mapped[str | None] = mapped_column(String, nullable=True)
    detail: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_now)


class AssistantEvalProposal(Base):
    """优化提案（草稿变更）— M2 覆盖 prompt_patch / model_swap 两根杠杆。

    payload 保存目标值全量快照：prompt_patch 存替换后的完整
    system_prompt_extra 与提案时基线；model_swap 存目标模型配置。
    沙箱回放（M2）与投产（M3）消费同一份 payload，杜绝"验证的
    与投产的不一致"。
    """

    __tablename__ = "assistant_eval_proposals"
    __table_args__ = (
        Index("ix_ae_proposals_ontology_created", "ontology_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    ontology_id: Mapped[str] = mapped_column(String, nullable=False)
    # M2 固定 ontology_agent；预留其它助手接入沙箱回放后扩展
    assistant_key: Mapped[str] = mapped_column(String(50), nullable=False,
                                               default="ontology_agent")
    # prompt_patch | model_swap
    type: Mapped[str] = mapped_column(String(20), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    rationale: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # prompt_patch: {system_prompt_extra, base_system_prompt_extra}
    # model_swap:   {model_config_id, model_name, base_model_config_id}
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    # {task_id, badcase_conversation_ids, categories}
    evidence: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    # draft | validated | superseded（M3 扩展 applied | rolled_back）
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")
    created_by: Mapped[str | None] = mapped_column(
        String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_now, onupdate=_now
    )


class AssistantEvalExperiment(Base):
    """双臂沙箱实验：基准集 × {当前生产配置臂, 草稿提案配置臂} 同天回放对比。

    门禁只认留出集（heldout）增量，阈值下界为 max(入参 threshold,
    2×最近一次噪声校准 overall_noise)——既防基准过拟合，也防 judge
    抖动被误读为优化。
    """

    __tablename__ = "assistant_eval_experiments"
    __table_args__ = (
        Index("ix_ae_experiments_ontology_created", "ontology_id", "created_at"),
        # 索引名与迁移 0086 严格一致（初始迁移按 metadata 建表时同名）
        Index("ix_ae_experiments_proposal", "proposal_id"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    ontology_id: Mapped[str] = mapped_column(String, nullable=False)
    proposal_id: Mapped[str] = mapped_column(
        String, ForeignKey("assistant_eval_proposals.id", ondelete="CASCADE"),
        nullable=False,
    )
    benchmark_set_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("assistant_eval_benchmark_sets.id", ondelete="SET NULL"),
        nullable=True,
    )
    # queued → running → success | error
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued")
    # {dimension_keys, threshold, benchmark_set_id, sandbox_conversation_ids}
    params: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    judge_model_config_id: Mapped[str | None] = mapped_column(String, nullable=True)
    judge_model_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    # {baseline, trial, by_split, gate}，各臂含 per_dim/overall/scored/failed
    result: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str | None] = mapped_column(
        String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)


class AssistantEvalExperimentItem(Base):
    """单条基准会话在某臂的回放评分与轨迹快照。

    沙箱会话在评分后即删除，完整轨迹只以本表快照形式存在——实验
    结果自包含，不依赖任何活会话。
    """

    __tablename__ = "assistant_eval_experiment_items"
    __table_args__ = (
        Index("ix_ae_exp_items_experiment", "experiment_id"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    experiment_id: Mapped[str] = mapped_column(
        String, ForeignKey("assistant_eval_experiments.id", ondelete="CASCADE"),
        nullable=False,
    )
    # baseline | trial
    arm: Mapped[str] = mapped_column(String(10), nullable=False)
    conversation_id: Mapped[str] = mapped_column(String, nullable=False)
    conversation_title: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    split: Mapped[str] = mapped_column(String(10), nullable=False, default="train")
    overall_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    scores: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    # {engine_error}
    flags: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    # {query, response, openai_messages, actions, tool_error_count}
    transcript: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_now)


class AssistantEvalProfileVersion(Base):
    """AgentProfile 版本快照 — 自动投产的回退锚点与审计链。

    snapshot 保存变更前的全量 PROFILE_FIELDS；投产前先对近期生产会话
    抽样评分作为看守基线（pre_apply_stats，无样本时回退用实验 baseline
    臂统计）。回退 = 把 snapshot 写回 profile，前一版本恢复 active。
    """

    __tablename__ = "assistant_eval_profile_versions"
    __table_args__ = (
        Index("ix_ae_versions_ontology_created", "ontology_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    ontology_id: Mapped[str] = mapped_column(String, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    # 变更前 AgentProfile 全量字段快照
    snapshot: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    # {proposal_id, experiment_id, trigger: manual|autopilot}
    source: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    # active | superseded | rolled_back
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    # 投产前生产会话抽样评分：{overall, per_dim, conversations}
    pre_apply_stats: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    # 投产后看守已确认未劣化
    verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_by: Mapped[str | None] = mapped_column(
        String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_now)


class AssistantEvalAutopilotConfig(Base):
    """值守开关 — 每个本体一条：定时自转的优化循环（无人值守投产）。

    循环：投产後看守（劣化即回退）→ 采样评估 → 坏例并基准 → LLM 生成
    prompt_patch 提案 → 双臂沙箱实验 → 门禁通过且预算未耗尽 → 自动投产。
    连续失败 3 轮自动熔断（suspended），等待人工介入。
    """

    __tablename__ = "assistant_eval_autopilot_configs"
    __table_args__ = (
        UniqueConstraint("ontology_id", name="uq_ae_autopilot_ontology"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    ontology_id: Mapped[str] = mapped_column(String, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # 每天本地时区该时刻（HH:MM）触发一轮
    run_at: Mapped[str] = mapped_column(String(5), nullable=False, default="03:00")
    benchmark_set_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("assistant_eval_benchmark_sets.id", ondelete="SET NULL"),
        nullable=True,
    )
    dimension_keys: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    model_config_id: Mapped[str | None] = mapped_column(String, nullable=True)
    # 留出集增量门禁阈值（下界仍为 max(threshold, 2×噪声地板)）
    threshold: Mapped[float] = mapped_column(Float, nullable=False, default=5.0)
    # 预算硬顶：滚动 7 天内自动投产次数上限
    max_applies_per_week: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    # 每轮评估的采样窗口（天）
    sample_days: Mapped[int] = mapped_column(Integer, nullable=False, default=14)
    # 连续失败熔断
    suspended: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    suspend_reason: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    last_dispatched_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_cycle_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # success | skipped_busy | skipped_no_badcase | skipped_budget | rolled_back | error
    last_cycle_status: Mapped[str] = mapped_column(String(30), nullable=False, default="")
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_by: Mapped[str | None] = mapped_column(
        String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_now, onupdate=_now
    )
