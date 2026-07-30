from __future__ import annotations

# Compatibility imports are intentionally retained: older tests and extensions
# patch these names on the router module. Handlers pass the sensitive seams into
# application services at request time.
import io
import uuid
from datetime import datetime, timezone

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Response,
    UploadFile,
    status,
)
from fastapi.responses import StreamingResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.models import User
from app.deps import get_current_user, get_db
from app.model_configs.models import ModelConfig
from app.super_assistant import (
    conversation_service as _conversation_service,
)
from app.super_assistant import mcp_server_service
from app.super_assistant import skill_service as _skill_service
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
    minio_tool_manifest,
)


router = APIRouter()

# Private compatibility helpers remain importable by identity while their
# implementation now lives with the application transaction it protects.
_conversation = _conversation_service._conversation
_skill = _skill_service._skill
_storage_error = _skill_service._storage_error


def _mcp_http_error(
    exc: mcp_server_service.McpServerServiceError,
) -> HTTPException:
    if isinstance(exc, mcp_server_service.McpServerNotFoundError):
        status_code = 404
    elif isinstance(exc, mcp_server_service.McpServerConflictError):
        status_code = 409
    elif isinstance(
        exc,
        mcp_server_service.McpServerUnavailableError,
    ):
        status_code = 503
    else:
        status_code = 400
    return HTTPException(status_code=status_code, detail=str(exc))


@router.get("/conversations", response_model=list[ConversationOut])
def list_conversations(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return _conversation_service.list_conversations(db, current_user)


@router.post(
    "/conversations",
    response_model=ConversationOut,
    status_code=201,
)
def create_conversation(
    body: ConversationCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return _conversation_service.create_conversation(
        body,
        db,
        current_user,
    )


@router.patch(
    "/conversations/{conversation_id}",
    response_model=ConversationOut,
)
def update_conversation(
    conversation_id: str,
    body: ConversationUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return _conversation_service.update_conversation(
        conversation_id,
        body,
        db,
        current_user,
        conversation_lookup_fn=_conversation,
    )


@router.delete(
    "/conversations/{conversation_id}",
    status_code=204,
)
def delete_conversation(
    conversation_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return _conversation_service.delete_conversation(
        conversation_id,
        db,
        current_user,
        conversation_lookup_fn=_conversation,
    )


@router.get(
    "/conversations/{conversation_id}/messages",
    response_model=list[MessageOut],
)
def list_messages(
    conversation_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return _conversation_service.list_messages(
        conversation_id,
        db,
        current_user,
        conversation_lookup_fn=_conversation,
    )


@router.post("/conversations/{conversation_id}/chat")
def chat(
    conversation_id: str,
    body: ChatRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return _conversation_service.chat(
        conversation_id,
        body,
        db,
        current_user,
        conversation_lookup_fn=_conversation,
        stream_chat_fn=stream_chat,
    )


@router.post(
    "/conversations/{conversation_id}/cancel",
    status_code=202,
)
def cancel_chat(
    conversation_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return _conversation_service.cancel_chat(
        conversation_id,
        db,
        current_user,
        conversation_lookup_fn=_conversation,
    )


@router.post("/tool-runs/{tool_run_id}/decision")
def decide_tool_run(
    tool_run_id: str,
    body: ApprovalRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return _conversation_service.decide_tool_run(
        tool_run_id,
        body,
        db,
        current_user,
    )


@router.get("/skills", response_model=list[SkillOut])
def list_skills(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return _skill_service.list_skills(db, current_user)


@router.post("/skills", response_model=SkillOut, status_code=201)
def create_skill(
    body: SkillCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return _skill_service.create_skill(body, db, current_user)


@router.post(
    "/skills/import",
    response_model=SkillOut,
    status_code=201,
)
async def import_skill(
    archive: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await _skill_service.import_skill(
        archive,
        db,
        current_user,
    )


@router.patch("/skills/{skill_id}", response_model=SkillOut)
def update_skill(
    skill_id: str,
    body: SkillUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return _skill_service.update_skill(
        skill_id,
        body,
        db,
        current_user,
        skill_lookup_fn=_skill,
    )


@router.delete("/skills/{skill_id}", status_code=204)
def remove_skill(
    skill_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return _skill_service.remove_skill(
        skill_id,
        db,
        current_user,
        skill_lookup_fn=_skill,
        storage_error_fn=_storage_error,
    )


@router.get("/skills/{skill_id}/files")
def list_skill_files(
    skill_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return _skill_service.list_skill_files(
        skill_id,
        db,
        current_user,
        skill_lookup_fn=_skill,
        storage_error_fn=_storage_error,
    )


@router.get("/skills/{skill_id}/files/{file_path:path}")
def get_skill_file(
    skill_id: str,
    file_path: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return _skill_service.get_skill_file(
        skill_id,
        file_path,
        db,
        current_user,
        skill_lookup_fn=_skill,
        storage_error_fn=_storage_error,
    )


@router.put("/skills/{skill_id}/files/{file_path:path}")
def put_skill_file(
    skill_id: str,
    file_path: str,
    body: SkillFileContent,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return _skill_service.put_skill_file(
        skill_id,
        file_path,
        body,
        db,
        current_user,
        skill_lookup_fn=_skill,
        storage_error_fn=_storage_error,
    )


@router.delete(
    "/skills/{skill_id}/files/{file_path:path}",
    status_code=204,
)
def remove_skill_file(
    skill_id: str,
    file_path: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return _skill_service.remove_skill_file(
        skill_id,
        file_path,
        db,
        current_user,
        skill_lookup_fn=_skill,
        storage_error_fn=_storage_error,
    )


@router.get("/skills/{skill_id}/export")
def export_skill(
    skill_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return _skill_service.export_skill(
        skill_id,
        db,
        current_user,
        skill_lookup_fn=_skill,
        storage_error_fn=_storage_error,
    )


@router.get("/mcp-servers", response_model=list[McpServerOut])
def list_mcp_servers(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return mcp_server_service.list_mcp_servers(
        db,
        current_user.id,
        include_builtins=True,
    )


@router.post(
    "/mcp-servers",
    response_model=McpServerOut,
    status_code=201,
)
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


@router.patch(
    "/mcp-servers/{server_id}",
    response_model=McpServerOut,
)
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
            include_builtins=True,
        )
    except mcp_server_service.McpServerServiceError as exc:
        raise _mcp_http_error(exc) from exc


@router.delete("/mcp-servers/{server_id}", status_code=204)
def remove_mcp_server(
    server_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        mcp_server_service.remove_mcp_server(
            db,
            current_user.id,
            server_id,
            include_builtins=True,
        )
    except mcp_server_service.McpServerServiceError as exc:
        raise _mcp_http_error(exc) from exc
    return Response(status_code=204)


@router.post(
    "/mcp-servers/{server_id}/test",
    response_model=McpTestOut,
)
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
            include_builtins=True,
        )
    except mcp_server_service.McpServerServiceError as exc:
        raise _mcp_http_error(exc) from exc


@router.post(
    "/mcp-servers/platform-minio",
    response_model=McpServerOut,
)
def install_platform_minio_mcp(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        return mcp_server_service.install_platform_minio_mcp(
            db,
            current_user.id,
            configured_minio_service_cls=ConfiguredMinioService,
            minio_tool_manifest_fn=minio_tool_manifest,
        )
    except mcp_server_service.McpServerServiceError as exc:
        raise _mcp_http_error(exc) from exc
