"""assistant evaluation flywheel M2 (沙箱双臂实验)

数据飞轮 M2：优化提案（草稿变更）、双臂沙箱实验及其逐条评分快照；
基准集增加本体绑定；本体助手会话表增加沙箱标记（用户侧列表不可见）。
Revision ID: 0086_assistant_eval_flywheel_m2
Revises: 0085_assistant_eval_flywheel_m1
Create Date: 2026-08-30
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect


revision = "0086_assistant_eval_flywheel_m2"
down_revision = "0085_assistant_eval_flywheel_m1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(sa_inspect(bind).get_table_names())

    # 部分 stamp 的库可能没有早期迁移建的表：反射列前先确认表存在
    if "fo_agent_conversations" in tables:
        conv_columns = {c["name"] for c in sa_inspect(bind)
                        .get_columns("fo_agent_conversations")}
        if "is_sandbox" not in conv_columns:
            op.add_column("fo_agent_conversations",
                          sa.Column("is_sandbox", sa.Boolean(), nullable=False,
                                    server_default=sa.text("false")))

    if "assistant_eval_benchmark_sets" in tables:
        bench_columns = {c["name"] for c in sa_inspect(bind)
                         .get_columns("assistant_eval_benchmark_sets")}
        if "ontology_id" not in bench_columns:
            op.add_column("assistant_eval_benchmark_sets",
                          sa.Column("ontology_id", sa.String(), nullable=True))
            op.create_index("ix_ae_bench_sets_ontology",
                            "assistant_eval_benchmark_sets", ["ontology_id"])

    if "assistant_eval_proposals" not in tables:
        op.create_table(
            "assistant_eval_proposals",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("ontology_id", sa.String(), nullable=False),
            sa.Column("assistant_key", sa.String(length=50), nullable=False),
            sa.Column("type", sa.String(length=20), nullable=False),
            sa.Column("title", sa.String(length=200), nullable=False),
            sa.Column("rationale", sa.Text(), nullable=False),
            sa.Column("payload", sa.JSON(), nullable=False),
            sa.Column("evidence", sa.JSON(), nullable=False),
            sa.Column("status", sa.String(length=20), nullable=False),
            sa.Column("created_by", sa.String(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_ae_proposals_ontology_created",
                        "assistant_eval_proposals",
                        ["ontology_id", "created_at"])

    if "assistant_eval_experiments" not in tables:
        op.create_table(
            "assistant_eval_experiments",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("ontology_id", sa.String(), nullable=False),
            sa.Column("proposal_id", sa.String(), nullable=False),
            sa.Column("benchmark_set_id", sa.String(), nullable=True),
            sa.Column("status", sa.String(length=20), nullable=False),
            sa.Column("params", sa.JSON(), nullable=False),
            sa.Column("judge_model_config_id", sa.String(), nullable=True),
            sa.Column("judge_model_name", sa.String(length=200), nullable=False),
            sa.Column("result", sa.JSON(), nullable=False),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("created_by", sa.String(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("finished_at", sa.DateTime(), nullable=True),
            sa.Column("duration_ms", sa.Integer(), nullable=True),
            sa.ForeignKeyConstraint(
                ["proposal_id"], ["assistant_eval_proposals.id"], ondelete="CASCADE"
            ),
            sa.ForeignKeyConstraint(
                ["benchmark_set_id"], ["assistant_eval_benchmark_sets.id"],
                ondelete="SET NULL",
            ),
            sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_ae_experiments_ontology_created",
                        "assistant_eval_experiments",
                        ["ontology_id", "created_at"])
        op.create_index("ix_ae_experiments_proposal", "assistant_eval_experiments",
                        ["proposal_id"])

    if "assistant_eval_experiment_items" not in tables:
        op.create_table(
            "assistant_eval_experiment_items",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("experiment_id", sa.String(), nullable=False),
            sa.Column("arm", sa.String(length=10), nullable=False),
            sa.Column("conversation_id", sa.String(), nullable=False),
            sa.Column("conversation_title", sa.String(length=300), nullable=False),
            sa.Column("split", sa.String(length=10), nullable=False),
            sa.Column("overall_score", sa.Float(), nullable=True),
            sa.Column("scores", sa.JSON(), nullable=False),
            sa.Column("flags", sa.JSON(), nullable=False),
            sa.Column("transcript", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(
                ["experiment_id"], ["assistant_eval_experiments.id"], ondelete="CASCADE"
            ),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_ae_exp_items_experiment",
                        "assistant_eval_experiment_items", ["experiment_id"])


def downgrade() -> None:
    bind = op.get_bind()
    tables = set(sa_inspect(bind).get_table_names())
    if "assistant_eval_experiment_items" in tables:
        op.drop_index("ix_ae_exp_items_experiment",
                      table_name="assistant_eval_experiment_items")
        op.drop_table("assistant_eval_experiment_items")
    if "assistant_eval_experiments" in tables:
        # 表可能由初始 metadata 建出（索引名与 ORM 一致），逐个防御式删除
        op.drop_index("ix_ae_experiments_proposal",
                      table_name="assistant_eval_experiments")
        op.drop_index("ix_ae_experiments_ontology_created",
                      table_name="assistant_eval_experiments")
        op.drop_table("assistant_eval_experiments")
    if "assistant_eval_proposals" in tables:
        op.drop_index("ix_ae_proposals_ontology_created",
                      table_name="assistant_eval_proposals")
        op.drop_table("assistant_eval_proposals")
    if "assistant_eval_benchmark_sets" in tables:
        bench_columns = {c["name"] for c in sa_inspect(bind)
                         .get_columns("assistant_eval_benchmark_sets")}
        if "ontology_id" in bench_columns:
            op.drop_index("ix_ae_bench_sets_ontology",
                          table_name="assistant_eval_benchmark_sets")
            op.drop_column("assistant_eval_benchmark_sets", "ontology_id")
    if "fo_agent_conversations" in tables:
        conv_columns = {c["name"] for c in sa_inspect(bind)
                        .get_columns("fo_agent_conversations")}
        if "is_sandbox" in conv_columns:
            op.drop_column("fo_agent_conversations", "is_sandbox")
