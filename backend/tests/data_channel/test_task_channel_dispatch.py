"""UI 手动运行与数据集导入经 NATS 任务通道派发的契约测试。"""
from __future__ import annotations

import json

import pytest
from fastapi import HTTPException

from app.data_channel.datasets import mutation_service
from app.data_channel.pipelines import execution_service
from app.models.v2.pipeline import Pipeline, PipelineRun


def _published_pipeline(db) -> Pipeline:
    pipe = Pipeline(
        id="pipe-manual-run",
        name="手动运行流水线",
        spec={},
        status="published",
        enabled=True,
        column_definitions=[{"field_key": "id"}],
    )
    db.add(pipe)
    db.commit()
    return pipe


def test_enqueue_pipeline_run_dispatches_nats_task(monkeypatch, db):
    pipeline = _published_pipeline(db)
    dispatched = []
    monkeypatch.setattr(
        "app.data_channel.pipeline_tasks.dispatch.dispatch_task",
        lambda subject, payload: dispatched.append((subject, payload)),
    )

    result = execution_service.enqueue_pipeline_run(
        pipeline.id,
        db,
        require_production_executable_fn=lambda _pipeline: None,
    )

    run = db.query(PipelineRun).filter_by(id=result["run_id"]).one()
    assert result == {"run_id": run.id, "status": "pending"}
    assert run.status == "pending"
    assert dispatched == [
        (
            "task.pipeline.run",
            {"pipeline_id": pipeline.id, "run_id": run.id},
        ),
    ]


def test_enqueue_pipeline_run_dispatch_failure_marks_run_failed(monkeypatch, db):
    """投递失败：run 立即标 failed 并写中性 error_log，不抛 503。"""
    pipeline = _published_pipeline(db)

    def channel_down(*_args, **_kwargs):
        raise RuntimeError("nats secret detail")

    monkeypatch.setattr(
        "app.data_channel.pipeline_tasks.dispatch.dispatch_task", channel_down
    )

    result = execution_service.enqueue_pipeline_run(
        pipeline.id,
        db,
        require_production_executable_fn=lambda _pipeline: None,
    )

    run = db.query(PipelineRun).filter_by(id=result["run_id"]).one()
    assert result["status"] == "failed"
    assert result["error"] == run.error_log
    assert run.status == "failed"
    assert "任务派发失败" in run.error_log
    assert run.finished_at is not None
    # 失败文案不泄露通道内部异常之外的实现细节
    assert "Celery" not in run.error_log
    assert "Redis" not in run.error_log


def test_enqueue_pipeline_run_missing_pipeline_is_404(db):
    with pytest.raises(HTTPException) as exc_info:
        execution_service.enqueue_pipeline_run(
            "pipe-missing",
            db,
            require_production_executable_fn=lambda _pipeline: None,
        )
    assert exc_info.value.status_code == 404


def _create_import_job(tmp_path, monkeypatch) -> str:
    from app.config import settings
    from app.data_channel.datasets.import_jobs import create_import_job

    monkeypatch.setattr(settings, "uploads_dir", str(tmp_path / "uploads"))
    manifest = create_import_job(
        owner_id="user-1", filename="台账.csv", extension="csv"
    )
    return manifest["job_id"]


def test_dispatch_dataset_import_task_publishes_nats_message(
    tmp_path, monkeypatch
):
    import logging

    job_id = _create_import_job(tmp_path, monkeypatch)
    dispatched = []
    monkeypatch.setattr(
        "app.data_channel.pipeline_tasks.dispatch.dispatch_task",
        lambda subject, payload: dispatched.append((subject, payload)),
    )

    status = mutation_service.dispatch_dataset_import_task(
        job_id,
        kind="inspect",
        operation="解析",
        settings_obj=None,
        logger_obj=logging.getLogger("test.dataset_import.dispatch"),
    )

    assert dispatched == [
        ("task.dataset.import", {"job_id": job_id, "kind": "inspect"}),
    ]
    assert status["execution_mode"] == "nats"


def test_dispatch_dataset_import_task_failure_is_fail_closed(
    tmp_path, monkeypatch, caplog
):
    """投递失败写 status.json failed + HTTP 503，文案不泄露通道细节。"""
    import logging

    from app.data_channel.datasets.import_jobs import read_status

    job_id = _create_import_job(tmp_path, monkeypatch)

    def channel_down(*_args, **_kwargs):
        raise RuntimeError("nats secret detail")

    monkeypatch.setattr(
        "app.data_channel.pipeline_tasks.dispatch.dispatch_task", channel_down
    )
    logger = logging.getLogger("test.dataset_import.dispatch")
    with caplog.at_level("ERROR", logger="test.dataset_import.dispatch"):
        with pytest.raises(HTTPException) as exc_info:
            mutation_service.dispatch_dataset_import_task(
                job_id,
                kind="commit",
                operation="导入",
                settings_obj=None,
                logger_obj=logger,
            )

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == "后台任务通道不可用，数据集导入任务未投递"
    assert "nats secret detail" not in exc_info.value.detail
    assert "任务未执行" in caplog.text
    assert "nats secret detail" not in caplog.text

    status = read_status(job_id)
    assert status["status"] == "failed"
    assert status["execution_mode"] == "nats"
    assert status["phase"] == "后台任务投递失败"
    assert status["error"] == "后台任务通道不可用"
    assert "nats secret detail" not in json.dumps(status, ensure_ascii=False)
