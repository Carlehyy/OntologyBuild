"""
数据任务池调度器 (APScheduler 后台线程)
- 只调度已发布流水线对应的 PipelineTask
- 旧 DataSyncTask 仅保留历史审计，不再注册或执行
- 同一任务同一时间只允许一个实例运行（内存锁）
"""
from __future__ import annotations

import logging
import threading
from typing import Any

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

logger = logging.getLogger(__name__)

# Job ID 前缀
_JOB_PREFIX = "sync_task:"
_PIPE_JOB_PREFIX = "pipe_task:"
_DATASET_EVENT_JOB_ID = "dataset_version_events:drain"
# 正在执行的任务锁
_running_locks: dict[str, threading.Lock] = {}
_global_lock = threading.Lock()


def _get_task_lock(task_id: str) -> threading.Lock:
    with _global_lock:
        if task_id not in _running_locks:
            _running_locks[task_id] = threading.Lock()
        return _running_locks[task_id]


def _job_runner(task_id: str) -> None:
    """兼容旧 APScheduler 引用；退休任务永远不再执行。"""
    logger.warning("已忽略退休的 DataSyncTask 调度回调: %s", task_id)


def _pipeline_job_runner(task_id: str) -> None:
    """调度器回调：安全执行流水线调度任务（任务池新语义）"""
    lock = _get_task_lock(f"pipe:{task_id}")
    if not lock.acquire(blocking=False):
        logger.info(f"PipelineTask {task_id} 正在执行，跳过本次调度")
        return
    try:
        from app.data_channel.pipeline_tasks.engine import execute_pipeline_task
        result = execute_pipeline_task(task_id, trigger_type="scheduled")
        if result.get("status") == "ok":
            logger.info(f"PipelineTask {task_id} 调度执行成功 lake_rows={result.get('lake_rows')}")
        else:
            logger.error(f"PipelineTask {task_id} 调度执行失败: {result.get('error')}")
    except Exception as e:
        logger.exception(f"PipelineTask {task_id} 调度执行异常: {e}")
    finally:
        lock.release()


def _dataset_event_job_runner() -> None:
    """Continuously drain durable lake-version events."""
    try:
        from app.data_channel.datasets.version_events import (
            drain_dataset_version_events,
        )
        result = drain_dataset_version_events()
        if result.get("processed") or result.get("retried"):
            logger.info("DatasetVersion event outbox: %s", result)
    except Exception:
        logger.exception("DatasetVersion event outbox worker failed")


class SyncScheduler:
    """同步任务调度器 — 单例"""

    _instance: "SyncScheduler | None" = None
    _instance_lock = threading.Lock()

    def __init__(self):
        self._scheduler = BackgroundScheduler(timezone="Asia/Shanghai")
        self._started = False
        self._last_error: str | None = None

    @property
    def scheduler(self):
        return self._scheduler

    @property
    def started(self):
        return self._started

    @property
    def healthy(self) -> bool:
        return bool(
            self._started
            and getattr(self._scheduler, "running", False)
            and self._last_error is None)

    @property
    def last_error(self) -> str | None:
        return self._last_error

    @classmethod
    def get(cls) -> "SyncScheduler":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def start(self) -> None:
        if self._started:
            return
        try:
            self._scheduler.start()
            self._started = True
            self.reload_all()
            from app.config import settings
            self._scheduler.add_job(
                _dataset_event_job_runner,
                trigger=IntervalTrigger(
                    seconds=max(1, int(settings.dataset_event_poll_seconds or 2))),
                id=_DATASET_EVENT_JOB_ID,
                replace_existing=True,
                coalesce=True,
                max_instances=1,
                misfire_grace_time=30,
            )
            # Fail closed on a missing production migration and recover any
            # events left behind by the previous process before reporting ready.
            from app.data_channel.datasets.version_events import (
                drain_dataset_version_events,
            )
            drain_dataset_version_events(
                limit=int(settings.dataset_event_batch_size or 20),
                strict_schema=settings.environment == "production",
            )
            logger.info("DataSyncScheduler 已启动")
        except Exception as e:
            self._last_error = str(e)
            self._started = False
            logger.error(f"DataSyncScheduler 启动失败: {e}")

    def shutdown(self) -> None:
        if self._started:
            try:
                self._scheduler.shutdown(wait=False)
            except Exception:
                pass
            self._started = False

    def _job_id(self, task_id: str) -> str:
        return f"{_JOB_PREFIX}{task_id}"

    def _add_job_for_task(self, task) -> None:
        """移除旧任务残留 Job；DataSyncTask 不再进入执行主链路。"""
        job_id = self._job_id(task.id)
        try:
            self._scheduler.remove_job(job_id)
        except Exception:
            pass
        logger.info("DataSyncTask %s 已退休，不注册调度", task.id)

    def _add_job_for_pipeline_task(self, task) -> None:
        """为流水线调度任务注册 APScheduler Job（与同步任务同一调度器实例）"""
        job_id = f"{_PIPE_JOB_PREFIX}{task.id}"
        try:
            self._scheduler.remove_job(job_id)
        except Exception:
            pass
        if not task.enabled:
            return
        if task.schedule_type == "CRON" and task.cron_expression:
            try:
                parts = task.cron_expression.strip().split()
                if len(parts) != 5:
                    logger.warning(f"PipelineTask {task.id} cron 表达式不合法: {task.cron_expression}")
                    return
                trigger = CronTrigger(
                    minute=parts[0], hour=parts[1], day=parts[2],
                    month=parts[3], day_of_week=parts[4],
                )
                self._scheduler.add_job(
                    _pipeline_job_runner, trigger=trigger, id=job_id,
                    args=[task.id], replace_existing=True,
                    misfire_grace_time=60, coalesce=True, max_instances=1,
                )
                logger.info(f"已注册流水线任务 CRON 调度: {task.name} ({task.cron_expression})")
            except Exception as e:
                logger.error(f"注册流水线任务 CRON 失败 {task.id}: {e}")
        elif task.schedule_type == "INTERVAL" and task.interval_seconds and task.interval_seconds > 0:
            try:
                trigger = IntervalTrigger(seconds=task.interval_seconds)
                self._scheduler.add_job(
                    _pipeline_job_runner, trigger=trigger, id=job_id,
                    args=[task.id], replace_existing=True,
                    misfire_grace_time=60, coalesce=True, max_instances=1,
                )
                logger.info(f"已注册流水线任务 INTERVAL 调度: {task.name} ({task.interval_seconds}s)")
            except Exception as e:
                logger.error(f"注册流水线任务 INTERVAL 失败 {task.id}: {e}")
        # MANUAL: 不注册调度

    def reload_all(self) -> None:
        """只从 DB 加载 PipelineTask，并清除所有旧 SyncTask Job。"""
        if not self._started:
            return
        self._last_error = None
        try:
            from app.database import SessionLocal
            from app.data_channel.pipeline_tasks.models import PipelineTask
            db = SessionLocal()
            try:
                # 先清除所有相关 job
                for job in self._scheduler.get_jobs():
                    if (job.id.startswith(_JOB_PREFIX)
                            or job.id.startswith(_PIPE_JOB_PREFIX)):
                        try:
                            self._scheduler.remove_job(job.id)
                        except Exception:
                            pass
                for t in db.query(PipelineTask).all():
                    self._add_job_for_pipeline_task(t)
            finally:
                db.close()
        except Exception as e:
            self._last_error = str(e)
            logger.error(f"reload_all 失败: {e}")

    def reload_pipeline_task(self, task_id: str) -> None:
        """更新单个流水线调度任务的 Job（任务 CRUD 后调用）"""
        if not self._started:
            return
        try:
            from app.database import SessionLocal
            from app.data_channel.pipeline_tasks.models import PipelineTask
            db = SessionLocal()
            try:
                task = db.query(PipelineTask).filter(PipelineTask.id == task_id).first()
                if task:
                    self._add_job_for_pipeline_task(task)
                else:
                    try:
                        self._scheduler.remove_job(f"{_PIPE_JOB_PREFIX}{task_id}")
                    except Exception:
                        pass
            finally:
                db.close()
        except Exception as e:
            logger.error(f"reload_pipeline_task 失败: {e}")

    def reload_task(self, task_id: str) -> None:
        """兼容旧路由：只清理残留 Job，绝不重新注册。"""
        if not self._started:
            return
        try:
            self._scheduler.remove_job(self._job_id(task_id))
        except Exception as e:
            # APScheduler 未找到 job 是正常的幂等结果。
            if e.__class__.__name__ != "JobLookupError":
                logger.error(f"清理旧 DataSyncTask job 失败: {e}")


def get_sync_scheduler() -> SyncScheduler:
    return SyncScheduler.get()
