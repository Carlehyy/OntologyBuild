"""
本体版本化模型 — 支持版本历史、diff、回滚
"""
import uuid
from datetime import datetime, timezone
from sqlalchemy import (
    String, DateTime, Text, JSON, Integer, ForeignKey, UniqueConstraint, Index,
)
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class OntologyVersion(Base):
    """完整本体快照节点。

    release 节点形成稳定发布主线（v0/v1/v2）；draft 节点形成从任意完整
    快照分出的树（v1.1/v1.1.1）。节点从不做增量存储，避免祖先损坏或缺失
    时无法还原待发布结构。
    """
    __tablename__ = "ontology_versions"
    __table_args__ = (UniqueConstraint("ontology_id", "version_number"),)

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    ontology_id: Mapped[str] = mapped_column(String, ForeignKey("ontology_projects.id", ondelete="CASCADE"), nullable=False)
    version_number: Mapped[str] = mapped_column(String(20), nullable=False)  # e.g. "v1.2.3"
    version_label: Mapped[str] = mapped_column(String(100), nullable=True)  # e.g. "正式发布版"
    description: Mapped[str] = mapped_column(Text, nullable=True)

    parent_version_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("ontology_versions.id", ondelete="SET NULL"),
        nullable=True, index=True)
    base_release_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("ontology_versions.id", ondelete="SET NULL"),
        nullable=True, index=True)
    promoted_from_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("ontology_versions.id", ondelete="SET NULL"),
        nullable=True)
    # draft | release。试跑是附着于冻结草稿 revision 的运行记录，不是第三类快照。
    node_kind: Mapped[str] = mapped_column(
        String(20), nullable=False, default="release", server_default="release")
    # editing | trial_ready | released | superseded
    lifecycle_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="released", server_default="released")
    revision: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0")
    snapshot_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

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


class OntologyTrialRun(Base):
    """针对草稿精确 revision 的隔离试跑记录。"""
    __tablename__ = "ontology_trial_runs"
    __table_args__ = (
        Index("ix_ontology_trial_runs_version_created", "version_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: str(uuid.uuid4()))
    ontology_id: Mapped[str] = mapped_column(
        String, ForeignKey("ontology_projects.id", ondelete="CASCADE"),
        nullable=False, index=True)
    version_id: Mapped[str] = mapped_column(
        String, ForeignKey("ontology_versions.id", ondelete="CASCADE"),
        nullable=False, index=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # running | passed | failed | stale
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="running", server_default="running")
    dataset_versions: Mapped[list] = mapped_column(JSON, default=list)
    result_json: Mapped[dict] = mapped_column(JSON, default=dict)
    impact_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_by: Mapped[str] = mapped_column(
        String, ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class OntologyTrialObject(Base):
    """真实湖数据在试跑空间中的完整对象投影。"""
    __tablename__ = "ontology_trial_objects"
    __table_args__ = (
        UniqueConstraint("trial_run_id", "object_id",
                         name="uq_trial_object_run_object"),
        Index("ix_trial_object_run_type", "trial_run_id", "object_type_id"),
    )

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: str(uuid.uuid4()))
    trial_run_id: Mapped[str] = mapped_column(
        String, ForeignKey("ontology_trial_runs.id", ondelete="CASCADE"),
        nullable=False)
    object_id: Mapped[str] = mapped_column(String, nullable=False)
    object_type_id: Mapped[str] = mapped_column(String, nullable=False)
    properties: Mapped[dict] = mapped_column(JSON, default=dict)
    source_dataset_id: Mapped[str | None] = mapped_column(String, nullable=True)
    source_dataset_version_id: Mapped[str | None] = mapped_column(String, nullable=True)
    external_id: Mapped[str | None] = mapped_column(String(500), nullable=True)


class OntologyTrialLink(Base):
    """试跑空间中的完整关系投影。"""
    __tablename__ = "ontology_trial_links"
    __table_args__ = (
        UniqueConstraint("trial_run_id", "link_id",
                         name="uq_trial_link_run_link"),
        Index("ix_trial_link_run_type", "trial_run_id", "link_type_id"),
    )

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: str(uuid.uuid4()))
    trial_run_id: Mapped[str] = mapped_column(
        String, ForeignKey("ontology_trial_runs.id", ondelete="CASCADE"),
        nullable=False)
    link_id: Mapped[str] = mapped_column(String, nullable=False)
    link_type_id: Mapped[str] = mapped_column(String, nullable=False)
    source_object_id: Mapped[str] = mapped_column(String, nullable=False)
    target_object_id: Mapped[str] = mapped_column(String, nullable=False)
    properties: Mapped[dict] = mapped_column(JSON, default=dict)


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
