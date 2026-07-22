"""Store tabular dataset version payloads in the platform database.

Revision ID: 0044_dataset_data_blob
Revises: 0043_repair_sa_mcp_table
"""

from alembic import op
import sqlalchemy as sa


revision = "0044_dataset_data_blob"
down_revision = "0043_repair_sa_mcp_table"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "v2_dataset_versions",
        sa.Column("data_blob", sa.LargeBinary(), nullable=True),
    )
    op.add_column(
        "v2_dataset_versions",
        sa.Column("data_size", sa.BigInteger(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("v2_dataset_versions", "data_size")
    op.drop_column("v2_dataset_versions", "data_blob")
