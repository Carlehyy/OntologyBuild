"""add permanent anonymous sharing for pipeline file assets

Revision ID: 0047_pipeline_file_shares
Revises: 0046_steward_context
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "0047_pipeline_file_shares"
down_revision = "0046_steward_context"
branch_labels = None
depends_on = None


TABLE = "v2_pipeline_file_assets"
TOKEN_INDEX = "uq_pipeline_file_assets_share_token_hash"


def _column_names() -> set[str]:
    return {
        column["name"]
        for column in inspect(op.get_bind()).get_columns(TABLE)
    }


def _index_names() -> set[str]:
    return {
        index["name"]
        for index in inspect(op.get_bind()).get_indexes(TABLE)
        if index.get("name")
    }


def upgrade() -> None:
    if not inspect(op.get_bind()).has_table(TABLE):
        return
    columns = _column_names()
    with op.batch_alter_table(TABLE) as batch:
        if "share_token_hash" not in columns:
            batch.add_column(sa.Column(
                "share_token_hash", sa.String(length=64), nullable=True))
        if "share_token_encrypted" not in columns:
            batch.add_column(sa.Column(
                "share_token_encrypted", sa.Text(), nullable=True))
        if "share_created_at" not in columns:
            batch.add_column(sa.Column(
                "share_created_at", sa.DateTime(timezone=True), nullable=True))
        if "share_revoked_at" not in columns:
            batch.add_column(sa.Column(
                "share_revoked_at", sa.DateTime(timezone=True), nullable=True))
    if TOKEN_INDEX not in _index_names():
        op.create_index(
            TOKEN_INDEX,
            TABLE,
            ["share_token_hash"],
            unique=True,
        )


def downgrade() -> None:
    if not inspect(op.get_bind()).has_table(TABLE):
        return
    if TOKEN_INDEX in _index_names():
        op.drop_index(TOKEN_INDEX, table_name=TABLE)
    columns = _column_names()
    with op.batch_alter_table(TABLE) as batch:
        for name in (
            "share_revoked_at",
            "share_created_at",
            "share_token_encrypted",
            "share_token_hash",
        ):
            if name in columns:
                batch.drop_column(name)
