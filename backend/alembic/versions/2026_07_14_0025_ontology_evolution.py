"""add complete ontology version tree and isolated trial runs

Revision ID: 0025_ontology_evolution
Revises: 0024_dataset_version_events
Create Date: 2026-07-14
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect


revision = "0025_ontology_evolution"
down_revision = "0024_dataset_version_events"
branch_labels = None
depends_on = None


def _has_table(name: str) -> bool:
    return name in sa_inspect(op.get_bind()).get_table_names()


def _columns(name: str) -> set[str]:
    if not _has_table(name):
        return set()
    return {item["name"] for item in sa_inspect(op.get_bind()).get_columns(name)}


def upgrade() -> None:
    project_columns = _columns("ontology_projects")
    if "current_release_id" not in project_columns:
        with op.batch_alter_table("ontology_projects") as batch:
            batch.add_column(sa.Column("current_release_id", sa.String(), nullable=True))
            batch.create_foreign_key(
                "fk_ontology_projects_current_release_id", "ontology_versions",
                ["current_release_id"], ["id"], ondelete="SET NULL")

    version_columns = _columns("ontology_versions")
    additions = (
        ("parent_version_id", sa.String(), True),
        ("base_release_id", sa.String(), True),
        ("promoted_from_id", sa.String(), True),
        ("node_kind", sa.String(length=20), False),
        ("lifecycle_status", sa.String(length=20), False),
        ("revision", sa.Integer(), False),
        ("snapshot_hash", sa.String(length=64), True),
        ("published_at", sa.DateTime(), True),
    )
    if any(name not in version_columns for name, _, _ in additions):
        with op.batch_alter_table("ontology_versions") as batch:
            for name, type_, nullable in additions:
                if name in version_columns:
                    continue
                server_default = None
                if name == "node_kind":
                    server_default = "release"
                elif name == "lifecycle_status":
                    server_default = "released"
                elif name == "revision":
                    server_default = "0"
                batch.add_column(sa.Column(
                    name, type_, nullable=nullable, server_default=server_default))
            for column in ("parent_version_id", "base_release_id", "promoted_from_id"):
                if column not in version_columns:
                    batch.create_foreign_key(
                        f"fk_ontology_versions_{column}", "ontology_versions",
                        [column], ["id"], ondelete="SET NULL")
        for column in ("parent_version_id", "base_release_id"):
            if column not in version_columns:
                op.create_index(
                    f"ix_ontology_versions_{column}", "ontology_versions", [column])

    if not _has_table("ontology_trial_runs"):
        op.create_table(
            "ontology_trial_runs",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("ontology_id", sa.String(), nullable=False),
            sa.Column("version_id", sa.String(), nullable=False),
            sa.Column("revision", sa.Integer(), nullable=False),
            sa.Column("snapshot_hash", sa.String(length=64), nullable=False),
            sa.Column("status", sa.String(length=20), nullable=False,
                      server_default="running"),
            sa.Column("dataset_versions", sa.JSON(), nullable=True),
            sa.Column("result_json", sa.JSON(), nullable=True),
            sa.Column("impact_hash", sa.String(length=64), nullable=True),
            sa.Column("created_by", sa.String(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("completed_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["ontology_id"], ["ontology_projects.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["version_id"], ["ontology_versions.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        )
        op.create_index("ix_ontology_trial_runs_ontology_id", "ontology_trial_runs", ["ontology_id"])
        op.create_index("ix_ontology_trial_runs_version_id", "ontology_trial_runs", ["version_id"])
        op.create_index("ix_ontology_trial_runs_version_created", "ontology_trial_runs", ["version_id", "created_at"])

    if not _has_table("ontology_trial_objects"):
        op.create_table(
            "ontology_trial_objects",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("trial_run_id", sa.String(), nullable=False),
            sa.Column("object_id", sa.String(), nullable=False),
            sa.Column("object_type_id", sa.String(), nullable=False),
            sa.Column("properties", sa.JSON(), nullable=True),
            sa.Column("source_dataset_id", sa.String(), nullable=True),
            sa.Column("source_dataset_version_id", sa.String(), nullable=True),
            sa.Column("external_id", sa.String(length=500), nullable=True),
            sa.ForeignKeyConstraint(["trial_run_id"], ["ontology_trial_runs.id"], ondelete="CASCADE"),
            sa.UniqueConstraint("trial_run_id", "object_id", name="uq_trial_object_run_object"),
        )
        op.create_index("ix_trial_object_run_type", "ontology_trial_objects", ["trial_run_id", "object_type_id"])

    if not _has_table("ontology_trial_links"):
        op.create_table(
            "ontology_trial_links",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("trial_run_id", sa.String(), nullable=False),
            sa.Column("link_id", sa.String(), nullable=False),
            sa.Column("link_type_id", sa.String(), nullable=False),
            sa.Column("source_object_id", sa.String(), nullable=False),
            sa.Column("target_object_id", sa.String(), nullable=False),
            sa.Column("properties", sa.JSON(), nullable=True),
            sa.ForeignKeyConstraint(["trial_run_id"], ["ontology_trial_runs.id"], ondelete="CASCADE"),
            sa.UniqueConstraint("trial_run_id", "link_id", name="uq_trial_link_run_link"),
        )
        op.create_index("ix_trial_link_run_type", "ontology_trial_links", ["trial_run_id", "link_type_id"])

    op.execute(
        "UPDATE ontology_versions SET node_kind='release', lifecycle_status='released', revision=0 "
        "WHERE node_kind IS NULL OR lifecycle_status IS NULL OR revision IS NULL")
    op.execute(
        "UPDATE ontology_versions SET published_at=created_at "
        "WHERE published_at IS NULL")
    # 存量项目先指向最近的历史发布快照；没有任何版本记录的项目由首次
    # version-tree 查询从当前运行结构冻结迁移基线。
    op.execute(
        "UPDATE ontology_projects SET current_release_id=("
        "SELECT v.id FROM ontology_versions v "
        "WHERE v.ontology_id=ontology_projects.id AND v.node_kind='release' "
        "ORDER BY v.published_at DESC, v.created_at DESC LIMIT 1"
        ") WHERE current_release_id IS NULL")


def downgrade() -> None:
    for table in ("ontology_trial_links", "ontology_trial_objects", "ontology_trial_runs"):
        if _has_table(table):
            op.drop_table(table)
    version_columns = _columns("ontology_versions")
    with op.batch_alter_table("ontology_versions") as batch:
        for column in (
            "published_at", "snapshot_hash", "revision", "lifecycle_status",
            "node_kind", "promoted_from_id", "base_release_id", "parent_version_id",
        ):
            if column in version_columns:
                batch.drop_column(column)
    if "current_release_id" in _columns("ontology_projects"):
        with op.batch_alter_table("ontology_projects") as batch:
            batch.drop_column("current_release_id")
