from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from app.auth.models import User
from app.deps import get_current_user, get_db
from app.super_assistant import mcp_server_service
from app.super_assistant.schemas import (
    McpServerCreate,
    McpServerOut,
    McpServerUpdate,
    McpTestOut,
)


router = APIRouter()


def _mcp_http_error(
    exc: mcp_server_service.McpServerServiceError,
) -> HTTPException:
    if isinstance(exc, mcp_server_service.McpServerNotFoundError):
        status_code = 404
    elif isinstance(exc, mcp_server_service.McpServerConflictError):
        status_code = 409
    else:
        status_code = 400
    return HTTPException(status_code=status_code, detail=str(exc))


@router.get("/mcp-servers", response_model=list[McpServerOut])
def list_mcp_servers(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List user-added MCP servers; platform-internal tools stay out of Community."""
    return mcp_server_service.list_mcp_servers(
        db,
        current_user.id,
        include_builtins=False,
    )


@router.post("/mcp-servers", response_model=McpServerOut, status_code=201)
def create_mcp_server(
    body: McpServerCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        return mcp_server_service.create_mcp_server(
            db,
            current_user.id,
            body,
        )
    except mcp_server_service.McpServerServiceError as exc:
        raise _mcp_http_error(exc) from exc


@router.patch("/mcp-servers/{server_id}", response_model=McpServerOut)
def update_mcp_server(
    server_id: str,
    body: McpServerUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        return mcp_server_service.update_mcp_server(
            db,
            current_user.id,
            server_id,
            body,
            include_builtins=False,
        )
    except mcp_server_service.McpServerServiceError as exc:
        raise _mcp_http_error(exc) from exc


@router.delete("/mcp-servers/{server_id}", status_code=204)
def remove_mcp_server(
    server_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Response:
    try:
        mcp_server_service.remove_mcp_server(
            db,
            current_user.id,
            server_id,
            include_builtins=False,
        )
    except mcp_server_service.McpServerServiceError as exc:
        raise _mcp_http_error(exc) from exc
    return Response(status_code=204)


@router.post("/mcp-servers/{server_id}/test", response_model=McpTestOut)
async def test_mcp_server(
    server_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        return await mcp_server_service.test_mcp_server(
            db,
            current_user.id,
            server_id,
            include_builtins=False,
        )
    except mcp_server_service.McpServerServiceError as exc:
        raise _mcp_http_error(exc) from exc
