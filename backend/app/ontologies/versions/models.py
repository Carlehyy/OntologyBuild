"""
本体版本化模型 — 支持版本历史、diff、回滚
"""
import uuid
from datetime import datetime, timezone
from sqlalchemy import String, DateTime, Text, JSON, Integer, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class OntologyVersion(Base):
    """本体版本快照 — 每次发布时创建"""
    __tablename__ = "ontology_versions"
    __table_args__ = (UniqueConstraint("ontology_id", "version_number"),)

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    ontology_id: Mapped[str] = mapped_column(String, ForeignKey("ontology_projects.id", ondelete="CASCADE"), nullable=False)
    version_number: Mapped[str] = mapped_column(String(20), nullable=False)  # e.g. "v1.2.3"
    version_label: Mapped[str] = mapped_column(String(100), nullable=True)  # e.g. "正式发布版"
    description: Mapped[str] = mapped_column(Text, nullable=True)

    # 快照内容
    snapshot_entities: Mapped[list] = mapped_column(JSON, default=list)
    snapshot_relations: Mapped[list] = mapped_column(JSON, default=list)
    snapshot_logic: Mapped[list] = mapped_column(JSON, default=list)
    snapshot_actions: Mapped[list] = mapped_column(JSON, default=list)
    # 正规本体模型快照（图谱编辑器的 fo_* 模式层）
    # {objectTypes: [], linkTypes: [], actions: [], functions: []}
    snapshot_formal: Mapped[dict] = mapped_column(JSON, nullable=True)

    # 变更统计
    change_summary: Mapped[dict] = mapped_column(JSON, default=dict)  # {added: N, modified: N, deleted: N}

    created_by: Mapped[str] = mapped_column(String, ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


class OntologyChangeLog(Base):
    """变更日志 — 记录每次编辑操作"""
    __tablename__ = "ontology_change_logs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    ontology_id: Mapped[str] = mapped_column(String, ForeignKey("ontology_projects.id", ondelete="CASCADE"), nullable=False)
    version_id: Mapped[str] = mapped_column(String, ForeignKey("ontology_versions.id"), nullable=True)

    # 变更类型: create, update, delete
    action: Mapped[str] = mapped_column(String(20), nullable=False)
    # 对象类型: entity, relation, logic, action, ontology
    object_type: Mapped[str] = mapped_column(String(30), nullable=False)
    object_id: Mapped[str] = mapped_column(String, nullable=False)
    object_name: Mapped[str] = mapped_column(String(200), nullable=True)

    # 变更前后
    before: Mapped[dict] = mapped_column(JSON, nullable=True)
    after: Mapped[dict] = mapped_column(JSON, nullable=True)

    created_by: Mapped[str] = mapped_column(String, ForeignKey("users.id"), nullable=False)
    created_by_name: Mapped[str] = mapped_column(String(50), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
