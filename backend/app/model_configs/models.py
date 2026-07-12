import uuid
from datetime import datetime, timezone
from sqlalchemy import String, DateTime, ForeignKey, Text, JSON, Boolean, Integer, Float, Index, text
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base

class ModelConfig(Base):
    __tablename__ = "model_configs"
    __table_args__ = (
        Index(
            "uq_model_configs_default_llm",
            "is_default",
            unique=True,
            postgresql_where=text("is_default = true AND config_type = 'llm'"),
            sqlite_where=text("is_default = 1 AND config_type = 'llm'"),
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    config_type: Mapped[str] = mapped_column(String(30), nullable=False, default="llm")  # llm|ocr|other
    api_base: Mapped[str] = mapped_column(String(500), nullable=True)
    api_key_encrypted: Mapped[str] = mapped_column(Text, nullable=True)
    provider: Mapped[str] = mapped_column(String(50), nullable=False)  # llm: openai|anthropic|compatible; ocr: paddleocr|tesseract|external_api
    models: Mapped[dict] = mapped_column(JSON, default=list)
    options: Mapped[dict] = mapped_column(JSON, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_test_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    last_tested_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_test_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_by: Mapped[str] = mapped_column(String, ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class ModelCallLog(Base):
    """每次 LLM 调用的轻量统计记录 — 不保存调用内容，仅保存指标。"""
    __tablename__ = "model_call_logs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    model_config_id: Mapped[str] = mapped_column(String, ForeignKey("model_configs.id", ondelete="CASCADE"), nullable=False, index=True)
    model_name: Mapped[str] = mapped_column(String(200), nullable=False)
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)  # success | error | timeout
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_message: Mapped[str] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
