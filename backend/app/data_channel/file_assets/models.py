from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class PipelineFileAsset(Base):
    """Private object uploaded during one managed n8n invocation.

    ``storage_uri`` and ``object_key`` are deliberately internal.  Workflow
    output and browser clients only receive an opaque FileRef id plus the
    authenticated platform download route.
    """

    __tablename__ = "v2_pipeline_file_assets"
    __table_args__ = (
        Index(
            "uq_pipeline_file_assets_idempotency",
            "pipeline_id", "invocation_id", "idempotency_key", unique=True,
        ),
        Index(
            "ix_pipeline_file_assets_expiry",
            "status", "expires_at", "created_at",
        ),
        Index("ix_pipeline_file_assets_dataset_version", "dataset_version_id"),
        CheckConstraint(
            "purpose IN ('preview','run')",
            name="ck_pipeline_file_assets_purpose",
        ),
        CheckConstraint(
            "status IN ('ready','committed','deleted','failed')",
            name="ck_pipeline_file_assets_status",
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    pipeline_id: Mapped[str | None] = mapped_column(
        String,
        ForeignKey("v2_pipelines.id", ondelete="SET NULL"),
        nullable=True,
    )
    workflow_id: Mapped[str] = mapped_column(String(100), nullable=False)
    invocation_id: Mapped[str] = mapped_column(String(100), nullable=False)
    owner_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )
    dataset_version_id: Mapped[str | None] = mapped_column(
        String,
        ForeignKey("v2_dataset_versions.id", ondelete="CASCADE"),
        nullable=True,
    )
    purpose: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ready")
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    original_name: Mapped[str] = mapped_column(String(500), nullable=False)
    object_key: Mapped[str] = mapped_column(Text, nullable=False)
    storage_uri: Mapped[str | None] = mapped_column(Text, nullable=True)
    size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    content_type: Mapped[str] = mapped_column(String(200), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    committed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
