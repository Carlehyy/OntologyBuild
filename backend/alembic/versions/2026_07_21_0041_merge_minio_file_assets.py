"""merge MinIO platform and pipeline file asset heads

Revision ID: 0041_merge_minio_files
Revises: 0040_minio_platform, 0040_pipeline_file_assets
Create Date: 2026-07-21
"""

revision = "0041_merge_minio_files"
down_revision = ("0040_minio_platform", "0040_pipeline_file_assets")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
