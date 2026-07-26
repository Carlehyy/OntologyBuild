"""persist canonical relation lineage for promoted trial links

Revision ID: 0048_trial_link_lineage
Revises: 0047_pipeline_file_shares
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "0048_trial_link_lineage"
down_revision = "0047_pipeline_file_shares"
branch_labels = None
depends_on = None


TABLE = "ontology_trial_links"
INDEX = "ix_ontology_trial_links_source_relation_id"


def _columns() -> set[str]:
    return {
        column["name"]
        for column in inspect(op.get_bind()).get_columns(TABLE)
    }


def _indexes() -> set[str]:
    return {
        index["name"]
        for index in inspect(op.get_bind()).get_indexes(TABLE)
        if index.get("name")
    }


def upgrade() -> None:
    if not inspect(op.get_bind()).has_table(TABLE):
        return
    if "source_relation_id" not in _columns():
        with op.batch_alter_table(TABLE) as batch:
            batch.add_column(sa.Column(
                "source_relation_id", sa.String(), nullable=True))
    if INDEX not in _indexes():
        op.create_index(
            INDEX, TABLE, ["source_relation_id"], unique=False)


def downgrade() -> None:
    if not inspect(op.get_bind()).has_table(TABLE):
        return
    if INDEX in _indexes():
        op.drop_index(INDEX, table_name=TABLE)
    if "source_relation_id" in _columns():
        with op.batch_alter_table(TABLE) as batch:
            batch.drop_column("source_relation_id")
