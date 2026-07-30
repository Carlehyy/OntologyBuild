"""Synchronous pipeline trigger orchestration.

Route transformation lives in :mod:`route_executor`; this service owns only
the run-record lifecycle and invocation of the shared worker task.
"""
from __future__ import annotations


def execute_pipeline(pipeline_id: str, triggered_by: str = "") -> dict:
    """Run a published pipeline through the same task used by manual/Celery runs."""
    from datetime import datetime, timezone

    from app.database import SessionLocal
    from app.models.v2.pipeline import Pipeline, PipelineRun

    db = SessionLocal()
    try:
        pipe = db.query(Pipeline).filter(Pipeline.id == pipeline_id).first()
        if not pipe:
            return {"status": "error", "error": f"Pipeline {pipeline_id} 不存在"}
        if pipe.enabled is False:
            return {
                "status": "error",
                "error": f"Pipeline「{pipe.name}」已停用，跳过链式触发",
            }
        if not pipe.source_dataset_id and not pipe.definition:
            return {"status": "error", "error": "Pipeline 未绑定源数据集"}

        run = PipelineRun(
            pipeline_id=pipeline_id,
            status="pending",
            started_at=datetime.now(timezone.utc),
            stats={"triggered_by": triggered_by} if triggered_by else {},
        )
        db.add(run)
        db.commit()
        run_id = run.id
    finally:
        db.close()

    from app.tasks.v2.pipeline_run import pipeline_run_task

    task = getattr(pipeline_run_task, "run", pipeline_run_task)
    task(pipeline_id, run_id)

    db = SessionLocal()
    try:
        done = db.query(PipelineRun).filter(PipelineRun.id == run_id).first()
        stats = (done.stats or {}) if done else {}
        return {
            "status": "ok" if (done and done.status == "success") else "error",
            "pipeline_id": pipeline_id,
            "run_id": run_id,
            "rows_in": stats.get("rows_in", 0),
            "rows_out": stats.get("rows_out", 0),
            "curated_dataset_ids": stats.get("curated_dataset_ids", []),
            "error": done.error_log if done else None,
        }
    finally:
        db.close()
