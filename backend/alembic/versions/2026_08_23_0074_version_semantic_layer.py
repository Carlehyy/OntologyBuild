"""version semantic layer: snapshot_semantic + exploration ontology binding

``ontology_versions.snapshot_semantic`` 存储业务语义层产物
（7 类业务画布 + 需求文档 + 指纹），与画布布局一样是展示/语义元数据，
不参与 snapshot_hash / revision 契约。

``bx_sessions.ontology_id`` / ``ontology_version_id`` 记录探索会话绑定的
本体与版本锚点；``bx_drafts.applied_version_id`` 记录草稿应用后落地的版本。
bx_* 表沿用无外键惯例，仅加裸 String 列与查询索引。

Revision ID: 0074_version_semantic_layer
Revises: 0073_drop_agent_config
Create Date: 2026-08-23
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect


revision = "0074_version_semantic_layer"
down_revision = "0073_drop_agent_config"
branch_labels = None
depends_on = None

SESSION_INDEXES = {
    "ix_bx_sessions_ontology_id": ["ontology_id"],
    "ix_bx_sessions_ontology_version_id": ["ontology_version_id"],
}


def _columns(table: str) -> set[str]:
    inspector = sa_inspect(op.get_bind())
    if not inspector.has_table(table):
        return set()
    return {column["name"] for column in inspector.get_columns(table)}


def _indexes(table: str) -> set[str]:
    inspector = sa_inspect(op.get_bind())
    if not inspector.has_table(table):
        return set()
    return {
        index["name"]
        for index in inspector.get_indexes(table)
        if index.get("name")
    }


def upgrade() -> None:
    version_columns = _columns("ontology_versions")
    if version_columns and "snapshot_semantic" not in version_columns:
        with op.batch_alter_table("ontology_versions") as batch:
            batch.add_column(
                sa.Column("snapshot_semantic", sa.JSON(), nullable=True)
            )

    session_columns = _columns("bx_sessions")
    if session_columns:
        with op.batch_alter_table("bx_sessions") as batch:
            if "ontology_id" not in session_columns:
                batch.add_column(
                    sa.Column("ontology_id", sa.String(), nullable=True)
                )
            if "ontology_version_id" not in session_columns:
                batch.add_column(
                    sa.Column("ontology_version_id", sa.String(), nullable=True)
                )
        existing_indexes = _indexes("bx_sessions")
        for name, columns in SESSION_INDEXES.items():
            if name not in existing_indexes:
                op.create_index(name, "bx_sessions", columns, unique=False)

    draft_columns = _columns("bx_drafts")
    if draft_columns and "applied_version_id" not in draft_columns:
        with op.batch_alter_table("bx_drafts") as batch:
            batch.add_column(
                sa.Column("applied_version_id", sa.String(), nullable=True)
            )


def downgrade() -> None:
    if "applied_version_id" in _columns("bx_drafts"):
        with op.batch_alter_table("bx_drafts") as batch:
            batch.drop_column("applied_version_id")

    session_columns = _columns("bx_sessions")
    if session_columns:
        existing_indexes = _indexes("bx_sessions")
        for name in SESSION_INDEXES:
            if name in existing_indexes:
                op.drop_index(name, table_name="bx_sessions")
        with op.batch_alter_table("bx_sessions") as batch:
            if "ontology_version_id" in session_columns:
                batch.drop_column("ontology_version_id")
            if "ontology_id" in session_columns:
                batch.drop_column("ontology_id")

    if "snapshot_semantic" in _columns("ontology_versions"):
        with op.batch_alter_table("ontology_versions") as batch:
            batch.drop_column("snapshot_semantic")
