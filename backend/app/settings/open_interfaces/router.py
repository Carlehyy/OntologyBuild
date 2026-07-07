from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.deps import get_db, require_admin
from app.models.mcp import McpInterfaceConfig
from app.models.user import User
from app.schemas.mcp import McpInfoOut, McpInterfaceListOut, McpInterfaceOut, McpOpenBody
from app.services.mcp_catalog import get_interface, list_interfaces, list_published
from app import mcp_server

router = APIRouter()


@router.get("/interfaces", response_model=McpInterfaceListOut)
def get_interfaces(request: Request, db: Session = Depends(get_db), _admin: User = Depends(require_admin)):
    items = list_interfaces(request.app, db)
    return {
        "items": items,
        "total": len(items),
        "enabled_count": len([i for i in items if i["enabled"]]),
    }


@router.post("/interfaces/{operation_id}/open", response_model=McpInterfaceOut)
def set_interface_open(
    operation_id: str,
    body: McpOpenBody,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    item = get_interface(request.app, db, operation_id)
    if not item:
        raise HTTPException(status_code=404, detail="Interface not found")
    if item.get("excluded"):
        raise HTTPException(status_code=400, detail=item.get("exclude_reason") or "Interface is excluded")
    if not item.get("supported"):
        raise HTTPException(status_code=400, detail=item.get("unsupported_reason") or "Interface is not supported")

    config = db.query(McpInterfaceConfig).filter(McpInterfaceConfig.operation_id == operation_id).first()
    if not config:
        config = McpInterfaceConfig(
            operation_id=operation_id,
            method=item["method"],
            path=item["path"],
            created_by=current_user.id,
        )
        db.add(config)
    config.method = item["method"]
    config.path = item["path"]
    config.enabled = body.open
    config.updated_by = current_user.id
    if body.display_name is not None:
        config.display_name = body.display_name
    if body.description is not None:
        config.description = body.description
    db.commit()
    db.refresh(config)

    updated = get_interface(request.app, db, operation_id)
    return updated


@router.get("/info", response_model=McpInfoOut)
def info(request: Request, db: Session = Depends(get_db), _admin: User = Depends(require_admin)):
    return {
        "endpoint": "/mcp",
        "transport": "streamable-http",
        "server_name": mcp_server.SERVER_NAME,
        "token_required": True,
        "auth": "Authorization: Bearer <OntoPrompt user JWT>",
        "published_count": len(list_published(request.app, db)),
    }
