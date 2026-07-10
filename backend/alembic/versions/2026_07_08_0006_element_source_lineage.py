"""add source lineage column to ontology element tables

业务探索草稿落地时写入血缘出处（sessionId/documentId/draftId/draftKey/sourceRefs），
补齐「每条事实带出处指针」原则在 Schema 层的对称性。

Revision ID: 0006_element_source_lineage
Revises: 0005_model_call_log
Create Date: 2026-07-08
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect

revision = "0006_element_source_lineage"
down_revision = "0005_model_call_log"
branch_labels = None
depends_on = None

_TABLES = ["fo_object_types", "fo_link_types", "fo_action_types", "fo_functions", "sentinels"]


def _column_exists(table: str, column: str) -> bool:
    conn = op.get_bind()
    inspector = sa_inspect(conn)
    if not inspector.has_table(table):
        return False
    return column in [c["name"] for c in inspector.get_columns(table)]


def upgrade() -> None:
    for table in _TABLES:
        # 这些是可选的本体编辑子系统表；某个部署没有启用该子系统时跳过。
        if sa_inspect(op.get_bind()).has_table(table) and not _column_exists(table, "source"):
            op.add_column(table, sa.Column("source", sa.JSON(), nullable=True))


def downgrade() -> None:
    for table in _TABLES:
        if sa_inspect(op.get_bind()).has_table(table) and _column_exists(table, "source"):
            op.drop_column(table, "source")
