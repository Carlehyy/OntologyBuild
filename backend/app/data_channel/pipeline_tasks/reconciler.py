"""
流水线执行对账器

executor 进程被打断（部署、宕机）时，正在执行的任务会留下
``status='running'`` 的任务行和 ``pending/running`` 的运行记录。执行引擎
自身不主动清理——租约未过期前它们可能属于另一个正常的执行者。本对账器
只做两件幂等收口，全部用条件 UPDATE（防与正常执行竞争）：

a) ``running`` 且租约已过期的任务 → 置 failed、清 token/lease；
b) 任务域 ``pending/running`` 运行记录，其任务不存在或租约已过期/为空
   → 置 failed。

``task_id`` 为 NULL 的运行记录属于 Celery 手动运行域（后续 PR 退役），
本对账器不碰；租约仍有效的任务/运行也不碰（可能是正常长任务）。
"""
from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import or_

logger = logging.getLogger(__name__)

INTERRUPTED_TASK_ERROR = "执行中断（进程退出），租约过期后由对账器收口"
INTERRUPTED_RUN_ERROR = "执行中断（进程退出），由对账器收口"


def reconcile_pipeline_executions(db) -> dict:
    """收口进程退出留下的中断执行，返回 ``{"tasks_failed", "runs_failed"}``。"""
    from app.data_channel.pipeline_tasks.models import PipelineTask
    from app.data_channel.pipelines.models import PipelineRun

    now = datetime.utcnow()

    # a) running 任务租约过期 → failed。条件 UPDATE 保证只收口过期租约，
    #    并发的新 claim / 正常 release 不会被覆盖。
    tasks_failed = db.query(PipelineTask).filter(
        PipelineTask.status == "running",
        PipelineTask.lease_expires_at.isnot(None),
        PipelineTask.lease_expires_at <= now,
    ).update({
        PipelineTask.status: "failed",
        PipelineTask.execution_token: None,
        PipelineTask.lease_expires_at: None,
        PipelineTask.last_error: INTERRUPTED_TASK_ERROR,
        PipelineTask.updated_at: now,
    }, synchronize_session=False)

    # b) 任务域 stuck run：任务已删除，或任务租约为空/已过期（执行者已消失）。
    stuck_ids = [
        row[0]
        for row in db.query(PipelineRun.id)
        .outerjoin(PipelineTask, PipelineRun.task_id == PipelineTask.id)
        .filter(
            PipelineRun.status.in_(("pending", "running")),
            PipelineRun.task_id.isnot(None),
            or_(
                PipelineTask.id.is_(None),
                PipelineTask.lease_expires_at.is_(None),
                PipelineTask.lease_expires_at <= now,
            ),
        )
        .all()
    ]
    runs_failed = 0
    if stuck_ids:
        # 复查 status 的条件 UPDATE：与刚正常结束的 run 竞争时不覆盖其结果
        runs_failed = db.query(PipelineRun).filter(
            PipelineRun.id.in_(stuck_ids),
            PipelineRun.status.in_(("pending", "running")),
        ).update({
            PipelineRun.status: "failed",
            PipelineRun.error_log: INTERRUPTED_RUN_ERROR,
            PipelineRun.finished_at: now,
        }, synchronize_session=False)

    db.commit()
    result = {
        "tasks_failed": int(tasks_failed or 0),
        "runs_failed": int(runs_failed or 0),
    }
    if result["tasks_failed"] or result["runs_failed"]:
        logger.info("流水线执行对账收口: %s", result)
    return result
