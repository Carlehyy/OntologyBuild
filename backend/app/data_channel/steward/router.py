"""
数据管家 API — /api/v2/steward/*

  GET    /status                     n8n 配置/连通性 + LLM 可用性 + 记录统计
  POST   /chat                       对话（默认 SSE 流式；stream=false 同步返回）
  GET    /conversations              当前用户的会话列表
  GET    /conversations/{id}         会话详情（含完整工具轨迹，审计视图）
  DELETE /conversations/{id}
  GET    /pipelines                  受管流水线记录列表（受管流水线面板）
  GET    /pipelines/{id}             记录详情（workflow 摘要，只读）
  POST   /pipelines/bootstrap        流水线列表「新建 n8n 流水线」：骨架工作流登记

职权边界：数据管家对 n8n 只有两项持久写权限——新建流水线（bootstrap / 对话工具
create_pipeline）与编排未发布未启用的流水线（对话工具 update_workflow）。此外，
它可按用户明确要求触发一次不入湖、执行后恢复启停状态的预览，也可在当前会话
隔离空间创建、编辑和删除文件，但不能访问任何其他文件路径。
发布只存在于流水线编辑向导，且发布后永久封版；
归档/删除不走 steward，归档在流水线列表（走 service.archive）。
"""
from __future__ import annotations

from fastapi import (
    APIRouter,
    Depends,
    File,
    Query,
    Response,
    UploadFile,
)
from sqlalchemy.orm import Session

from app.data_channel.steward import (
    browser_session_service as _browser_session_service,
)
from app.data_channel.steward import (
    browser_source_service as _browser_source_service,
)
from app.data_channel.steward import (
    lifecycle_service as _lifecycle_service,
)
from app.data_channel.steward import query_service as _query_service
from app.data_channel.steward import service
from app.data_channel.steward import (
    streaming_service as _streaming_service,
)
from app.data_channel.steward.contracts import (
    BindBrowserSourceBody,
    BootstrapBody,
    BrowserClickBody,
    BrowserLiveControlBody,
    BrowserLiveInputBody,
    BrowserLiveLeaseBody,
    BrowserTypeBody,
    BrowserUrlBody,
    ChatBody,
    CreateBrowserSourceBody,
    CreateConversationBody,
    UpdateBrowserSourceBody,
)
from app.data_channel.steward.orchestrator import run_steward_turn
from app.deps import get_current_user, get_db
from app.model_configs.selector import select_llm_model_config


router = APIRouter()

# Compatibility aliases retained for existing imports and test patch seams.
_ok = _query_service._ok
_handle = _lifecycle_service._handle
_conv_out = _query_service._conv_out
_msg_out = _query_service._msg_out
_require_conversation = _query_service._require_conversation
_browser_error = _browser_session_service._browser_error


# ── 状态 ──────────────────────────────────────────────────────────

@router.get("/status")
def steward_status(
    db: Session = Depends(get_db),
    _=Depends(get_current_user),
):
    return _query_service.steward_status(
        db,
        service_module=service,
        select_llm_model_config_fn=select_llm_model_config,
    )


# ── 对话 ──────────────────────────────────────────────────────────

@router.post("/chat")
def chat(
    body: ChatBody,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return _streaming_service.chat(
        body,
        db,
        current_user,
        run_turn_fn=run_steward_turn,
    )


# ── 会话 ──────────────────────────────────────────────────────────

@router.post("/conversations", status_code=201)
def create_conversation(
    body: CreateConversationBody,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return _lifecycle_service.create_conversation(
        body,
        db,
        current_user,
        conv_out_fn=_conv_out,
    )


@router.get("/conversations")
def list_conversations(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return _query_service.list_conversations(
        db,
        current_user,
        conv_out_fn=_conv_out,
    )


@router.get("/conversations/{conversation_id}")
def get_conversation(
    conversation_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return _query_service.get_conversation(
        conversation_id,
        db,
        current_user,
        require_conversation_fn=_require_conversation,
        conv_out_fn=_conv_out,
        msg_out_fn=_msg_out,
    )


@router.get("/conversations/{conversation_id}/export")
def export_conversation(
    conversation_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """导出完整会话审计记录；与界面详情不同，此处不截断消息数量。"""
    return _query_service.export_conversation(
        conversation_id,
        db,
        current_user,
        require_conversation_fn=_require_conversation,
        conv_out_fn=_conv_out,
        msg_out_fn=_msg_out,
    )


@router.delete("/conversations/{conversation_id}", status_code=204)
def delete_conversation(
    conversation_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return _lifecycle_service.delete_conversation(
        conversation_id,
        db,
        current_user,
        require_conversation_fn=_require_conversation,
    )


# ── 会话文件 ──────────────────────────────────────────────────────

@router.get("/conversations/{conversation_id}/files")
def list_conversation_files(
    conversation_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return _query_service.list_conversation_files(
        conversation_id,
        db,
        current_user,
        require_conversation_fn=_require_conversation,
    )


@router.get("/conversations/{conversation_id}/files/{artifact_id}/preview")
def preview_conversation_file(
    conversation_id: str,
    artifact_id: str,
    max_chars: int = Query(60_000, ge=1, le=100_000),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """返回会话文件的安全文本预览；原始二进制仍由下载端点提供。"""
    return _query_service.preview_conversation_file(
        conversation_id,
        artifact_id,
        max_chars,
        db,
        current_user,
        require_conversation_fn=_require_conversation,
    )


@router.post("/conversations/{conversation_id}/files", status_code=201)
async def upload_conversation_file(
    conversation_id: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return _lifecycle_service.upload_conversation_file(
        conversation_id,
        file,
        db,
        current_user,
        require_conversation_fn=_require_conversation,
    )


@router.get("/conversations/{conversation_id}/files/{artifact_id}")
def download_conversation_file(
    conversation_id: str,
    artifact_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return _query_service.download_conversation_file(
        conversation_id,
        artifact_id,
        db,
        current_user,
        require_conversation_fn=_require_conversation,
    )


@router.delete(
    "/conversations/{conversation_id}/files/{artifact_id}",
    status_code=204,
)
def delete_conversation_file(
    conversation_id: str,
    artifact_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return _lifecycle_service.delete_conversation_file(
        conversation_id,
        artifact_id,
        db,
        current_user,
        require_conversation_fn=_require_conversation,
    )


@router.get("/conversations/{conversation_id}/archive")
def archive_conversation_files(
    conversation_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return _query_service.archive_conversation_files(
        conversation_id,
        db,
        current_user,
        require_conversation_fn=_require_conversation,
    )


# ── 会话浏览器 ────────────────────────────────────────────────────

@router.get("/browser/status")
def browser_status(_=Depends(get_current_user)):
    return _browser_source_service.browser_status()


@router.get("/browser/sources")
def list_browser_sources(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return _browser_source_service.list_browser_sources(
        db, current_user
    )


@router.post("/browser/sources", status_code=201)
def create_browser_source(
    body: CreateBrowserSourceBody,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return _browser_source_service.create_browser_source(
        body, db, current_user
    )


@router.patch("/browser/sources/{source_id}")
def update_browser_source(
    source_id: str,
    body: UpdateBrowserSourceBody,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return _browser_source_service.update_browser_source(
        source_id, body, db, current_user
    )


@router.post("/browser/sources/{source_id}/rotate-token")
def rotate_browser_source_token(
    source_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return _browser_source_service.rotate_browser_source_token(
        source_id, db, current_user
    )


@router.delete("/browser/sources/{source_id}", status_code=204)
def delete_browser_source(
    source_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return _browser_source_service.delete_browser_source(
        source_id, db, current_user
    )


@router.post("/browser/sources/{source_id}/test")
def test_browser_source(
    source_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return _browser_source_service.test_browser_source(
        source_id,
        db,
        current_user,
        browser_error_fn=_browser_error,
    )


@router.get("/browser/companion/script")
def download_browser_companion(_=Depends(get_current_user)):
    return _browser_source_service.download_browser_companion()


@router.put("/conversations/{conversation_id}/browser/source")
def bind_browser_source(
    conversation_id: str,
    body: BindBrowserSourceBody,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return _browser_session_service.bind_browser_source(
        conversation_id,
        body,
        db,
        current_user,
        require_conversation_fn=_require_conversation,
        conv_out_fn=_conv_out,
    )


@router.post("/conversations/{conversation_id}/browser/start")
def start_browser(
    conversation_id: str,
    body: BrowserUrlBody,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return _browser_session_service.start_browser(
        conversation_id,
        body,
        db,
        current_user,
        require_conversation_fn=_require_conversation,
        browser_error_fn=_browser_error,
    )


@router.post("/conversations/{conversation_id}/browser/navigate")
def navigate_browser(
    conversation_id: str,
    body: BrowserUrlBody,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return _browser_session_service.navigate_browser(
        conversation_id,
        body,
        db,
        current_user,
        require_conversation_fn=_require_conversation,
        browser_error_fn=_browser_error,
    )


@router.get("/conversations/{conversation_id}/browser/state")
def browser_state(
    conversation_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return _browser_session_service.browser_state(
        conversation_id,
        db,
        current_user,
        require_conversation_fn=_require_conversation,
        browser_error_fn=_browser_error,
    )


@router.get("/conversations/{conversation_id}/browser/session")
def browser_session(
    conversation_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Tell the live-view UI whether the Agent already opened this browser."""
    return _browser_session_service.browser_session(
        conversation_id,
        db,
        current_user,
        require_conversation_fn=_require_conversation,
        browser_error_fn=_browser_error,
    )


@router.post("/conversations/{conversation_id}/browser/click")
def browser_click(
    conversation_id: str,
    body: BrowserClickBody,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return _browser_session_service.browser_click(
        conversation_id,
        body,
        db,
        current_user,
        require_conversation_fn=_require_conversation,
        browser_error_fn=_browser_error,
    )


@router.post("/conversations/{conversation_id}/browser/type")
def browser_type(
    conversation_id: str,
    body: BrowserTypeBody,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return _browser_session_service.browser_type(
        conversation_id,
        body,
        db,
        current_user,
        require_conversation_fn=_require_conversation,
        browser_error_fn=_browser_error,
    )


@router.get("/conversations/{conversation_id}/browser/captures")
def browser_captures(
    conversation_id: str,
    keyword: str | None = None,
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return _browser_session_service.browser_captures(
        conversation_id,
        keyword,
        limit,
        db,
        current_user,
        require_conversation_fn=_require_conversation,
    )


@router.post(
    "/conversations/{conversation_id}/browser/captures/"
    "{capture_id}/download"
)
def browser_capture_download(
    conversation_id: str,
    capture_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return _browser_session_service.browser_capture_download(
        conversation_id,
        capture_id,
        db,
        current_user,
        require_conversation_fn=_require_conversation,
        browser_error_fn=_browser_error,
    )


@router.post("/conversations/{conversation_id}/browser/ticket")
def browser_live_ticket(
    conversation_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return _browser_session_service.browser_live_ticket(
        conversation_id,
        db,
        current_user,
        require_conversation_fn=_require_conversation,
    )


@router.post("/conversations/{conversation_id}/browser/live-http")
def attach_browser_live_http(
    conversation_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Start the authenticated HTTP fallback when WebSocket is unavailable."""
    return _browser_session_service.attach_browser_live_http(
        conversation_id,
        db,
        current_user,
        require_conversation_fn=_require_conversation,
        browser_error_fn=_browser_error,
    )


@router.post(
    "/conversations/{conversation_id}/browser/live-http/frame"
)
def browser_live_http_frame(
    conversation_id: str,
    body: BrowserLiveLeaseBody,
    response: Response,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return _browser_session_service.browser_live_http_frame(
        conversation_id,
        body,
        response,
        db,
        current_user,
        require_conversation_fn=_require_conversation,
        browser_error_fn=_browser_error,
    )


@router.post(
    "/conversations/{conversation_id}/browser/live-http/input"
)
def browser_live_http_input(
    conversation_id: str,
    body: BrowserLiveInputBody,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return _browser_session_service.browser_live_http_input(
        conversation_id,
        body,
        db,
        current_user,
        require_conversation_fn=_require_conversation,
        browser_error_fn=_browser_error,
    )


@router.post(
    "/conversations/{conversation_id}/browser/live-http/control"
)
def browser_live_http_control(
    conversation_id: str,
    body: BrowserLiveControlBody,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return _browser_session_service.browser_live_http_control(
        conversation_id,
        body,
        db,
        current_user,
        require_conversation_fn=_require_conversation,
        browser_error_fn=_browser_error,
    )


@router.post(
    "/conversations/{conversation_id}/browser/live-http/release"
)
def release_browser_live_http(
    conversation_id: str,
    body: BrowserLiveLeaseBody,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return _browser_session_service.release_browser_live_http(
        conversation_id,
        body,
        db,
        current_user,
        require_conversation_fn=_require_conversation,
        browser_error_fn=_browser_error,
    )


# ── 受管流水线面板 ────────────────────────────────────────────────

@router.get("/pipelines")
def list_pipeline_records(
    include_archived: bool = Query(False),
    db: Session = Depends(get_db),
    _=Depends(get_current_user),
):
    """受管流水线面板：只列数据管家可编排的流水线 —— 未发布 且 未启用。

    发布即封版、编排移交编辑向导；已发布或 n8n 侧已激活的流水线超出管家职权
    （同 require_orchestrable 口径），到「数据流水线」列表管理，不在此面板出现。
    """
    return _query_service.list_pipeline_records(
        include_archived,
        db,
        service_module=service,
    )


@router.post("/pipelines/bootstrap", status_code=201)
def bootstrap_pipeline(
    body: BootstrapBody,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """流水线列表「新建 n8n 流水线」：后台自动在 n8n 创建骨架工作流（草稿纳管）。

    与对话工具 create_pipeline 同源治理：创建即未发布、n8n 侧不激活；
    编排完善在数据管家对话完成，发布在流水线编辑向导完成。
    返回治理记录供前端深链跳转。
    """
    return _lifecycle_service.bootstrap_pipeline(
        body,
        db,
        current_user,
        service_module=service,
        handle_fn=_handle,
    )


@router.get("/pipelines/{record_id}")
def get_pipeline_record(
    record_id: str,
    db: Session = Depends(get_db),
    _=Depends(get_current_user),
):
    return _query_service.get_pipeline_record(
        record_id,
        db,
        service_module=service,
    )
