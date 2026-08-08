"""流水线任务 NATS 派发与调用点切换的契约测试。"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta

import pytest
from fastapi import BackgroundTasks, HTTPException

from app.data_channel.pipeline_tasks import dispatch as dispatch_module
from app.data_channel.pipeline_tasks.dispatch import (
    PIPELINE_EXECUTE_SUBJECT,
    PIPELINE_STREAM,
    dispatch_pipeline_task,
    ensure_pipeline_stream,
)
from app.data_channel.pipeline_tasks.execution_service import trigger_task
from app.data_channel.pipeline_tasks.models import PipelineTask
from app.data_channel.sync_tasks import scheduler as scheduler_module
from app.data_channel.sync_tasks.scheduler import SyncScheduler
from app.models.v2.pipeline import Pipeline


class _FakeJS:
    def __init__(self, calls, *, add_stream_error: Exception | None = None):
        self._calls = calls
        self._add_stream_error = add_stream_error

    async def add_stream(self, config):
        self._calls.setdefault("add_stream", []).append(config)
        if self._add_stream_error is not None:
            raise self._add_stream_error


class _FakeNC:
    def __init__(self, calls, **js_kwargs):
        self._js = _FakeJS(calls, **js_kwargs)
        self._calls = calls

    def jetstream(self):
        return self._js

    async def drain(self):
        self._calls["drained"] = True


@pytest.fixture
def fake_nats(monkeypatch):
    """替换 nats.connect 为记录型假客户端，返回记录字典。"""
    import nats

    calls: dict = {"published": []}

    class PublishingJS(_FakeJS):
        async def publish(self, subject, payload, headers=None):
            calls["published"].append((subject, payload, headers))

    class PublishingNC(_FakeNC):
        def __init__(self, calls_):
            super().__init__(calls_)
            self._js = PublishingJS(calls_)

    async def fake_connect(url, **kwargs):
        calls["connect"] = (url, kwargs)
        return PublishingNC(calls)

    monkeypatch.setattr(nats, "connect", fake_connect)
    monkeypatch.setattr(dispatch_module, "_stream_ensured", False)
    monkeypatch.setattr(
        "app.config.settings.nats_url", "nats://fake-nats:4222",
    )
    return calls


def test_dispatch_publishes_payload_subject_and_msg_id(fake_nats):
    dispatch_pipeline_task("task-1", "scheduled")

    url, kwargs = fake_nats["connect"]
    assert url == "nats://fake-nats:4222"
    assert kwargs["connect_timeout"] == 3

    (subject, payload, headers), = fake_nats["published"]
    assert subject == PIPELINE_EXECUTE_SUBJECT == "pipeline.task.execute"
    body = json.loads(payload.decode())
    assert body["task_id"] == "task-1"
    assert body["trigger_type"] == "scheduled"
    assert datetime.fromisoformat(body["dispatched_at"])
    assert re.fullmatch(r"task-1:scheduled:\d+", headers["Nats-Msg-Id"])
    assert fake_nats["drained"] is True


def test_dispatch_ensures_work_queue_stream_once(fake_nats, monkeypatch):
    from nats.js.api import RetentionPolicy

    dispatch_pipeline_task("task-1", "scheduled")
    dispatch_pipeline_task("task-2", "manual")

    (config,), = [fake_nats["add_stream"]]
    assert config.name == PIPELINE_STREAM == "PIPELINE_TASKS"
    assert config.subjects == ["pipeline.task.execute"]
    assert config.retention == RetentionPolicy.WORK_QUEUE
    assert config.max_age == 7 * 24 * 3600
    assert config.duplicate_window == 10 * 60
    # 进程内缓存：同一进程只确保一次 Stream
    assert len(fake_nats["published"]) == 2


def test_dispatch_without_nats_url_fails_with_chinese_message(monkeypatch):
    monkeypatch.setattr("app.config.settings.nats_url", "")

    with pytest.raises(RuntimeError, match="未配置 NATS_URL"):
        dispatch_pipeline_task("task-1", "manual")


@pytest.mark.asyncio
async def test_ensure_stream_tolerates_already_exists():
    calls: dict = {"add_stream": []}
    js = _FakeJS(
        calls,
        add_stream_error=Exception("stream name already in use"),
    )

    await ensure_pipeline_stream(js)  # 不抛异常即为通过


@pytest.mark.asyncio
async def test_ensure_stream_propagates_real_errors():
    calls: dict = {"add_stream": []}
    js = _FakeJS(calls, add_stream_error=Exception("permission denied"))

    with pytest.raises(Exception, match="permission denied"):
        await ensure_pipeline_stream(js)


def _published_pipeline(db) -> Pipeline:
    pipe = Pipeline(
        id="pipe-dispatch",
        name="派发流水线",
        spec={},
        status="published",
        enabled=True,
        column_definitions=[{"field_key": "id"}],
    )
    db.add(pipe)
    db.commit()
    return pipe


def _idle_task(db) -> PipelineTask:
    _published_pipeline(db)
    task = PipelineTask(
        id="task-dispatch",
        name="派发任务",
        description="",
        pipeline_id="pipe-dispatch",
        status="idle",
    )
    db.add(task)
    db.commit()
    return task


def test_pipeline_job_runner_dispatches_instead_of_executing(monkeypatch):
    dispatched = []

    def fake_dispatch(task_id, trigger_type):
        dispatched.append((task_id, trigger_type))

    def forbidden_execute(*_args, **_kwargs):  # pragma: no cover - 防御断言
        raise AssertionError("调度回调不得再内联执行 execute_pipeline_task")

    monkeypatch.setattr(
        "app.data_channel.pipeline_tasks.dispatch.dispatch_pipeline_task",
        fake_dispatch,
    )
    monkeypatch.setattr(
        "app.data_channel.pipeline_tasks.engine.execute_pipeline_task",
        forbidden_execute,
    )

    scheduler_module._pipeline_job_runner("task-1")

    assert dispatched == [("task-1", "scheduled")]


def test_pipeline_job_runner_survives_dispatch_failure(monkeypatch):
    def failing_dispatch(_task_id, _trigger_type):
        raise RuntimeError("channel down")

    monkeypatch.setattr(
        "app.data_channel.pipeline_tasks.dispatch.dispatch_pipeline_task",
        failing_dispatch,
    )

    # 派发失败只记日志等下一周期重试，不能炸掉调度线程
    scheduler_module._pipeline_job_runner("task-1")

    # 锁已释放：下一周期可以再次派发
    dispatched = []
    monkeypatch.setattr(
        "app.data_channel.pipeline_tasks.dispatch.dispatch_pipeline_task",
        lambda task_id, trigger_type: dispatched.append((task_id, trigger_type)),
    )
    scheduler_module._pipeline_job_runner("task-1")
    assert dispatched == [("task-1", "scheduled")]


def test_trigger_task_async_dispatches(monkeypatch, db):
    task = _idle_task(db)
    dispatched = []

    def forbidden_execute(*_args, **_kwargs):  # pragma: no cover - 防御断言
        raise AssertionError("sync=false 不得再内联执行 execute_pipeline_task")

    monkeypatch.setattr(
        "app.data_channel.pipeline_tasks.dispatch.dispatch_pipeline_task",
        lambda task_id, trigger_type: dispatched.append((task_id, trigger_type)),
    )
    monkeypatch.setattr(
        "app.data_channel.pipeline_tasks.engine.execute_pipeline_task",
        forbidden_execute,
    )

    result = trigger_task(task.id, BackgroundTasks(), False, db)

    assert result == {"status": "triggered", "task_id": task.id}
    assert dispatched == [(task.id, "manual")]


def test_trigger_task_async_dispatch_failure_returns_503(monkeypatch, db):
    task = _idle_task(db)

    def failing_dispatch(_task_id, _trigger_type):
        raise RuntimeError("未配置 NATS_URL")

    monkeypatch.setattr(
        "app.data_channel.pipeline_tasks.dispatch.dispatch_pipeline_task",
        failing_dispatch,
    )

    with pytest.raises(HTTPException) as exc_info:
        trigger_task(task.id, BackgroundTasks(), False, db)

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == "任务派发失败：消息通道不可用，请稍后重试"


def test_trigger_task_sync_still_executes_inline(monkeypatch, db):
    task = _idle_task(db)
    executed = []

    def fake_execute(task_id, trigger_type):
        executed.append((task_id, trigger_type))
        return {"status": "ok", "task_id": task_id}

    def forbidden_dispatch(*_args, **_kwargs):  # pragma: no cover - 防御断言
        raise AssertionError("sync=true 保持内联执行，不得派发")

    monkeypatch.setattr(
        "app.data_channel.pipeline_tasks.engine.execute_pipeline_task",
        fake_execute,
    )
    monkeypatch.setattr(
        "app.data_channel.pipeline_tasks.dispatch.dispatch_pipeline_task",
        forbidden_dispatch,
    )

    result = trigger_task(task.id, BackgroundTasks(), True, db)

    assert result["status"] == "ok"
    assert executed == [(task.id, "manual")]


def test_scheduler_start_registers_reconcile_job(monkeypatch):
    monkeypatch.setattr(SyncScheduler, "reload_all", lambda self: None)
    monkeypatch.setattr(
        "app.data_channel.datasets.version_events.drain_dataset_version_events",
        lambda **_kwargs: {},
    )
    instance = SyncScheduler()

    instance.start()
    try:
        job = instance.scheduler.get_job("pipeline_executions:reconcile")
        assert job is not None
        assert job.coalesce is True
        assert job.max_instances == 1
        assert job.misfire_grace_time == 60
        assert job.trigger.interval.total_seconds() == 300
    finally:
        instance.shutdown()


def test_reconcile_job_runner_opens_session_and_swallows_errors(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "app.data_channel.pipeline_tasks.reconciler.reconcile_pipeline_executions",
        lambda db: calls.append(db) or {"tasks_failed": 0, "runs_failed": 0},
    )
    monkeypatch.setattr("app.database.SessionLocal", lambda: object())

    scheduler_module._pipeline_reconcile_job_runner()

    assert len(calls) == 1

    def failing_reconcile(_db):
        raise RuntimeError("db down")

    monkeypatch.setattr(
        "app.data_channel.pipeline_tasks.reconciler.reconcile_pipeline_executions",
        failing_reconcile,
    )
    # 对账器异常不能炸掉调度线程
    scheduler_module._pipeline_reconcile_job_runner()
