"""数据任务池增量游标（Foundry 式源端水位）契约测试。

覆盖：游标列契约校验、CRUD 流转（改列重置水位）、full_refresh 触发链、
引擎 run_params 注入、水位仅成功推进、产出词法最大值计算。
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.data_channel.pipeline_tasks.models import PipelineTask
from app.data_channel.pipeline_tasks.router import (
    PipelineTaskCreate,
    PipelineTaskUpdate,
    _validate,
    create_task,
    update_task,
)
from app.models.v2.pipeline import Pipeline, PipelineRun


def _published_pipeline(db, pipe_id: str = "pipe-incr") -> Pipeline:
    definitions = [
        {"field_key": "order_id", "field_name": "订单编号",
         "field_type": "string", "is_primary_key": True, "nullable": False},
        {"field_key": "updated_at", "field_name": "更新时间",
         "field_type": "timestamp", "is_primary_key": False, "nullable": True},
    ]
    pipe = Pipeline(
        id=pipe_id, name="订单流水线", spec={}, status="published",
        enabled=True, column_definitions=definitions,
    )
    db.add(pipe)
    db.commit()
    return pipe


def _body(pipe_id: str = "pipe-incr", **overrides) -> PipelineTaskCreate:
    data = {
        "name": "订单增量入湖",
        "description": "按增量游标只拉新数据",
        "pipeline_id": pipe_id,
        "write_mode": "upsert",
        "schedule_type": "MANUAL",
    }
    data.update(overrides)
    return PipelineTaskCreate(**data)


def _no_scheduler(monkeypatch):
    monkeypatch.setattr(
        "app.data_channel.pipeline_tasks.router._refresh_scheduler",
        lambda _task_id: None,
    )


# ── 校验与 CRUD ─────────────────────────────────────────────
def test_cursor_column_must_exist_in_published_contract(db):
    _published_pipeline(db)

    with pytest.raises(HTTPException, match="增量游标列「created_at」不在流水线发布契约"):
        _validate(db, _body(cursor_column="created_at"))


def test_cursor_column_empty_by_default_and_persisted(db, monkeypatch):
    _published_pipeline(db)
    _no_scheduler(monkeypatch)

    created = create_task(_body(cursor_column="updated_at"), db)
    task = db.query(PipelineTask).filter(PipelineTask.id == created["id"]).one()
    assert task.cursor_column == "updated_at"
    assert task.last_cursor_value == ""
    assert created["cursor_column"] == "updated_at"

    created2 = create_task(_body(name="全量任务"), db)
    task2 = db.query(PipelineTask).filter(PipelineTask.id == created2["id"]).one()
    assert task2.cursor_column == ""


def test_cursor_column_change_resets_watermark(db, monkeypatch):
    _published_pipeline(db)
    _no_scheduler(monkeypatch)
    created = create_task(_body(cursor_column="updated_at"), db)
    task = db.query(PipelineTask).filter(PipelineTask.id == created["id"]).one()
    task.last_cursor_value = "2026-08-01T00:00:00"
    db.commit()

    # 改游标列 → 水位归零（旧水位对新列不可比较）
    update_task(created["id"], PipelineTaskUpdate(cursor_column="order_id"), db)
    db.expire_all()
    task = db.query(PipelineTask).filter(PipelineTask.id == created["id"]).one()
    assert task.cursor_column == "order_id"
    assert task.last_cursor_value == ""

    # 与游标无关的编辑不动水位
    task.last_cursor_value = "2026-08-02T00:00:00"
    db.commit()
    update_task(created["id"], PipelineTaskUpdate(description="改个描述"), db)
    db.expire_all()
    task = db.query(PipelineTask).filter(PipelineTask.id == created["id"]).one()
    assert task.last_cursor_value == "2026-08-02T00:00:00"


# ── full_refresh 触发链 ─────────────────────────────────────
def test_trigger_full_refresh_reaches_dispatch(db, monkeypatch):
    _published_pipeline(db)
    _no_scheduler(monkeypatch)
    created = create_task(_body(cursor_column="updated_at"), db)

    sent = []
    monkeypatch.setattr(
        "app.data_channel.pipeline_tasks.dispatch.dispatch_pipeline_task",
        lambda task_id, trigger_type, full_refresh=False: sent.append(
            (task_id, trigger_type, full_refresh)))
    from app.data_channel.pipeline_tasks import execution_service

    result = execution_service.trigger_task(created["id"], None, False, db, True)
    assert result["status"] == "triggered"
    assert sent == [(created["id"], "manual", True)]

    result = execution_service.trigger_task(created["id"], None, False, db, False)
    assert sent[-1] == (created["id"], "manual", False)


def test_dispatch_payload_carries_full_refresh(monkeypatch):
    captured = {}
    from app.data_channel.pipeline_tasks import dispatch

    def fake_sync(subject, payload, msg_id):
        captured.update(subject=subject, payload=payload, msg_id=msg_id)

    monkeypatch.setattr(dispatch, "_dispatch_sync", fake_sync)
    dispatch.dispatch_pipeline_task("task-1", "scheduled", full_refresh=True)
    assert captured["payload"] == {
        "task_id": "task-1", "trigger_type": "scheduled", "full_refresh": True}
    assert captured["msg_id"].startswith("task-1:scheduled:")


# ── 引擎：run_params 注入与水位推进 ──────────────────────────
def _run_with_fake_pipeline(db, monkeypatch, *, cursor_column="updated_at",
                            last_watermark="", full_refresh=False,
                            fake_status="success",
                            fake_watermark_after="2026-08-10T00:00:00"):
    """用假 pipeline_run_task 跑 engine 全流程，返回 (task, run, result)。"""
    import uuid as _uuid
    from sqlalchemy.orm import sessionmaker
    from app.data_channel.pipeline_tasks import engine as task_engine
    from app.tasks.v2.pipeline_run import pipeline_run_task

    pipe_id = f"pipe-incr-{_uuid.uuid4().hex[:8]}"
    _published_pipeline(db, pipe_id)
    _no_scheduler(monkeypatch)
    created = create_task(_body(pipe_id, cursor_column=cursor_column), db)
    if last_watermark:
        task = db.query(PipelineTask).filter(PipelineTask.id == created["id"]).one()
        task.last_cursor_value = last_watermark
        db.commit()
    runtime_session = sessionmaker(bind=db.get_bind(), expire_on_commit=True)
    monkeypatch.setattr("app.database.SessionLocal", runtime_session)

    def fake_pipeline_run(pipeline_id, run_id, write_opts):
        run_db = runtime_session()
        try:
            run = run_db.query(PipelineRun).filter(PipelineRun.id == run_id).one()
            run.status = fake_status
            run.stats = {**(run.stats or {}),
                         "lake_rows": 3, "rows_out": 3,
                         "watermark_after": fake_watermark_after}
            run.error_log = "" if fake_status == "success" else "模拟失败"
            run_db.commit()
        finally:
            run_db.close()

    monkeypatch.setattr(pipeline_run_task, "run", fake_pipeline_run, raising=False)
    result = task_engine.execute_pipeline_task(
        created["id"], full_refresh=full_refresh)
    run_db = runtime_session()
    try:
        run = run_db.query(PipelineRun).filter(
            PipelineRun.task_id == created["id"]).order_by(
            PipelineRun.created_at.desc()).first()
        run_stats = dict(run.stats or {})
    finally:
        run_db.close()
    db.expire_all()
    task = db.query(PipelineTask).filter(PipelineTask.id == created["id"]).one()
    return task, run_stats, result


def test_engine_injects_run_params_and_advances_watermark_on_success(db, monkeypatch):
    task, stats, result = _run_with_fake_pipeline(db, monkeypatch)
    assert result["status"] == "ok"
    # 首次运行：水位从空起步，成功后推进到当次产出最大值
    assert stats["run_params"] == {
        "cursor_column": "updated_at", "cursor_since": "", "full_refresh": False}
    assert task.last_cursor_value == "2026-08-10T00:00:00"
    # 配置快照口径固化当次游标配置
    assert stats["config_snapshot"]["cursor_column"] == "updated_at"
    assert stats["config_snapshot"]["full_refresh"] is False


def test_engine_uses_stored_watermark_and_full_refresh_ignores_it(db, monkeypatch):
    task, stats, _ = _run_with_fake_pipeline(
        db, monkeypatch, last_watermark="2026-08-05T00:00:00")
    assert stats["run_params"]["cursor_since"] == "2026-08-05T00:00:00"
    assert task.last_cursor_value == "2026-08-10T00:00:00"

    task2, stats2, _ = _run_with_fake_pipeline(
        db, monkeypatch, last_watermark="2026-08-05T00:00:00", full_refresh=True)
    assert stats2["run_params"]["cursor_since"] == ""
    assert stats2["run_params"]["full_refresh"] is True
    assert task2.last_cursor_value == "2026-08-10T00:00:00"


def test_engine_does_not_advance_watermark_on_failure(db, monkeypatch):
    task, stats, result = _run_with_fake_pipeline(
        db, monkeypatch, last_watermark="2026-08-05T00:00:00",
        fake_status="failed")
    assert result["status"] == "error"
    assert task.last_cursor_value == "2026-08-05T00:00:00"  # 保持不变


def test_engine_without_cursor_column_injects_no_run_params(db, monkeypatch):
    task, stats, result = _run_with_fake_pipeline(db, monkeypatch, cursor_column="")
    assert result["status"] == "ok"
    assert "run_params" not in stats
    assert task.last_cursor_value == ""


# ── 水位计算：产出词法最大值 ────────────────────────────────
def test_watermark_after_computed_from_output_rows(db, monkeypatch):
    """_save_curated_dataset：run_params 声明游标列时，按产出文本最大值记账。"""
    from types import SimpleNamespace
    from app.data_channel.datasets.service import DatasetService
    from app.tasks.v2.pipeline_run import _save_curated_dataset

    class _Storage:
        def put_bytes(self, bucket, key, data, content_type=""):
            return f"s3://{bucket}/{key}"

        def get_object(self, uri):
            raise KeyError(uri)

        def delete_object(self, uri):
            pass

    monkeypatch.setattr(
        "app.data_channel.datasets.service.get_storage_service", lambda: _Storage())
    pl = Pipeline(id="pipe-wm", name="水位流水线", spec={}, status="published",
                  enabled=True, column_definitions=[])
    db.add(pl)
    db.commit()
    svc = DatasetService(db, storage=_Storage())
    source = {"dataset_id": None, "filename": "orders", "route": "A"}
    ctx = SimpleNamespace(rows_in=0, meta={})
    run_params = {"cursor_column": "updated_at", "cursor_since": "",
                  "full_refresh": False}

    out = _save_curated_dataset(
        db, svc, pl, source, [
            {"order_id": "A-2", "updated_at": "2026-08-10T03:00:00"},
            {"order_id": "A-1", "updated_at": "2026-08-10T09:30:00"},
            {"order_id": "A-3", "updated_at": "2026-08-09T23:59:59"},
        ], ctx, False,
        write_opts={"mode": "append", "skip_empty": False},
        run_params=run_params)
    assert out["watermark_after"] == "2026-08-10T09:30:00"

    # 空产出 → None（不推进水位）
    out2 = _save_curated_dataset(
        db, svc, pl, source, [], SimpleNamespace(rows_in=0, meta={}), False,
        write_opts={"mode": "append", "skip_empty": False},
        run_params=run_params)
    assert out2["watermark_after"] is None
