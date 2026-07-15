"""add governed analysis report templates and runs

Revision ID: 0026_analysis_reports
Revises: 0025_ontology_evolution
Create Date: 2026-07-15
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect


revision = "0026_analysis_reports"
down_revision = "0025_ontology_evolution"
branch_labels = None
depends_on = None


def _has_table(name: str) -> bool:
    return name in sa_inspect(op.get_bind()).get_table_names()


def upgrade() -> None:
    if not _has_table("fo_analysis_report_templates"):
        op.create_table(
            "fo_analysis_report_templates",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("ontology_id", sa.String(), nullable=False),
            sa.Column("created_by", sa.String(), nullable=False),
            sa.Column("name", sa.String(length=240), nullable=False),
            sa.Column("description", sa.Text(), nullable=False, server_default=""),
            sa.Column("source_prompt", sa.Text(), nullable=False, server_default=""),
            sa.Column("generation_mode", sa.String(length=20), nullable=False, server_default="ai"),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="draft"),
            sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("sections", sa.JSON(), nullable=False),
            sa.Column("style", sa.JSON(), nullable=False),
            sa.Column("default_model_id", sa.String(), nullable=True),
            sa.Column("last_preview_run_id", sa.String(), nullable=True),
            sa.Column("last_preview_revision", sa.Integer(), nullable=True),
            sa.Column("published_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["ontology_id"], ["ontology_projects.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        )
        op.create_index("ix_report_templates_ontology_id", "fo_analysis_report_templates", ["ontology_id"])
        op.create_index("ix_report_templates_created_by", "fo_analysis_report_templates", ["created_by"])
        op.create_index(
            "ix_report_templates_ontology_status",
            "fo_analysis_report_templates", ["ontology_id", "status"])

    if not _has_table("fo_analysis_report_runs"):
        op.create_table(
            "fo_analysis_report_runs",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("template_id", sa.String(), nullable=False),
            sa.Column("ontology_id", sa.String(), nullable=False),
            sa.Column("created_by", sa.String(), nullable=False),
            sa.Column("trigger_type", sa.String(length=20), nullable=False, server_default="preview"),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="running"),
            sa.Column("template_revision", sa.Integer(), nullable=False),
            sa.Column("template_snapshot", sa.JSON(), nullable=False),
            sa.Column("section_results", sa.JSON(), nullable=False),
            sa.Column("quality_report", sa.JSON(), nullable=False),
            sa.Column("html_content", sa.Text(), nullable=False, server_default=""),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("started_at", sa.DateTime(), nullable=False),
            sa.Column("completed_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["template_id"], ["fo_analysis_report_templates.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["ontology_id"], ["ontology_projects.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        )
        op.create_index("ix_report_runs_template_id", "fo_analysis_report_runs", ["template_id"])
        op.create_index("ix_report_runs_ontology_id", "fo_analysis_report_runs", ["ontology_id"])
        op.create_index("ix_report_runs_created_by", "fo_analysis_report_runs", ["created_by"])
        op.create_index(
            "ix_report_runs_template_started",
            "fo_analysis_report_runs", ["template_id", "started_at"])


def downgrade() -> None:
    if _has_table("fo_analysis_report_runs"):
        op.drop_table("fo_analysis_report_runs")
    if _has_table("fo_analysis_report_templates"):
        op.drop_table("fo_analysis_report_templates")
