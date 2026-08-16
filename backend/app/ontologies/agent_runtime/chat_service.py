"""Synchronous and streaming execution for Agent Runtime conversations."""
from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from typing import Any

from sqlalchemy.orm import Session

from app.ontologies.agent_runtime import schemas as S


def run_sync(
    db: Session,
    ontology_id: str,
    current_user: Any,
    body: S.ChatRequest,
    *,
    run_turn_fn: Callable[..., Iterator[dict]],
) -> dict:
    events = list(
        run_turn_fn(
            db,
            ontology_id,
            current_user,
            body.message,
            conversation_id=body.conversation_id,
            model_id=body.model_id,
            release_id=body.release_id,
            run_id=body.run_id,
        )
    )
    answer = next(
        (event for event in events if event["type"] == "answer"),
        None,
    )
    error = next(
        (event for event in events if event["type"] == "error"),
        None,
    )
    meta = next(
        (event for event in events if event["type"] == "meta"),
        {},
    )
    steps = [event for event in events if event["type"] == "step"]
    return {
        "conversationId": meta.get("conversationId"),
        "model": meta.get("model"),
        "steps": [
            {key: value for key, value in step.items() if key != "type"}
            for step in steps
        ],
        "content": (answer or {}).get("content"),
        "citations": (answer or {}).get("citations") or [],
        "proposals": (answer or {}).get("proposals") or [],
        "usage": (answer or {}).get("usage"),
        "verification": (answer or {}).get("verification"),
        "error": (error or {}).get("message"),
    }


def stream_events(
    ontology_id: str,
    current_user: Any,
    body: S.ChatRequest,
    *,
    run_turn_fn: Callable[..., Iterator[dict]],
) -> Iterator[str]:
    """Own the session that must outlive FastAPI's request dependency."""
    from app.database import SessionLocal

    session = SessionLocal()
    try:
        for event in run_turn_fn(
            session,
            ontology_id,
            current_user,
            body.message,
            conversation_id=body.conversation_id,
            model_id=body.model_id,
            release_id=body.release_id,
            run_id=body.run_id,
        ):
            yield (
                "data: "
                f"{json.dumps(event, ensure_ascii=False, default=str)}"
                "\n\n"
            )
    finally:
        session.close()
