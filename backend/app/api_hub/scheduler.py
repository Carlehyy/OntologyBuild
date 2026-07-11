"""
进程内定时调度（APScheduler）。start.bat 启一个进程，就同时有了 Web 服务 + 定时任务，
不依赖 Windows 任务计划程序。Cron 用本机时区，5 段标准格式。
"""
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from . import config, credential, db

_scheduler = BackgroundScheduler()
_JOB_ID = "w3_refresh"
_SETTING_KEY = "refresh_cron"


def _job():
    credential.refresh()


def get_cron() -> str:
    return db.get_setting(_SETTING_KEY, config.DEFAULT_CRON)


def validate_cron(expr: str) -> None:
    """非法表达式抛 ValueError。"""
    CronTrigger.from_crontab(expr)


def _apply(expr: str) -> None:
    trigger = CronTrigger.from_crontab(expr)
    if _scheduler.get_job(_JOB_ID):
        _scheduler.reschedule_job(_JOB_ID, trigger=trigger)
    else:
        _scheduler.add_job(_job, trigger=trigger, id=_JOB_ID, replace_existing=True)


def start() -> None:
    global _scheduler
    # APScheduler instances cannot be restarted after shutdown. OntologyBuild's
    # lifespan is entered repeatedly by TestClient, so recreate on each start.
    if _scheduler.running:
        return
    _scheduler = BackgroundScheduler()
    expr = get_cron()
    try:
        _apply(expr)
    except ValueError:
        _apply(config.DEFAULT_CRON)
    _scheduler.start()


def set_cron(expr: str) -> None:
    validate_cron(expr)          # 先校验，非法直接抛
    db.set_setting(_SETTING_KEY, expr)
    _apply(expr)


def next_run() -> str | None:
    job = _scheduler.get_job(_JOB_ID)
    if job and job.next_run_time:
        return job.next_run_time.isoformat()
    return None


def shutdown() -> None:
    if _scheduler.running:
        _scheduler.shutdown(wait=False)
