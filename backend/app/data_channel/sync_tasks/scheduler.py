"""
数据同步任务调度器 (APScheduler 后台线程)
- 支持 CRON（cron 表达式）和 INTERVAL（秒级间隔）两种调度类型
- MANUAL 类型不自动调度，仅手动触发
- 服务启动时加载所有 enabled 的 SyncTask，注册 Job；提供 reload 方法动态刷新
- 同一任务同一时间只允许一个实例运行（内存锁）
"""
from __future__ import annotations

import logging
import threading
from typing import Any

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.models.v2.sync_task import TriggerType

logger = logging.getLogger(__name__)

# Job ID 前缀
_JOB_PREFIX = "sync_task:"
_PIPE_JOB_PREFIX = "pipe_task:"
# 正在执行的任务锁
_running_locks: dict[str, threading.Lock] = {}
_global_lock = threading.Lock()


def _get_task_lock(task_id: str) -> threading.Lock:
    with _global_lock:
        if task_id not in _running_locks:
            _running_locks[task_id] = threading.Lock()
        return _running_locks[task_id]


def _job_runner(task_id: str) -> None:
    """调度器回调：安全执行同步任务"""
    lock = _get_task_lock(task_id)
    if not lock.acquire(blocking=False):
        logger.info(f"SyncTask {task_id} 正在执行，跳过本次调度")
        return
    try:
        from app.services.v2.sync_engine import execute_sync_task
        result = execute_sync_task(task_id, trigger_type=TriggerType.SCHEDULED.value)
        if result.get("status") == "ok":
            logger.info(f"SyncTask {task_id} 调度执行成功 rows={result.get('rows')}")
        else:
            logger.error(f"SyncTask {task_id} 调度执行失败: {result.get('error')}")
    except Exception as e:
        logger.exception(f"SyncTask {task_id} 调度执行异常: {e}")
    finally:
        lock.release()


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
        """根据任务调度配置添加 APScheduler Job"""
        job_id = self._job_id(task.id)
        # 先移除已存在的
        try:
            self._scheduler.remove_job(job_id)
        except Exception:
            pass
        if not task.enabled:
            return
        if task.schedule_type == "CRON" and task.cron_expression:
            try:
                # cron 表达式是 5 段标准格式（分 时 日 月 周）
                parts = task.cron_expression.strip().split()
                if len(parts) != 5:
                    logger.warning(f"SyncTask {task.id} cron 表达式不合法: {task.cron_expression}")
                    return
                trigger = CronTrigger(
                    minute=parts[0], hour=parts[1], day=parts[2],
                    month=parts[3], day_of_week=parts[4],
                )
                self._scheduler.add_job(
                    _job_runner, trigger=trigger, id=job_id,
                    args=[task.id], replace_existing=True,
                    misfire_grace_time=60, coalesce=True, max_instances=1,
                )
                logger.info(f"已注册 CRON 调度任务: {task.name} ({task.cron_expression})")
            except Exception as e:
                logger.error(f"注册 CRON 任务失败 {task.id}: {e}")
        elif task.schedule_type == "INTERVAL" and task.interval_seconds and task.interval_seconds > 0:
            try:
                trigger = IntervalTrigger(seconds=task.interval_seconds)
                self._scheduler.add_job(
                    _job_runner, trigger=trigger, id=job_id,
                    args=[task.id], replace_existing=True,
                    misfire_grace_time=60, coalesce=True, max_instances=1,
                )
                logger.info(f"已注册 INTERVAL 调度任务: {task.name} ({task.interval_seconds}s)")
            except Exception as e:
                logger.error(f"注册 INTERVAL 任务失败 {task.id}: {e}")
        # MANUAL: 不注册调度

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
        """从 DB 加载所有 SyncTask + PipelineTask 并重建 Job 列表"""
        if not self._started:
            return
        self._last_error = None
        try:
            from app.database import SessionLocal
            from app.models.v2.sync_task import DataSyncTask
            from app.data_channel.pipeline_tasks.models import PipelineTask
            db = SessionLocal()
            try:
                # 先清除所有相关 job
                for job in self._scheduler.get_jobs():
                    if job.id.startswith(_JOB_PREFIX) or job.id.startswith(_PIPE_JOB_PREFIX):
                        try:
                            self._scheduler.remove_job(job.id)
                        except Exception:
                            pass
                for t in db.query(DataSyncTask).all():
                    self._add_job_for_task(t)
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
        """更新单个任务的调度（任务 CRUD 后调用）"""
        if not self._started:
            return
        try:
            from app.database import SessionLocal
            from app.models.v2.sync_task import DataSyncTask
            db = SessionLocal()
            try:
                task = db.query(DataSyncTask).filter(DataSyncTask.id == task_id).first()
                if task:
                    self._add_job_for_task(task)
                else:
                    try:
                        self._scheduler.remove_job(self._job_id(task_id))
                    except Exception:
                        pass
            finally:
                db.close()
        except Exception as e:
            logger.error(f"reload_task 失败: {e}")


def get_sync_scheduler() -> SyncScheduler:
    return SyncScheduler.get()
