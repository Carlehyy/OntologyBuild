"""fact 溯源层与资产湖版本的端到端血缘：source_dataset_version_id + producer_run_id

动机：事实流（fo_property_facts）与数据链（v2_dataset_versions）此前无法互相
回溯——映射投影写事实不携带来源湖版本，湖版本也不知道由哪次流水线运行产出。
两列补齐后：湖行 → 湖版本(producer_run_id) → 运行(含 task_id) → 映射应用 →
属性事实(source_dataset_version_id) 一键闭环。

幂等性：inspector 守卫（表/列存在性），可安全重跑；新列可空，无回填。
索引只建单列索引（现有坐标索引已覆盖常规查询路径）。
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0072_fact_lake_lineage"
down_revision = "0071_world_model_services_menu_key"
branch_labels = None
depends_on = None


def _has_column(inspector, table: str, column: str) -> bool:
    return column in {c["name"] for c in inspector.get_columns(table)}


def _has_index(inspector, table: str, index: str) -> bool:
    try:
        return index in {i["name"] for i in inspector.get_indexes(table)}
    except Exception:  # noqa: BLE001 — 表不存在时按无索引处理
        return False


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("fo_property_facts") and not _has_column(
        inspector, "fo_property_facts", "source_dataset_version_id"
    ):
        op.add_column(
            "fo_property_facts",
            sa.Column("source_dataset_version_id", sa.String(), nullable=True),
        )
        op.create_index(
            "ix_fo_property_facts_source_dataset_version_id",
            "fo_property_facts",
            ["source_dataset_version_id"],
        )
    if inspector.has_table("v2_dataset_versions") and not _has_column(
        inspector, "v2_dataset_versions", "producer_run_id"
    ):
        op.add_column(
            "v2_dataset_versions",
            sa.Column("producer_run_id", sa.String(), nullable=True),
        )
        op.create_index(
            "ix_v2_dataset_versions_producer_run_id",
            "v2_dataset_versions",
            ["producer_run_id"],
        )
        # SQLite 不支持 ALTER ADD CONSTRAINT；生产为 PostgreSQL，正常建外键。
        if bind.dialect.name != "sqlite" and inspector.has_table(
            "v2_pipeline_runs"
        ):
            op.create_foreign_key(
                "fk_v2_dataset_versions_producer_run",
                "v2_dataset_versions",
                "v2_pipeline_runs",
                ["producer_run_id"],
                ["id"],
                ondelete="SET NULL",
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("v2_dataset_versions"):
        if _has_index(
            inspector, "v2_dataset_versions", "ix_v2_dataset_versions_producer_run_id"
        ):
            op.drop_index(
                "ix_v2_dataset_versions_producer_run_id",
                table_name="v2_dataset_versions",
            )
        if _has_column(inspector, "v2_dataset_versions", "producer_run_id"):
            if bind.dialect.name != "sqlite":
                op.drop_constraint(
                    "fk_v2_dataset_versions_producer_run",
                    "v2_dataset_versions",
                    type_="foreignkey",
                )
            op.drop_column("v2_dataset_versions", "producer_run_id")
    if inspector.has_table("fo_property_facts"):
        if _has_index(
            inspector,
            "fo_property_facts",
            "ix_fo_property_facts_source_dataset_version_id",
        ):
            op.drop_index(
                "ix_fo_property_facts_source_dataset_version_id",
                table_name="fo_property_facts",
            )
        if _has_column(
            inspector, "fo_property_facts", "source_dataset_version_id"
        ):
            op.drop_column("fo_property_facts", "source_dataset_version_id")
