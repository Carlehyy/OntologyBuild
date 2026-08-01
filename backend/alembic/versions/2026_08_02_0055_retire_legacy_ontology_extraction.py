"""retire the legacy document-to-ontology extraction subsystem

Revision ID: 0055_retire_legacy_extraction
Revises: 0054_fact_lineage_indexes

Data-loss notice
----------------
The five tables removed here belong exclusively to the retired rules,
prompt-template, Open Interfaces, v1 upload/execution, and v2 extraction
flows.  ``upgrade`` permanently discards their rows.  ``downgrade`` restores
the historical table shapes only for schema round-trip verification and
forensics; it is not a supported application rollback and cannot reconstruct
discarded rows or uploaded objects.  Application rollback requires restoring
the retained pre-migration database/object-storage backup before starting the
old runtime.
"""

from __future__ import annotations

import logging

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "0055_retire_legacy_extraction"
down_revision = "0054_fact_lineage_indexes"
branch_labels = None
depends_on = None


LOGGER = logging.getLogger("alembic.runtime.migration")
RETIRED_TABLES_IN_DROP_ORDER = (
    "extraction_tasks",
    "uploaded_files",
    "prompts",
    "rules_config",
    "mcp_interface_configs",
)
DATA_LOSS_NOTICE = (
    "Retiring legacy ontology extraction tables permanently discards their "
    "rows; downgrade recreates empty tables only."
)


def _has_table(table_name: str) -> bool:
    return inspect(op.get_bind()).has_table(table_name)


def _drop_if_present(table_name: str) -> None:
    if _has_table(table_name):
        op.drop_table(table_name)


def upgrade() -> None:
    LOGGER.warning("%s Tables: %s", DATA_LOSS_NOTICE, ", ".join(
        RETIRED_TABLES_IN_DROP_ORDER,
    ))
    # extraction_tasks must precede prompts because it owns prompt_id.
    # uploaded_files is removed before its legacy ontology upload API vanishes.
    for table_name in RETIRED_TABLES_IN_DROP_ORDER:
        _drop_if_present(table_name)


def downgrade() -> None:
    LOGGER.warning("Downgrade restores empty legacy tables only; removed data "
                   "and uploaded objects are not recoverable from this revision.")

    if not _has_table("rules_config"):
        op.create_table(
            "rules_config",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("rule_key", sa.String(length=100), nullable=False),
            sa.Column("rule_value", sa.String(length=200), nullable=False),
            sa.Column("rule_label_cn", sa.String(length=200), nullable=False),
            sa.Column("rule_label_en", sa.String(length=200), nullable=False),
            sa.Column("editable", sa.Boolean(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("rule_key"),
        )

    if not _has_table("prompts"):
        op.create_table(
            "prompts",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("name", sa.String(length=200), nullable=False),
            sa.Column("domain", sa.String(length=100), nullable=False),
            sa.Column("content", sa.Text(), nullable=False),
            sa.Column("version", sa.String(length=20), nullable=False),
            sa.Column("created_by", sa.String(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
        )

    if not _has_table("uploaded_files"):
        op.create_table(
            "uploaded_files",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("ontology_id", sa.String(), nullable=False),
            sa.Column("filename", sa.String(length=500), nullable=False),
            sa.Column("file_path", sa.String(length=1000), nullable=False),
            sa.Column("file_size", sa.Integer(), nullable=False),
            sa.Column("mime_type", sa.String(length=200), nullable=True),
            sa.Column("converted_md", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(
                ["ontology_id"],
                ["ontology_projects.id"],
                ondelete="CASCADE",
            ),
            sa.PrimaryKeyConstraint("id"),
        )

    if not _has_table("extraction_tasks"):
        op.create_table(
            "extraction_tasks",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("ontology_id", sa.String(), nullable=False),
            sa.Column("prompt_id", sa.String(), nullable=True),
            sa.Column("model_id", sa.String(), nullable=True),
            sa.Column("status", sa.String(length=20), nullable=False),
            sa.Column("parameters", sa.JSON(), nullable=False),
            sa.Column("progress", sa.JSON(), nullable=False),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("validation_report", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["model_id"], ["model_configs.id"]),
            sa.ForeignKeyConstraint(
                ["ontology_id"],
                ["ontology_projects.id"],
                ondelete="CASCADE",
            ),
            sa.ForeignKeyConstraint(["prompt_id"], ["prompts.id"]),
            sa.PrimaryKeyConstraint("id"),
        )

    if not _has_table("mcp_interface_configs"):
        op.create_table(
            "mcp_interface_configs",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("operation_id", sa.String(length=300), nullable=False),
            sa.Column("method", sa.String(length=10), nullable=False),
            sa.Column("path", sa.String(length=500), nullable=False),
            sa.Column("enabled", sa.Boolean(), nullable=False),
            sa.Column("display_name", sa.String(length=200), nullable=True),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("created_by", sa.String(), nullable=True),
            sa.Column("updated_by", sa.String(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
            sa.ForeignKeyConstraint(["updated_by"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("operation_id"),
        )
        op.create_index(
            "ix_mcp_interface_configs_operation_id",
            "mcp_interface_configs",
            ["operation_id"],
        )
