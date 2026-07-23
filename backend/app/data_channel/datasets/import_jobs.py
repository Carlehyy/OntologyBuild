"""Filesystem-backed state for asynchronous manual dataset imports.

The upload directory is already a shared volume between the API and Celery
worker.  Keeping transient import state below one isolated root avoids adding a
database migration and makes completed/failed jobs straightforward for an
administrator to inspect or remove.
"""
from __future__ import annotations

import json
import os
import shutil
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Any

from app.config import settings


IMPORT_ROOT_NAME = "dataset-imports"
MANIFEST_NAME = "manifest.json"
STATUS_NAME = "status.json"
METADATA_NAME = "metadata.json"


def _json_default(value: Any) -> str:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def import_root() -> Path:
    root = Path(settings.uploads_dir).expanduser().resolve() / IMPORT_ROOT_NAME
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    return root


def normalize_job_id(job_id: str) -> str:
    try:
        return str(uuid.UUID(str(job_id)))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError("无效的导入任务 ID") from exc


def job_directory(job_id: str) -> Path:
    return import_root() / normalize_job_id(job_id)


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"导入任务状态文件损坏：{path.name}") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"导入任务状态文件格式无效：{path.name}")
    return value


def _write_json_atomic(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, default=_json_default),
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def create_import_job(*, owner_id: str, filename: str, extension: str) -> dict:
    job_id = str(uuid.uuid4())
    folder = job_directory(job_id)
    folder.mkdir(parents=True, exist_ok=False, mode=0o700)
    now = datetime.now().astimezone().isoformat()
    manifest = {
        "job_id": job_id,
        "owner_id": str(owner_id),
        "filename": Path(filename).name,
        "extension": str(extension).lower(),
        "file_size": 0,
        "created_at": now,
    }
    status = {
        "job_id": job_id,
        "status": "uploading",
        "filename": manifest["filename"],
        "file_size": 0,
        "created_at": now,
        "updated_at": now,
    }
    _write_json_atomic(folder / MANIFEST_NAME, manifest)
    _write_json_atomic(folder / STATUS_NAME, status)
    return manifest


def read_manifest(job_id: str) -> dict:
    return _read_json(job_directory(job_id) / MANIFEST_NAME)


def update_manifest(job_id: str, **patch: Any) -> dict:
    folder = job_directory(job_id)
    manifest = _read_json(folder / MANIFEST_NAME)
    manifest.update(patch)
    _write_json_atomic(folder / MANIFEST_NAME, manifest)
    return manifest


def read_status(job_id: str) -> dict:
    return _read_json(job_directory(job_id) / STATUS_NAME)


def update_status(job_id: str, *, status: str | None = None, **patch: Any) -> dict:
    folder = job_directory(job_id)
    current = _read_json(folder / STATUS_NAME)
    if status is not None:
        current["status"] = status
    current.update(patch)
    current["updated_at"] = datetime.now().astimezone().isoformat()
    _write_json_atomic(folder / STATUS_NAME, current)
    return current


def source_path(job_id: str, extension: str | None = None) -> Path:
    ext = str(extension or read_manifest(job_id).get("extension") or "").lower()
    if not ext or not ext.isalnum():
        raise ValueError("导入文件扩展名无效")
    return job_directory(job_id) / f"source.{ext}"


def write_metadata(job_id: str, metadata: dict) -> None:
    _write_json_atomic(job_directory(job_id) / METADATA_NAME, metadata)


def read_metadata(job_id: str) -> dict:
    return _read_json(job_directory(job_id) / METADATA_NAME)


def remove_job(job_id: str) -> None:
    """Remove one explicitly resolved job directory, never the import root."""
    folder = job_directory(job_id)
    if folder.parent != import_root():
        raise RuntimeError("拒绝清理导入根目录之外的路径")
    shutil.rmtree(folder, ignore_errors=True)


def assert_job_owner(job_id: str, owner_id: str) -> dict:
    manifest = read_manifest(job_id)
    if str(manifest.get("owner_id") or "") != str(owner_id):
        raise PermissionError("无权访问该导入任务")
    return manifest
