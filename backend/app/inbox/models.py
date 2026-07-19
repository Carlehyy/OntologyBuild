from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    # The existing operational tables persist UTC-naive values. Keep the inbox
    # on the same convention and serialize them with an explicit Z at the API.
    return datetime.utcnow()


class InboxItem(Base):
    """One business incident or work item, shared by all of its recipients."""

    __tablename__ = "inbox_items"
    __table_args__ = (
        CheckConstraint("kind IN ('task','alert','notice')", name="ck_inbox_items_kind"),
        CheckConstraint(
            "priority IN ('urgent','high','normal','low')",
            name="ck_inbox_items_priority",
        ),
        CheckConstraint(
            "business_state IN ('open','resolved','cancelled','expired')",
            name="ck_inbox_items_business_state",
        ),
        Index("ix_inbox_items_source", "source_system", "source_type", "source_id"),
        Index("ix_inbox_items_state_last", "business_state", "last_occurred_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    schema_version: Mapped[str] = mapped_column(String(12), nullable=False, default="v1")
    source_system: Mapped[str] = mapped_column(String(80), nullable=False)
    source_type: Mapped[str] = mapped_column(String(120), nullable=False)
    source_id: Mapped[str] = mapped_column(String(200), nullable=False)
    correlation_key: Mapped[str] = mapped_column(String(300), nullable=False)
    # A portable partial-unique constraint: populated only while the item is
    # open and cleared on resolution. NULL values may repeat on SQLite/Postgres.
    open_key: Mapped[str | None] = mapped_column(String(255), nullable=True, unique=True)

    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    priority: Mapped[str] = mapped_column(String(20), nullable=False, default="normal")
    business_state: Mapped[str] = mapped_column(String(20), nullable=False, default="open")
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    safe_context: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    resource: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    actions: Mapped[list] = mapped_column(JSON, nullable=False, default=list)

    occurrence_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    latest_occurrence_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    first_occurred_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_now)
    last_occurred_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_now)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    resolution_reason: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_now, onupdate=_now)


class InboxDelivery(Base):
    """Per-user delivery/read state for an inbox item."""

    __tablename__ = "inbox_deliveries"
    __table_args__ = (
        UniqueConstraint("item_id", "recipient_user_id", name="uq_inbox_delivery_recipient"),
        CheckConstraint(
            "delivery_state IN ('unread','read','archived')",
            name="ck_inbox_deliveries_state",
        ),
        Index("ix_inbox_deliveries_user_state", "recipient_user_id", "delivery_state"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    item_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("inbox_items.id", ondelete="CASCADE"), nullable=False, index=True
    )
    recipient_user_id: Mapped[str] = mapped_column(
        String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    delivery_state: Mapped[str] = mapped_column(String(20), nullable=False, default="unread")
    read_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_now, onupdate=_now)


class InboxEventReceipt(Base):
    """Idempotency receipt for the versioned producer contract."""

    __tablename__ = "inbox_event_receipts"

    event_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    operation: Mapped[str] = mapped_column(String(20), nullable=False)
    source_system: Mapped[str] = mapped_column(String(80), nullable=False)
    source_type: Mapped[str] = mapped_column(String(120), nullable=False)
    item_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("inbox_items.id", ondelete="SET NULL"), nullable=True
    )
    processed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_now)


class InboxOutboxEvent(Base):
    """Durable hand-off from a domain transaction to the inbox projection."""

    __tablename__ = "inbox_outbox_events"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','processing','completed')",
            name="ck_inbox_outbox_status",
        ),
        Index("ix_inbox_outbox_pending", "status", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(120), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_now)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
