"""mapping knowledge entries: reusable column→property anchors

Revision ID: 0063_mapping_knowledge_entries
Revises: 0062_curated_lake_tables
Create Date: 2026-08-12

数据飞轮沉淀层：人工确认过的「湖列 → 本体属性」映射知识表，供映射建议
服务（mapping-suggestions）检索复用。锚定点使用语义名（object_name /
property_name）而非本体内部 id，保证跨本体可复用；仅由人工保存过的映射
回流写入。

迁移 0003 会对当前 Base.metadata 做 create_all：全新库在到达本迁移前
可能已按当前模型建好该表，此处按表守卫跳过重复创建。
"""

from alembic import op
import sqlalchemy as sa


revision = "0063_mapping_knowledge_entries"
down_revision = "0062_curated_lake_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if sa.inspect(bind).has_table("v2_mapping_knowledge_entries"):
        return
    op.create_table(
        "v2_mapping_knowledge_entries",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("column_key", sa.String(length=200), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=False, server_default=""),
        sa.Column("col_type", sa.String(length=20), nullable=False, server_default="string"),
        sa.Column("object_name", sa.String(length=200), nullable=False),
        sa.Column("property_name", sa.String(length=200), nullable=False),
        sa.Column("confirm_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("last_confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "column_key", "display_name", "col_type",
            "object_name", "property_name",
            name="uq_mapping_knowledge_anchor",
        ),
    )
    op.create_index(
        "ix_v2_mapping_knowledge_entries_column_key",
        "v2_mapping_knowledge_entries",
        ["column_key"],
    )
    op.create_index(
        "ix_v2_mapping_knowledge_entries_display_name",
        "v2_mapping_knowledge_entries",
        ["display_name"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    if not sa.inspect(bind).has_table("v2_mapping_knowledge_entries"):
        return
    op.drop_index(
        "ix_v2_mapping_knowledge_entries_display_name",
        table_name="v2_mapping_knowledge_entries",
    )
    op.drop_index(
        "ix_v2_mapping_knowledge_entries_column_key",
        table_name="v2_mapping_knowledge_entries",
    )
    op.drop_table("v2_mapping_knowledge_entries")
