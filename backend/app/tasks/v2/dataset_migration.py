"""成品数据集异步迁移任务体：由 NATS executor 线程内直接调用。

轻量入口：真实逻辑在 ``migration_service``，保持与 ``dataset_import.py``
相同的「任务模块薄、领域服务厚」分层。
"""
from __future__ import annotations


def migrate_curated_to_manual(job_id: str, source_dataset_id: str) -> None:
    from app.data_channel.datasets.migration_service import run_migration

    run_migration(job_id, source_dataset_id)
