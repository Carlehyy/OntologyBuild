"""assistant evaluation flywheel M3 (值守自动化: 版本化 / 自动投产 / 回退)

数据飞轮 M3：AgentProfile 版本快照链（自动投产的回退锚点，投产前
生产抽样评分作看守基线）与值守开关配置（每本体一条，定时自转优化
循环，含预算硬顶与连续失败熔断）。
Revision ID: 0087_assistant_eval_flywheel_m3
Revises: 0086_assistant_eval_flywheel_m2
Create Date: 2026-08-30
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect


revision = "0087_assistant_eval_flywheel_m3"
down_revision = "0086_assistant_eval_flywheel_m2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(sa_inspect(bind).get_table_names())

    if "assistant_eval_profile_versions" not in tables:
        op.create_table(
            "assistant_eval_profile_versions",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("ontology_id", sa.String(), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("snapshot", sa.JSON(), nullable=False),
            sa.Column("source", sa.JSON(), nullable=False),
            sa.Column("status", sa.String(length=20), nullable=False),
            sa.Column("pre_apply_stats", sa.JSON(), nullable=False),
            sa.Column("verified", sa.Boolean(), nullable=False),
            sa.Column("created_by", sa.String(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_ae_versions_ontology_created",
                        "assistant_eval_profile_versions",
                        ["ontology_id", "created_at"])

    if "assistant_eval_autopilot_configs" not in tables:
        op.create_table(
            "assistant_eval_autopilot_configs",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("ontology_id", sa.String(), nullable=False),
            sa.Column("enabled", sa.Boolean(), nullable=False),
            sa.Column("run_at", sa.String(length=5), nullable=False),
            sa.Column("benchmark_set_id", sa.String(), nullable=True),
            sa.Column("dimension_keys", sa.JSON(), nullable=False),
            sa.Column("model_config_id", sa.String(), nullable=True),
            sa.Column("threshold", sa.Float(), nullable=False),
            sa.Column("max_applies_per_week", sa.Integer(), nullable=False),
            sa.Column("sample_days", sa.Integer(), nullable=False),
            sa.Column("suspended", sa.Boolean(), nullable=False),
            sa.Column("suspend_reason", sa.String(length=500), nullable=False),
            sa.Column("last_dispatched_at", sa.DateTime(), nullable=True),
            sa.Column("last_cycle_at", sa.DateTime(), nullable=True),
            sa.Column("last_cycle_status", sa.String(length=30), nullable=False),
            sa.Column("consecutive_failures", sa.Integer(), nullable=False),
            sa.Column("created_by", sa.String(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(
                ["benchmark_set_id"], ["assistant_eval_benchmark_sets.id"],
                ondelete="SET NULL",
            ),
            sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("ontology_id", name="uq_ae_autopilot_ontology"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    tables = set(sa_inspect(bind).get_table_names())
    if "assistant_eval_autopilot_configs" in tables:
        op.drop_table("assistant_eval_autopilot_configs")
    if "assistant_eval_profile_versions" in tables:
        op.drop_index("ix_ae_versions_ontology_created",
                      table_name="assistant_eval_profile_versions")
        op.drop_table("assistant_eval_profile_versions")
