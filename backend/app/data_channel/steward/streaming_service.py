"""Synchronous and SSE chat response orchestration."""
from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from typing import Any

from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.data_channel.steward.contracts import ChatBody


def _ok(data):
    return {"data": data}


def chat(
    body: ChatBody,
    db: Session,
    current_user,
    *,
    run_turn_fn: Callable[..., Iterable[dict[str, Any]]],
):
    """Run one turn without changing the established sync/SSE event contract."""
    if not (body.message or "").strip():
        raise HTTPException(422, "message 不能为空")

    if not body.stream:
        events = list(run_turn_fn(
            db,
            current_user,
            body.message,
            conversation_id=body.conversationId,
            model_id=body.modelId,
            target_record_id=body.targetRecordId,
            web_search=body.webSearch,
        ))
        answer = next((event for event in events if event["type"] == "answer"), None)
        error = next((event for event in events if event["type"] == "error"), None)
        meta = next((event for event in events if event["type"] == "meta"), {})
        steps = [event for event in events if event["type"] == "step"]
        return _ok({
            "conversationId": meta.get("conversationId"),
            "model": meta.get("model"),
            "steps": [
                {key: value for key, value in step.items() if key != "type"}
                for step in steps
            ],
            "content": (answer or {}).get("content"),
            "touchedPipelineIds": (answer or {}).get("touchedPipelineIds") or [],
            "usage": (answer or {}).get("usage"),
            "error": (error or {}).get("message"),
        })

    # The stream outlives request dependencies, so it owns an independent session.
    user = current_user

    def event_stream():
        from app.database import SessionLocal

        session = SessionLocal()
        try:
            for event in run_turn_fn(
                session,
                user,
                body.message,
                conversation_id=body.conversationId,
                model_id=body.modelId,
                target_record_id=body.targetRecordId,
                web_search=body.webSearch,
            ):
                yield (
                    "data: "
                    f"{json.dumps(event, ensure_ascii=False, default=str)}\n\n"
                )
        finally:
            session.close()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
