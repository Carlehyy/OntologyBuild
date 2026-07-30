"""Batch-shape validation and third-party Event Registry ingestion."""
from __future__ import annotations

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.events import service
from app.events.deps import IngestContext
from app.events.schemas import IngestEvent


def ingest_events(db: Session, body, context: IngestContext) -> dict:
    if isinstance(body, list):
        items, single = body, False
    elif isinstance(body, dict) and isinstance(body.get("events"), list):
        items, single = body["events"], False
    elif isinstance(body, dict):
        items, single = [body], True
    else:
        raise HTTPException(
            422,
            "请求体应为事件对象、[...] 或 {events:[...]}",
        )

    if not items:
        raise HTTPException(422, "没有可上传的事件")
    if len(items) > 500:
        raise HTTPException(413, "单次批量上限 500 条")

    results = []
    created = duplicated = failed = 0
    for index, raw in enumerate(items):
        try:
            item = IngestEvent.model_validate(raw)
            event, idempotent = service.ingest_event(
                db,
                item,
                context.key,
                context.client_ip,
            )
            if idempotent:
                duplicated += 1
            else:
                created += 1
            results.append({
                "index": index,
                "ok": True,
                "idempotent": idempotent,
                "event": service.event_out(event),
            })
        except HTTPException as exc:
            failed += 1
            results.append({
                "index": index,
                "ok": False,
                "error": exc.detail,
                "status": exc.status_code,
            })
        except ValidationError as exc:
            failed += 1
            messages = "; ".join(
                f"{'.'.join(str(part) for part in error['loc'])}: "
                f"{error['msg']}"
                for error in exc.errors()
            )
            results.append({
                "index": index,
                "ok": False,
                "error": messages or "字段校验失败",
                "status": 422,
            })

    if single:
        result = results[0]
        if not result["ok"]:
            raise HTTPException(
                result.get("status", 422),
                result["error"],
            )
        return {
            **result["event"],
            "idempotent": result["idempotent"],
        }
    return {
        "created": created,
        "duplicated": duplicated,
        "failed": failed,
        "total": len(items),
        "results": results,
    }
