"""merge assistant graph and analysis report migration heads

Revision ID: 0027_merge_agent_graph_reports
Revises: 0026_agent_graph_indexes, 0026_analysis_reports
Create Date: 2026-07-15
"""


revision = "0027_merge_agent_graph_reports"
down_revision = ("0026_agent_graph_indexes", "0026_analysis_reports")
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Both parent migrations own independent schema changes; only join history."""


def downgrade() -> None:
    """Downgrading the merge node restores both independent migration heads."""
