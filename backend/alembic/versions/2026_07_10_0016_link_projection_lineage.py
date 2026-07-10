"""add formal link source relation lineage

Revision ID: 0016_link_projection_lineage
Revises: 0015_action_release_binding
"""
from alembic import op
import sqlalchemy as sa


revision = "0016_link_projection_lineage"
down_revision = "0015_action_release_binding"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if table not in inspector.get_table_names():
        return set()
    return {column["name"] for column in inspector.get_columns(table)}


def upgrade() -> None:
    columns = _columns("fo_link_instances")
    if not columns or "source_relation_id" in columns:
        return
    with op.batch_alter_table("fo_link_instances") as batch:
        batch.add_column(sa.Column("source_relation_id", sa.String(), nullable=True))
        batch.create_index(
            "ix_fo_link_instances_source_relation_id",
            ["source_relation_id"], unique=False)


def downgrade() -> None:
    if "source_relation_id" not in _columns("fo_link_instances"):
        return
    with op.batch_alter_table("fo_link_instances") as batch:
        batch.drop_index("ix_fo_link_instances_source_relation_id")
        batch.drop_column("source_relation_id")
