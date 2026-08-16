"""
流水线调度任务 (Pipeline Task)
任务池的定位是「调度方」：数据流水线与数据资产湖之间的桥梁。
一条任务声明四件事——触发哪条已发布的流水线、按什么调度节奏触发、
流水线的最终产物以什么入库方式写进数据资产湖、可选的源端增量游标
（cursor_column + last_cursor_value：声明后平台把上游产出的游标词法
最大值在运行成功时回写为下次起点；如何按游标过滤源端仍属流水线职责）。
"""
from __future__ import annotations
import enum
import uuid
from datetime import datetime
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
)
from app.database import Base


def _uuid():
    return str(uuid.uuid4())


class WriteMode(str, enum.Enum):
    OVERWRITE = "overwrite"          # 全量覆盖：资产 = 本次流水线输出
    APPEND = "append"                # 直接追加：本次输出追加到资产尾部
    UPSERT = "upsert"                # 主键合并：按主键去重取最新，可选软删除标记
    APPEND_DEDUP = "append_dedup"    # 去重追加：按整行内容去重后追加（无主键防重复导入）


class TaskScheduleType(str, enum.Enum):
    MANUAL = "MANUAL"
    CRON = "CRON"
    INTERVAL = "INTERVAL"


class PipelineTaskStatus(str, enum.Enum):
    IDLE = "idle"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"


class PipelineTask(Base):
    """流水线调度任务 — 触发已发布流水线，最终产物按入库方式进资产湖"""
    __tablename__ = "v2_pipeline_tasks"

    id = Column(String(36), primary_key=True, default=_uuid)
    name = Column(String(200), nullable=False, index=True)
    description = Column(Text, default="")

    # 关联流水线（仅允许已发布）
    pipeline_id = Column(
        String(36),
        ForeignKey("v2_pipelines.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    # 入库方式
    write_mode = Column(String(20), default=WriteMode.OVERWRITE.value, nullable=False)
    # 兼容列：仅保存流水线发布契约的主键快照，API/UI 均不可独立定义。
    # 待存量任务完成迁移后可删除；运行时始终读取 Pipeline.column_definitions。
    primary_key = Column(String(200), default="")
    soft_delete_column = Column(String(200), default="")   # upsert 模式可选：源端软删除标识列
    skip_empty = Column(Boolean, default=True, nullable=False)  # 空输出保护：流水线输出 0 行时跳过入库

    # 源端增量游标（可选）：cursor_column 为流水线契约列名（词法可比较——
    # ISO8601 时间戳 / 零填充序号 / 自增 ID）；last_cursor_value 为上次成功
    # 运行推进到的水位。两者皆空 = 每次全量（存量任务默认形态）。
    cursor_column = Column(String(200), default="")
    last_cursor_value = Column(Text, default="")

    # 调度
    schedule_type = Column(String(20), default=TaskScheduleType.MANUAL.value, nullable=False)
    cron_expression = Column(String(100), default="")
    interval_seconds = Column(Integer, default=0)
    enabled = Column(Boolean, default=True, nullable=False)

    # 运行状态（最近一次）
    status = Column(String(20), default=PipelineTaskStatus.IDLE.value, nullable=False)
    # 数据库执行租约：调度器/HTTP/多进程必须先原子领取，不能依赖进程内
    # ``status == running`` 检查。token 防止旧执行者在租约过期后覆盖新执行结果。
    execution_token = Column(String(36), nullable=True, index=True)
    lease_expires_at = Column(DateTime, nullable=True, index=True)
    last_run_at = Column(DateTime, nullable=True)
    last_rows = Column(Integer, default=0)
    last_error = Column(Text, default="")

    # 收件箱投递的默认负责人。历史任务由迁移从关联流水线创建人回填；
    # 无法确定时，投递服务会安全回退到启用中的管理员。
    created_by = Column(
        String,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    __table_args__ = (
        CheckConstraint(
            "write_mode IN ('overwrite','append','upsert','append_dedup')",
            name="ck_pipeline_tasks_write_mode",
        ),
        CheckConstraint(
            "schedule_type IN ('MANUAL','CRON','INTERVAL')",
            name="ck_pipeline_tasks_schedule_type",
        ),
        CheckConstraint(
            "status IN ('idle','running','success','failed')",
            name="ck_pipeline_tasks_status",
        ),
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "pipeline_id": self.pipeline_id,
            "write_mode": self.write_mode,
            "primary_key": self.primary_key,
            "soft_delete_column": self.soft_delete_column,
            "skip_empty": bool(self.skip_empty),
            "cursor_column": self.cursor_column or "",
            "last_cursor_value": self.last_cursor_value or "",
            "schedule_type": self.schedule_type,
            "cron_expression": self.cron_expression,
            "interval_seconds": self.interval_seconds,
            "enabled": bool(self.enabled),
            "status": self.status,
            "lease_expires_at": self.lease_expires_at.isoformat() if self.lease_expires_at else None,
            "last_run_at": self.last_run_at.isoformat() if self.last_run_at else None,
            "last_rows": self.last_rows,
            "last_error": self.last_error,
            "created_by": self.created_by,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
