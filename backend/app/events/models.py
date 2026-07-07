"""
事件登记 (Event Registry) — 数据模型

平台「智能助手」与「数据通道」之间的事件采集入口。收集真实业务事件
（异常/变更/观察/业务动作……）作为后续本体优化的原始素材。

两条录入通道，来源清晰区分：
  - platform  平台用户填表登记 (source_type=platform)
  - api       第三方系统经 X-API-Key 上传 (source_type=api, 记 ingest_key_id + client_ip)

四张表：
  - RegisteredEvent   事件主体（内容 + 双时态 + 出处溯源 + 本体桥接）
  - EventAttachment   附件（落盘 uploads_dir/events/<event_id>/，带 sha256 校验）
  - EventAuditLog     追加式审计轨迹（每次变更一行，seq 单调递增，可溯源）
  - EventIngestKey    第三方上传密钥（key_hash 存 sha256，明文仅创建时返回一次）

出处词汇（source/actor_id/confidence/supersedes_id）刻意对齐事实层
PropertyFact（app/ontologies/formal_modeling/models.py），保持与建模内核一致。
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    String, Text, DateTime, Float, Boolean, Integer, JSON, ForeignKey,
    UniqueConstraint, Index,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


# —— 常量：所有取值集中在此，service 层做归一 ——
SOURCE_PLATFORM = "platform"   # 平台录入
SOURCE_API = "api"             # 第三方接口上传
SOURCE_SYSTEM = "system"       # 平台内部产生（预留）

STATUS_ACTIVE = "active"
STATUS_ARCHIVED = "archived"

SEVERITIES = ("info", "low", "medium", "high", "critical")

# 审计动作
ACTION_CREATED = "created"
ACTION_UPDATED = "updated"
ACTION_STATUS_CHANGED = "status_changed"
ACTION_ATTACHMENT_ADDED = "attachment_added"
ACTION_ATTACHMENT_REMOVED = "attachment_removed"
ACTION_INGESTED = "ingested"
ACTION_INGEST_DUPLICATE = "ingest_duplicate"


class RegisteredEvent(Base):
    """一条登记的事件。append-only 采集日志的最小单元。"""
    __tablename__ = "event_registry"
    __table_args__ = (
        # (source_system, source_ref) = 幂等键。SQLite 对多个 NULL 不去重，
        # 平台录入无 source_ref 天然互不冲突；第三方带 source_ref 才受约束。
        UniqueConstraint("source_system", "source_ref", name="uq_event_source_ref"),
        Index("ix_event_registry_status", "status"),
        Index("ix_event_registry_source_type", "source_type"),
        Index("ix_event_registry_recorded_at", "recorded_at"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    event_no: Mapped[str] = mapped_column(String(40), unique=True, index=True)

    # —— 内容 ——
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True, default="")
    event_type: Mapped[str | None] = mapped_column(String(100), nullable=True, default="")
    severity: Mapped[str] = mapped_column(String(20), default="info")
    tags: Mapped[list] = mapped_column(JSON, default=list)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)  # 任意结构化数据（第三方机器承载区）

    # —— 时间（双时态）——
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # 业务发生时间
    recorded_at: Mapped[datetime] = mapped_column(DateTime, default=_now)          # 登记入库时间

    # —— 出处 / 溯源（审计核心，对齐 PropertyFact 词汇）——
    source_type: Mapped[str] = mapped_column(String(20), default=SOURCE_PLATFORM)
    source_system: Mapped[str | None] = mapped_column(String(200), nullable=True)  # 第三方系统名
    source_ref: Mapped[str | None] = mapped_column(String(200), nullable=True)     # 外部业务单号
    reporter_type: Mapped[str] = mapped_column(String(20), default="user")         # user|service|device
    reporter_id: Mapped[str | None] = mapped_column(String, nullable=True)
    reporter_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    ingest_key_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)  # 哪把密钥上传
    client_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)       # 来源 IP
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)          # 机器来源置信度

    # —— 本体桥接（为后续本体优化留锚点）——
    ontology_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    subject_ref: Mapped[str | None] = mapped_column(String(300), nullable=True)     # 关联业务对象标识
    supersedes_id: Mapped[str | None] = mapped_column(String, nullable=True)        # 更正链：本事件更正的旧事件

    # —— 生命周期（极简，仅收集与检索）——
    status: Mapped[str] = mapped_column(String(20), default=STATUS_ACTIVE)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class EventAttachment(Base):
    __tablename__ = "event_attachments"
    __table_args__ = (Index("ix_event_attachments_event_id", "event_id"),)

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    event_id: Mapped[str] = mapped_column(
        String, ForeignKey("event_registry.id", ondelete="CASCADE"), nullable=False)
    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    file_path: Mapped[str] = mapped_column(String(1000), nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, default=0)
    mime_type: Mapped[str | None] = mapped_column(String(200), nullable=True)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)  # 完整性校验，溯源可对账
    uploaded_by: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class EventAuditLog(Base):
    """追加式审计轨迹。每次对事件的变更写一行，永不改写——溯源的真理流。"""
    __tablename__ = "event_audit_logs"
    __table_args__ = (Index("ix_event_audit_event_seq", "event_id", "seq"),)

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    event_id: Mapped[str] = mapped_column(
        String, ForeignKey("event_registry.id", ondelete="CASCADE"), nullable=False)
    seq: Mapped[int] = mapped_column(Integer, default=1)  # 每事件内单调递增，确定性排序

    action: Mapped[str] = mapped_column(String(40), nullable=False)
    actor_type: Mapped[str] = mapped_column(String(20), default="user")  # user|service|device|system
    actor_id: Mapped[str | None] = mapped_column(String, nullable=True)
    actor_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    changes: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # {field: {from, to}} 前后值
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class EventIngestKey(Base):
    """第三方上传密钥。key_hash 存 sha256，明文（ob_ingest_<prefix>_<secret>）仅创建时返回一次。"""
    __tablename__ = "event_ingest_keys"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(200), nullable=False)          # 密钥名（= 第三方来源标识）
    key_prefix: Mapped[str] = mapped_column(String(32), index=True)          # 明文可见前缀，便于识别
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)  # sha256(明文)
    # 明文密钥留存，便于在面板反复复制（内部平台按用户要求，牺牲「只存哈希」的安全约定）
    secret_plain: Mapped[str | None] = mapped_column(String(120), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    allowed_source_system: Mapped[str | None] = mapped_column(String(200), nullable=True)  # 可选作用域
    created_by: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
