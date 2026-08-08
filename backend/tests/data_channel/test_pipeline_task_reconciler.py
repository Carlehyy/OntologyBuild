"""流水线执行对账器：只收口租约已过期的中断执行。"""
from __future__ import annotations

from datetime import datetime, timedelta

from app.data_channel.pipeline_tasks.models import PipelineTask
from app.data_channel.pipeline_tasks.reconciler import (
    INTERRUPTED_RUN_ERROR,
    INTERRUPTED_TASK_ERROR,
    reconcile_pipeline_executions,
)
from app.models.v2.pipeline import Pipeline, PipelineRun


def _pipeline(db) -> Pipeline:
    pipe = Pipeline(
        id="pipe-reconcile",
        name="对账流水线",
        spec={},
        status="published",
        enabled=True,
        column_definitions=[{"field_key": "id"}],
    )
    db.add(pipe)
    db.commit()
    return pipe


def _task(db, task_id, *, status, token=None, lease=None) -> PipelineTask:
    task = PipelineTask(
        id=task_id,
        name=f"任务-{task_id}",
        description="",
        pipeline_id="pipe-reconcile",
        status=status,
        execution_token=token,
        lease_expires_at=lease,
    )
    db.add(task)
    db.commit()
    return task


def _run(db, run_id, *, task_id, status) -> PipelineRun:
    run = PipelineRun(
        id=run_id,
        pipeline_id="pipe-reconcile",
        task_id=task_id,
        status=status,
        started_at=datetime.utcnow(),
    )
    db.add(run)
    db.commit()
    return run


def test_reconcile_collects_only_expired_lease_interruptions(db):
    now = datetime.utcnow()
    _pipeline(db)
    # a) 过期租约的 running 任务 → 收口
    expired = _task(
        db, "task-expired", status="running",
        token="tok-expired", lease=now - timedelta(seconds=1),
    )
    # 活租约的 running 任务（可能是正常长任务）→ 不碰
    live = _task(
        db, "task-live", status="running",
        token="tok-live", lease=now + timedelta(hours=1),
    )
    # b) 任务域 stuck run：租约过期 → 收口
    orphan_run = _run(db, "run-orphan", task_id="task-expired", status="pending")
    # 任务租约仍有效的 running run → 不碰
    live_run = _run(db, "run-live", task_id="task-live", status="running")
    # task_id 为 NULL 的 stuck run 属 Celery 域 → 不碰
    celery_run = _run(db, "run-celery", task_id=None, status="pending")
    # 任务已不存在的 run（sqlite 不强制外键，可保留悬空引用）→ 收口
    ghost = _task(
        db, "task-ghost", status="running",
        token="tok-ghost", lease=now + timedelta(hours=1),
    )
    ghost_run = _run(db, "run-ghost", task_id=ghost.id, status="pending")
    db.delete(ghost)
    db.commit()

    result = reconcile_pipeline_executions(db)

    assert result == {"tasks_failed": 1, "runs_failed": 2}

    db.expire_all()
    expired = db.query(PipelineTask).filter_by(id="task-expired").one()
    assert expired.status == "failed"
    assert expired.execution_token is None
    assert expired.lease_expires_at is None
    assert expired.last_error == INTERRUPTED_TASK_ERROR

    live = db.query(PipelineTask).filter_by(id="task-live").one()
    assert live.status == "running"
    assert live.execution_token == "tok-live"
    assert live.lease_expires_at > datetime.utcnow()

    orphan_run = db.query(PipelineRun).filter_by(id="run-orphan").one()
    assert orphan_run.status == "failed"
    assert orphan_run.error_log == INTERRUPTED_RUN_ERROR
    assert orphan_run.finished_at is not None

    ghost_run = db.query(PipelineRun).filter_by(id="run-ghost").one()
    assert ghost_run.status == "failed"
    assert ghost_run.error_log == INTERRUPTED_RUN_ERROR

    live_run = db.query(PipelineRun).filter_by(id="run-live").one()
    assert live_run.status == "running"
    assert live_run.error_log is None
    assert live_run.finished_at is None

    celery_run = db.query(PipelineRun).filter_by(id="run-celery").one()
    assert celery_run.status == "pending"
    assert celery_run.finished_at is None


def test_reconcile_is_idempotent_when_nothing_is_interrupted(db):
    now = datetime.utcnow()
    _pipeline(db)
    _task(
        db, "task-ok", status="running",
        token="tok-ok", lease=now + timedelta(hours=1),
    )
    _run(db, "run-ok", task_id="task-ok", status="running")

    first = reconcile_pipeline_executions(db)
    second = reconcile_pipeline_executions(db)

    assert first == {"tasks_failed": 0, "runs_failed": 0}
    assert second == {"tasks_failed": 0, "runs_failed": 0}
    db.expire_all()
    assert db.query(PipelineTask).filter_by(id="task-ok").one().status == "running"
    assert db.query(PipelineRun).filter_by(id="run-ok").one().status == "running"


def test_reconcile_collects_run_when_task_lease_is_empty(db):
    """租约为空 + stuck run：执行者已消失（正常 release 后残留），应收口。"""
    _pipeline(db)
    _task(db, "task-released", status="idle", token=None, lease=None)
    _run(db, "run-stale", task_id="task-released", status="pending")

    result = reconcile_pipeline_executions(db)

    assert result == {"tasks_failed": 0, "runs_failed": 1}
    db.expire_all()
    run = db.query(PipelineRun).filter_by(id="run-stale").one()
    assert run.status == "failed"
    assert run.error_log == INTERRUPTED_RUN_ERROR
    # 任务本身不是 running，不属于 a) 类收口
    task = db.query(PipelineTask).filter_by(id="task-released").one()
    assert task.status == "idle"
