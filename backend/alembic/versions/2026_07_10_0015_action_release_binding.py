"""bind action approvals to an immutable ontology release

Revision ID: 0015_action_release_binding
Revises: 0014_schema_reconciliation
"""
from alembic import op
import sqlalchemy as sa


revision = "0015_action_release_binding"
down_revision = "0014_schema_reconciliation"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if table not in inspector.get_table_names():
        return set()
    return {column["name"] for column in inspector.get_columns(table)}


def upgrade() -> None:
    columns = _columns("fo_action_logs")
    if not columns or "ontology_version" in columns:
        return
    with op.batch_alter_table("fo_action_logs") as batch:
        batch.add_column(sa.Column("ontology_version", sa.String(length=20), nullable=True))
        batch.create_index("ix_fo_action_logs_ontology_version", ["ontology_version"], unique=False)


def downgrade() -> None:
    if "ontology_version" not in _columns("fo_action_logs"):
        return
    with op.batch_alter_table("fo_action_logs") as batch:
        batch.drop_index("ix_fo_action_logs_ontology_version")
        batch.drop_column("ontology_version")
