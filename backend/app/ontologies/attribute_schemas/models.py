"""
类型化属性 Schema — 结构化属性定义与校验
"""
import uuid
from datetime import datetime, timezone
from sqlalchemy import String, DateTime, Text, JSON, Boolean, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class AttributeSchema(Base):
    """本体属性定义 — 每个属性有类型、约束、校验规则"""
    __tablename__ = "attribute_schemas"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    ontology_id: Mapped[str] = mapped_column(String, ForeignKey("ontology_projects.id", ondelete="CASCADE"), nullable=False)

    # 属性标识
    name: Mapped[str] = mapped_column(String(100), nullable=False)  # 内部名 e.g. "registered_capital"
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)  # 显示名 e.g. "注册资本"
    description: Mapped[str] = mapped_column(Text, nullable=True)

    # 数据类型: string, number, integer, boolean, date, enum, range, url, email
    data_type: Mapped[str] = mapped_column(String(30), nullable=False, default="string")

    # 约束条件 (JSON)
    constraints: Mapped[dict] = mapped_column(JSON, default=dict)
    # constraints 结构示例:
    # {
    #   "required": true,
    #   "min_length": 1,
    #   "max_length": 200,
    #   "pattern": "^\\d+$",
    #   "enum": ["A", "B", "C"],
    #   "min": 0,
    #   "max": 1000000000,
    #   "unit": "万元",
    #   "precision": 2,
    #   "date_format": "%Y-%m-%d",
    # }

    # 默认值
    default_value: Mapped[str] = mapped_column(String(500), nullable=True)

    # 适用对象类型 (哪些entity type可以用这个属性)
    applies_to_types: Mapped[list] = mapped_column(JSON, default=list)

    # 是否启用
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    # 排序
    sort_order: Mapped[int] = mapped_column(default=0)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class VocabularyEntry(Base):
    """词表条目 — 同义词/别名管理，用于实体对齐和抽取"""
    __tablename__ = "vocabulary_entries"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    ontology_id: Mapped[str] = mapped_column(String, ForeignKey("ontology_projects.id", ondelete="CASCADE"), nullable=False)

    # 标准词 (canonical)
    canonical: Mapped[str] = mapped_column(String(200), nullable=False)

    # 同义词列表
    synonyms: Mapped[list] = mapped_column(JSON, default=list)

    # 缩写
    abbreviations: Mapped[list] = mapped_column(JSON, default=list)

    # 实体类型关联
    entity_type: Mapped[str] = mapped_column(String(100), nullable=True)

    # 关联实体ID
    linked_entity_id: Mapped[str] = mapped_column(String, ForeignKey("entities.id"), nullable=True)

    # 来源: manual, extraction, import
    source: Mapped[str] = mapped_column(String(20), default="manual")

    # 置信度
    confidence: Mapped[float] = mapped_column(default=1.0)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
