"""超级助手会话内容搜索：标题与消息全文的属权隔离 ILIKE 检索。"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.auth.models import User
from app.super_assistant.models import (
    SuperAssistantConversation,
    SuperAssistantMessage,
)
from app.super_assistant.schemas import (
    SearchConversationHit,
    SearchMessageHit,
    SearchResultOut,
)

# 单次检索最多扫描的消息命中行数：防止大库全表扫描拖垮请求
_MAX_MESSAGE_SCAN = 500
_SNIPPET_RADIUS = 40
_HITS_PER_CONVERSATION = 3


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _snippet(content: str, keyword: str) -> str:
    index = content.lower().find(keyword.lower())
    if index < 0:
        return content[: _SNIPPET_RADIUS * 2]
    start = max(0, index - _SNIPPET_RADIUS)
    end = min(len(content), index + len(keyword) + _SNIPPET_RADIUS)
    return f"{'…' if start > 0 else ''}{content[start:end]}{'…' if end < len(content) else ''}"


def search_conversations(
    db: Session,
    current_user: User,
    *,
    q: str,
    limit: int,
) -> SearchResultOut:
    keyword = q.strip()
    if not keyword:
        return SearchResultOut(query=q, conversations=[])
    pattern = f"%{_escape_like(keyword)}%"

    title_rows = (
        db.query(SuperAssistantConversation)
        .filter(
            SuperAssistantConversation.owner_id == current_user.id,
            SuperAssistantConversation.title.ilike(pattern, escape="\\"),
        )
        .all()
    )
    title_hit_ids = {row.id for row in title_rows}

    message_rows = (
        db.query(SuperAssistantMessage)
        .join(
            SuperAssistantConversation,
            SuperAssistantConversation.id == SuperAssistantMessage.conversation_id,
        )
        .filter(
            SuperAssistantConversation.owner_id == current_user.id,
            # 流式占位消息内容不完整，不参与检索
            SuperAssistantMessage.status != "streaming",
            SuperAssistantMessage.content.ilike(pattern, escape="\\"),
        )
        .order_by(SuperAssistantMessage.created_at.desc())
        .limit(_MAX_MESSAGE_SCAN)
        .all()
    )
    hits_by_conversation: dict[str, list[SearchMessageHit]] = {}
    for row in message_rows:
        hits = hits_by_conversation.setdefault(row.conversation_id, [])
        if len(hits) < _HITS_PER_CONVERSATION:
            hits.append(SearchMessageHit(
                message_id=row.id,
                role=row.role,
                snippet=_snippet(row.content, keyword),
                created_at=row.created_at,
            ))

    candidate_ids = title_hit_ids | set(hits_by_conversation)
    if not candidate_ids:
        return SearchResultOut(query=q, conversations=[])
    conversations = (
        db.query(SuperAssistantConversation)
        .filter(SuperAssistantConversation.id.in_(candidate_ids))
        .order_by(SuperAssistantConversation.updated_at.desc())
        .limit(limit)
        .all()
    )
    return SearchResultOut(
        query=q,
        conversations=[
            SearchConversationHit(
                id=conversation.id,
                title=conversation.title,
                status=conversation.status,
                updated_at=conversation.updated_at,
                title_matched=conversation.id in title_hit_ids,
                message_hits=hits_by_conversation.get(conversation.id, []),
            )
            for conversation in conversations
        ],
    )
