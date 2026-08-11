"""merge 0063 parallel heads (trigger_type / mapping knowledge / card clicks)

Revision ID: 0064_merge_0063_heads
Revises: 0063_pipeline_run_trigger_type, 0063_mapping_knowledge_entries, 0063_ontology_assistant_card_clicks
Create Date: 2026-08-12

三条 0063 迁移由并行开发同时链在 0062_curated_lake_tables 之后，形成多 head。
按 0020/0027/0041 的既有惯例以空合并迁移收口，不改任何一条 0063 的内容。
"""

revision = "0064_merge_0063_heads"
down_revision = (
    "0063_pipeline_run_trigger_type",
    "0063_mapping_knowledge_entries",
    "0063_ontology_assistant_card_clicks",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
