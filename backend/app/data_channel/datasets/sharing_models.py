"""Anonymous sharing and approval records for manually maintained datasets."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ManualDatasetShare(Base):
    __tablename__ = "v2_manual_dataset_shares"
    __table_args__ = (
        Index("uq_manual_dataset_shares_token_hash", "token_hash", unique=True),
        Index("ix_manual_dataset_shares_dataset_id", "dataset_id"),
        CheckConstraint("permission IN ('view','edit')", name="ck_manual_dataset_shares_permission"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    dataset_id: Mapped[str] = mapped_column(
        String, ForeignKey("v2_datasets.id", ondelete="CASCADE"), nullable=False)
    # The digest remains the public-request lookup key.  The encrypted copy lets
    # authenticated managers retrieve and copy an existing share link again.
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    token_encrypted: Mapped[str] = mapped_column(Text, nullable=False, default="")
    permission: Mapped[str] = mapped_column(String(10), nullable=False)  # view | edit
    label: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    created_by: Mapped[str | None] = mapped_column(
        String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)


class ManualDatasetChange(Base):
    __tablename__ = "v2_manual_dataset_changes"
    __table_args__ = (
        Index("ix_manual_dataset_changes_dataset_status", "dataset_id", "status"),
        Index("ix_manual_dataset_changes_share_id", "share_id"),
        CheckConstraint("status IN ('pending','approved','rejected')", name="ck_manual_dataset_changes_status"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    share_id: Mapped[str] = mapped_column(
        String, ForeignKey("v2_manual_dataset_shares.id", ondelete="CASCADE"), nullable=False)
    dataset_id: Mapped[str] = mapped_column(
        String, ForeignKey("v2_datasets.id", ondelete="CASCADE"), nullable=False)
    base_version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    edits: Mapped[dict] = mapped_column(JSON, nullable=False)
    summary: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    review_comment: Mapped[str] = mapped_column(Text, nullable=False, default="")
    reviewed_by: Mapped[str | None] = mapped_column(
        String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    applied_version_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
