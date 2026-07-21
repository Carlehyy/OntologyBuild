from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from app.auth.models import User
from app.deps import get_current_user, get_db
from app.super_assistant.models import SuperAssistantMcpServer
from app.super_assistant.router import (
    create_mcp_server as create_user_mcp_server,
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


def _community_server(
    db: Session,
    owner_id: str,
    server_id: str,
) -> SuperAssistantMcpServer:
    item = db.query(SuperAssistantMcpServer).filter(
        SuperAssistantMcpServer.id == server_id,
        SuperAssistantMcpServer.owner_id == owner_id,
        SuperAssistantMcpServer.builtin_key.is_(None),
    ).first()
    if item is None:
        raise HTTPException(status_code=404, detail="MCP Server 不存在")
    return item


@router.get("/mcp-servers", response_model=list[McpServerOut])
def list_mcp_servers(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List user-added MCP servers; platform-internal tools stay out of Community."""
    return db.query(SuperAssistantMcpServer).filter(
        SuperAssistantMcpServer.owner_id == current_user.id,
        SuperAssistantMcpServer.builtin_key.is_(None),
    ).order_by(SuperAssistantMcpServer.updated_at.desc()).all()


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
    _community_server(db, current_user.id, server_id)
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
    _community_server(db, current_user.id, server_id)
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
    _community_server(db, current_user.id, server_id)
    return await test_user_mcp_server(
        server_id=server_id,
        db=db,
        current_user=current_user,
    )
