"""add governed assistant dynamic sentinels

Revision ID: 0038_dynamic_sentinels
Revises: 0037_custom_role
Create Date: 2026-07-20
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect


revision = "0038_dynamic_sentinels"
down_revision = "0037_custom_role"
branch_labels = None
depends_on = None


def _columns() -> set[str]:
    inspector = sa_inspect(op.get_bind())
    if "sentinels" not in inspector.get_table_names():
        return set()
    return {column["name"] for column in inspector.get_columns("sentinels")}


def _indexes() -> set[str]:
    inspector = sa_inspect(op.get_bind())
    if "sentinels" not in inspector.get_table_names():
        return set()
    return {index["name"] for index in inspector.get_indexes("sentinels")}


def _checks() -> set[str]:
    inspector = sa_inspect(op.get_bind())
    if "sentinels" not in inspector.get_table_names():
        return set()
    return {item.get("name") for item in inspector.get_check_constraints("sentinels")}


def upgrade() -> None:
    additions = (
        ("origin", sa.Column("origin", sa.String(32), nullable=False,
                             server_default="release_builtin")),
        ("bound_release_id", sa.Column("bound_release_id", sa.String(), nullable=True)),
        ("created_by", sa.Column("created_by", sa.String(), nullable=True)),
        ("definition_revision", sa.Column("definition_revision", sa.Integer(),
                                           nullable=False, server_default="1")),
        ("validation_report", sa.Column("validation_report", sa.JSON(), nullable=True)),
        ("last_trial_at", sa.Column("last_trial_at", sa.DateTime(), nullable=True)),
        ("last_trial_release_id", sa.Column("last_trial_release_id", sa.String(), nullable=True)),
        ("last_trial_revision", sa.Column("last_trial_revision", sa.Integer(), nullable=True)),
        ("last_trial_report", sa.Column("last_trial_report", sa.JSON(), nullable=True)),
        ("retired_at", sa.Column("retired_at", sa.DateTime(), nullable=True)),
    )
    if _columns():
        for name, column in additions:
            if name not in _columns():
                op.add_column("sentinels", column)

        for column in ("origin", "bound_release_id", "created_by", "retired_at"):
            index_name = f"ix_sentinels_{column}"
            if index_name not in _indexes():
                op.create_index(index_name, "sentinels", [column], unique=False)

        if "ck_sentinels_origin" not in _checks():
            with op.batch_alter_table("sentinels") as batch:
                batch.create_check_constraint(
                    "ck_sentinels_origin",
                    "origin IN ('release_builtin', 'assistant_dynamic')",
                )

    inspector = sa_inspect(op.get_bind())
    if "fo_agent_conversations" in inspector.get_table_names():
        conversation_columns = {
            item["name"] for item in inspector.get_columns("fo_agent_conversations")
        }
        if "ontology_release_id" not in conversation_columns:
            op.add_column(
                "fo_agent_conversations",
                sa.Column("ontology_release_id", sa.String(), nullable=True),
            )
        conversation_indexes = {
            item["name"] for item in inspector.get_indexes("fo_agent_conversations")
        }
        if "ix_fo_agent_conversations_ontology_release_id" not in conversation_indexes:
            op.create_index(
                "ix_fo_agent_conversations_ontology_release_id",
                "fo_agent_conversations", ["ontology_release_id"], unique=False,
            )


def downgrade() -> None:
    inspector = sa_inspect(op.get_bind())
    if "fo_agent_conversations" in inspector.get_table_names():
        conversation_columns = {
            item["name"] for item in inspector.get_columns("fo_agent_conversations")
        }
        if "ontology_release_id" in conversation_columns:
            conversation_indexes = {
                item["name"] for item in inspector.get_indexes("fo_agent_conversations")
            }
            with op.batch_alter_table("fo_agent_conversations") as batch:
                if "ix_fo_agent_conversations_ontology_release_id" in conversation_indexes:
                    batch.drop_index("ix_fo_agent_conversations_ontology_release_id")
                batch.drop_column("ontology_release_id")
    if not _columns():
        return
    with op.batch_alter_table("sentinels") as batch:
        if "ck_sentinels_origin" in _checks():
            batch.drop_constraint("ck_sentinels_origin", type_="check")
        for column in ("origin", "bound_release_id", "created_by", "retired_at"):
            index_name = f"ix_sentinels_{column}"
            if index_name in _indexes():
                batch.drop_index(index_name)
        for column in reversed((
            "origin", "bound_release_id", "created_by", "definition_revision",
            "validation_report", "last_trial_at", "last_trial_release_id",
            "last_trial_revision", "last_trial_report", "retired_at",
        )):
            if column in _columns():
                batch.drop_column(column)
