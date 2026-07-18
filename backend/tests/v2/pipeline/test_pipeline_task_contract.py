"""数据任务只消费流水线发布契约，不再成为主键的第二权威源。"""

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from datetime import datetime, timedelta, timezone

from app.data_channel.pipeline_tasks.models import PipelineTask
from app.data_channel.pipeline_tasks.router import (
    PipelineTaskCreate,
    _validate,
    create_task,
    list_histories,
    list_tasks,
    selectable_pipelines,
    stats_overview,
    toggle_task,
)
from app.data_channel.pipeline_tasks.engine import _claim_task, _release_claim
from app.models.v2.pipeline import Pipeline, PipelineRun


def _published_pipeline(db, *, primary_key: str | None = "order_id") -> Pipeline:
    definitions = [
        {
            "field_key": "order_id",
            "field_name": "订单编号",
            "field_type": "string",
            "is_primary_key": primary_key == "order_id",
            "nullable": False,
        },
        {
            "field_key": "is_deleted",
            "field_name": "删除标记",
            "field_type": "boolean",
            "is_primary_key": False,
            "nullable": True,
        },
    ]
    pipe = Pipeline(
        id="pipe-contract",
        name="订单流水线",
        spec={},
        status="published",
        enabled=True,
        column_definitions=definitions,
    )
    db.add(pipe)
    db.commit()
    return pipe


def _body(**overrides) -> PipelineTaskCreate:
    data = {
        "name": "订单每日入湖",
        "description": "将订单流水线产物按计划写入资产湖",
        "pipeline_id": "pipe-contract",
        "write_mode": "upsert",
        "schedule_type": "MANUAL",
    }
    data.update(overrides)
    return PipelineTaskCreate(**data)


def test_task_rejects_primary_key_not_in_pipeline_contract(db):
    _published_pipeline(db)

    with pytest.raises(HTTPException, match="数据任务不再定义主键"):
        _validate(db, _body(primary_key="customer_id"))


@pytest.mark.parametrize("field", ["name", "description"])
def test_task_requires_non_blank_name_and_description(field):
    with pytest.raises(ValidationError, match=field):
        _body(**{field: "   "})


def test_selectable_pipeline_contract_exposes_labels_and_constraints(db):
    _published_pipeline(db)
    db.add_all([
        Pipeline(
            id="pipe-disabled", name="已停用流水线", spec={}, status="published",
            enabled=False, column_definitions=[{"field_key": "id"}],
        ),
        Pipeline(
            id="pipe-draft", name="草稿流水线", spec={}, status="draft",
            enabled=True, column_definitions=[{"field_key": "id"}],
        ),
    ])
    db.commit()

    result = selectable_pipelines(db)

    assert result["total"] == 1
    assert result["items"][0]["id"] == "pipe-contract"
    assert result["items"][0]["contract"]["columns"][0] == {
        "name": "order_id",
        "type": "string",
        "field_name": "订单编号",
        "is_primary_key": True,
        "nullable": False,
    }


def test_upsert_requires_pipeline_contract_primary_key(db):
    _published_pipeline(db, primary_key=None)

    with pytest.raises(HTTPException, match="流水线在发布契约中声明主键"):
        _validate(db, _body())


def test_task_persists_pipeline_primary_key_as_read_only_snapshot(db, monkeypatch):
    _published_pipeline(db)
    monkeypatch.setattr(
        "app.data_channel.pipeline_tasks.router._refresh_scheduler",
        lambda _task_id: None,
    )

    created = create_task(_body(primary_key=""), db)

    task = db.query(PipelineTask).filter(PipelineTask.id == created["id"]).one()
    assert task.primary_key == "order_id"
    assert created["primary_key"] == "order_id"


def test_soft_delete_column_must_exist_in_published_contract(db):
    _published_pipeline(db)

    with pytest.raises(HTTPException, match="不在流水线发布契约"):
        _validate(db, _body(soft_delete_column="deleted_at"))


def test_stats_returns_real_seven_day_series(db, monkeypatch):
    _published_pipeline(db)
    monkeypatch.setattr(
        "app.data_channel.pipeline_tasks.router._refresh_scheduler",
        lambda _task_id: None,
    )
    task = create_task(_body(), db)
    db.add_all([
        PipelineRun(
            pipeline_id="pipe-contract", task_id=task["id"], status="success",
            created_at=datetime.utcnow(),
        ),
        PipelineRun(
            pipeline_id="pipe-contract", task_id=task["id"], status="failed",
            created_at=datetime.utcnow() - timedelta(days=1),
        ),
    ])
    db.commit()

    stats = stats_overview(db)

    assert len(stats["trend_7d"]) == 7
    assert sum(day["runs"] for day in stats["trend_7d"]) == 2
    assert sum(day["errors"] for day in stats["trend_7d"]) == 1
    assert stats["idle"] == 1
    assert stats["success"] == 0
    assert [item["status"] for item in stats["recent_runs"]] == ["success", "failed"]
    assert all(item["task_name"] == "订单每日入湖" for item in stats["recent_runs"])


def test_task_search_includes_related_pipeline_name(db, monkeypatch):
    _published_pipeline(db)
    monkeypatch.setattr(
        "app.data_channel.pipeline_tasks.router._refresh_scheduler",
        lambda _task_id: None,
    )
    create_task(_body(name="每日入湖"), db)

    result = list_tasks(search="订单流水线", db=db)

    assert result["total"] == 1
    assert result["items"][0]["name"] == "每日入湖"


def test_stats_and_histories_use_shanghai_day_and_explicit_utc(
    db, monkeypatch,
):
    _published_pipeline(db)
    monkeypatch.setattr(
        "app.data_channel.pipeline_tasks.router._refresh_scheduler",
        lambda _task_id: None,
    )
    monkeypatch.setattr(
        "app.data_channel.pipeline_tasks.router._now_utc",
        lambda: datetime(2026, 7, 16, 4, 0, tzinfo=timezone.utc),
    )
    task = create_task(_body(), db)
    # UTC 7/15 16:30 = 上海 7/16 00:30，应计入上海“今日”。
    db.add(PipelineRun(
        pipeline_id="pipe-contract",
        task_id=task["id"],
        status="success",
        created_at=datetime(2026, 7, 15, 16, 30),
        started_at=datetime(2026, 7, 15, 16, 30),
        finished_at=datetime(2026, 7, 15, 16, 30, 5),
    ))
    db.commit()

    stats = stats_overview(db)
    history = list_histories(task["id"], db=db)

    assert stats["today_runs"] == 1
    assert next(
        day for day in stats["trend_7d"] if day["date"] == "2026-07-16"
    )["runs"] == 1
    assert history["items"][0]["started_at"] == "2026-07-15T16:30:00Z"
    assert history["items"][0]["finished_at"] == "2026-07-15T16:30:05Z"


def test_task_cannot_be_enabled_while_pipeline_is_disabled(db, monkeypatch):
    pipe = _published_pipeline(db)
    monkeypatch.setattr(
        "app.data_channel.pipeline_tasks.router._refresh_scheduler",
        lambda _task_id: None,
    )
    task = create_task(_body(enabled=False), db)
    pipe.enabled = False
    db.commit()

    with pytest.raises(HTTPException, match="不能启用"):
        toggle_task(task["id"], True, db)


def test_invalid_cron_is_rejected_before_task_is_saved(db):
    _published_pipeline(db)

    with pytest.raises(HTTPException, match="cron 表达式无效"):
        _validate(db, _body(schedule_type="CRON", cron_expression="99 * * * *"))


def test_task_claim_is_atomic_and_expired_lease_can_recover(db, monkeypatch):
    _published_pipeline(db)
    monkeypatch.setattr(
        "app.data_channel.pipeline_tasks.router._refresh_scheduler",
        lambda _task_id: None,
    )
    created = create_task(_body(write_mode="overwrite"), db)

    first, token, error = _claim_task(db, created["id"])
    assert first is not None and token and error is None
    second, second_token, error = _claim_task(db, created["id"])
    assert second is None and second_token is None and error == "任务正在执行中"

    first.lease_expires_at = datetime.utcnow() - timedelta(seconds=1)
    db.commit()
    recovered, recovered_token, error = _claim_task(db, created["id"])
    assert recovered is not None and recovered_token != token and error is None
    assert _release_claim(db, first, token, status="success") is False
    assert _release_claim(db, recovered, recovered_token, status="success") is True


def test_task_execution_materializes_pipeline_id_before_session_close(
    db, monkeypatch,
):
    """默认 expire_on_commit 会过期 ORM；执行边界不能携带 detached task。"""
    from sqlalchemy.orm import sessionmaker
    from app.data_channel.pipeline_tasks import engine as task_engine
    from app.tasks.v2.pipeline_run import pipeline_run_task

    _published_pipeline(db)
    monkeypatch.setattr(
        "app.data_channel.pipeline_tasks.router._refresh_scheduler",
        lambda _task_id: None,
    )
    created = create_task(_body(write_mode="overwrite"), db)
    runtime_session = sessionmaker(bind=db.get_bind(), expire_on_commit=True)
    monkeypatch.setattr("app.database.SessionLocal", runtime_session)

    calls = []

    def fake_pipeline_run(pipeline_id, run_id, write_opts):
        calls.append((pipeline_id, run_id, write_opts))
        run_db = runtime_session()
        try:
            run = run_db.query(PipelineRun).filter(PipelineRun.id == run_id).one()
            run.status = "success"
            run.stats = {**(run.stats or {}), "lake_rows": 2, "rows_out": 2}
            run_db.commit()
        finally:
            run_db.close()

    monkeypatch.setattr(pipeline_run_task, "run", fake_pipeline_run)

    result = task_engine.execute_pipeline_task(created["id"])

    assert result["status"] == "ok"
    assert result["lake_rows"] == 2
    assert calls[0][0] == "pipe-contract"
    assert calls[0][2]["primary_key"] == "order_id"


def _prepare_fault_injection_runtime(db, monkeypatch):
    """让执行引擎使用可注入故障的独立 Session。"""
    from sqlalchemy.orm import sessionmaker

    runtime_factory = sessionmaker(bind=db.get_bind(), expire_on_commit=True)
    runtime_db = runtime_factory()
    monkeypatch.setattr("app.database.SessionLocal", lambda: runtime_db)
    return runtime_factory, runtime_db


def _assert_initialization_failed(db, task_id: str, expected_error: str):
    db.expire_all()
    task = db.query(PipelineTask).filter(PipelineTask.id == task_id).one()
    assert task.status == "failed"
    assert task.execution_token is None
    assert task.lease_expires_at is None
    assert task.last_run_at is not None
    assert expected_error in task.last_error


def test_pipeline_run_insert_exception_releases_task_claim(db, monkeypatch):
    """数据库 INSERT/flush 失败时不能留下 6 小时 running lease。"""
    from sqlalchemy import event
    from app.data_channel.pipeline_tasks import engine as task_engine

    _published_pipeline(db)
    monkeypatch.setattr(
        "app.data_channel.pipeline_tasks.router._refresh_scheduler",
        lambda _task_id: None,
    )
    created = create_task(_body(write_mode="overwrite"), db)
    _prepare_fault_injection_runtime(db, monkeypatch)

    def fail_run_insert(_mapper, _connection, _target):
        raise RuntimeError("insert hook unavailable")

    event.listen(PipelineRun, "before_insert", fail_run_insert)
    try:
        result = task_engine.execute_pipeline_task(created["id"])
    finally:
        event.remove(PipelineRun, "before_insert", fail_run_insert)

    assert result["status"] == "error"
    assert "insert hook unavailable" in result["error"]
    _assert_initialization_failed(db, created["id"], "insert hook unavailable")
    assert db.query(PipelineRun).filter(PipelineRun.task_id == created["id"]).count() == 0


def test_pipeline_run_commit_exception_releases_task_claim(db, monkeypatch):
    """claim 提交成功、run 提交失败时，清理事务仍应把任务记为 failed。"""
    from app.data_channel.pipeline_tasks import engine as task_engine

    _published_pipeline(db)
    monkeypatch.setattr(
        "app.data_channel.pipeline_tasks.router._refresh_scheduler",
        lambda _task_id: None,
    )
    created = create_task(_body(write_mode="overwrite"), db)
    _, runtime_db = _prepare_fault_injection_runtime(db, monkeypatch)
    real_commit = runtime_db.commit
    commit_calls = 0

    def fail_second_commit():
        nonlocal commit_calls
        commit_calls += 1
        if commit_calls == 2:
            raise RuntimeError("run commit unavailable")
        return real_commit()

    monkeypatch.setattr(runtime_db, "commit", fail_second_commit)

    result = task_engine.execute_pipeline_task(created["id"])

    assert result["status"] == "error"
    assert commit_calls == 3  # claim、失败的 run commit、claim 清理
    _assert_initialization_failed(db, created["id"], "run commit unavailable")
    assert db.query(PipelineRun).filter(PipelineRun.task_id == created["id"]).count() == 0


def test_pipeline_run_refresh_exception_marks_run_and_task_failed(db, monkeypatch):
    """run 已提交但 refresh 失败时，不能留下 pending run 或 running task。"""
    from app.data_channel.pipeline_tasks import engine as task_engine

    _published_pipeline(db)
    monkeypatch.setattr(
        "app.data_channel.pipeline_tasks.router._refresh_scheduler",
        lambda _task_id: None,
    )
    created = create_task(_body(write_mode="overwrite"), db)
    _, runtime_db = _prepare_fault_injection_runtime(db, monkeypatch)
    real_refresh = runtime_db.refresh

    def fail_run_refresh(instance, *args, **kwargs):
        if isinstance(instance, PipelineRun):
            raise RuntimeError("run refresh unavailable")
        return real_refresh(instance, *args, **kwargs)

    monkeypatch.setattr(runtime_db, "refresh", fail_run_refresh)

    result = task_engine.execute_pipeline_task(created["id"])

    assert result["status"] == "error"
    _assert_initialization_failed(db, created["id"], "run refresh unavailable")
    run = db.query(PipelineRun).filter(PipelineRun.task_id == created["id"]).one()
    assert run.status == "failed"
    assert run.finished_at is not None
    assert "run refresh unavailable" in run.error_log


def test_initialization_failure_does_not_release_replacement_token(db, monkeypatch):
    """旧执行初始化失败时，只能标记自己的 run，不能释放恢复执行的 claim。"""
    from app.data_channel.pipeline_tasks import engine as task_engine

    _published_pipeline(db)
    monkeypatch.setattr(
        "app.data_channel.pipeline_tasks.router._refresh_scheduler",
        lambda _task_id: None,
    )
    created = create_task(_body(write_mode="overwrite"), db)
    runtime_factory, runtime_db = _prepare_fault_injection_runtime(db, monkeypatch)
    real_refresh = runtime_db.refresh
    replacement_token = None

    def replace_claim_then_fail_refresh(instance, *args, **kwargs):
        nonlocal replacement_token
        if not isinstance(instance, PipelineRun):
            return real_refresh(instance, *args, **kwargs)

        takeover_db = runtime_factory()
        try:
            takeover_db.query(PipelineTask).filter(
                PipelineTask.id == created["id"],
            ).update({
                PipelineTask.lease_expires_at: datetime.utcnow() - timedelta(seconds=1),
            }, synchronize_session=False)
            takeover_db.commit()
            replacement, replacement_token, error = _claim_task(
                takeover_db, created["id"],
            )
            assert replacement is not None and error is None
        finally:
            takeover_db.close()
        raise RuntimeError("refresh failed after lease takeover")

    monkeypatch.setattr(runtime_db, "refresh", replace_claim_then_fail_refresh)

    result = task_engine.execute_pipeline_task(created["id"])

    assert result["status"] == "error"
    db.expire_all()
    task = db.query(PipelineTask).filter(PipelineTask.id == created["id"]).one()
    assert replacement_token
    assert task.status == "running"
    assert task.execution_token == replacement_token
    assert task.lease_expires_at > datetime.utcnow()
    assert task.last_error == ""
    run = db.query(PipelineRun).filter(PipelineRun.task_id == created["id"]).one()
    assert run.status == "failed"
    assert "refresh failed after lease takeover" in run.error_log
