"""值守循环的管理员通知 — 经平台 inbox 契约投递（收件箱正门）。

仅推送"需要人注意"的事件：自动回退、预算耗尽、循环熔断。常规轮次
结果一律走审计时间线，避免收件箱被无人阅读的日志海淹没。预算类
重复告警按自然周去重（upsert 同一 correlation_key）。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.inbox.schemas import (
    InboxAudience,
    InboxContent,
    InboxEventIn,
    InboxResource,
    InboxSource,
)
from app.inbox.service import publish_event


def notify_admins(db: Session, *, kind: str, title: str, summary: str,
                  correlation_key: str, safe_context: dict | None = None,
                  weekly_dedupe: bool = False) -> None:
    """向全体活跃 admin 投递一条告警/通知（不提交，随调用方事务落库）。"""
    now = datetime.now(timezone.utc)
    year, week, _ = now.isocalendar()
    event_id = (
        f"assistant-eval-autopilot:{correlation_key}:{year}-W{week}"
        if weekly_dedupe
        else f"assistant-eval-autopilot:{correlation_key}:{uuid.uuid4()}"
    )
    publish_event(db, InboxEventIn(
        eventId=event_id,
        occurredAt=now,
        operation="upsert" if weekly_dedupe else "append",
        source=InboxSource(
            system="assistant_evaluation",
            type="autopilot",
            id=correlation_key,
            correlationKey=correlation_key,
        ),
        item=InboxContent(
            kind=kind,
            priority="high" if kind == "alert" else "normal",
            title=title[:300],
            summary=(summary or "")[:4000],
            safeContext=safe_context or {},
        ),
        resource=InboxResource(
            type="assistant_eval",
            id=correlation_key,
            label="助手评估 · 数据飞轮",
            href="/#/settings/assistant-eval",
        ),
        audience=InboxAudience(type="role", role="admin"),
    ))
