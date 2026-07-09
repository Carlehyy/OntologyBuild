import uuid
from datetime import datetime, timezone
from sqlalchemy import String, DateTime, JSON, Float, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base

class OntologyMapping(Base):
    __tablename__ = "v2_ontology_mappings"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    ontology_id: Mapped[str] = mapped_column(String, ForeignKey("ontology_projects.id", ondelete="CASCADE"), nullable=False)
    curated_dataset_id: Mapped[str | None] = mapped_column(String, ForeignKey("v2_curated_datasets.id"), nullable=True)
    entity_class: Mapped[str] = mapped_column(String(200), nullable=False)
    field_mapping: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    # 人工绑定：数据灌入到图谱编辑器里已有的对象实体（model-first 流程的核心）。
    # 为空时按 entity_class 名字匹配已有类型，仍无则由投影自建类型（data-first 流程）。
    target_object_type_id: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="draft")
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

class OntologyLinkMapping(Base):
    __tablename__ = "v2_ontology_link_mappings"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    ontology_id: Mapped[str] = mapped_column(String, ForeignKey("ontology_projects.id", ondelete="CASCADE"), nullable=False)
    src_dataset_id: Mapped[str | None] = mapped_column(String, ForeignKey("v2_curated_datasets.id"), nullable=True)
    tgt_dataset_id: Mapped[str | None] = mapped_column(String, ForeignKey("v2_curated_datasets.id"), nullable=True)
    relation_type: Mapped[str] = mapped_column(String(100), nullable=False)
    src_key: Mapped[str] = mapped_column(String(100), nullable=False)
    tgt_key: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="draft")
    # —— 胖关系（LPG 边属性）——
    # 绑定到手绘 LinkType，让边属性名对齐其 properties schema；为空时按 relation_type 名匹配/自建。
    link_type_id: Mapped[str | None] = mapped_column(String, nullable=True)
    # 连接表 / 关系数据集：为空 → 直连外键「瘦关系」(src_key∈src_dataset, tgt_key∈tgt_dataset)；
    # 有值 → 连接表「胖关系」(src_key/tgt_key 为连接表内指向两端主键的外键列，属性列由 field_mapping 采集)。
    edge_dataset_id: Mapped[str | None] = mapped_column(String, ForeignKey("v2_curated_datasets.id"), nullable=True)
    # {边属性名: 连接表列名} —— 采集进 LinkInstance.properties（镜像 OntologyMapping.field_mapping）。
    field_mapping: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
