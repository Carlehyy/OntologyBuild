"""add model call log query index

Revision ID: 0033_model_call_log_query_index
Revises: 0032_super_assistant
Create Date: 2026-07-18
"""
from alembic import op
from sqlalchemy import inspect as sa_inspect


revision = "0033_model_call_log_query_index"
down_revision = "0032_super_assistant"
branch_labels = None
depends_on = None


INDEX_NAME = "ix_model_call_logs_model_created"


def upgrade() -> None:
    inspector = sa_inspect(op.get_bind())
    if not inspector.has_table("model_call_logs"):
        return
    indexes = {index["name"] for index in inspector.get_indexes("model_call_logs")}
    if INDEX_NAME not in indexes:
        op.create_index(INDEX_NAME, "model_call_logs", ["model_config_id", "created_at"])


def downgrade() -> None:
    inspector = sa_inspect(op.get_bind())
    if not inspector.has_table("model_call_logs"):
        return
    indexes = {index["name"] for index in inspector.get_indexes("model_call_logs")}
    if INDEX_NAME in indexes:
        op.drop_index(INDEX_NAME, table_name="model_call_logs")
