"""
流水线调度任务执行引擎
执行链：任务触发 → 运行已发布流水线（与手动运行完全同一路径）→
流水线的最终产物按任务声明的入库方式（write_mode）写入数据资产湖。
"""
from __future__ import annotations

import logging
from datetime import datetime

logger = logging.getLogger(__name__)


def execute_pipeline_task(task_id: str, trigger_type: str = "manual") -> dict:
    """执行一条流水线调度任务。返回 {"status": "ok"|"error", ...}"""
    from app.database import SessionLocal
    from app.data_channel.pipeline_tasks.models import PipelineTask
    from app.models.v2.pipeline import Pipeline, PipelineRun

    db = SessionLocal()
    try:
        task = db.query(PipelineTask).filter(PipelineTask.id == task_id).first()
        if not task:
            return {"status": "error", "error": f"PipelineTask {task_id} not found"}
        if task.status == "running":
            return {"status": "error", "error": "任务正在执行中"}

        pipe = db.query(Pipeline).filter(Pipeline.id == task.pipeline_id).first()
        if not pipe:
            _fail(db, task, "关联的流水线不存在，可能已被删除")
            return {"status": "error", "error": task.last_error}
        if (pipe.status or "draft") != "published":
            _fail(db, task, f"流水线「{pipe.name}」当前状态为未发布，任务只能触发已发布的流水线")
            return {"status": "error", "error": task.last_error}
        if pipe.enabled is False:  # NULL（老数据）视为启用
            if trigger_type == "manual":
                # 手动触发：用户在等结果，明确报错
                _fail(db, task, f"流水线「{pipe.name}」已停用，请在流水线列表打开启用开关后再调度")
                return {"status": "error", "error": task.last_error}
            # 定时/间隔调度：停用是预期内的暂停，跳过而非失败——
            # 避免停用期间每次触发都刷一条失败记录
            msg = f"流水线「{pipe.name}」已停用，本次调度已跳过（启用后自动恢复）"
            task.last_error = msg
            db.commit()
            logger.info("PipelineTask %s 跳过：%s", task_id, msg)
            return {"status": "skipped", "task_id": task_id, "reason": msg}

        from app.data_channel.datasets.lake_gate import contract_pk
        pipeline_pk = contract_pk(pipe.column_definitions)
        if (task.write_mode or "overwrite") == "upsert" and not pipeline_pk:
            _fail(db, task, "主键合并无法执行：关联流水线的已发布数据契约没有主键")
            return {"status": "error", "error": task.last_error}

        task.status = "running"
        task.last_error = ""
        run = PipelineRun(
            pipeline_id=task.pipeline_id,
            task_id=task.id,
            status="pending",
            started_at=datetime.utcnow(),
            # 审计：执行时刻的任务配置快照——配置日后被改，这条记录仍还原当时口径
            stats={
                "triggered_by": f"task:{task.id}",
                "trigger_type": trigger_type,
                "config_snapshot": {
                    "task_name": task.name,
                    "pipeline_id": task.pipeline_id,
                    "pipeline_name": pipe.name,
                    "pipeline_version": getattr(pipe, "version", None),
                    "write_mode": task.write_mode,
                    "primary_key": pipeline_pk,
                    "primary_key_source": "pipeline_contract",
                    "soft_delete_column": task.soft_delete_column or "",
                    "skip_empty": bool(task.skip_empty),
                    "schedule_type": task.schedule_type,
                    "cron_expression": task.cron_expression or "",
                    "interval_seconds": task.interval_seconds or 0,
                },
            },
        )
        db.add(run)
        db.commit()
        db.refresh(run)
        run_id = run.id

        write_opts = {
            "mode": task.write_mode or "overwrite",
            "primary_key": pipeline_pk,
            "soft_delete_column": task.soft_delete_column or "",
            "skip_empty": bool(task.skip_empty),
        }
    finally:
        db.close()

    # 与手动/Celery 运行同一条执行路径，额外携带入库方式
    from app.tasks.v2.pipeline_run import pipeline_run_task
    fn = getattr(pipeline_run_task, "run", pipeline_run_task)  # Celery task 或裸函数均可
    try:
        fn(task.pipeline_id, run_id, write_opts)
    except Exception as e:  # noqa: BLE001
        logger.exception(f"PipelineTask {task_id} 执行异常")

    # 回读运行结果，更新任务状态
    db = SessionLocal()
    try:
        task = db.query(PipelineTask).filter(PipelineTask.id == task_id).first()
        run = db.query(PipelineRun).filter(PipelineRun.id == run_id).first()
        stats = (run.stats or {}) if run else {}
        ok = bool(run and run.status == "success")
        if task:
            task.status = "success" if ok else "failed"
            task.last_run_at = datetime.utcnow()
            task.last_rows = int(stats.get("lake_rows") or stats.get("rows_out") or 0)
            task.last_error = "" if ok else ((run.error_log if run else "") or "执行失败")
            db.commit()
        return {
            "status": "ok" if ok else "error",
            "task_id": task_id,
            "run_id": run_id,
            "rows_in": stats.get("rows_in", 0),
            "rows_out": stats.get("rows_out", 0),
            "lake_rows": stats.get("lake_rows"),
            "write_mode": stats.get("write_mode"),
            "curated_dataset_ids": stats.get("curated_dataset_ids", []),
            "error": (run.error_log if run else None) if not ok else None,
        }
    finally:
        db.close()


def _fail(db, task, message: str) -> None:
    task.status = "failed"
    task.last_error = message
    task.last_run_at = datetime.utcnow()
    db.commit()
