"""add data steward model-context view state

Revision ID: 0046_steward_context
Revises: 0045_decision_simulation
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "0046_steward_context"
down_revision = "0045_decision_simulation"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    return {column["name"] for column in inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    if not inspect(op.get_bind()).has_table("v2_steward_conversations"):
        return
    columns = _columns("v2_steward_conversations")
    with op.batch_alter_table("v2_steward_conversations") as batch:
        if "context_summary" not in columns:
            batch.add_column(sa.Column(
                "context_summary", sa.Text(), nullable=False, server_default=""))
        if "summary_message_count" not in columns:
            batch.add_column(sa.Column(
                "summary_message_count", sa.Integer(), nullable=False, server_default="0"))
        if "working_memory" not in columns:
            batch.add_column(sa.Column(
                "working_memory", sa.JSON(), nullable=False, server_default="{}"))
        if "context_stats" not in columns:
            batch.add_column(sa.Column(
                "context_stats", sa.JSON(), nullable=False, server_default="{}"))


def downgrade() -> None:
    if not inspect(op.get_bind()).has_table("v2_steward_conversations"):
        return
    columns = _columns("v2_steward_conversations")
    with op.batch_alter_table("v2_steward_conversations") as batch:
        for name in (
            "context_stats",
            "working_memory",
            "summary_message_count",
            "context_summary",
        ):
            if name in columns:
                batch.drop_column(name)
