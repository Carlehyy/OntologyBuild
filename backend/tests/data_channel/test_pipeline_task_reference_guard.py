import pytest
from fastapi import HTTPException

from app.data_channel.pipeline_tasks.models import PipelineTask
from app.data_channel.pipeline_tasks.router import pipeline_filter_options
from app.data_channel.pipelines.router import (
    EnabledBody,
    list_pipelines,
    set_pipeline_enabled,
)
from app.models.v2.pipeline import Pipeline


def _pipeline(*, enabled: bool = True) -> Pipeline:
    return Pipeline(
        name="订单清洗流水线",
        domain="测试",
        route="A",
        spec={},
        status="published",
        enabled=enabled,
    )


def _task(pipeline_id: str, *, enabled: bool = False) -> PipelineTask:
    return PipelineTask(
        name="每日订单入湖",
        pipeline_id=pipeline_id,
        write_mode="overwrite",
        schedule_type="MANUAL",
        enabled=enabled,
        status="idle",
    )


def test_referenced_pipeline_cannot_change_enabled_even_when_task_disabled(db):
    pipeline = _pipeline()
    db.add(pipeline)
    db.flush()
    task = _task(pipeline.id, enabled=False)
    db.add(task)
    db.commit()

    with pytest.raises(HTTPException) as exc_info:
        set_pipeline_enabled(pipeline.id, EnabledBody(enabled=False), db)

    assert exc_info.value.status_code == 409
    assert "已被 1 个数据任务关联" in exc_info.value.detail
    assert "每日订单入湖" in exc_info.value.detail
    assert db.get(Pipeline, pipeline.id).enabled is True


def test_pipeline_enabled_can_change_after_last_task_relation_is_removed(db):
    pipeline = _pipeline()
    db.add(pipeline)
    db.flush()
    task = _task(pipeline.id)
    db.add(task)
    db.commit()

    db.delete(task)
    db.commit()

    result = set_pipeline_enabled(pipeline.id, EnabledBody(enabled=False), db)

    assert result["enabled"] is False
    assert db.get(Pipeline, pipeline.id).enabled is False


def test_pipeline_list_and_filter_options_share_relation_count(db):
    pipeline = _pipeline()
    db.add(pipeline)
    db.flush()
    db.add_all([_task(pipeline.id), _task(pipeline.id)])
    db.commit()

    page = list_pipelines(
        search="",
        domain="",
        status="",
        engine="",
        enabled=None,
        page=1,
        page_size=20,
        paginated=True,
        db=db,
    )
    item = next(row for row in page["items"] if row["id"] == pipeline.id)
    options = pipeline_filter_options(db)
    option = next(row for row in options["items"] if row["id"] == pipeline.id)

    assert item["task_count"] == 2
    assert option == {
        "id": pipeline.id,
        "name": pipeline.name,
        "task_count": 2,
    }
