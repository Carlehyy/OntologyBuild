"""
数据同步任务模型 (Data Sync Task)
参考 Palantir Foundry 的 Sync 设计：Sync 层只负责"把数据从源头可靠地搬进来并版本化"，
复杂转换交给 Pipeline 处理。同步任务与 Connection 解耦（Connection 只描述连接信息），
SyncTask 描述"同步什么、怎么同步、何时同步、同步到哪"。
"""
from __future__ import annotations
import enum
import uuid
from datetime import datetime
from sqlalchemy import Column, String, DateTime, Boolean, Integer, Text, JSON, ForeignKey, Index, Enum as SAEnum
from sqlalchemy.orm import relationship
from app.database import Base


def _uuid():
    return str(uuid.uuid4())


class SyncMode(str, enum.Enum):
    SNAPSHOT = "SNAPSHOT"   # 全量快照：每次全量覆盖，产生新版本
    APPEND = "APPEND"       # 增量追加：基于 watermark 追加新增记录


class ScheduleType(str, enum.Enum):
    MANUAL = "MANUAL"
    CRON = "CRON"
    INTERVAL = "INTERVAL"


class SyncStatus(str, enum.Enum):
    IDLE = "idle"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"


class TriggerType(str, enum.Enum):
    MANUAL = "manual"
    SCHEDULED = "scheduled"
    PIPELINE = "pipeline"


class HistoryStatus(str, enum.Enum):
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    CANCELLED = "cancelled"


class DataSyncTask(Base):
    """数据同步任务 — 一条任务绑定一个源连接 + 一张源表/一段查询 + 一个目标 Dataset"""
    __tablename__ = "v2_data_sync_tasks"

    id = Column(String(36), primary_key=True, default=_uuid)
    name = Column(String(200), nullable=False, index=True)
    description = Column(Text, default="")

    # 源
    connection_id = Column(String(36), ForeignKey("v2_connections.id", ondelete="CASCADE"), nullable=False, index=True)
    source_table = Column(String(200), default="")  # 目标表名（SQL 类连接必选）
    source_query = Column(Text, default="")         # 自定义 SQL（与 source_table 二选一）

    # 同步模式
    # SNAPSHOT: 每次拉全量，生成新版本覆盖 Dataset 的当前数据（历史版本可回滚）
    # APPEND:   基于 watermark_column 增量追加，不做物理 UPDATE/DELETE
    #           可选 primary_key 做按主键去重取最新；可选 is_deleted_column 做软删除识别
    sync_mode = Column(String(20), default="APPEND", nullable=False)
    primary_key = Column(String(200), default="")            # 去重主键列名（APPEND 模式下生效，逗号分隔多列）
    watermark_column = Column(String(200), default="")      # 增量水印列（时间戳/自增ID，APPEND 模式必选）
    is_deleted_column = Column(String(200), default="")     # 软删除标识列（源端逻辑删除字段，如 is_deleted/del_flag）

    # 调度
    schedule_type = Column(String(20), default="MANUAL", nullable=False)  # MANUAL / CRON / INTERVAL
    cron_expression = Column(String(100), default="")
    interval_seconds = Column(Integer, default=0)
    enabled = Column(Boolean, default=True, nullable=False)

    # 链式触发
    trigger_pipeline_id = Column(String(36), ForeignKey("v2_pipelines.id", ondelete="SET NULL"), nullable=True)

    # 运行状态（最近一次）
    status = Column(String(20), default="IDLE", nullable=False)  # IDLE/RUNNING/SUCCESS/FAILED
    last_sync_at = Column(DateTime, nullable=True)
    last_rows = Column(Integer, default=0)
    last_error = Column(Text, default="")
    last_watermark = Column(Text, default="")  # 文本形式存储（与列类型兼容，字符串化）

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # 关系
    connection = relationship("Connection", backref="sync_tasks", foreign_keys=[connection_id])
    trigger_pipeline = relationship("Pipeline", foreign_keys=[trigger_pipeline_id])
    histories = relationship(
        "DataSyncHistory",
        back_populates="task",
        cascade="all, delete-orphan",
        order_by="DataSyncHistory.started_at.desc()",
    )

    __table_args__ = (
        Index("idx_sync_conn", "connection_id"),
        Index("idx_sync_status", "status"),
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "connection_id": self.connection_id,
            "source_table": self.source_table,
            "source_query": self.source_query,
            "sync_mode": self.sync_mode,
            "primary_key": self.primary_key,
            "watermark_column": self.watermark_column,
            "is_deleted_column": self.is_deleted_column,
            "schedule_type": self.schedule_type,
            "cron_expression": self.cron_expression,
            "interval_seconds": self.interval_seconds,
            "enabled": self.enabled,
            "trigger_pipeline_id": self.trigger_pipeline_id,
            "status": self.status,
            "last_sync_at": self.last_sync_at.isoformat() if self.last_sync_at else None,
            "last_rows": self.last_rows,
            "last_error": self.last_error,
            "last_watermark": self.last_watermark,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class DataSyncHistory(Base):
    """同步任务执行历史 — 每次触发一条记录"""
    __tablename__ = "v2_data_sync_histories"

    id = Column(String(36), primary_key=True, default=_uuid)
    task_id = Column(String(36), ForeignKey("v2_data_sync_tasks.id", ondelete="CASCADE"), nullable=False, index=True)
    trigger_type = Column(String(20), default="MANUAL", nullable=False)  # MANUAL / SCHEDULED

    started_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    ended_at = Column(DateTime, nullable=True)
    duration_ms = Column(Integer, default=0)

    status = Column(String(20), default="RUNNING", nullable=False)  # RUNNING/SUCCESS/FAILED
    source_count = Column(Integer, default=0)     # 本次从源端拉取的行数
    inserted_count = Column(Integer, default=0)   # 新增行数
    updated_count = Column(Integer, default=0)    # 去重后更新行数（主键冲突替换）
    deleted_count = Column(Integer, default=0)    # 软删除标记行数
    error_message = Column(Text, default="")

    watermark_before = Column(Text, default="")
    watermark_after = Column(Text, default="")

    # 输出目标
    dataset_id = Column(String(36), nullable=True)
    dataset_version = Column(Integer, default=0)

    task = relationship("DataSyncTask", back_populates="histories")

    __table_args__ = (
        Index("idx_sync_history_task", "task_id", "started_at"),
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "task_id": self.task_id,
            "trigger_type": self.trigger_type,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "ended_at": self.ended_at.isoformat() if self.ended_at else None,
            "duration_ms": self.duration_ms,
            "status": self.status,
            "source_count": self.source_count,
            "inserted_count": self.inserted_count,
            "updated_count": self.updated_count,
            "deleted_count": self.deleted_count,
            "error_message": self.error_message,
            "watermark_before": self.watermark_before,
            "watermark_after": self.watermark_after,
            "dataset_id": self.dataset_id,
            "dataset_version": self.dataset_version,
        }
