"""Synchronous and streaming execution for Agent Runtime conversations."""
from __future__ import annotations

import json
import logging
import queue
import threading
from collections.abc import Callable, Iterator
from typing import Any

from sqlalchemy.orm import Session

from app.ontologies.agent_runtime import schemas as S

logger = logging.getLogger(__name__)


def run_sync(
    db: Session,
    ontology_id: str,
    current_user: Any,
    body: S.ChatRequest,
    *,
    run_turn_fn: Callable[..., Iterator[dict]],
) -> dict:
    events = list(
        run_turn_fn(
            db,
            ontology_id,
            current_user,
            body.message,
            conversation_id=body.conversation_id,
            model_id=body.model_id,
            release_id=body.release_id,
            run_id=body.run_id,
        )
    )
    answer = next(
        (event for event in events if event["type"] == "answer"),
        None,
    )
    error = next(
        (event for event in events if event["type"] == "error"),
        None,
    )
    meta = next(
        (event for event in events if event["type"] == "meta"),
        {},
    )
    steps = [event for event in events if event["type"] == "step"]
    return {
        "conversationId": meta.get("conversationId"),
        "model": meta.get("model"),
        "steps": [
            {key: value for key, value in step.items() if key != "type"}
            for step in steps
        ],
        "content": (answer or {}).get("content"),
        "citations": (answer or {}).get("citations") or [],
        "proposals": (answer or {}).get("proposals") or [],
        "usage": (answer or {}).get("usage"),
        "verification": (answer or {}).get("verification"),
        "error": (error or {}).get("message"),
    }


def stream_events(
    ontology_id: str,
    current_user: Any,
    body: S.ChatRequest,
    *,
    run_turn_fn: Callable[..., Iterator[dict]],
) -> Iterator[str]:
    """SSE 桥：回合在独立守护线程中执行，本生成器只负责把事件推给客户端。

    浏览器刷新/关闭、代理超时等会提前关闭 SSE。若直接在响应生成器里推进回合，
    客户端断开触发的 GeneratorExit 会把执行到一半的回合一起带走——用户的提问
    已落库而回答永远不会出现（MYW-71 的「消息中断」）。这里用队列把「执行」
    与「推送」解耦：断开只结束推送，回合继续运行至终态并落库；前端凭 run_id
    轮询 ``chat/runs/{run_id}`` 恢复展示。
    """
    from app.database import SessionLocal

    events: queue.Queue[Any] = queue.Queue()
    sentinel = object()

    def _consume() -> None:
        session = SessionLocal()
        try:
            for event in run_turn_fn(
                session,
                ontology_id,
                current_user,
                body.message,
                conversation_id=body.conversation_id,
                model_id=body.model_id,
                release_id=body.release_id,
                run_id=body.run_id,
            ):
                events.put(event)
        except Exception:  # noqa: BLE001 — run_turn_fn 已兜底，此处防御线程静默死亡
            logger.exception("agent chat 消费线程异常")
            events.put({"type": "error", "message": "智能体执行失败: 后台线程异常"})
        finally:
            session.close()
            events.put(sentinel)

    threading.Thread(
        target=_consume,
        name=f"agent-chat-{(body.run_id or 'turn')[:8]}",
        daemon=True,
    ).start()
    # 哨兵在回合完全结束（含落库、会话关闭）后才入队：客户端看到流结束时，
    # 回合结果必然已持久化，前端随后拉取会话即可拿到完整回答。
    while True:
        event = events.get()
        if event is sentinel:
            return
        yield (
            "data: "
            f"{json.dumps(event, ensure_ascii=False, default=str)}"
            "\n\n"
        )
