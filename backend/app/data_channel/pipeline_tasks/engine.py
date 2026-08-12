"""
流水线调度任务执行引擎
执行链：任务触发 → 运行已发布流水线（与手动运行完全同一路径）→
流水线的最终产物按任务声明的入库方式（write_mode）写入数据资产湖。
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta

from sqlalchemy import or_

logger = logging.getLogger(__name__)

_TASK_LEASE = timedelta(hours=6)


def _claim_task(db, task_id: str) -> tuple[object | None, str | None, str | None]:
    """原子领取任务；返回 ``(task, token, error)``。

    ``status`` 只是展示状态，真正的互斥依据是未过期 lease。条件 UPDATE 在
    数据库完成，因而多个 Web worker / scheduler 进程最多只有一个领取成功。
    """
    from app.data_channel.pipeline_tasks.models import PipelineTask

    now = datetime.utcnow()
    token = str(uuid.uuid4())
    claimed = db.query(PipelineTask).filter(
        PipelineTask.id == task_id,
        or_(
            PipelineTask.execution_token.is_(None),
            PipelineTask.lease_expires_at.is_(None),
            PipelineTask.lease_expires_at <= now,
        ),
    ).update({
        PipelineTask.status: "running",
        PipelineTask.execution_token: token,
        PipelineTask.lease_expires_at: now + _TASK_LEASE,
        PipelineTask.last_error: "",
        PipelineTask.updated_at: now,
    }, synchronize_session=False)
    db.commit()
    if not claimed:
        exists = db.query(PipelineTask.id).filter(PipelineTask.id == task_id).first()
        return None, None, ("任务正在执行中" if exists else f"PipelineTask {task_id} not found")
    task = db.query(PipelineTask).filter(PipelineTask.id == task_id).first()
    return task, token, None


def _release_claim(db, task, token: str, *, status: str, error: str = "",
                   rows: int = 0, run_id: str | None = None,
                   trigger_type: str | None = None) -> bool:
    """仅当前租约持有者能落最终状态，防止过期旧执行覆盖恢复后的新执行。"""
    return _release_claim_by_id(
        db, task.id, token, status=status, error=error, rows=rows,
        run_id=run_id, trigger_type=trigger_type,
    )


def _release_claim_by_id(db, task_id: str, token: str, *, status: str,
                         error: str = "", rows: int = 0,
                         run_id: str | None = None,
                         trigger_type: str | None = None) -> bool:
    """按稳定标量释放租约，供 ORM 对象可能失效的异常恢复路径使用。"""
    from app.data_channel.pipeline_tasks.models import PipelineTask

    occurred_at = datetime.utcnow()
    updated = db.query(PipelineTask).filter(
        PipelineTask.id == task_id,
        PipelineTask.execution_token == token,
    ).update({
        PipelineTask.status: status,
        PipelineTask.execution_token: None,
        PipelineTask.lease_expires_at: None,
        PipelineTask.last_run_at: occurred_at,
        PipelineTask.last_rows: rows,
        PipelineTask.last_error: error,
        PipelineTask.updated_at: occurred_at,
    }, synchronize_session=False)
    inbox_event_id = None
    if updated and status in {"success", "failed"}:
        from app.inbox.service import enqueue_pipeline_task_result
        inbox_event_id = enqueue_pipeline_task_result(
            db,
            task_id=task_id,
            status=status,
            error=error,
            occurrence_id=run_id or token,
            run_id=run_id,
            trigger_type=trigger_type,
            occurred_at=occurred_at,
        )
    db.commit()
    if inbox_event_id:
        # The task result and outbox event committed atomically. Projection is
        # attempted immediately; on failure the event remains pending and will
        # be retried at the next inbox read or application startup.
        from sqlalchemy.orm import sessionmaker
        inbox_db = sessionmaker(bind=db.get_bind())()
        try:
            from app.inbox.service import drain_outbox
            drain_outbox(inbox_db, event_id=inbox_event_id)
        except Exception:  # noqa: BLE001 - task outcome is already durable
            logger.exception("PipelineTask %s 收件箱事件即时投递失败", task_id)
        finally:
            inbox_db.close()
    return bool(updated)


def _record_run_initialization_failure(
    db,
    *,
    task_id: str,
    run_id: str,
    execution_token: str,
    message: str,
    trigger_type: str,
) -> bool:
    """收口运行记录初始化失败，且绝不覆盖后来接管租约的执行者。

    ``commit`` 可能已真正落库、也可能完全没有执行，所以清理逻辑同时兼容
    两种结果：存在的 pending run 标为 failed；不存在则只释放任务 claim。
    两项写入共用一次提交，避免任务已释放但审计运行仍永久 pending。
    """
    from app.models.v2.pipeline import PipelineRun

    db.rollback()
    now = datetime.utcnow()
    db.query(PipelineRun).filter(
        PipelineRun.id == run_id,
        PipelineRun.task_id == task_id,
        PipelineRun.status == "pending",
    ).update({
        PipelineRun.status: "failed",
        PipelineRun.finished_at: now,
        PipelineRun.error_log: message,
    }, synchronize_session=False)
    return _release_claim_by_id(
        db,
        task_id,
        execution_token,
        status="failed",
        error=message,
        run_id=run_id,
        trigger_type=trigger_type,
    )


def _recover_run_initialization_failure(
    session_factory,
    db,
    *,
    task_id: str,
    run_id: str,
    execution_token: str,
    message: str,
    trigger_type: str,
) -> bool:
    """清理失败初始化；原会话不可用时换新会话重试一次。"""
    try:
        return _record_run_initialization_failure(
            db,
            task_id=task_id,
            run_id=run_id,
            execution_token=execution_token,
            message=message,
            trigger_type=trigger_type,
        )
    except Exception:  # noqa: BLE001 - 正在恢复数据库异常，必须切换会话重试
        logger.exception(
            "PipelineTask %s 初始化失败后使用原会话收口失败，改用新会话重试",
            task_id,
        )
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass

    retry_db = session_factory()
    try:
        return _record_run_initialization_failure(
            retry_db,
            task_id=task_id,
            run_id=run_id,
            execution_token=execution_token,
            message=message,
            trigger_type=trigger_type,
        )
    finally:
        retry_db.close()


def execute_pipeline_task(task_id: str, trigger_type: str = "manual") -> dict:
    """执行一条流水线调度任务。返回 {"status": "ok"|"error", ...}"""
    from app.database import SessionLocal
    from app.data_channel.pipeline_tasks.models import PipelineTask
    from app.models.v2.pipeline import Pipeline, PipelineRun

    db = SessionLocal()
    try:
        task, execution_token, claim_error = _claim_task(db, task_id)
        if not task:
            return {"status": "error", "error": claim_error}

        pipe = db.query(Pipeline).filter(Pipeline.id == task.pipeline_id).first()
        if not pipe:
            _fail(db, task, "关联的流水线不存在，可能已被删除", execution_token)
            return {"status": "error", "error": task.last_error}
        if (pipe.status or "draft") != "published":
            _fail(db, task, f"流水线「{pipe.name}」当前状态为未发布，任务只能触发已发布的流水线", execution_token)
            return {"status": "error", "error": task.last_error}
        if pipe.enabled is False:  # NULL（老数据）视为启用
            if trigger_type == "manual":
                # 手动触发：用户在等结果，明确报错
                _fail(db, task, f"流水线「{pipe.name}」已停用，请在流水线列表打开启用开关后再调度", execution_token)
                return {"status": "error", "error": task.last_error}
            # 定时/间隔调度：停用是预期内的暂停，跳过而非失败——
            # 避免停用期间每次触发都刷一条失败记录
            msg = f"流水线「{pipe.name}」已停用，本次调度已跳过（启用后自动恢复）"
            _release_claim(db, task, execution_token, status="idle", error=msg)
            logger.info("PipelineTask %s 跳过：%s", task_id, msg)
            return {"status": "skipped", "task_id": task_id, "reason": msg}

        from app.data_channel.datasets.lake_gate import contract_pk
        pipeline_pk = contract_pk(pipe.column_definitions)
        if (task.write_mode or "overwrite") == "upsert" and not pipeline_pk:
            _fail(db, task, "主键合并无法执行：关联流水线的已发布数据契约没有主键", execution_token)
            return {"status": "error", "error": task.last_error}

        # 先物化跨提交边界需要的标量。Session 默认 expire_on_commit=True，
        # 运行记录提交后继续读取 task/pipe ORM 会额外引入一次失败窗口。
        pipeline_id = task.pipeline_id
        write_opts = {
            "mode": task.write_mode or "overwrite",
            "primary_key": pipeline_pk,
            "soft_delete_column": task.soft_delete_column or "",
            "skip_empty": bool(task.skip_empty),
        }
        config_snapshot = {
            "task_name": task.name,
            "pipeline_id": pipeline_id,
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
        }

        # 预先生成 ID，使 commit 已落库但 refresh 失败时仍能精确标记该运行。
        run_id = str(uuid.uuid4())
        try:
            run = PipelineRun(
                id=run_id,
                pipeline_id=pipeline_id,
                task_id=task.id,
                status="pending",
                # 真实列与 stats 键同步填列：历史过滤走索引列，HTTP 契约不变
                trigger_type=trigger_type,
                started_at=datetime.utcnow(),
                # 审计：执行时刻的任务配置快照——配置日后被改，这条记录仍还原当时口径
                stats={
                    "triggered_by": f"task:{task.id}",
                    "trigger_type": trigger_type,
                    "config_snapshot": config_snapshot,
                },
            )
            db.add(run)
            db.commit()
            db.refresh(run)
        except Exception as exc:  # noqa: BLE001 - 任一初始化阶段都必须释放 claim
            message = f"流水线运行记录初始化失败：{exc}"
            logger.exception(
                "PipelineTask %s 创建 PipelineRun %s 失败",
                task_id,
                run_id,
            )
            released = _recover_run_initialization_failure(
                SessionLocal,
                db,
                task_id=task_id,
                run_id=run_id,
                execution_token=execution_token,
                message=message,
                trigger_type=trigger_type,
            )
            if not released:
                logger.warning(
                    "PipelineTask %s 的初始化失败结果未释放 claim：租约已由新 token 接管",
                    task_id,
                )
            return {
                "status": "error",
                "task_id": task_id,
                "error": message,
            }
    finally:
        db.close()

    # 与手动/Celery 运行同一条执行路径，额外携带入库方式
    from app.tasks.v2.pipeline_run import pipeline_run_task
    fn = getattr(pipeline_run_task, "run", pipeline_run_task)  # Celery task 或裸函数均可
    try:
        fn(pipeline_id, run_id, write_opts)
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
            final_error = "" if ok else ((run.error_log if run else "") or "执行失败")
            released = _release_claim(
                db, task, execution_token,
                status="success" if ok else "failed",
                error=final_error,
                rows=int(stats.get("lake_rows") or stats.get("rows_out") or 0),
                run_id=run_id,
                trigger_type=trigger_type,
            )
            if not released:
                logger.warning(
                    "PipelineTask %s 的执行租约已被恢复任务接管，忽略旧执行结果 run=%s",
                    task_id, run_id,
                )
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


def _fail(db, task, message: str, token: str) -> None:
    _release_claim(db, task, token, status="failed", error=message)
