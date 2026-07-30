"""Requirement-document application services for Exploration."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.exploration import canvas as C
from app.exploration import schemas as S
from app.exploration.document import (
    document_source_state,
    generate_document,
)
from app.exploration.models import (
    ExplorationDocument,
    ExplorationSession,
)
from app.exploration.session_service import _ok, _require_session
from app.model_configs.selector import (
    llm_call_kwargs,
    select_llm_model_config,
)


def _document_out(
    document: ExplorationDocument,
    session: ExplorationSession,
    *,
    list_item: bool = False,
    document_source_state_fn=document_source_state,
    schemas_module=S,
) -> dict:
    state = document_source_state_fn(document, session)
    payload = {
        "id": document.id,
        "session_id": document.session_id,
        "title": document.title,
        "version": document.version,
        "created_at": document.created_at,
        **state,
    }
    schema = schemas_module.DocumentListItem
    if not list_item:
        payload["content_md"] = document.content_md
        schema = schemas_module.DocumentOut
    return schema.model_validate(payload).model_dump(by_alias=True)


def _require_document(
    db: Session,
    document_id: str,
    current_user,
    *,
    document_model=ExplorationDocument,
    require_session_fn=_require_session,
) -> ExplorationDocument:
    document = db.query(document_model).filter(
        document_model.id == document_id
    ).first()
    if not document:
        raise HTTPException(404, "需求文档不存在")
    require_session_fn(db, document.session_id, current_user)
    return document


def create_document(
    session_id: str,
    body,
    db: Session,
    current_user,
    *,
    require_session_fn=_require_session,
    canvas_module=C,
    select_llm_model_config_fn=select_llm_model_config,
    llm_call_kwargs_fn=llm_call_kwargs,
    generate_document_fn=generate_document,
    document_out_fn: Callable[
        [ExplorationDocument, ExplorationSession],
        dict,
    ] = _document_out,
    ok_fn: Callable[[Any], dict] = _ok,
):
    session = require_session_fn(db, session_id, current_user)
    completeness = canvas_module.completeness(session.canvas)
    if not any(completeness["counts"].values()):
        raise HTTPException(
            422,
            "画布还是空的 —— 先通过对话沉淀一些业务模型再生成文档",
        )
    config = select_llm_model_config_fn(db, model_id=body.model_id)
    try:
        call_kwargs = llm_call_kwargs_fn(config)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    document = generate_document_fn(db, session, call_kwargs)
    return ok_fn(document_out_fn(document, session))


def list_documents(
    session_id: str,
    db: Session,
    current_user,
    *,
    require_session_fn=_require_session,
    document_model=ExplorationDocument,
    document_out_fn: Callable[..., dict] = _document_out,
    ok_fn: Callable[[Any], dict] = _ok,
):
    session = require_session_fn(db, session_id, current_user)
    rows = (
        db.query(document_model)
        .filter(document_model.session_id == session.id)
        .order_by(document_model.version.desc())
        .all()
    )
    return ok_fn([
        document_out_fn(document, session, list_item=True)
        for document in rows
    ])


def get_document(
    document_id: str,
    db: Session,
    current_user,
    *,
    require_document_fn=_require_document,
    require_session_fn=_require_session,
    document_out_fn: Callable[
        [ExplorationDocument, ExplorationSession],
        dict,
    ] = _document_out,
    ok_fn: Callable[[Any], dict] = _ok,
):
    document = require_document_fn(db, document_id, current_user)
    session = require_session_fn(
        db,
        document.session_id,
        current_user,
    )
    return ok_fn(document_out_fn(document, session))
