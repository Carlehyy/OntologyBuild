"""三维场景 — 读路径查询（列表、详情、版本、运行日志）。"""
from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.scenes.models import (
    Scene, SceneRuntimeLog, SceneVersion,
    SCENE_STATUSES, STATUS_DRAFT,
)


def _iso(value):
    return value.isoformat() if value else None


def require_scene(db: Session, scene_id: str) -> Scene:
    scene = db.query(Scene).filter(Scene.id == scene_id).one_or_none()
    if scene is None:
        raise HTTPException(
            status_code=404, detail={"code": "scene_not_found"})
    return scene


def scene_out(scene: Scene, *, version_count: int | None = None) -> dict:
    out = {
        "id": scene.id,
        "name": scene.name,
        "description": scene.description or "",
        "icon": scene.icon or "boxes",
        "status": scene.status,
        "current_version_no": scene.current_version_no,
        "published_version_no": scene.published_version_no,
        "created_by": scene.created_by,
        "created_at": _iso(scene.created_at),
        "updated_at": _iso(scene.updated_at),
    }
    if version_count is not None:
        out["version_count"] = version_count
    return out


def list_scenes(
    db: Session, *, q: str | None = None, status: str | None = None,
    page: int = 1, page_size: int = 20,
) -> dict:
    query = db.query(Scene)
    if q:
        like = f"%{q}%"
        query = query.filter(or_(Scene.name.ilike(like), Scene.description.ilike(like)))
    if status and status != "all":
        if status not in SCENE_STATUSES:
            raise HTTPException(
                status_code=400,
                detail={"code": "invalid_status", "message": "status 仅支持 draft/published/all"},
            )
        query = query.filter(Scene.status == status)
    total = query.count()
    rows = (
        query.order_by(Scene.updated_at.desc(), Scene.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return {"items": [scene_out(row) for row in rows], "total": total}


def scene_detail(db: Session, scene: Scene) -> dict:
    version_count = (
        db.query(func.count(SceneVersion.id))
        .filter(SceneVersion.scene_id == scene.id)
        .scalar()
    )
    return scene_out(scene, version_count=int(version_count or 0))


def version_out(version: SceneVersion, *, include_definition: bool = False) -> dict:
    out = {
        "id": version.id,
        "scene_id": version.scene_id,
        "version_no": version.version_no,
        "source": version.source,
        "note": version.note or "",
        "created_by": version.created_by,
        "created_at": _iso(version.created_at),
    }
    if include_definition:
        out["definition"] = version.definition
    return out


def list_versions(
    db: Session, scene: Scene, *, include_definition: bool = False,
) -> dict:
    rows = (
        db.query(SceneVersion)
        .filter(SceneVersion.scene_id == scene.id)
        .order_by(SceneVersion.version_no.desc())
        .all()
    )
    return {
        "items": [
            version_out(row, include_definition=include_definition)
            for row in rows
        ],
        "total": len(rows),
    }


def get_version(db: Session, scene: Scene, version_no: int) -> dict:
    version = (
        db.query(SceneVersion)
        .filter(
            SceneVersion.scene_id == scene.id,
            SceneVersion.version_no == version_no,
        )
        .one_or_none()
    )
    if version is None:
        raise HTTPException(
            status_code=404, detail={"code": "scene_version_not_found"})
    return version_out(version, include_definition=True)


def log_out(log: SceneRuntimeLog) -> dict:
    return {
        "id": log.id,
        "scene_id": log.scene_id,
        "level": log.level,
        "object_id": log.object_id,
        "event_key": log.event_key,
        "message": log.message,
        "payload": log.payload or {},
        "occurred_at": _iso(log.occurred_at),
        "recorded_at": _iso(log.recorded_at),
    }


def list_runtime_logs(
    db: Session, scene: Scene, *, level: str | None = None,
    object_id: str | None = None, page: int = 1, page_size: int = 20,
) -> dict:
    """运行日志对草稿态同样可查（历史留存），前端仅在发布态突出入口。"""
    query = db.query(SceneRuntimeLog).filter(SceneRuntimeLog.scene_id == scene.id)
    if level and level != "all":
        query = query.filter(SceneRuntimeLog.level == level)
    if object_id:
        query = query.filter(SceneRuntimeLog.object_id == object_id)
    total = query.count()
    rows = (
        query.order_by(SceneRuntimeLog.occurred_at.desc(), SceneRuntimeLog.recorded_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return {"items": [log_out(row) for row in rows], "total": total}
