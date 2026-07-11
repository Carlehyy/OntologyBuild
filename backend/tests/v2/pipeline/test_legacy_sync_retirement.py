from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import BackgroundTasks, HTTPException

from app.data_channel.sync_tasks import engine, router
from app.data_channel.sync_tasks.scheduler import SyncScheduler


@pytest.mark.parametrize(
    ("operation", "args"),
    [
        (
            router.create_task,
            (router.SyncTaskCreate(name="legacy", connection_id="conn-1"), None),
        ),
        (
            router.update_task,
            ("legacy-1", router.SyncTaskUpdate(name="legacy"), None),
        ),
        (router.toggle_task, ("legacy-1", True, None)),
        (
            router.trigger_task,
            ("legacy-1", BackgroundTasks(), False, None),
        ),
    ],
)
def test_legacy_sync_write_and_execution_entrypoints_are_gone(operation, args):
    with pytest.raises(HTTPException) as exc_info:
        operation(*args)

    assert exc_info.value.status_code == 410
    assert "n8n" in exc_info.value.detail
    assert "PipelineTask" in exc_info.value.detail


def test_legacy_engine_cannot_touch_data_sources_even_if_called_directly():
    result = engine.execute_sync_task("legacy-1")

    assert result["status"] == "error"
    assert result["task_id"] == "legacy-1"
    assert "已停用" in result["error"]


def test_scheduler_only_removes_legacy_jobs_and_never_registers_them():
    scheduler = SyncScheduler()
    scheduler._scheduler = MagicMock()
    task = SimpleNamespace(id="legacy-1")

    scheduler._add_job_for_task(task)

    scheduler._scheduler.remove_job.assert_called_once_with("sync_task:legacy-1")
    scheduler._scheduler.add_job.assert_not_called()
