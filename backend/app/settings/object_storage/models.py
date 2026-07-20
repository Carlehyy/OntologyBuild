from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Index, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class MinioConfig(Base):
    """Administrator-managed, single-row MinIO connection configuration."""

    __tablename__ = "minio_config"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: "default")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    endpoint: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    secure: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    region: Mapped[str] = mapped_column(String(100), nullable=False, default="us-east-1")
    default_bucket: Mapped[str] = mapped_column(String(255), nullable=False, default="openontology")
    access_key_encrypted: Mapped[str] = mapped_column(Text, nullable=False, default="")
    secret_key_encrypted: Mapped[str] = mapped_column(Text, nullable=False, default="")
    read_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    write_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    delete_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    mcp_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    mcp_token_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    mcp_token_hint: Mapped[str] = mapped_column(String(12), nullable=False, default="")
    connected: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_test_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    last_test_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    last_tested_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_now, onupdate=_now)


class MinioOperationAudit(Base):
    """Bounded, credential-free audit trail for HTTP and MCP mutations."""

    __tablename__ = "minio_operation_audits"
    __table_args__ = (
        Index("ix_minio_audit_created", "created_at"),
        Index("ix_minio_audit_operation_created", "operation", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    actor_type: Mapped[str] = mapped_column(String(30), nullable=False, default="platform")
    actor_id: Mapped[str | None] = mapped_column(String, nullable=True)
    operation: Mapped[str] = mapped_column(String(100), nullable=False)
    bucket: Mapped[str | None] = mapped_column(String(255), nullable=True)
    object_key: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    details: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_now)
