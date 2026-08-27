"""
回合状态注册表 (Chat Run Registry) — 与 ``chat_cancel`` 同一进程内语义：
run_id 由客户端生成并随 chat 请求传入。SSE 推送与回合执行解耦后（见
``chat_service.stream_events``），用户离开页面只断开推送，回合仍在后台
执行至终态并落库；前端凭 run_id 轮询本注册表即可知道回合是否仍在执行、
回答落到了哪个会话，从而恢复「正在处理」的展示（MYW-71）。

单进程 uvicorn 部署下注册表即全局事实（与取消注册表同一边界假设）；
终态条目保留一段恢复窗口，过期后由下次访问惰性清除。
"""
from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Optional

# 终态条目保留时长：覆盖「离开页面后返回」的恢复窗口。
FINISHED_TTL_SECONDS = 30 * 60

# 终态取值（unknown 仅供查询端点表达「注册表不认识该 run」）。
TERMINAL_STATUSES = ("succeeded", "error", "cancelled")
KNOWN_STATUSES = ("running",) + TERMINAL_STATUSES + ("unknown",)


@dataclass
class ChatRunInfo:
    ontology_id: str
    conversation_id: Optional[str]
    status: str
    started_at: float
    finished_at: Optional[float] = None


class ChatRunRegistry:
    """进程内 run_id → 回合状态。线程安全；首次终态生效，后续标记忽略。"""

    def __init__(self,
                 now_fn: Callable[[], float] = time.time,
                 finished_ttl_seconds: float = FINISHED_TTL_SECONDS) -> None:
        self._now = now_fn
        self._ttl = finished_ttl_seconds
        self._runs: dict[str, ChatRunInfo] = {}
        self._lock = threading.Lock()

    def register(self, run_id: str, ontology_id: str) -> None:
        if not run_id:
            return
        with self._lock:
            self._evict_expired_locked()
            self._runs[run_id] = ChatRunInfo(
                ontology_id=ontology_id,
                conversation_id=None,
                status="running",
                started_at=self._now(),
            )

    def attach_conversation(self, run_id: str, conversation_id: Optional[str]) -> None:
        """meta 事件拿到会话 id 后回填，供查询端点透出给恢复中的前端。"""
        if not run_id or not conversation_id:
            return
        with self._lock:
            info = self._runs.get(run_id)
            if info is not None:
                info.conversation_id = str(conversation_id)

    def mark_finished(self, run_id: str, status: str) -> None:
        if not run_id or status not in TERMINAL_STATUSES:
            return
        with self._lock:
            info = self._runs.get(run_id)
            if info is not None and info.status == "running":
                info.status = status
                info.finished_at = self._now()

    def get(self, run_id: str) -> Optional[ChatRunInfo]:
        """查询回合快照；条目已过期清除时返回 None。返回拷贝，避免跨线程共享可变条目。"""
        with self._lock:
            self._evict_expired_locked()
            info = self._runs.get(run_id)
        return ChatRunInfo(**vars(info)) if info is not None else None

    def _evict_expired_locked(self) -> None:
        now = self._now()
        expired = [run_id for run_id, info in self._runs.items()
                   if info.finished_at is not None
                   and now - info.finished_at > self._ttl]
        for run_id in expired:
            self._runs.pop(run_id, None)


chat_run_registry = ChatRunRegistry()


def run_status_payload(run_id: str, ontology_id: str) -> dict:
    """查询端点的响应载荷；``unknown`` 表示注册表不认识该 run（已过期清除、
    进程重启或非本进程执行），前端据此停止轮询、以会话内容为准。"""
    from datetime import datetime, timezone

    info = chat_run_registry.get(run_id)
    if info is None or info.ontology_id != ontology_id:
        return {
            "runId": run_id,
            "conversationId": None,
            "status": "unknown",
            "startedAt": None,
            "finishedAt": None,
        }

    def iso(ts: Optional[float]) -> Optional[str]:
        return None if ts is None else datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()

    return {
        "runId": run_id,
        "conversationId": info.conversation_id,
        "status": info.status,
        "startedAt": iso(info.started_at),
        "finishedAt": iso(info.finished_at),
    }
