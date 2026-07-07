import uuid
from datetime import datetime, timezone
from sqlalchemy import String, DateTime, Boolean
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class AgentConfig(Base):
    """Single-row configuration for QwenPaw agent integration."""

    __tablename__ = "agent_config"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: "default"
    )
    base_url: Mapped[str] = mapped_column(
        String(500), nullable=False, default=""
    )
    auth_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    username: Mapped[str] = mapped_column(
        String(200), nullable=False, default=""
    )
    password_encrypted: Mapped[str] = mapped_column(
        String(500), nullable=False, default=""
    )
    token: Mapped[str] = mapped_column(
        String(2000), nullable=False, default=""
    )
    target_agent_id: Mapped[str] = mapped_column(
        String(200), nullable=False, default=""
    )
    target_agent_name: Mapped[str] = mapped_column(
        String(200), nullable=False, default=""
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
