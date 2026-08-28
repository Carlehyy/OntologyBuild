"""tickets tables (工单 / 平台使用反馈)

全角色可用的反馈通道：用户提交工单（标题 + 反馈正文 + 附件），
管理员在五态流水线（pending/verifying/accepted/completed/cancelled）
上处理并留下必填评论。三张表——工单主体、附件（落盘
uploads_dir/tickets/<ticket_id>/）、处理轨迹（每次处理一行，seq 单调递增）。

Revision ID: 0082_tickets
Revises: 0081_assistant_widget_config
Create Date: 2026-08-28
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect


revision = "0082_tickets"
down_revision = "0081_assistant_widget_config"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(sa_inspect(bind).get_table_names())

    if "tickets" not in tables:
        op.create_table(
            "tickets",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("ticket_no", sa.String(length=40), nullable=False),
            sa.Column("title", sa.String(length=500), nullable=False),
            sa.Column("content", sa.Text(), nullable=False),
            sa.Column("submitter_id", sa.String(), nullable=True),
            sa.Column("submitter_name", sa.String(length=200), nullable=True),
            sa.Column("status", sa.String(length=20), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["submitter_id"], ["users.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("ticket_no", name="uq_tickets_ticket_no"),
        )
        op.create_index("ix_tickets_status", "tickets", ["status"])
        op.create_index("ix_tickets_submitter_id", "tickets", ["submitter_id"])
        op.create_index("ix_tickets_created_at", "tickets", ["created_at"])

    if "ticket_attachments" not in tables:
        op.create_table(
            "ticket_attachments",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("ticket_id", sa.String(), nullable=False),
            sa.Column("filename", sa.String(length=500), nullable=False),
            sa.Column("file_path", sa.String(length=1000), nullable=False),
            sa.Column("file_size", sa.Integer(), nullable=True),
            sa.Column("mime_type", sa.String(length=200), nullable=True),
            sa.Column("sha256", sa.String(length=64), nullable=True),
            sa.Column("uploaded_by", sa.String(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["ticket_id"], ["tickets.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "ix_ticket_attachments_ticket_id", "ticket_attachments", ["ticket_id"])

    if "ticket_progress_logs" not in tables:
        op.create_table(
            "ticket_progress_logs",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("ticket_id", sa.String(), nullable=False),
            sa.Column("seq", sa.Integer(), nullable=True),
            sa.Column("from_status", sa.String(length=20), nullable=True),
            sa.Column("to_status", sa.String(length=20), nullable=False),
            sa.Column("comment", sa.Text(), nullable=False),
            sa.Column("actor_id", sa.String(), nullable=True),
            sa.Column("actor_name", sa.String(length=200), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["ticket_id"], ["tickets.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "ix_ticket_progress_ticket_seq", "ticket_progress_logs",
            ["ticket_id", "seq"])


def downgrade() -> None:
    op.drop_index("ix_ticket_progress_ticket_seq", table_name="ticket_progress_logs")
    op.drop_table("ticket_progress_logs")
    op.drop_index("ix_ticket_attachments_ticket_id", table_name="ticket_attachments")
    op.drop_table("ticket_attachments")
    op.drop_index("ix_tickets_created_at", table_name="tickets")
    op.drop_index("ix_tickets_submitter_id", table_name="tickets")
    op.drop_index("ix_tickets_status", table_name="tickets")
    op.drop_table("tickets")
