"""业务探索会话文件空间与上下文压缩

Revision ID: 0021_exploration_workspace
Revises: 0020_merge_steward_card
Create Date: 2026-07-12
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "0021_exploration_workspace"
down_revision = "0020_merge_steward_card"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    return {c["name"] for c in inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    if inspect(op.get_bind()).has_table("bx_sessions"):
        cols = _columns("bx_sessions")
        with op.batch_alter_table("bx_sessions") as batch:
            if "context_summary" not in cols:
                batch.add_column(sa.Column("context_summary", sa.Text(), nullable=False, server_default=""))
            if "summary_message_count" not in cols:
                batch.add_column(sa.Column("summary_message_count", sa.Integer(), nullable=False, server_default="0"))
            if "context_stats" not in cols:
                batch.add_column(sa.Column("context_stats", sa.JSON(), nullable=False, server_default="{}"))

    if inspect(op.get_bind()).has_table("bx_attachments"):
        cols = _columns("bx_attachments")
        with op.batch_alter_table("bx_attachments") as batch:
            if "relative_path" not in cols:
                batch.add_column(sa.Column("relative_path", sa.String(500), nullable=False, server_default=""))
            if "sha256" not in cols:
                batch.add_column(sa.Column("sha256", sa.String(64), nullable=True))
            if "version" not in cols:
                batch.add_column(sa.Column("version", sa.Integer(), nullable=False, server_default="1"))
            if "source" not in cols:
                batch.add_column(sa.Column("source", sa.String(20), nullable=False, server_default="upload"))
            if "editable" not in cols:
                batch.add_column(sa.Column("editable", sa.Boolean(), nullable=False, server_default=sa.false()))
            if "updated_at" not in cols:
                batch.add_column(sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()))
        op.execute("UPDATE bx_attachments SET relative_path = filename WHERE relative_path = ''")


def downgrade() -> None:
    if inspect(op.get_bind()).has_table("bx_attachments"):
        cols = _columns("bx_attachments")
        with op.batch_alter_table("bx_attachments") as batch:
            for name in ("updated_at", "editable", "source", "version", "sha256", "relative_path"):
                if name in cols:
                    batch.drop_column(name)
    if inspect(op.get_bind()).has_table("bx_sessions"):
        cols = _columns("bx_sessions")
        with op.batch_alter_table("bx_sessions") as batch:
            for name in ("context_stats", "summary_message_count", "context_summary"):
                if name in cols:
                    batch.drop_column(name)
