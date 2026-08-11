import uuid
from datetime import datetime, timezone
from sqlalchemy import Boolean, String, DateTime, JSON, Text, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base

class Pipeline(Base):
    __tablename__ = "v2_pipelines"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    domain: Mapped[str | None] = mapped_column(String(100), nullable=True, default="通用")
    description: Mapped[str | None] = mapped_column(Text, nullable=True, default="")
    source_dataset_id: Mapped[str | None] = mapped_column(String, ForeignKey("v2_datasets.id"), nullable=True)
    route: Mapped[str | None] = mapped_column(String(1), nullable=True)  # A|B|C (legacy, inferred from definition)
    spec: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)  # legacy steps format
    definition: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # new DSL: {nodes: [...], edges: [...]}
    # 字段定义（含主键声明）：入湖列名映射、类型、是否主键、可空性
    # [{field_key, field_name, field_type, is_primary_key, nullable}]
    # 已发布后不可修改；首次 dryRun 后可从列信息自动初始化默认定义
    column_definitions: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # 最近一次“试运行输出 × 当前编排 × 字段契约”全量校验凭证。
    # 发布端点必须独立核验该凭证，不能只依赖前端向导是否走过校验步骤。
    validation_attestation: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    target_curated_ids: Mapped[list | None] = mapped_column(JSON, nullable=True)
    schedule_cron: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # 生命周期：draft（草稿，可改）| published（封版）| archived（审计保留、不可运行）。
    # 运行态（running/failed）属于 PipelineRun，不回写此字段；
    # 历史遗留的 editing/running/failed 值由 migration 0008 归一。
    status: Mapped[str] = mapped_column(String(20), default="draft")
    # 启用开关：False 时任务池调度与同步链式触发都不执行该流水线；
    # 与 status(published) 正交——发布决定"能否被挂接"，启用决定"当下是否生效"。
    # 只有已发布才能启用（发布时可选同时启用），新建默认未启用。
    # 手动试运行（dry-run 预览）不受影响，便于停用期间调试。
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    branch: Mapped[str | None] = mapped_column(String(50), nullable=True, default="main")
    version: Mapped[int] = mapped_column(default=1)
    created_by: Mapped[str | None] = mapped_column(String, ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class PipelineVersion(Base):
    __tablename__ = "v2_pipeline_versions"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    pipeline_id: Mapped[str] = mapped_column(String, ForeignKey("v2_pipelines.id", ondelete="CASCADE"), nullable=False)
    version: Mapped[int] = mapped_column(nullable=False)
    definition: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # 发布那一刻的字段契约快照——契约是封版的核心工件，版本必须能回溯
    column_definitions: Mapped[list | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="draft")
    created_by: Mapped[str | None] = mapped_column(String, ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("uq_pipeline_versions_pipeline_version", "pipeline_id", "version", unique=True),
    )


class PipelineRun(Base):
    __tablename__ = "v2_pipeline_runs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    pipeline_id: Mapped[str] = mapped_column(String, ForeignKey("v2_pipelines.id", ondelete="CASCADE"), nullable=False)
    task_id: Mapped[str | None] = mapped_column(
        String,
        ForeignKey("v2_pipeline_tasks.id", ondelete="SET NULL"),
        nullable=True,
    )  # 由哪条调度任务触发（血缘）
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")  # pending|running|success|failed|cancelled
    # 触发方式（manual|scheduled）真实列：全局历史按触发方式过滤是高频查询，
    # 走 stats JSON 只能全表扫描；写入路径与 stats["trigger_type"] 同步填列，
    # 存量行由迁移 0063 回填，NULL 与缺键同义（按 manual 计）。
    trigger_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    stats: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error_log: Mapped[str | None] = mapped_column(Text, nullable=True)
    dataset_version_id: Mapped[str | None] = mapped_column(String, ForeignKey("v2_dataset_versions.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    # 运行历史是高频查询入口（任务池列表、执行动态、按流水线回看）；
    # stats 为重 JSON 列，没有索引时历史积累会把列表查询拖成全表扫描。
    __table_args__ = (
        Index("ix_v2_pipeline_runs_pipeline_created", "pipeline_id", "created_at"),
        Index("ix_v2_pipeline_runs_task_created", "task_id", "created_at"),
        Index("ix_v2_pipeline_runs_created_at", "created_at"),
        Index("ix_v2_pipeline_runs_trigger_type", "trigger_type"),
    )


class PipelineScriptVersion(Base):
    """Python 脚本流水线的保存历史。

    每次「保存」都把通过执行与格式校验的脚本冻结为一版（最多保留最近
    若干版，见 python_engine.service）。与发布封版快照（PipelineVersion）
    不同：这里是草稿期的编辑历史，供脚本编辑页查看/恢复。
    """
    __tablename__ = "v2_pipeline_script_versions"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    pipeline_id: Mapped[str] = mapped_column(String, ForeignKey("v2_pipelines.id", ondelete="CASCADE"), nullable=False)
    version_no: Mapped[int] = mapped_column(nullable=False)
    script: Mapped[str] = mapped_column(Text, nullable=False)
    # 保存那次执行的输出摘要（列结构/行数/耗时），供历史列表快速判断
    output_columns: Mapped[list | None] = mapped_column(JSON, nullable=True)
    row_count: Mapped[int] = mapped_column(default=0, nullable=False)
    duration_ms: Mapped[int] = mapped_column(default=0, nullable=False)
    created_by: Mapped[str | None] = mapped_column(String, ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("uq_pipeline_script_versions_pipeline_version", "pipeline_id", "version_no", unique=True),
    )
