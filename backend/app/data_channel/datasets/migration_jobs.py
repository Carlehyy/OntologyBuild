"""Filesystem-backed state for curated→manual asynchronous migrations.

与 ``import_jobs.py`` 同一套状态机约定：上传目录本身已是 API 与后台
executor 共享卷，把迁移任务的瞬态状态收敛在独立根目录下即可避免一次
数据库迁移，且已完成/失败的任务对管理员来说直观可查、可整体清理。
"""
from __future__ import annotations

import json
import os
import shutil
import uuid
from datetime import datetime
from pathlib import Path

from app.config import settings


MIGRATION_ROOT_NAME = "dataset-migrations"
MANIFEST_NAME = "manifest.json"
STATUS_NAME = "status.json"

# 终态：列表接口据此区分"仍在进行需要轮询"的任务
TERMINAL_STATUSES = ("completed", "failed")


def migration_root() -> Path:
    root = Path(settings.uploads_dir).expanduser().resolve() / MIGRATION_ROOT_NAME
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    return root


def normalize_job_id(job_id: str) -> str:
    try:
        return str(uuid.UUID(str(job_id)))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError("无效的迁移任务 ID") from exc


def job_directory(job_id: str) -> Path:
    return migration_root() / normalize_job_id(job_id)


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"迁移任务状态文件损坏：{path.name}") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"迁移任务状态文件格式无效：{path.name}")
    return value


def _write_json_atomic(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(value, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def create_migration_job(*, owner_id: str, source_dataset_id: str,
                         source_name: str, target_name: str) -> dict:
    job_id = str(uuid.uuid4())
    folder = job_directory(job_id)
    folder.mkdir(parents=True, exist_ok=False, mode=0o700)
    now = datetime.now().astimezone().isoformat()
    manifest = {
        "job_id": job_id,
        "owner_id": str(owner_id),
        "source_dataset_id": str(source_dataset_id),
        "source_name": source_name,
        "target_name": target_name,
        "created_at": now,
    }
    status = {
        "job_id": job_id,
        "status": "queued",
        "source_dataset_name": source_name,
        "target_name": target_name,
        "progress": 0,
        "phase": "等待后台执行迁移",
        "error": None,
        "created_at": now,
        "updated_at": now,
    }
    _write_json_atomic(folder / MANIFEST_NAME, manifest)
    _write_json_atomic(folder / STATUS_NAME, status)
    return manifest


def read_manifest(job_id: str) -> dict:
    return _read_json(job_directory(job_id) / MANIFEST_NAME)


def read_status(job_id: str) -> dict:
    return _read_json(job_directory(job_id) / STATUS_NAME)


def update_status(job_id: str, *, status: str | None = None, **patch) -> dict:
    folder = job_directory(job_id)
    current = _read_json(folder / STATUS_NAME)
    if status is not None:
        current["status"] = status
    current.update(patch)
    current["updated_at"] = datetime.now().astimezone().isoformat()
    _write_json_atomic(folder / STATUS_NAME, current)
    return current


def remove_job(job_id: str) -> None:
    """Remove one explicitly resolved job directory, never the migration root."""
    folder = job_directory(job_id)
    if folder.parent != migration_root():
        raise RuntimeError("拒绝清理迁移根目录之外的路径")
    shutil.rmtree(folder, ignore_errors=True)


def assert_job_owner(job_id: str, owner_id: str) -> dict:
    manifest = read_manifest(job_id)
    if str(manifest.get("owner_id") or "") != str(owner_id):
        raise PermissionError("无权访问该迁移任务")
    return manifest


def list_jobs(owner_id: str, limit: int = 20) -> list[dict]:
    """返回该用户最近创建的迁移任务（含终态），按创建时间倒序。"""
    jobs: list[dict] = []
    root = migration_root()
    try:
        entries = list(root.iterdir())
    except OSError:
        return []
    for folder in entries:
        if not folder.is_dir():
            continue
        try:
            manifest = _read_json(folder / MANIFEST_NAME)
            status = _read_json(folder / STATUS_NAME)
        except (FileNotFoundError, RuntimeError):
            continue  # 半写入或已损坏的任务目录直接跳过，不影响整体列表
        if str(manifest.get("owner_id") or "") != str(owner_id):
            continue
        merged = {
            **manifest,
            **{key: value for key, value in status.items() if key != "job_id"},
        }
        jobs.append(merged)
    jobs.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
    return jobs[: max(1, limit)]
