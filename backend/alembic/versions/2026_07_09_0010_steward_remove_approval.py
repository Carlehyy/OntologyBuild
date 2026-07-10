"""数据管家审批流废除：发布唯一入口收敛到流水线编辑向导

1. v2_n8n_pipelines.status 归一：审批状态机（pending_approval/approved/
   rejected）退役，记录只剩 draft（在管）/ archived（已归档）两态。
   发布身份由影子流水线 v2_pipelines.status 唯一承载——已批准记录的影子
   本就是 published，归一治理态不影响其发布/调度资格。
2. 删审批字段：submitted_at / approved_by / approved_at / reject_reason /
   approved_snapshot（审批快照退役；发布快照由 v2_pipeline_versions 承载）。

Revision ID: 0010_steward_remove_approval
Revises: 0009_dataset_storage_hardening
Create Date: 2026-07-09
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect

revision = "0010_steward_remove_approval"
down_revision = "0009_dataset_storage_hardening"
branch_labels = None
depends_on = None

_TABLE = "v2_n8n_pipelines"
_LEGACY_COLUMNS = ("submitted_at", "approved_by", "approved_at",
                   "reject_reason", "approved_snapshot")


def _existing_columns() -> set[str]:
    inspector = sa_inspect(op.get_bind())
    if not inspector.has_table(_TABLE):
        return set()
    return {c["name"] for c in inspector.get_columns(_TABLE)}


def upgrade() -> None:
    # 部署脚本每次 stamp 回 0006 再 upgrade，本迁移会被反复执行——
    # 状态归一天然幂等，删列必须带存在性检查（项目惯例，见 0008/0009）
    if not sa_inspect(op.get_bind()).has_table(_TABLE):
        # 旧环境可能从未启用数据管家；正式建表由后续契约迁移负责。
        return
    op.execute(
        f"UPDATE {_TABLE} SET status='draft' "
        f"WHERE status IN ('pending_approval', 'approved', 'rejected')"
    )
    present = _existing_columns() & set(_LEGACY_COLUMNS)
    if present:
        # batch 模式：SQLite 走重建表路径（兼容不支持 DROP COLUMN 的旧版本），
        # 其他方言退化为普通 ALTER
        with op.batch_alter_table(_TABLE) as batch:
            for col in _LEGACY_COLUMNS:
                if col in present:
                    batch.drop_column(col)


def downgrade() -> None:
    if not sa_inspect(op.get_bind()).has_table(_TABLE):
        return
    # 审批数据不可恢复，仅补回空列保证旧代码可运行
    present = _existing_columns()
    with op.batch_alter_table(_TABLE) as batch:
        if "submitted_at" not in present:
            batch.add_column(sa.Column("submitted_at", sa.DateTime(), nullable=True))
        if "approved_by" not in present:
            batch.add_column(sa.Column("approved_by", sa.String(), nullable=True))
        if "approved_at" not in present:
            batch.add_column(sa.Column("approved_at", sa.DateTime(), nullable=True))
        if "reject_reason" not in present:
            batch.add_column(sa.Column("reject_reason", sa.Text(), nullable=True))
        if "approved_snapshot" not in present:
            batch.add_column(sa.Column("approved_snapshot", sa.JSON(), nullable=True))
