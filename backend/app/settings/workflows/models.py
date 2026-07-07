from datetime import datetime, timezone
from sqlalchemy import String, DateTime, Boolean, Integer
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class WorkflowConfig(Base):
    """Single-row configuration for workflow/n8n integration."""

    __tablename__ = "workflow_config"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: "default"
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    api_url: Mapped[str] = mapped_column(
        String(500), nullable=False, default=""
    )
    api_key_encrypted: Mapped[str] = mapped_column(
        String(1000), nullable=False, default=""
    )
    timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
