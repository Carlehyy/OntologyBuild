"""add durable ontology projection fence

Revision ID: 0055_ontology_projection_fence
Revises: 0054_fact_lineage_indexes
"""

from alembic import op
import sqlalchemy as sa


revision = "0055_ontology_projection_fence"
down_revision = "0054_fact_lineage_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ontology_projects",
        sa.Column(
            "projection_status",
            sa.String(length=20),
            nullable=False,
            server_default="ready",
        ),
    )
    op.add_column(
        "ontology_projects",
        sa.Column("projection_error", sa.Text(), nullable=True),
    )
    # Existing SQL/Formal truth may predate the stable-ID, validated Neo4j
    # contract. Never declare those projections ready without rebuilding them.
    projects = sa.table(
        "ontology_projects",
        sa.column("projection_status", sa.String(length=20)),
        sa.column("projection_error", sa.Text()),
    )
    op.execute(
        projects.update().values(
            projection_status="repair_required",
            projection_error=(
                "Upgrade requires a validated Neo4j projection rebuild"
            ),
        )
    )


def downgrade() -> None:
    op.drop_column("ontology_projects", "projection_error")
    op.drop_column("ontology_projects", "projection_status")
