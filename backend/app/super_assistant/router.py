from __future__ import annotations

import io
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.models import User
from app.deps import get_current_user, get_db
from app.model_configs.models import ModelConfig
from app.super_assistant.mcp_client import (
    McpClientError,
    decrypt_env,
    decrypt_headers,
    discover_tools,
    encrypt_env,
    encrypt_headers,
    normalize_connection,
)
from app.super_assistant.models import (
    SuperAssistantConversation,
    SuperAssistantMcpServer,
    SuperAssistantMessage,
    SuperAssistantSkill,
    SuperAssistantToolRun,
)
from app.super_assistant.runtime import stream_chat
from app.super_assistant.schemas import (
    ApprovalRequest,
    ChatRequest,
    ConversationCreate,
    ConversationOut,
    ConversationUpdate,
    McpServerCreate,
    McpServerOut,
    McpServerUpdate,
    McpTestOut,
    MessageOut,
    SkillCreate,
    SkillFileContent,
    SkillOut,
    SkillUpdate,
)
from app.super_assistant.skill_store import (
    SkillStoreError,
    build_manifest,
    create_skill_folder,
    delete_file,
    delete_skill_folder,
    export_skill_archive,
    import_skill_archive,
    parse_skill_markdown,
    read_text_file,
    render_skill_markdown,
    skill_directory,
    write_text_file,
)
from app.settings.object_storage.models import MinioConfig
from app.settings.object_storage.service import (
    ConfiguredMinioService,
    MinioServiceError,
    minio_tool_manifest,
)


router = APIRouter()


def _conversation(db: Session, owner_id: str, conversation_id: str) -> SuperAssistantConversation:
    item = db.query(SuperAssistantConversation).filter(
        SuperAssistantConversation.id == conversation_id,
        SuperAssistantConversation.owner_id == owner_id,
    ).first()
    if not item:
        raise HTTPException(status_code=404, detail="会话不存在")
    return item


def _skill(db: Session, owner_id: str, skill_id: str) -> SuperAssistantSkill:
    item = db.query(SuperAssistantSkill).filter(
        SuperAssistantSkill.id == skill_id,
        SuperAssistantSkill.owner_id == owner_id,
    ).first()
    if not item:
        raise HTTPException(status_code=404, detail="Skill 不存在")
    return item


def _server(db: Session, owner_id: str, server_id: str) -> SuperAssistantMcpServer:
    item = db.query(SuperAssistantMcpServer).filter(
        SuperAssistantMcpServer.id == server_id,
        SuperAssistantMcpServer.owner_id == owner_id,
    ).first()
    if not item:
        raise HTTPException(status_code=404, detail="MCP Server 不存在")
    return item


def _storage_error(exc: Exception) -> HTTPException:
    return HTTPException(status_code=400, detail=str(exc))


@router.get("/conversations", response_model=list[ConversationOut])
def list_conversations(
    db: Session = Depends(get_db), current_user: User = Depends(get_current_user),
):
    return db.query(SuperAssistantConversation).filter(
        SuperAssistantConversation.owner_id == current_user.id,
        SuperAssistantConversation.status != "deleted",
    ).order_by(SuperAssistantConversation.updated_at.desc()).all()


@router.post("/conversations", response_model=ConversationOut, status_code=201)
def create_conversation(
    body: ConversationCreate,
    db: Session = Depends(get_db), current_user: User = Depends(get_current_user),
):
    if body.model_config_id:
        model = db.query(ModelConfig).filter(
            ModelConfig.id == body.model_config_id,
            ModelConfig.config_type == "llm",
            ModelConfig.enabled.is_(True),
        ).first()
        if not model:
            raise HTTPException(status_code=400, detail="所选模型不存在或未启用")
    item = SuperAssistantConversation(
        owner_id=current_user.id,
        title=body.title.strip() or "新会话",
        model_config_id=body.model_config_id,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


@router.patch("/conversations/{conversation_id}", response_model=ConversationOut)
def update_conversation(
    conversation_id: str, body: ConversationUpdate,
    db: Session = Depends(get_db), current_user: User = Depends(get_current_user),
):
    item = _conversation(db, current_user.id, conversation_id)
    if body.title is not None:
        item.title = body.title.strip()
    if "model_config_id" in body.model_fields_set:
        if body.model_config_id:
            model = db.query(ModelConfig).filter(
                ModelConfig.id == body.model_config_id,
                ModelConfig.config_type == "llm",
                ModelConfig.enabled.is_(True),
            ).first()
            if not model:
                raise HTTPException(status_code=400, detail="所选模型不存在或未启用")
        item.model_config_id = body.model_config_id
    db.commit()
    db.refresh(item)
    return item


@router.delete("/conversations/{conversation_id}", status_code=204)
def delete_conversation(
    conversation_id: str,
    db: Session = Depends(get_db), current_user: User = Depends(get_current_user),
):
    item = _conversation(db, current_user.id, conversation_id)
    db.query(SuperAssistantToolRun).filter(
        SuperAssistantToolRun.conversation_id == item.id,
    ).delete(synchronize_session=False)
    db.query(SuperAssistantMessage).filter(
        SuperAssistantMessage.conversation_id == item.id,
    ).delete(synchronize_session=False)
    db.delete(item)
    db.commit()
    return Response(status_code=204)


@router.get("/conversations/{conversation_id}/messages", response_model=list[MessageOut])
def list_messages(
    conversation_id: str,
    db: Session = Depends(get_db), current_user: User = Depends(get_current_user),
):
    item = _conversation(db, current_user.id, conversation_id)
    return db.query(SuperAssistantMessage).filter(
        SuperAssistantMessage.conversation_id == item.id,
    ).order_by(SuperAssistantMessage.created_at.asc()).all()


@router.post("/conversations/{conversation_id}/chat")
def chat(
    conversation_id: str, body: ChatRequest,
    db: Session = Depends(get_db), current_user: User = Depends(get_current_user),
):
    conversation = _conversation(db, current_user.id, conversation_id)
    active = db.query(SuperAssistantMessage).filter(
        SuperAssistantMessage.conversation_id == conversation.id,
        SuperAssistantMessage.role == "assistant",
        SuperAssistantMessage.status == "streaming",
    ).order_by(SuperAssistantMessage.created_at.desc()).first()
    if active:
        created_at = active.created_at
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        if (datetime.now(timezone.utc) - created_at).total_seconds() < 600:
            raise HTTPException(status_code=409, detail="当前会话仍有一条回复正在生成")
        active.status = "error"
        active.content = "上一次生成意外中断"

    if body.model_config_id:
        model = db.query(ModelConfig).filter(
            ModelConfig.id == body.model_config_id,
            ModelConfig.config_type == "llm",
            ModelConfig.enabled.is_(True),
        ).first()
        if not model:
            raise HTTPException(status_code=400, detail="所选模型不存在或未启用")
        conversation.model_config_id = body.model_config_id

    user_count = db.query(SuperAssistantMessage).filter(
        SuperAssistantMessage.conversation_id == conversation.id,
        SuperAssistantMessage.role == "user",
    ).count()
    user_message = SuperAssistantMessage(
        conversation_id=conversation.id,
        role="user",
        content=body.message.strip(),
        status="complete",
    )
    assistant_message = SuperAssistantMessage(
        conversation_id=conversation.id,
        role="assistant",
        content="",
        status="streaming",
    )
    db.add_all([user_message, assistant_message])
    if user_count == 0 and conversation.title == "新会话":
        conversation.title = body.message.strip().replace("\n", " ")[:40]
    conversation.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(assistant_message)

    return StreamingResponse(
        stream_chat(
            conversation_id=conversation.id,
            owner_id=current_user.id,
            assistant_message_id=assistant_message.id,
            requested_model_id=body.model_config_id,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.post("/conversations/{conversation_id}/cancel", status_code=202)
def cancel_chat(
    conversation_id: str,
    db: Session = Depends(get_db), current_user: User = Depends(get_current_user),
):
    conversation = _conversation(db, current_user.id, conversation_id)
    active = db.query(SuperAssistantMessage).filter(
        SuperAssistantMessage.conversation_id == conversation.id,
        SuperAssistantMessage.role == "assistant",
        SuperAssistantMessage.status == "streaming",
    ).order_by(SuperAssistantMessage.created_at.desc()).first()
    if active:
        active.status = "cancelled"
        active.content = active.content or "已停止生成"
        db.commit()
    return {"cancelled": bool(active)}


@router.post("/tool-runs/{tool_run_id}/decision")
def decide_tool_run(
    tool_run_id: str, body: ApprovalRequest,
    db: Session = Depends(get_db), current_user: User = Depends(get_current_user),
):
    tool_run = db.query(SuperAssistantToolRun).join(
        SuperAssistantConversation,
        SuperAssistantConversation.id == SuperAssistantToolRun.conversation_id,
    ).filter(
        SuperAssistantToolRun.id == tool_run_id,
        SuperAssistantConversation.owner_id == current_user.id,
    ).first()
    if not tool_run:
        raise HTTPException(status_code=404, detail="工具调用不存在")
    if tool_run.status != "awaiting_confirmation":
        raise HTTPException(status_code=409, detail="该工具调用已处理或已过期")
    tool_run.decision = body.decision
    tool_run.status = "approved" if body.decision == "approve" else "denied"
    if body.decision == "deny":
        tool_run.completed_at = datetime.now(timezone.utc)
    db.commit()
    return {"id": tool_run.id, "status": tool_run.status}


@router.get("/skills", response_model=list[SkillOut])
def list_skills(
    db: Session = Depends(get_db), current_user: User = Depends(get_current_user),
):
    return db.query(SuperAssistantSkill).filter(
        SuperAssistantSkill.owner_id == current_user.id,
    ).order_by(SuperAssistantSkill.updated_at.desc()).all()


@router.post("/skills", response_model=SkillOut, status_code=201)
def create_skill(
    body: SkillCreate,
    db: Session = Depends(get_db), current_user: User = Depends(get_current_user),
):
    item = SuperAssistantSkill(
        id=str(uuid.uuid4()),
        owner_id=current_user.id,
        name=body.name,
        # Retained columns keep existing databases migration-compatible. Skill
        # identity and activation come exclusively from standard SKILL.md data.
        display_name=body.name,
        description=body.description,
        triggers=[],
        folder_path="",
        enabled=body.enabled,
    )
    folder = skill_directory(current_user.id, item.id)
    item.folder_path = str(folder)
    markdown = render_skill_markdown(
        name=item.name,
        description=item.description,
        content=body.content,
    )
    try:
        create_skill_folder(folder, markdown)
        item.manifest = build_manifest(folder)
        db.add(item)
        db.commit()
        db.refresh(item)
        return item
    except (SkillStoreError, IntegrityError) as exc:
        db.rollback()
        try:
            delete_skill_folder(folder)
        except SkillStoreError:
            pass
        detail = "同名 Skill 已存在" if isinstance(exc, IntegrityError) else str(exc)
        raise HTTPException(status_code=409 if isinstance(exc, IntegrityError) else 400, detail=detail) from exc


@router.post("/skills/import", response_model=SkillOut, status_code=201)
async def import_skill(
    archive: UploadFile = File(...),
    db: Session = Depends(get_db), current_user: User = Depends(get_current_user),
):
    if not (archive.filename or "").lower().endswith(".zip"):
        raise HTTPException(status_code=400, detail="请上传 .zip 格式的 Skill 文件夹")
    from app.shared.config import settings

    limit = settings.super_assistant_max_skill_archive_mb * 1024 * 1024
    data = await archive.read(limit + 1)
    item = SuperAssistantSkill(
        id=str(uuid.uuid4()),
        owner_id=current_user.id,
        name="pending",
        display_name="pending",
        folder_path="",
    )
    folder = skill_directory(current_user.id, item.id)
    item.folder_path = str(folder)
    try:
        metadata = import_skill_archive(data, folder)
        item.name = metadata["name"]
        item.display_name = metadata["name"]
        item.description = metadata["description"]
        item.triggers = []
        item.manifest = build_manifest(folder)
        db.add(item)
        db.commit()
        db.refresh(item)
        return item
    except (SkillStoreError, IntegrityError) as exc:
        db.rollback()
        try:
            delete_skill_folder(folder)
        except SkillStoreError:
            pass
        detail = "同名 Skill 已存在" if isinstance(exc, IntegrityError) else str(exc)
        raise HTTPException(status_code=409 if isinstance(exc, IntegrityError) else 400, detail=detail) from exc


@router.patch("/skills/{skill_id}", response_model=SkillOut)
def update_skill(
    skill_id: str, body: SkillUpdate,
    db: Session = Depends(get_db), current_user: User = Depends(get_current_user),
):
    item = _skill(db, current_user.id, skill_id)
    if body.enabled is not None:
        item.enabled = body.enabled
    db.commit()
    db.refresh(item)
    return item


@router.delete("/skills/{skill_id}", status_code=204)
def remove_skill(
    skill_id: str,
    db: Session = Depends(get_db), current_user: User = Depends(get_current_user),
):
    item = _skill(db, current_user.id, skill_id)
    try:
        delete_skill_folder(skill_directory(current_user.id, item.id))
    except SkillStoreError as exc:
        raise _storage_error(exc) from exc
    db.delete(item)
    db.commit()
    return Response(status_code=204)


@router.get("/skills/{skill_id}/files")
def list_skill_files(
    skill_id: str,
    db: Session = Depends(get_db), current_user: User = Depends(get_current_user),
):
    item = _skill(db, current_user.id, skill_id)
    try:
        item.manifest = build_manifest(skill_directory(current_user.id, item.id))
        db.commit()
        return item.manifest
    except SkillStoreError as exc:
        raise _storage_error(exc) from exc


@router.get("/skills/{skill_id}/files/{file_path:path}")
def get_skill_file(
    skill_id: str, file_path: str,
    db: Session = Depends(get_db), current_user: User = Depends(get_current_user),
):
    item = _skill(db, current_user.id, skill_id)
    try:
        folder = skill_directory(current_user.id, item.id)
        return {"path": file_path, "content": read_text_file(folder, file_path)}
    except SkillStoreError as exc:
        raise _storage_error(exc) from exc


@router.put("/skills/{skill_id}/files/{file_path:path}")
def put_skill_file(
    skill_id: str, file_path: str, body: SkillFileContent,
    db: Session = Depends(get_db), current_user: User = Depends(get_current_user),
):
    item = _skill(db, current_user.id, skill_id)
    folder = skill_directory(current_user.id, item.id)
    try:
        metadata = parse_skill_markdown(body.content) if file_path == "SKILL.md" else None
        if metadata and metadata["name"] != item.name:
            duplicate = db.query(SuperAssistantSkill).filter(
                SuperAssistantSkill.owner_id == current_user.id,
                SuperAssistantSkill.name == metadata["name"],
                SuperAssistantSkill.id != item.id,
            ).first()
            if duplicate:
                raise SkillStoreError("同名 Skill 已存在")
        write_text_file(folder, file_path, body.content)
        manifest = build_manifest(folder)
        from app.shared.config import settings
        if len(manifest) > settings.super_assistant_max_skill_files:
            if not any(entry["path"] == file_path for entry in item.manifest):
                delete_file(folder, file_path)
            raise SkillStoreError("Skill 文件数量超过限制")
        if metadata:
            item.name = metadata["name"]
            item.display_name = metadata["name"]
            item.description = metadata["description"]
            item.triggers = []
        item.manifest = manifest
        item.revision += 1
        db.commit()
        return {"path": file_path, "revision": item.revision, "manifest": manifest}
    except SkillStoreError as exc:
        db.rollback()
        raise _storage_error(exc) from exc


@router.delete("/skills/{skill_id}/files/{file_path:path}", status_code=204)
def remove_skill_file(
    skill_id: str, file_path: str,
    db: Session = Depends(get_db), current_user: User = Depends(get_current_user),
):
    item = _skill(db, current_user.id, skill_id)
    try:
        folder = skill_directory(current_user.id, item.id)
        delete_file(folder, file_path)
        item.manifest = build_manifest(folder)
        item.revision += 1
        db.commit()
        return Response(status_code=204)
    except SkillStoreError as exc:
        db.rollback()
        raise _storage_error(exc) from exc


@router.get("/skills/{skill_id}/export")
def export_skill(
    skill_id: str,
    db: Session = Depends(get_db), current_user: User = Depends(get_current_user),
):
    item = _skill(db, current_user.id, skill_id)
    try:
        payload = export_skill_archive(skill_directory(current_user.id, item.id))
    except SkillStoreError as exc:
        raise _storage_error(exc) from exc
    return StreamingResponse(
        io.BytesIO(payload),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{item.name}.zip"'},
    )


@router.get("/mcp-servers", response_model=list[McpServerOut])
def list_mcp_servers(
    db: Session = Depends(get_db), current_user: User = Depends(get_current_user),
):
    return db.query(SuperAssistantMcpServer).filter(
        SuperAssistantMcpServer.owner_id == current_user.id,
    ).order_by(SuperAssistantMcpServer.updated_at.desc()).all()


@router.post("/mcp-servers", response_model=McpServerOut, status_code=201)
def create_mcp_server(
    body: McpServerCreate,
    db: Session = Depends(get_db), current_user: User = Depends(get_current_user),
):
    try:
        transport, url, command, args = normalize_connection(
            transport=body.transport, url=body.url, command=body.command, args=body.args,
        )
        encrypted, names = encrypt_headers(body.headers)
        env_encrypted, env_names = encrypt_env(body.env)
    except McpClientError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    item = SuperAssistantMcpServer(
        owner_id=current_user.id,
        name=body.name,
        builtin_key=None,
        transport=transport,
        url=url,
        headers_encrypted=encrypted,
        header_names=names,
        command=command,
        args=args,
        env_encrypted=env_encrypted,
        env_names=env_names,
        enabled=body.enabled,
        require_confirmation=body.require_confirmation,
    )
    try:
        db.add(item)
        db.commit()
        db.refresh(item)
        return item
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="同名 MCP Server 已存在") from exc


@router.patch("/mcp-servers/{server_id}", response_model=McpServerOut)
def update_mcp_server(
    server_id: str, body: McpServerUpdate,
    db: Session = Depends(get_db), current_user: User = Depends(get_current_user),
):
    item = _server(db, current_user.id, server_id)
    try:
        if item.builtin_key and body.model_fields_set - {"enabled", "require_confirmation"}:
            raise McpClientError("平台内置 MCP 仅允许修改启用和执行确认设置")
        connection_changed = any(value is not None for value in (
            body.transport, body.url, body.command, body.args,
        ))
        if connection_changed:
            transport, url, command, args = normalize_connection(
                transport=body.transport or item.transport,
                url=body.url if body.url is not None else item.url,
                command=body.command if body.command is not None else item.command,
                args=body.args if body.args is not None else item.args,
            )
            item.transport = transport
            item.url = url
            item.command = command
            item.args = args
            item.tool_manifest = []
            item.last_test_status = None
            item.last_test_message = None
        if body.headers is not None:
            item.headers_encrypted, item.header_names = encrypt_headers(body.headers)
            item.tool_manifest = []
            item.last_test_status = None
            item.last_test_message = None
        if body.env is not None:
            item.env_encrypted, item.env_names = encrypt_env(body.env)
            item.tool_manifest = []
            item.last_test_status = None
            item.last_test_message = None
        if body.enabled is not None:
            item.enabled = body.enabled
        if body.require_confirmation is not None:
            item.require_confirmation = body.require_confirmation
        db.commit()
        db.refresh(item)
        return item
    except McpClientError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/mcp-servers/{server_id}", status_code=204)
def remove_mcp_server(
    server_id: str,
    db: Session = Depends(get_db), current_user: User = Depends(get_current_user),
):
    item = _server(db, current_user.id, server_id)
    db.delete(item)
    db.commit()
    return Response(status_code=204)


@router.post("/mcp-servers/{server_id}/test", response_model=McpTestOut)
async def test_mcp_server(
    server_id: str,
    db: Session = Depends(get_db), current_user: User = Depends(get_current_user),
):
    item = _server(db, current_user.id, server_id)
    item.last_tested_at = datetime.now(timezone.utc)
    try:
        if item.builtin_key == "minio":
            service = ConfiguredMinioService.from_db(db)
            if not service.config.mcp_enabled:
                raise MinioServiceError("MinIO MCP 已被管理员停用")
            service.status()
            tools = minio_tool_manifest()
        else:
            tools = await discover_tools(
                transport=item.transport,
                url=item.url,
                headers=decrypt_headers(item.headers_encrypted),
                command=item.command,
                args=item.args,
                env=decrypt_env(item.env_encrypted),
            )
        item.tool_manifest = tools
        item.last_test_status = "success"
        item.last_test_message = f"连接成功，发现 {len(tools)} 个工具"
        db.commit()
        return McpTestOut(ok=True, message=item.last_test_message, tools=tools)
    except Exception as exc:
        item.tool_manifest = []
        item.last_test_status = "error"
        item.last_test_message = str(exc)[:500]
        db.commit()
        return McpTestOut(ok=False, message=str(exc), tools=[])


@router.post("/mcp-servers/platform-minio", response_model=McpServerOut)
def install_platform_minio_mcp(
    db: Session = Depends(get_db), current_user: User = Depends(get_current_user),
):
    config = db.query(MinioConfig).filter(MinioConfig.id == "default").first()
    if not config or not config.enabled or not config.connected or not config.mcp_enabled:
        raise HTTPException(status_code=409, detail="平台 MinIO MCP 尚未由管理员连接并启用")
    try:
        ConfiguredMinioService.from_db(db).status()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"平台 MinIO 当前不可用：{exc}") from exc

    item = db.query(SuperAssistantMcpServer).filter(
        SuperAssistantMcpServer.owner_id == current_user.id,
        SuperAssistantMcpServer.builtin_key == "minio",
    ).first()
    if not item:
        name_taken = db.query(SuperAssistantMcpServer).filter(
            SuperAssistantMcpServer.owner_id == current_user.id,
            SuperAssistantMcpServer.name == "platform_minio",
        ).first()
        if name_taken:
            raise HTTPException(status_code=409, detail="已有同名 platform_minio MCP，请先重命名或删除")
        item = SuperAssistantMcpServer(
            owner_id=current_user.id,
            name="platform_minio",
            builtin_key="minio",
            transport="streamable_http",
            url="builtin://minio",
            headers_encrypted=None,
            header_names=[],
            command=None,
            args=[],
            env_encrypted=None,
            env_names=[],
            enabled=True,
            require_confirmation=True,
        )
        db.add(item)
    item.tool_manifest = minio_tool_manifest()
    item.last_test_status = "success"
    item.last_test_message = f"平台内置连接成功，发现 {len(item.tool_manifest)} 个工具"
    item.last_tested_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(item)
    return item
