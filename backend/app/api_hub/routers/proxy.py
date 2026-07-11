"""Fail-closed service endpoint used by n8n to invoke managed interfaces."""
from __future__ import annotations

import json
import secrets

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import Response

from .. import config, db, executor
from .interfaces import _row_to_dict

router = APIRouter(prefix="/api-hub/proxy", tags=["api-hub-proxy"])


def _authorize(authorization: str | None) -> None:
    expected = config.SYSTEM_MCP_TOKEN
    supplied = (authorization or "").removeprefix("Bearer ").strip()
    if not expected:
        raise HTTPException(503, "API_HUB_SYSTEM_MCP_TOKEN 尚未配置，接口代理拒绝服务")
    if not supplied or not secrets.compare_digest(supplied, expected):
        raise HTTPException(401, "接口代理令牌无效")


@router.api_route("/{interface_id}", methods=["GET", "POST"])
async def invoke_interface(interface_id: int, request: Request,
                           authorization: str | None = Header(None)):
    _authorize(authorization)
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM interfaces WHERE id = ? AND open_enabled = 1", (interface_id,)
        ).fetchone()
    if not row:
        raise HTTPException(404, "接口不存在或尚未加入开放清单")
    iface = _row_to_dict(row)

    query_override = dict(request.query_params)
    body_override = None
    if request.method == "POST":
        raw = await request.body()
        if raw:
            try:
                payload = json.loads(raw)
            except ValueError as exc:
                raise HTTPException(422, "代理 POST Body 必须是 JSON") from exc
            if not isinstance(payload, dict):
                raise HTTPException(422, "代理 POST Body 必须是对象")
            extra_query = payload.get("query") or {}
            if not isinstance(extra_query, dict):
                raise HTTPException(422, "query 必须是对象")
            query_override.update(extra_query)
            if "body" in payload:
                body_value = payload["body"]
                body_override = body_value if isinstance(body_value, str) else json.dumps(body_value, ensure_ascii=False)

    result = executor.run_interface(
        iface, query_override=query_override or None, body_override=body_override)
    status = result.get("status_code") or (502 if result.get("error") else 200)
    content_type = (result.get("content_type") or "application/json").split(";", 1)[0]
    headers = {"X-Api-Hub-Run-Id": str(result.get("run_id") or "")}
    if result.get("error") and not result.get("response_body"):
        return Response(
            json.dumps({"error": result["error"]}, ensure_ascii=False), status_code=status,
            media_type="application/json", headers=headers,
        )
    return Response(result.get("response_body") or "", status_code=status,
                    media_type=content_type, headers=headers)
