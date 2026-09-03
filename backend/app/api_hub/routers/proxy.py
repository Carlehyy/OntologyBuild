"""Fail-closed service endpoint used by n8n to invoke managed interfaces."""
from __future__ import annotations

import json
import secrets

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import Response

from .. import config, db, executor
from ..agent_service import AgentInterfaceError, request_overrides_for_interface
from ..interface_service import _row_to_dict
from ..personal_ref import interface_has_personal_refs

internal_router = APIRouter(
    prefix="/api-hub/internal/interfaces", tags=["api-hub-internal-proxy"]
)


def _authorize_internal(authorization: str | None) -> None:
    expected = config.INTERNAL_PROXY_TOKEN
    supplied = (authorization or "").removeprefix("Bearer ").strip()
    if not expected:
        raise HTTPException(503, "API_HUB_INTERNAL_PROXY_TOKEN 尚未配置，内部接口代理拒绝服务")
    if not supplied or not secrets.compare_digest(supplied, expected):
        raise HTTPException(401, "内部接口代理令牌无效")


def _proxy_response(result: dict) -> Response:
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


def _inflate_internal_payload(payload: dict) -> dict:
    """Restore n8n key/value Body fields to the proxy envelope.

    n8n evaluates expressions on individual values.  Keys such as
    ``query.page`` and ``body.filter.status`` therefore avoid brittle JSON-text
    interpolation while retaining nested request bodies.
    """
    restored = {
        key: value for key, value in payload.items()
        if "." not in key
    }

    def assign(root: dict, path: list[str], value) -> None:
        target = root
        for part in path[:-1]:
            child = target.get(part)
            if not isinstance(child, dict):
                child = {}
                target[part] = child
            target = child
        target[path[-1]] = value

    for raw_key, value in payload.items():
        if "." not in raw_key:
            continue
        section, remainder = raw_key.split(".", 1)
        if section not in {"path", "query", "headers", "body"} or not remainder:
            raise HTTPException(422, f"内部代理参数名无效：{raw_key}")
        bucket = restored.setdefault(section, {})
        if not isinstance(bucket, dict):
            raise HTTPException(422, f"内部代理 {section} 参数结构冲突")
        assign(bucket, remainder.split("."), value)
    return restored


@internal_router.post("/{interface_id}/invoke")
async def invoke_internal_interface(
    interface_id: int,
    request: Request,
    authorization: str | None = Header(None),
):
    """Invoke a managed interface from n8n without exposing management MCP.

    The workflow pins ``interface_revision``.  Editing an interface therefore
    fails closed instead of silently changing a published pipeline's behavior.
    Dynamic path/query/header values are checked against the interface contract.
    """
    _authorize_internal(authorization)
    try:
        payload = await request.json()
    except ValueError as exc:
        raise HTTPException(422, "内部代理 Body 必须是 JSON 对象") from exc
    if not isinstance(payload, dict):
        raise HTTPException(422, "内部代理 Body 必须是 JSON 对象")
    payload = _inflate_internal_payload(payload)

    with db.get_conn() as conn:
        row = conn.execute("SELECT * FROM interfaces WHERE id = ?", (interface_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "接口不存在")
    interface = _row_to_dict(row)
    if interface_has_personal_refs(interface):
        raise HTTPException(
            400,
            "接口配置含个人变量占位符（{{privacy:}}/{{env:}}）：流水线链路没有"
            "用户身份、不会解析占位符；请去除占位符后重新编排，或改用平台 UI 调用。",
        )

    supplied_revision = payload.get("interface_revision")
    current_revision = int(interface.get("config_revision") or 1)
    try:
        supplied_revision = int(supplied_revision)
    except (TypeError, ValueError) as exc:
        raise HTTPException(422, "interface_revision 必须是整数") from exc
    if supplied_revision != current_revision:
        raise HTTPException(
            409,
            f"接口配置版本已变化：流水线绑定 revision={supplied_revision}，"
            f"当前 revision={current_revision}；请在数据管家中重新编排并发布流水线。",
        )

    try:
        overrides = request_overrides_for_interface(
            interface,
            path=payload.get("path"),
            query=payload.get("query"),
            headers=payload.get("headers"),
            body=payload.get("body") if "body" in payload else None,
            source="n8n_internal",
        )
    except AgentInterfaceError as exc:
        raise HTTPException(422, str(exc)) from exc
    return _proxy_response(executor.run_interface(interface, overrides))
