from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import datetime, timezone

from fastapi import HTTPException, Response
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.auth.models import User
from app.model_configs.models import ModelConfig
from app.super_assistant.models import (
    SuperAssistantConversation,
    SuperAssistantMessage,
    SuperAssistantToolRun,
)
from app.super_assistant.schemas import (
    ApprovalRequest,
    ChatRequest,
    ConversationCreate,
    ConversationUpdate,
)


ConversationLookup = Callable[
    [Session, str, str],
    SuperAssistantConversation,
]
ChatStream = Callable[..., Iterator[str]]


def _conversation(
    db: Session,
    owner_id: str,
    conversation_id: str,
) -> SuperAssistantConversation:
    item = db.query(SuperAssistantConversation).filter(
        SuperAssistantConversation.id == conversation_id,
        SuperAssistantConversation.owner_id == owner_id,
    ).first()
    if not item:
        raise HTTPException(status_code=404, detail="会话不存在")
    return item


def _enabled_model(db: Session, model_config_id: str) -> ModelConfig:
    model = db.query(ModelConfig).filter(
        ModelConfig.id == model_config_id,
        ModelConfig.config_type == "llm",
        ModelConfig.enabled.is_(True),
    ).first()
    if not model:
        raise HTTPException(
            status_code=400,
            detail="所选模型不存在或未启用",
        )
    return model


def list_conversations(
    db: Session,
    current_user: User,
) -> list[SuperAssistantConversation]:
    return db.query(SuperAssistantConversation).filter(
        SuperAssistantConversation.owner_id == current_user.id,
        SuperAssistantConversation.status != "deleted",
    ).order_by(SuperAssistantConversation.updated_at.desc()).all()


def create_conversation(
    body: ConversationCreate,
    db: Session,
    current_user: User,
) -> SuperAssistantConversation:
    if body.model_config_id:
        _enabled_model(db, body.model_config_id)
    item = SuperAssistantConversation(
        owner_id=current_user.id,
        title=body.title.strip() or "新会话",
        model_config_id=body.model_config_id,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def update_conversation(
    conversation_id: str,
    body: ConversationUpdate,
    db: Session,
    current_user: User,
    *,
    conversation_lookup_fn: ConversationLookup = _conversation,
) -> SuperAssistantConversation:
    item = conversation_lookup_fn(
        db,
        current_user.id,
        conversation_id,
    )
    if body.title is not None:
        item.title = body.title.strip()
    if "model_config_id" in body.model_fields_set:
        if body.model_config_id:
            _enabled_model(db, body.model_config_id)
        item.model_config_id = body.model_config_id
    db.commit()
    db.refresh(item)
    return item


def delete_conversation(
    conversation_id: str,
    db: Session,
    current_user: User,
    *,
    conversation_lookup_fn: ConversationLookup = _conversation,
) -> Response:
    item = conversation_lookup_fn(
        db,
        current_user.id,
        conversation_id,
    )
    db.query(SuperAssistantToolRun).filter(
        SuperAssistantToolRun.conversation_id == item.id,
    ).delete(synchronize_session=False)
    db.query(SuperAssistantMessage).filter(
        SuperAssistantMessage.conversation_id == item.id,
    ).delete(synchronize_session=False)
    db.delete(item)
    db.commit()
    return Response(status_code=204)


def list_messages(
    conversation_id: str,
    db: Session,
    current_user: User,
    *,
    conversation_lookup_fn: ConversationLookup = _conversation,
) -> list[SuperAssistantMessage]:
    item = conversation_lookup_fn(
        db,
        current_user.id,
        conversation_id,
    )
    return db.query(SuperAssistantMessage).filter(
        SuperAssistantMessage.conversation_id == item.id,
    ).order_by(SuperAssistantMessage.created_at.asc()).all()


def chat(
    conversation_id: str,
    body: ChatRequest,
    db: Session,
    current_user: User,
    *,
    conversation_lookup_fn: ConversationLookup = _conversation,
    stream_chat_fn: ChatStream,
) -> StreamingResponse:
    conversation = conversation_lookup_fn(
        db,
        current_user.id,
        conversation_id,
    )
    active = db.query(SuperAssistantMessage).filter(
        SuperAssistantMessage.conversation_id == conversation.id,
        SuperAssistantMessage.role == "assistant",
        SuperAssistantMessage.status == "streaming",
    ).order_by(SuperAssistantMessage.created_at.desc()).first()
    if active:
        created_at = active.created_at
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        if (
            datetime.now(timezone.utc) - created_at
        ).total_seconds() < 600:
            raise HTTPException(
                status_code=409,
                detail="当前会话仍有一条回复正在生成",
            )
        active.status = "error"
        active.content = "上一次生成意外中断"

    if body.model_config_id:
        _enabled_model(db, body.model_config_id)
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
        conversation.title = (
            body.message.strip().replace("\n", " ")[:40]
        )
    conversation.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(assistant_message)

    return StreamingResponse(
        stream_chat_fn(
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


def cancel_chat(
    conversation_id: str,
    db: Session,
    current_user: User,
    *,
    conversation_lookup_fn: ConversationLookup = _conversation,
) -> dict[str, bool]:
    conversation = conversation_lookup_fn(
        db,
        current_user.id,
        conversation_id,
    )
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


def decide_tool_run(
    tool_run_id: str,
    body: ApprovalRequest,
    db: Session,
    current_user: User,
) -> dict[str, str]:
    tool_run = db.query(SuperAssistantToolRun).join(
        SuperAssistantConversation,
        SuperAssistantConversation.id
        == SuperAssistantToolRun.conversation_id,
    ).filter(
        SuperAssistantToolRun.id == tool_run_id,
        SuperAssistantConversation.owner_id == current_user.id,
    ).first()
    if not tool_run:
        raise HTTPException(
            status_code=404,
            detail="工具调用不存在",
        )
    if tool_run.status != "awaiting_confirmation":
        raise HTTPException(
            status_code=409,
            detail="该工具调用已处理或已过期",
        )
    tool_run.decision = body.decision
    tool_run.status = (
        "approved" if body.decision == "approve" else "denied"
    )
    if body.decision == "deny":
        tool_run.completed_at = datetime.now(timezone.utc)
    db.commit()
    return {"id": tool_run.id, "status": tool_run.status}
