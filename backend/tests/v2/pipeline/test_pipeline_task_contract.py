"""数据任务只消费流水线发布契约，不再成为主键的第二权威源。"""

import pytest
from fastapi import HTTPException
from datetime import datetime, timedelta

from app.data_channel.pipeline_tasks.models import PipelineTask
from app.data_channel.pipeline_tasks.router import (
    PipelineTaskCreate,
    _validate,
    create_task,
    stats_overview,
    toggle_task,
)
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
