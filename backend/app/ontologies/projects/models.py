import uuid
from datetime import datetime, timezone
from sqlalchemy import String, DateTime, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base

class OntologyProject(Base):
    __tablename__ = "ontology_projects"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    domain: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=True)
    # A stable built-in icon key (for example ``network`` or ``shopping-cart``).
    # Keep this nullable so historical rows remain readable without a backfill.
    icon: Mapped[str | None] = mapped_column(String(50), nullable=True)
    version: Mapped[str] = mapped_column(String(20), default="v0")
    status: Mapped[str] = mapped_column(String(20), default="draft")
    # 运行时唯一读取指针。不要再通过“最大的版本号”推断当前发布版。
    current_release_id: Mapped[str | None] = mapped_column(
        String,
        ForeignKey(
            "ontology_versions.id", ondelete="SET NULL", use_alter=True,
            name="fk_ontology_projects_current_release_id",
        ),
        nullable=True,
    )
    build_mode: Mapped[str] = mapped_column(String(30), default="simple_llm", nullable=True)
    created_by: Mapped[str] = mapped_column(String, ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
