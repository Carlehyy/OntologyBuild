"""add isolated decision simulation runs

Revision ID: 0045_decision_simulation
Revises: 0044_dataset_data_blob
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect


revision = "0045_decision_simulation"
down_revision = "0044_dataset_data_blob"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 0017 的历史兼容分支会在空库上用当前 metadata 补建缺表；因此从头升级时
    # 本表可能已经存在。真实的 0044 生产库则会走下面的显式建表分支。
    if sa_inspect(op.get_bind()).has_table("fo_decision_simulation_runs"):
        return
    op.create_table(
        "fo_decision_simulation_runs",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("ontology_id", sa.String(), nullable=False),
        sa.Column("ontology_release_id", sa.String(), nullable=True),
        sa.Column("conversation_id", sa.String(), nullable=True),
        sa.Column("created_by", sa.String(), nullable=False),
        sa.Column("model_config_id", sa.String(), nullable=True),
        sa.Column("model_name", sa.String(length=200), nullable=True),
        sa.Column("title", sa.String(length=240), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("specification", sa.JSON(), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("perspectives", sa.JSON(), nullable=False),
        sa.Column("evaluation", sa.JSON(), nullable=False),
        sa.Column("recommendation", sa.JSON(), nullable=False),
        sa.Column("diagnostics", sa.JSON(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["conversation_id"], ["fo_agent_conversations.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["ontology_id"], ["ontology_projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_fo_decision_simulation_runs_ontology_id",
        "fo_decision_simulation_runs", ["ontology_id"],
    )
    op.create_index(
        "ix_fo_decision_simulation_runs_ontology_release_id",
        "fo_decision_simulation_runs", ["ontology_release_id"],
    )
    op.create_index(
        "ix_fo_decision_simulation_runs_conversation_id",
        "fo_decision_simulation_runs", ["conversation_id"],
    )
    op.create_index(
        "ix_fo_decision_simulation_runs_created_by",
        "fo_decision_simulation_runs", ["created_by"],
    )
    op.create_index(
        "ix_decision_simulation_owner_started",
        "fo_decision_simulation_runs", ["ontology_id", "created_by", "started_at"],
    )
    op.create_index(
        "ix_decision_simulation_conversation_started",
        "fo_decision_simulation_runs", ["conversation_id", "started_at"],
    )


def downgrade() -> None:
    if sa_inspect(op.get_bind()).has_table("fo_decision_simulation_runs"):
        op.drop_table("fo_decision_simulation_runs")
