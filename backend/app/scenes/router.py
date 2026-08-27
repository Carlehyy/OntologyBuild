"""三维场景 HTTP 边界。

路由纪律（architecture 守卫测试断言）：本文件不出现 ORM 事务语句，
端点一律委托 service / query_service；端点函数名即 OpenAPI operation
契约的一部分，不得随手改名。整组路由由 main.py 挂 menu_guard("scenes")。
"""
from typing import Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.deps import get_db, get_current_user
from app.scenes import assistant_service, query_service, service
from app.scenes.schemas import (
    RuntimeLogAppend, SceneChatRequest, SceneConversationCreate,
    SceneCreate, SceneDefinitionSave, SceneUpdate,
)

router = APIRouter()


def _ok(data):
    return {"data": data}


_require_scene = query_service.require_scene

# —— 会话路由须在 /{scene_id} 动态段之前声明，避免被动态段吞掉 ——

@router.post("/conversations", status_code=201)
def create_scene_conversation(
    body: SceneConversationCreate, db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    return _ok(assistant_service.conversation_out(
        assistant_service.create_conversation(db, body, user)))


@router.get("/conversations")
def list_scene_conversations(
    scene_id: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db), _=Depends(get_current_user),
):
    return _ok(assistant_service.list_conversations(
        db, scene_id=scene_id, page=page, page_size=page_size))


@router.get("/conversations/{conversation_id}/messages")
def list_scene_conversation_messages(
    conversation_id: str, db: Session = Depends(get_db),
    _=Depends(get_current_user),
):
    conversation = assistant_service.require_conversation(db, conversation_id)
    return _ok(assistant_service.list_messages(db, conversation))


@router.post("/conversations/{conversation_id}/chat")
def chat_scene_conversation(
    conversation_id: str, body: SceneChatRequest,
    db: Session = Depends(get_db), user=Depends(get_current_user),
):
    conversation = assistant_service.require_conversation(db, conversation_id)
    return StreamingResponse(
        assistant_service.chat_stream(db, conversation, body, user),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )



@router.get("")
def list_scenes(
    q: Optional[str] = Query(None, description="名称/描述模糊搜索"),
    status: Optional[str] = Query(None, description="draft|published|all"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    db: Session = Depends(get_db), _=Depends(get_current_user),
):
    return _ok(query_service.list_scenes(
        db, q=q, status=status, page=page, page_size=page_size))


@router.post("", status_code=201)
def create_scene(
    body: SceneCreate, db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    return _ok(query_service.scene_detail(db, service.create_scene(db, body, user)))


@router.get("/{scene_id}")
def get_scene(
    scene_id: str, db: Session = Depends(get_db),
    _=Depends(get_current_user),
):
    return _ok(query_service.scene_detail(db, _require_scene(db, scene_id)))


@router.patch("/{scene_id}")
def update_scene(
    scene_id: str, body: SceneUpdate, db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    scene = _require_scene(db, scene_id)
    return _ok(query_service.scene_detail(db, service.update_scene_info(db, scene, body)))


@router.delete("/{scene_id}", status_code=204)
def delete_scene(
    scene_id: str, db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    scene = _require_scene(db, scene_id)
    service.delete_scene(db, scene)


@router.post("/{scene_id}/clone", status_code=201)
def clone_scene(
    scene_id: str, db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    scene = _require_scene(db, scene_id)
    cloned = service.clone_scene(db, scene, user)
    return _ok(query_service.scene_detail(db, cloned))


@router.put("/{scene_id}/definition")
def save_scene_definition(
    scene_id: str, body: SceneDefinitionSave,
    source: str = Query("manual", description="manual|assistant"),
    db: Session = Depends(get_db), user=Depends(get_current_user),
):
    scene = _require_scene(db, scene_id)
    version = service.save_definition(db, scene, body, source=source, user=user)
    return _ok({
        "scene": query_service.scene_detail(db, scene),
        "version": query_service.version_out(version, include_definition=True),
    })


@router.post("/{scene_id}/publish")
def publish_scene(
    scene_id: str, db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    scene = _require_scene(db, scene_id)
    return _ok(query_service.scene_detail(db, service.publish_scene(db, scene, user)))


@router.get("/{scene_id}/versions")
def list_scene_versions(
    scene_id: str, include_definition: bool = Query(False),
    db: Session = Depends(get_db), _=Depends(get_current_user),
):
    scene = _require_scene(db, scene_id)
    return _ok(query_service.list_versions(
        db, scene, include_definition=include_definition))


@router.get("/{scene_id}/versions/{version_no}")
def get_scene_version(
    scene_id: str, version_no: int, db: Session = Depends(get_db),
    _=Depends(get_current_user),
):
    scene = _require_scene(db, scene_id)
    return _ok(query_service.get_version(db, scene, version_no))


@router.get("/{scene_id}/runtime-logs")
def list_scene_runtime_logs(
    scene_id: str,
    level: Optional[str] = Query(None, description="info|normal|warning|alarm|all"),
    object_id: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    db: Session = Depends(get_db), _=Depends(get_current_user),
):
    scene = _require_scene(db, scene_id)
    return _ok(query_service.list_runtime_logs(
        db, scene, level=level, object_id=object_id,
        page=page, page_size=page_size))


@router.post("/{scene_id}/runtime-logs", status_code=201)
def append_scene_runtime_logs(
    scene_id: str, body: RuntimeLogAppend, db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    scene = _require_scene(db, scene_id)
    count = service.append_runtime_logs(db, scene, body)
    return _ok({"appended": count})
