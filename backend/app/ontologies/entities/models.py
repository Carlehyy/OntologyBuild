import uuid
from datetime import datetime, timezone
from sqlalchemy import String, DateTime, ForeignKey, Text, Float, JSON
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base

class Entity(Base):
    """[DEPRECATED] 旧扁平实体模型。

    已被正规本体 ObjectType / ObjectInstance (app.models.ontology_formal)
    取代。本表仅作为数据流水线的中间落地层保留，build_all() 写入后由
    formal_projection 投影到正规本体；v1 读端 (entities/graph router) 已
    通过 legacy_bridge 改读正规本体，不再以本表为可信数据源。

    勿在新代码中直接依赖本模型；新建模一律走 formal router / 图谱编辑器。
    """
    __tablename__ = "entities"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    ontology_id: Mapped[str] = mapped_column(String, ForeignKey("ontology_projects.id", ondelete="CASCADE"), nullable=False)
    name_cn: Mapped[str] = mapped_column(String(200), nullable=False)
    name_en: Mapped[str] = mapped_column(String(200), nullable=True)
    name_abbr: Mapped[str] = mapped_column(String(50), nullable=True)
    snomed_id: Mapped[str] = mapped_column(String(50), nullable=True)
    canonical_id: Mapped[str] = mapped_column(String(200), nullable=True)
    type: Mapped[str] = mapped_column(String(100), nullable=True)
    description: Mapped[str] = mapped_column(Text, nullable=True)
    properties: Mapped[dict] = mapped_column(JSON, default=dict)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    version: Mapped[str] = mapped_column(String(20), default="v0.1")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
