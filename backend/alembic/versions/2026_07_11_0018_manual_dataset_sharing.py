"""人工数据集匿名分享与审批草稿

Revision ID: 0018_manual_dataset_sharing
Revises: 0017_data_management_contract
Create Date: 2026-07-11
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "0018_manual_dataset_sharing"
down_revision = "0017_data_management_contract"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 0003 historically bootstraps all currently registered models with create_all.
    # Therefore a fresh upgrade may already contain these future tables; production
    # databases upgraded from 0017 do not. Keep this migration valid in both cases.
    if not inspect(op.get_bind()).has_table("v2_manual_dataset_shares"):
        op.create_table(
            "v2_manual_dataset_shares",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("dataset_id", sa.String(), nullable=False),
            sa.Column("token_hash", sa.String(64), nullable=False),
            sa.Column("permission", sa.String(10), nullable=False),
            sa.Column("label", sa.String(200), nullable=False, server_default=""),
            sa.Column("created_by", sa.String(), nullable=True),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.ForeignKeyConstraint(["dataset_id"], ["v2_datasets.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
            sa.CheckConstraint("permission IN ('view','edit')", name="ck_manual_dataset_shares_permission"),
        )
        op.create_index("uq_manual_dataset_shares_token_hash", "v2_manual_dataset_shares", ["token_hash"], unique=True)
        op.create_index("ix_manual_dataset_shares_dataset_id", "v2_manual_dataset_shares", ["dataset_id"])

    if not inspect(op.get_bind()).has_table("v2_manual_dataset_changes"):
        op.create_table(
            "v2_manual_dataset_changes",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("share_id", sa.String(), nullable=False),
            sa.Column("dataset_id", sa.String(), nullable=False),
            sa.Column("base_version_no", sa.Integer(), nullable=False),
            sa.Column("edits", sa.JSON(), nullable=False),
            sa.Column("summary", sa.JSON(), nullable=False),
            sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
            sa.Column("review_comment", sa.Text(), nullable=False, server_default=""),
            sa.Column("reviewed_by", sa.String(), nullable=True),
            sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("applied_version_no", sa.Integer(), nullable=True),
            sa.ForeignKeyConstraint(["share_id"], ["v2_manual_dataset_shares.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["dataset_id"], ["v2_datasets.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["reviewed_by"], ["users.id"], ondelete="SET NULL"),
            sa.CheckConstraint("status IN ('pending','approved','rejected')", name="ck_manual_dataset_changes_status"),
        )
        op.create_index("ix_manual_dataset_changes_dataset_status", "v2_manual_dataset_changes", ["dataset_id", "status"])
        op.create_index("ix_manual_dataset_changes_share_id", "v2_manual_dataset_changes", ["share_id"])


def downgrade() -> None:
    inspector = inspect(op.get_bind())
    if inspector.has_table("v2_manual_dataset_changes"):
        op.drop_table("v2_manual_dataset_changes")
    if inspect(op.get_bind()).has_table("v2_manual_dataset_shares"):
        op.drop_table("v2_manual_dataset_shares")
