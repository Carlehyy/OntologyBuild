"""
运行取消注册表 (Run Cancel Registry) — 借鉴 DataFoundry 的
``run-cancel-registry.ts``：进程内 ``run_id → threading.Event`` 注册表，
REST 取消端点查表置位；编排器在回合内协作式检查该标志。

进程内注册表意味着取消只对「当前进程正在流式执行的回合」生效——
SSE 连接与执行在同一进程内，满足产品语义；多 worker 部署的跨进程取消
不在本版本范围（与 DataFoundry v0.2 的实现边界一致）。
"""
from __future__ import annotations

import threading
from typing import Optional


class ChatCancelRegistry:
    """进程内 run 取消句柄注册表。run_id 由客户端生成并随 chat 请求传入。"""

    def __init__(self) -> None:
        self._events: dict[str, threading.Event] = {}
        self._lock = threading.Lock()

    def register(self, run_id: str) -> None:
        if not run_id:
            return
        with self._lock:
            # 重复 run_id 视为客户端重放：覆盖为同一事件，不叠加
            self._events[run_id] = threading.Event()

    def unregister(self, run_id: str) -> None:
        if not run_id:
            return
        with self._lock:
            self._events.pop(run_id, None)

    def request_cancel(self, run_id: str) -> bool:
        """请求取消；返回 False 表示该 run 不在本进程（已结束或跨进程）。"""
        with self._lock:
            event = self._events.get(run_id)
        if event is None:
            return False
        event.set()
        return True

    def is_cancelled(self, run_id: Optional[str]) -> bool:
        if not run_id:
            return False
        with self._lock:
            event = self._events.get(run_id)
        return bool(event and event.is_set())

    def active_runs(self) -> list[str]:
        with self._lock:
            return sorted(self._events.keys())


chat_cancel_registry = ChatCancelRegistry()
