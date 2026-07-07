from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.models.mcp import McpInterfaceConfig

EXCLUDED_PREFIXES = (
    "/api/v1/auth",
    "/api/v1/users",
    "/api/v1/settings",
    "/api/v1/mcp",
    "/api/v2/mcp",
    "/api/mcp",
    "/mcp",
)
EXCLUDED_PATHS = {"/health", "/api/health", "/openapi.json", "/docs", "/redoc"}
SUPPORTED_METHODS = {"GET", "POST", "PUT", "DELETE"}


@dataclass(frozen=True)
class InterfaceStatus:
    excluded: bool = False
    exclude_reason: str | None = None
    supported: bool = True
    unsupported_reason: str | None = None


def _operation_id(method: str, path: str, operation: dict[str, Any]) -> str:
    return operation.get("operationId") or f"{method.lower()}:{path}"


def _is_multipart(operation: dict[str, Any]) -> bool:
    content = (operation.get("requestBody") or {}).get("content") or {}
    return any(media_type.startswith("multipart/") for media_type in content)


def _normalize_parameters(operation: dict[str, Any]) -> list[dict[str, Any]]:
    params = []
    for param in operation.get("parameters") or []:
        params.append({
            "name": param.get("name", ""),
            "location": param.get("in", ""),
            "required": bool(param.get("required")),
            "schema": param.get("schema") or {},
        })
    return params


def _status_for(method: str, path: str, operation: dict[str, Any]) -> InterfaceStatus:
    if path in EXCLUDED_PATHS:
        return InterfaceStatus(excluded=True, exclude_reason="系统健康检查或文档接口不允许开放")
    if any(path == prefix or path.startswith(prefix + "/") for prefix in EXCLUDED_PREFIXES):
        return InterfaceStatus(excluded=True, exclude_reason="认证、用户、设置或 MCP 管理接口不允许开放")
    if method.upper() not in SUPPORTED_METHODS:
        return InterfaceStatus(supported=False, unsupported_reason=f"暂不支持 {method.upper()} 方法")
    if _is_multipart(operation):
        return InterfaceStatus(supported=False, unsupported_reason="暂不支持文件上传 / multipart 接口")
    return InterfaceStatus()


def list_interfaces(app, db: Session, include_excluded: bool = False) -> list[dict[str, Any]]:
    schema = app.openapi()
    configs = {
        c.operation_id: c
        for c in db.query(McpInterfaceConfig).all()
    }
    items: list[dict[str, Any]] = []
    for path, path_item in sorted((schema.get("paths") or {}).items()):
        for method, operation in sorted(path_item.items()):
            if method.upper() not in {"GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"}:
                continue
            opid = _operation_id(method, path, operation)
            status = _status_for(method, path, operation)
            if status.excluded and not include_excluded:
                continue
            config = configs.get(opid)
            enabled = bool(config and config.enabled and not status.excluded and status.supported)
            items.append({
                "operation_id": opid,
                "method": method.upper(),
                "path": path,
                "summary": operation.get("summary") or opid,
                "description": operation.get("description") or "",
                "tags": operation.get("tags") or [],
                "parameters": _normalize_parameters(operation),
                "request_body": operation.get("requestBody"),
                "enabled": enabled,
                "supported": status.supported,
                "unsupported_reason": status.unsupported_reason,
                "excluded": status.excluded,
                "exclude_reason": status.exclude_reason,
                "display_name": config.display_name if config else None,
                "config_description": config.description if config else None,
            })
    return items


def get_interface(app, db: Session, operation_id: str) -> dict[str, Any] | None:
    for item in list_interfaces(app, db, include_excluded=True):
        if item["operation_id"] == operation_id:
            return item
    return None


def list_published(app, db: Session) -> list[dict[str, Any]]:
    return [item for item in list_interfaces(app, db) if item["enabled"]]
