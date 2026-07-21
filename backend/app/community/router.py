from __future__ import annotations

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from app.auth.models import User
from app.deps import get_current_user, get_db
from app.super_assistant.router import (
    create_mcp_server as create_user_mcp_server,
    install_platform_minio_mcp as install_user_platform_minio_mcp,
    list_mcp_servers as list_user_mcp_servers,
    remove_mcp_server as remove_user_mcp_server,
    test_mcp_server as test_user_mcp_server,
    update_mcp_server as update_user_mcp_server,
)
from app.super_assistant.schemas import (
    McpServerCreate,
    McpServerOut,
    McpServerUpdate,
    McpTestOut,
)


router = APIRouter()


@router.get("/mcp-servers", response_model=list[McpServerOut])
def list_mcp_servers(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List the current user's MCP inventory shared with Super Assistant."""
    return list_user_mcp_servers(db=db, current_user=current_user)


@router.post("/mcp-servers", response_model=McpServerOut, status_code=201)
def create_mcp_server(
    body: McpServerCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return create_user_mcp_server(body=body, db=db, current_user=current_user)


@router.patch("/mcp-servers/{server_id}", response_model=McpServerOut)
def update_mcp_server(
    server_id: str,
    body: McpServerUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return update_user_mcp_server(
        server_id=server_id,
        body=body,
        db=db,
        current_user=current_user,
    )


@router.delete("/mcp-servers/{server_id}", status_code=204)
def remove_mcp_server(
    server_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Response:
    return remove_user_mcp_server(
        server_id=server_id,
        db=db,
        current_user=current_user,
    )


@router.post("/mcp-servers/{server_id}/test", response_model=McpTestOut)
async def test_mcp_server(
    server_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await test_user_mcp_server(
        server_id=server_id,
        db=db,
        current_user=current_user,
    )


@router.post("/mcp-servers/platform-minio", response_model=McpServerOut)
def install_platform_minio_mcp(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return install_user_platform_minio_mcp(db=db, current_user=current_user)
