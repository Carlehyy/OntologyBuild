import uuid
from datetime import datetime, timezone
from sqlalchemy import String, DateTime, Boolean
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base

class RulesConfig(Base):
    __tablename__ = "rules_config"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    rule_key: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    rule_value: Mapped[str] = mapped_column(String(200), nullable=False)
    rule_label_cn: Mapped[str] = mapped_column(String(200), nullable=False)
    rule_label_en: Mapped[str] = mapped_column(String(200), nullable=False)
    editable: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
