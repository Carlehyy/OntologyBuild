"""Session lifecycle and read-side application services for Exploration."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.exploration import canvas as C
from app.exploration import readiness as R
from app.exploration import schemas as S
from app.exploration.diagram import (
    DIAGRAM_KINDS,
    DiagramError,
    build_diagram,
)
from app.exploration.models import (
    ExplorationAttachment,
    ExplorationDocument,
    ExplorationDraft,
    ExplorationMessage,
    ExplorationSession,
)


def _ok(data):
    return {"data": data}


def _require_session(
    db: Session,
    session_id: str,
    current_user,
    *,
    session_model=ExplorationSession,
) -> ExplorationSession:
    session = db.query(session_model).filter(
        session_model.id == session_id
    ).first()
    if not session:
        raise HTTPException(404, "探索会话不存在")
    if (
        session.user_id
        and session.user_id != getattr(current_user, "id", None)
        and getattr(current_user, "role", "") != "admin"
    ):
        raise HTTPException(403, "无权访问他人会话")
    return session


def _session_out(
    session: ExplorationSession,
    *,
    schemas_module=S,
) -> dict:
    return schemas_module.SessionOut.model_validate(session).model_dump(
        by_alias=True
    )


def _message_out(
    message: ExplorationMessage,
    canvas: dict,
    *,
    schemas_module=S,
    build_diagram_fn=build_diagram,
    diagram_error_cls=DiagramError,
) -> dict:
    """Rebuild stored diagrams through the current deterministic quality gate."""
    data = schemas_module.MessageOut.model_validate(message).model_dump(
        by_alias=True
    )
    for step in data.get("steps") or []:
        stored = step.get("diagram")
        if not isinstance(stored, dict):
            continue
        arguments = (
            step.get("arguments")
            if isinstance(step.get("arguments"), dict)
            else {}
        )
        kind = str(stored.get("kind") or arguments.get("kind") or "")
        target = arguments.get("target")
        if not target and "·" not in str(stored.get("target") or ""):
            target = stored.get("target")
        try:
            step["diagram"] = build_diagram_fn(
                canvas,
                kind,
                str(target) if target else None,
            )
        except diagram_error_cls as error:
            step.pop("diagram", None)
            step["error"] = f"历史图表已被质量门拦截：{error}"
            step["summary"] = (
                "历史图表不再满足当前画布的质量要求，请让 AI 修复后重新生成"
            )
    return data


def list_sessions(
    db: Session,
    current_user,
    *,
    session_model=ExplorationSession,
    session_out_fn: Callable[[ExplorationSession], dict] = _session_out,
    ok_fn: Callable[[Any], dict] = _ok,
):
    rows = (
        db.query(session_model)
        .filter(
            session_model.user_id
            == getattr(current_user, "id", None)
        )
        .order_by(session_model.updated_at.desc())
        .limit(100)
        .all()
    )
    return ok_fn([session_out_fn(session) for session in rows])


def create_session(
    body,
    db: Session,
    current_user,
    *,
    session_model=ExplorationSession,
    canvas_module=C,
    session_out_fn: Callable[[ExplorationSession], dict] = _session_out,
    ok_fn: Callable[[Any], dict] = _ok,
):
    session = session_model(
        user_id=getattr(current_user, "id", None),
        title=(body.title or "").strip() or "新的业务探索",
        canvas=canvas_module.empty_canvas(),
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return ok_fn(session_out_fn(session))


def get_session(
    session_id: str,
    db: Session,
    current_user,
    *,
    session_model=ExplorationSession,
    message_model=ExplorationMessage,
    require_session_fn=_require_session,
    session_out_fn: Callable[[ExplorationSession], dict] = _session_out,
    message_out_fn: Callable[[ExplorationMessage, dict], dict] = _message_out,
    canvas_module=C,
    readiness_module=R,
    ok_fn: Callable[[Any], dict] = _ok,
):
    session = require_session_fn(db, session_id, current_user)
    messages = (
        db.query(message_model)
        .filter(message_model.session_id == session.id)
        .order_by(message_model.created_at.asc())
        .limit(300)
        .all()
    )
    return ok_fn({
        **session_out_fn(session),
        "canvas": canvas_module._ensure_canvas(session.canvas),
        "completeness": canvas_module.completeness(session.canvas),
        "readiness": readiness_module.evaluate(session.canvas),
        "messages": [
            message_out_fn(message, session.canvas)
            for message in messages
        ],
    })


def delete_session(
    session_id: str,
    db: Session,
    current_user,
    *,
    require_session_fn=_require_session,
    remove_attachment_file_fn: Callable[[str | None], None],
    attachment_model=ExplorationAttachment,
    message_model=ExplorationMessage,
    draft_model=ExplorationDraft,
    document_model=ExplorationDocument,
):
    """Delete a session while preserving the historical cleanup order.

    Physical attachment cleanup intentionally happens before relational deletes
    and the final commit, matching the established endpoint behavior.
    """
    session = require_session_fn(db, session_id, current_user)
    attachments = db.query(attachment_model).filter(
        attachment_model.session_id == session.id
    ).all()
    for attachment in attachments:
        remove_attachment_file_fn(attachment.file_path)
    db.query(attachment_model).filter(
        attachment_model.session_id == session.id
    ).delete()
    db.query(message_model).filter(
        message_model.session_id == session.id
    ).delete()
    db.query(draft_model).filter(
        draft_model.session_id == session.id
    ).delete()
    db.query(document_model).filter(
        document_model.session_id == session.id
    ).delete()
    db.delete(session)
    db.commit()


def get_canvas(
    session_id: str,
    db: Session,
    current_user,
    *,
    require_session_fn=_require_session,
    canvas_module=C,
    readiness_module=R,
    ok_fn: Callable[[Any], dict] = _ok,
):
    session = require_session_fn(db, session_id, current_user)
    return ok_fn({
        "canvas": canvas_module._ensure_canvas(session.canvas),
        "version": session.canvas_version,
        "completeness": canvas_module.completeness(session.canvas),
        "readiness": readiness_module.evaluate(session.canvas),
    })


def get_readiness(
    session_id: str,
    db: Session,
    current_user,
    *,
    require_session_fn=_require_session,
    readiness_module=R,
    ok_fn: Callable[[Any], dict] = _ok,
):
    session = require_session_fn(db, session_id, current_user)
    return ok_fn(readiness_module.evaluate(session.canvas))


def get_diagram(
    session_id: str,
    kind: str,
    target: str | None,
    db: Session,
    current_user,
    *,
    require_session_fn=_require_session,
    build_diagram_fn=build_diagram,
    diagram_error_cls=DiagramError,
    ok_fn: Callable[[Any], dict] = _ok,
):
    session = require_session_fn(db, session_id, current_user)
    try:
        return ok_fn(build_diagram_fn(session.canvas, kind, target))
    except diagram_error_cls as error:
        raise HTTPException(422, str(error)) from error


def list_diagram_kinds(
    session_id: str,
    db: Session,
    current_user,
    *,
    require_session_fn=_require_session,
    diagram_kinds=DIAGRAM_KINDS,
    ok_fn: Callable[[Any], dict] = _ok,
):
    require_session_fn(db, session_id, current_user)
    return ok_fn({
        "kinds": [
            {"kind": kind, "label": label}
            for kind, label in diagram_kinds.items()
        ],
    })
