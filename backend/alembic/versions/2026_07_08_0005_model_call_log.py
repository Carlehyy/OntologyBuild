"""add model call log table

Revision ID: 0005_model_call_log
Revises: 0004_model_config_state
Create Date: 2026-07-08
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect

revision = "0005_model_call_log"
down_revision = "0004_model_config_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa_inspect(op.get_bind())
    if not inspector.has_table("model_call_logs"):
        op.create_table(
            "model_call_logs",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("model_config_id", sa.String(), nullable=False),
            sa.Column("model_name", sa.String(200), nullable=False),
            sa.Column("provider", sa.String(50), nullable=False),
            sa.Column("status", sa.String(20), nullable=False),
            sa.Column("latency_ms", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.PrimaryKeyConstraint("id"),
            sa.ForeignKeyConstraint(["model_config_id"], ["model_configs.id"], ondelete="CASCADE"),
        )
        inspector = sa_inspect(op.get_bind())
    indexes = {index["name"] for index in inspector.get_indexes("model_call_logs")}
    if "ix_model_call_logs_model_config_id" not in indexes:
        op.create_index("ix_model_call_logs_model_config_id", "model_call_logs", ["model_config_id"])
    if "ix_model_call_logs_created_at" not in indexes:
        op.create_index("ix_model_call_logs_created_at", "model_call_logs", ["created_at"])


def downgrade() -> None:
    inspector = sa_inspect(op.get_bind())
    if not inspector.has_table("model_call_logs"):
        return
    indexes = {index["name"] for index in inspector.get_indexes("model_call_logs")}
    if "ix_model_call_logs_created_at" in indexes:
        op.drop_index("ix_model_call_logs_created_at", table_name="model_call_logs")
    if "ix_model_call_logs_model_config_id" in indexes:
        op.drop_index("ix_model_call_logs_model_config_id", table_name="model_call_logs")
    op.drop_table("model_call_logs")
