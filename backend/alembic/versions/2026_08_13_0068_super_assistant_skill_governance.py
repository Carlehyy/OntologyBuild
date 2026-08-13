"""super assistant skill governance: always_active + usage stats

超级助手技能治理（对标 small-rust-hermes），为 ``super_assistant_skills``
增加三列：

- ``always_active``：常驻技能开关。开启后 SKILL.md 全文直接内联进
  系统提示，不再依赖模型的 use_skill 渐进披露；
- ``use_count`` / ``last_used_at``：use_skill 成功执行的行内计数，
  作为 Skill 目录降权排序（use_count desc, name asc）的信号源，
  使用率为零的老技能自然沉底。

Revision ID: 0068_super_assistant_skill_governance
Revises: 0067_super_assistant_evolution
Create Date: 2026-08-13
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect


revision = "0068_super_assistant_skill_governance"
down_revision = "0067_super_assistant_evolution"
branch_labels = None
depends_on = None


_GOVERNANCE_COLUMNS = ("always_active", "use_count", "last_used_at")


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    if "super_assistant_skills" not in set(inspector.get_table_names()):
        return
    columns = {
        column["name"]
        for column in inspector.get_columns("super_assistant_skills")
    }
    if "always_active" not in columns:
        op.add_column(
            "super_assistant_skills",
            sa.Column("always_active", sa.Boolean(), nullable=False, server_default=sa.false()),
        )
    if "use_count" not in columns:
        op.add_column(
            "super_assistant_skills",
            sa.Column("use_count", sa.Integer(), nullable=False, server_default="0"),
        )
    if "last_used_at" not in columns:
        op.add_column(
            "super_assistant_skills",
            sa.Column("last_used_at", sa.DateTime(), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    if "super_assistant_skills" not in set(inspector.get_table_names()):
        return
    columns = {
        column["name"]
        for column in inspector.get_columns("super_assistant_skills")
    }
    for name in reversed(_GOVERNANCE_COLUMNS):
        if name in columns:
            op.drop_column("super_assistant_skills", name)
