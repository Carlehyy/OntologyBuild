from __future__ import annotations

import json
from typing import Any

import httpx
from fastapi import HTTPException

from app.services.mcp_catalog import get_interface

MAX_RESPONSE_CHARS = 20000


def _fill_path(path: str, params: dict[str, Any]) -> str:
    result = path
    for key, value in params.items():
        result = result.replace("{" + key + "}", str(value))
    if "{" in result or "}" in result:
        raise HTTPException(status_code=400, detail="Missing required path parameters")
    return result


def _json_text(obj: Any) -> str:
    text = obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False, indent=2)
    if len(text) > MAX_RESPONSE_CHARS:
        return text[:MAX_RESPONSE_CHARS] + f"\n\n…（响应过大，已截断至 {MAX_RESPONSE_CHARS} 字符）"
    return text


async def call_interface(app, db, operation_id: str, bearer_token: str, args: dict[str, Any]) -> dict[str, Any]:
    item = get_interface(app, db, operation_id)
    if not item or not item.get("enabled"):
        raise HTTPException(status_code=404, detail="Interface is not published")
    if item.get("excluded") or not item.get("supported"):
        raise HTTPException(status_code=400, detail=item.get("unsupported_reason") or item.get("exclude_reason") or "Interface is not supported")

    path_params = args.get("path") or {}
    query = args.get("query") or {}
    body = args.get("body", None)
    if not isinstance(path_params, dict) or not isinstance(query, dict):
        raise HTTPException(status_code=400, detail="path and query must be objects")

    url = _fill_path(item["path"], path_params)
    headers = {"Authorization": f"Bearer {bearer_token}"}
    if body is not None:
        headers["Content-Type"] = "application/json"

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://ontoprompt.local", timeout=60.0) as client:
        response = await client.request(
            item["method"],
            url,
            params=query,
            json=body if body is not None else None,
            headers=headers,
        )

    content_type = response.headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            response_body: Any = response.json()
        except ValueError:
            response_body = response.text
    else:
        response_body = response.text

    return {
        "status_code": response.status_code,
        "ok": 200 <= response.status_code < 400,
        "content_type": content_type,
        "body": response_body,
        "body_text": _json_text(response_body),
    }
