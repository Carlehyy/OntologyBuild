"""add global assistant card-click counter to ontology projects

Revision ID: 0063_ontology_assistant_card_clicks
Revises: 0062_curated_lake_tables
"""

from alembic import op
import sqlalchemy as sa


revision = "0063_ontology_assistant_card_clicks"
down_revision = "0062_curated_lake_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ontology_projects",
        sa.Column(
            "assistant_card_clicks",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )


def downgrade() -> None:
    op.drop_column("ontology_projects", "assistant_card_clicks")
