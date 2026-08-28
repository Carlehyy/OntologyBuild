"""导入/迁移任务状态文件的 Windows 读写竞态回归。

前端 1.2s 轮询 GET /imports/{id}（read_status）与执行器 update_status 并发时，
Windows 下裸 os.replace 会报 WinError 5（拒绝访问），进行中的任务被误标失败。
原子替换必须按 3 次 × 50ms 退避重试，重试耗尽仍失败才向上抛。
"""
from __future__ import annotations

import os

import pytest

from app.config import settings


@pytest.fixture()
def uploads_root(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "uploads_dir", str(tmp_path / "uploads"))
    return tmp_path / "uploads"


def _flaky_replace(monkeypatch, failures: int) -> dict:
    """让 os.replace 的前 failures 次抛 PermissionError（WinError 5 的映射）。"""
    real_replace = os.replace
    calls = {"count": 0}

    def flaky(src, dst):
        calls["count"] += 1
        if calls["count"] <= failures:
            raise PermissionError(5, "拒绝访问")
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", flaky)
    return calls


def test_update_status_retries_replace_through_reader_window(
        uploads_root, monkeypatch):
    from app.data_channel.datasets import import_jobs

    manifest = import_jobs.create_import_job(
        owner_id="u1", filename="a.csv", extension="csv")
    job_id = manifest["job_id"]
    calls = _flaky_replace(monkeypatch, failures=2)

    status = import_jobs.update_status(job_id, status="parsing", progress=15)

    assert status["status"] == "parsing"
    assert calls["count"] == 3  # 2 次撞上读窗口 + 1 次退避后成功
    assert import_jobs.read_status(job_id)["status"] == "parsing"


def test_update_status_raises_after_retries_exhausted(uploads_root, monkeypatch):
    from app.data_channel.datasets import import_jobs

    manifest = import_jobs.create_import_job(
        owner_id="u1", filename="a.csv", extension="csv")
    job_id = manifest["job_id"]
    calls = _flaky_replace(monkeypatch, failures=99)

    with pytest.raises(PermissionError):
        import_jobs.update_status(job_id, status="parsing")

    assert calls["count"] == import_jobs.REPLACE_ATTEMPTS
    # 任务状态未被半写入的临时文件破坏：上次成功写入的内容仍可读
    assert import_jobs.read_status(job_id)["status"] == "uploading"


def test_migration_update_status_shares_replace_retry(uploads_root, monkeypatch):
    """迁移任务与导入任务轮询同频，状态写入必须共用同一套退避重试。"""
    from app.data_channel.datasets import migration_jobs

    manifest = migration_jobs.create_migration_job(
        owner_id="u1", source_dataset_id="d1", source_name="源", target_name="目标")
    calls = _flaky_replace(monkeypatch, failures=1)

    status = migration_jobs.update_status(
        manifest["job_id"], status="running", progress=30)

    assert status["status"] == "running"
    assert calls["count"] == 2
    assert migration_jobs.read_status(manifest["job_id"])["status"] == "running"
