"""三维场景 — 写路径业务逻辑（状态机、版本冻结、克隆、日志上报）。"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.scenes import models as m
from app.scenes import validation
from app.scenes.schemas import (
    RuntimeLogAppend, SceneCreate, SceneDefinitionSave, SceneUpdate,
)

MAX_LOG_BATCH = 200
MAX_NAME_LENGTH = 120


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _bad_request(code: str, message: str) -> HTTPException:
    return HTTPException(status_code=400, detail={"code": code, "message": message})


def _invalid_definition(issues: list[dict]) -> HTTPException:
    return HTTPException(
        status_code=400,
        detail={"code": "invalid_scene_definition", "issues": issues},
    )


def _insert_version(
    db: Session, scene: m.Scene, *, definition: dict, version_no: int,
    source: str, note: str, user,
) -> m.SceneVersion:
    version = m.SceneVersion(
        scene_id=scene.id,
        version_no=version_no,
        definition=definition,
        source=source,
        note=note,
        created_by=getattr(user, "id", None),
    )
    db.add(version)
    return version


def _prune_versions(db: Session, scene: m.Scene, latest_no: int) -> None:
    """版本保留上限：超出 DEFINITION_VERSION_KEEP 的最老快照物理删除。"""
    cutoff = latest_no - m.DEFINITION_VERSION_KEEP
    if cutoff <= 0:
        return
    db.query(m.SceneVersion).filter(
        m.SceneVersion.scene_id == scene.id,
        m.SceneVersion.version_no <= cutoff,
    ).delete(synchronize_session=False)


def create_scene(db: Session, body: SceneCreate, user) -> m.Scene:
    scene = m.Scene(
        name=body.name.strip(),
        description=body.description or "",
        icon=body.icon or "boxes",
        created_by=getattr(user, "id", None),
    )
    db.add(scene)
    if body.definition is not None:
        issues = validation.validate_definition(body.definition)
        if issues:
            db.rollback()
            raise _invalid_definition(issues)
        db.flush()
        _insert_version(
            db, scene,
            definition=validation.normalize_definition(body.definition),
            version_no=1, source=m.VERSION_SOURCE_MANUAL, note="初始版本",
            user=user,
        )
        scene.current_version_no = 1
    db.commit()
    db.refresh(scene)
    return scene


def update_scene_info(db: Session, scene: m.Scene, body: SceneUpdate) -> m.Scene:
    """仅基本信息（名称/描述/图标）；定义内容必须走 save_definition 冻结版本。"""
    if body.name is not None:
        scene.name = body.name.strip()
    if body.description is not None:
        scene.description = body.description
    if body.icon is not None:
        scene.icon = body.icon
    db.commit()
    db.refresh(scene)
    return scene


def delete_scene(db: Session, scene: m.Scene) -> None:
    db.delete(scene)
    db.commit()


def clone_scene(db: Session, scene: m.Scene, user) -> m.Scene:
    """快照克隆：取当前生效定义生成全新草稿场景，版本历史从 v1 开始。

    当前生效定义 = 已发布版本（若存在——发布后继续编辑的场景虽回落
    草稿态，其已发布版本仍对外生效），否则取最新草稿版本。
    """
    source_no = scene.published_version_no or scene.current_version_no
    if not source_no:
        raise _bad_request("scene_not_clonable", "场景尚无任何版本定义，无法克隆")
    source_version = (
        db.query(m.SceneVersion)
        .filter(
            m.SceneVersion.scene_id == scene.id,
            m.SceneVersion.version_no == source_no,
        )
        .one_or_none()
    )
    if source_version is None:
        raise _bad_request("scene_not_clonable", f"版本 v{source_no} 定义缺失，无法克隆")

    new_name = f"{scene.name}-副本"[:MAX_NAME_LENGTH]
    cloned = m.Scene(
        name=new_name,
        description=scene.description,
        icon=scene.icon,
        status=m.STATUS_DRAFT,
        created_by=getattr(user, "id", None),
    )
    db.add(cloned)
    db.flush()
    _insert_version(
        db, cloned,
        definition=source_version.definition,
        version_no=1, source=m.VERSION_SOURCE_CLONE,
        note=f"克隆自「{scene.name}」v{source_no}", user=user,
    )
    cloned.current_version_no = 1
    db.commit()
    db.refresh(cloned)
    return cloned


def save_definition(
    db: Session, scene: m.Scene, body: SceneDefinitionSave, *,
    source: str, user,
) -> m.SceneVersion:
    """保存即冻结新版本；发布态场景被继续编辑时自动回到草稿态，
    已发布版本号保留（可随时重新发布）。"""
    issues = validation.validate_definition(body.definition)
    if issues:
        raise _invalid_definition(issues)

    if scene.status == m.STATUS_PUBLISHED:
        scene.status = m.STATUS_DRAFT
    next_no = scene.current_version_no + 1
    version = _insert_version(
        db, scene,
        definition=validation.normalize_definition(body.definition),
        version_no=next_no,
        source=source if source in (
            m.VERSION_SOURCE_MANUAL, m.VERSION_SOURCE_ASSISTANT,
        ) else m.VERSION_SOURCE_MANUAL,
        note=(body.note or "")[:500],
        user=user,
    )
    scene.current_version_no = next_no
    _prune_versions(db, scene, next_no)
    db.commit()
    db.refresh(version)
    return version


def publish_scene(db: Session, scene: m.Scene, user) -> m.Scene:
    """发布：冻结当前草稿版本为对外生效版本。"""
    if scene.current_version_no < 1:
        raise _bad_request("scene_no_version", "场景还没有任何版本定义，请先保存场景定义")
    if (
        scene.status == m.STATUS_PUBLISHED
        and scene.published_version_no == scene.current_version_no
    ):
        raise _bad_request("scene_already_published", "当前版本已处于发布态")
    scene.status = m.STATUS_PUBLISHED
    scene.published_version_no = scene.current_version_no
    db.commit()
    db.refresh(scene)
    return scene


def append_runtime_logs(db: Session, scene: m.Scene, body: RuntimeLogAppend) -> int:
    """前端引擎批量上报运行日志（规则命中/恢复等）。整批原子写入。"""
    entries = body.entries
    if not entries:
        raise _bad_request("empty_log_batch", "日志批次为空")
    if len(entries) > MAX_LOG_BATCH:
        raise _bad_request(
            "too_many_log_entries", f"单批最多 {MAX_LOG_BATCH} 条日志")
    for entry in entries:
        if entry.level not in m.LOG_LEVELS:
            raise _bad_request(
                "invalid_log_level",
                f"未知日志级别 {entry.level}，可选：{'/'.join(m.LOG_LEVELS)}")
    now = _now()
    for entry in entries:
        db.add(m.SceneRuntimeLog(
            scene_id=scene.id,
            level=entry.level,
            object_id=(entry.object_id or None),
            event_key=entry.event_key or "",
            message=entry.message,
            payload=entry.payload,
            occurred_at=entry.occurred_at or now,
        ))
    db.commit()
    return len(entries)
