"""merge 0074/0076 parallel heads (version semantic layer / scenes domain chain)

Revision ID: 0077_merge_semantic_scenes_heads
Revises: 0074_version_semantic_layer, 0076_network_search_trgm
Create Date: 2026-08-24

版本业务语义层（0074_version_semantic_layer）与场景域迁移链
（0074_scenes_domain → 0075 → 0076_network_search_trgm）由并行开发同时链在
0073_drop_agent_config 之后，形成双 head。
按 0020/0027/0041/0064 的既有惯例以空合并迁移收口，不改任何一边的内容。
"""

revision = "0077_merge_semantic_scenes_heads"
down_revision = (
    "0074_version_semantic_layer",
    "0076_network_search_trgm",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
