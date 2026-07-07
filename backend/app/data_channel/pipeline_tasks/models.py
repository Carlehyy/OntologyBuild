"""
流水线调度任务 (Pipeline Task)
任务池的定位是「调度方」：数据流水线与数据资产湖之间的桥梁。
一条任务只声明三件事——触发哪条已发布的流水线、按什么调度节奏触发、
流水线的最终产物以什么入库方式写进数据资产湖。
数据采集/加工细节（连接、表、水印等）全部属于流水线职责，任务不越界。
"""
from __future__ import annotations
import enum
import uuid
from datetime import datetime
from sqlalchemy import Column, String, DateTime, Boolean, Integer, Text
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
    pipeline_id = Column(String(36), nullable=False, index=True)

    # 入库方式
    write_mode = Column(String(20), default=WriteMode.OVERWRITE.value, nullable=False)
    primary_key = Column(String(200), default="")          # upsert 模式的主键列（逗号分隔多列）
    soft_delete_column = Column(String(200), default="")   # upsert 模式可选：源端软删除标识列
    skip_empty = Column(Boolean, default=True, nullable=False)  # 空输出保护：流水线输出 0 行时跳过入库

    # 调度
    schedule_type = Column(String(20), default=TaskScheduleType.MANUAL.value, nullable=False)
    cron_expression = Column(String(100), default="")
    interval_seconds = Column(Integer, default=0)
    enabled = Column(Boolean, default=True, nullable=False)

    # 运行状态（最近一次）
    status = Column(String(20), default=PipelineTaskStatus.IDLE.value, nullable=False)
    last_run_at = Column(DateTime, nullable=True)
    last_rows = Column(Integer, default=0)
    last_error = Column(Text, default="")

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

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
            "schedule_type": self.schedule_type,
            "cron_expression": self.cron_expression,
            "interval_seconds": self.interval_seconds,
            "enabled": bool(self.enabled),
            "status": self.status,
            "last_run_at": self.last_run_at.isoformat() if self.last_run_at else None,
            "last_rows": self.last_rows,
            "last_error": self.last_error,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
