"""Pipeline dependency checks shared by lifecycle entry points."""
from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session


def reject_if_sync_chain_refs(
    db: Session,
    pipeline_id: str,
    *,
    action: str,
) -> None:
    """Reject lifecycle changes while sync tasks target the pipeline."""
    from app.data_channel.sync_tasks.models import DataSyncTask

    references = db.query(DataSyncTask).filter(
        DataSyncTask.trigger_pipeline_id == pipeline_id,
    ).all()
    if references:
        names = "、".join(task.name for task in references[:3])
        suffix = "…" if len(references) > 3 else ""
        raise HTTPException(
            400,
            f"流水线被 {len(references)} 个同步任务设为链式触发目标"
            f"（{names}{suffix}），不能{action}。"
            "请先在这些同步任务中解除「同步后触发流水线」的配置。",
        )


_reject_if_sync_chain_refs = reject_if_sync_chain_refs
