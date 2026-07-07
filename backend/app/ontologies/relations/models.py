import uuid
from datetime import datetime, timezone
from sqlalchemy import String, DateTime, ForeignKey, Float, JSON
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base

class Relation(Base):
    """[DEPRECATED] 旧扁平关系模型。

    已被正规本体 LinkType / LinkInstance (app.models.ontology_formal) 取代。
    仅作流水线中间落地层保留；可信数据源为正规本体，v1 读端经 legacy_bridge
    改读正规本体。勿在新代码中直接依赖本模型。
    """
    __tablename__ = "relations"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    ontology_id: Mapped[str] = mapped_column(String, ForeignKey("ontology_projects.id", ondelete="CASCADE"), nullable=False)
    source_entity: Mapped[str] = mapped_column(String, ForeignKey("entities.id", ondelete="CASCADE"), nullable=False)
    target_entity: Mapped[str] = mapped_column(String, ForeignKey("entities.id", ondelete="CASCADE"), nullable=False)
    type: Mapped[str] = mapped_column(String(100), nullable=False)
    properties: Mapped[dict] = mapped_column(JSON, default=dict)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
