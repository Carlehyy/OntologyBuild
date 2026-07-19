from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class SuperAssistantConversation(Base):
    __tablename__ = "super_assistant_conversations"
    __table_args__ = (
        Index("ix_sa_conversations_owner_updated", "owner_id", "updated_at"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    owner_id: Mapped[str] = mapped_column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False, default="新会话")
    model_config_id: Mapped[str | None] = mapped_column(String, ForeignKey("model_configs.id", ondelete="SET NULL"), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_now, onupdate=_now)


class SuperAssistantMessage(Base):
    __tablename__ = "super_assistant_messages"
    __table_args__ = (
        Index("ix_sa_messages_conversation_created", "conversation_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    conversation_id: Mapped[str] = mapped_column(
        String, ForeignKey("super_assistant_conversations.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="complete")
    steps: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    token_usage: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_now)


class SuperAssistantToolRun(Base):
    __tablename__ = "super_assistant_tool_runs"
    __table_args__ = (
        Index("ix_sa_tool_runs_conversation_created", "conversation_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    conversation_id: Mapped[str] = mapped_column(
        String, ForeignKey("super_assistant_conversations.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    assistant_message_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("super_assistant_messages.id", ondelete="SET NULL"), nullable=True,
    )
    call_id: Mapped[str] = mapped_column(String(200), nullable=False)
    tool_name: Mapped[str] = mapped_column(String(300), nullable=False)
    server_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("super_assistant_mcp_servers.id", ondelete="SET NULL"), nullable=True,
    )
    arguments: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    result: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="pending")
    requires_confirmation: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    decision: Mapped[str | None] = mapped_column(String(20), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class SuperAssistantSkill(Base):
    __tablename__ = "super_assistant_skills"
    __table_args__ = (
        UniqueConstraint("owner_id", "name", name="uq_sa_skill_owner_name"),
        Index("ix_sa_skills_owner_updated", "owner_id", "updated_at"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    owner_id: Mapped[str] = mapped_column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    triggers: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    folder_path: Mapped[str] = mapped_column(String(1000), nullable=False)
    manifest: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_now, onupdate=_now)


class SuperAssistantMcpServer(Base):
    __tablename__ = "super_assistant_mcp_servers"
    __table_args__ = (
        UniqueConstraint("owner_id", "name", name="uq_sa_mcp_owner_name"),
        Index("ix_sa_mcp_owner_updated", "owner_id", "updated_at"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    owner_id: Mapped[str] = mapped_column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    transport: Mapped[str] = mapped_column(String(30), nullable=False, default="streamable_http")
    url: Mapped[str] = mapped_column(String(1000), nullable=False)
    headers_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    header_names: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    command: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    args: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    env_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    env_names: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    require_confirmation: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    tool_manifest: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    last_test_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    last_test_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    last_tested_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_now, onupdate=_now)
