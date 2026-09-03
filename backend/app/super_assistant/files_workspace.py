"""超级助手会话附件工作区：与 steward 共用 SessionWorkspace 实现、独立根目录。

只依赖 workspace/config，禁止 import conversation_service（防循环）。
"""
from __future__ import annotations

import logging
from pathlib import Path

from app.data_channel.steward.workspace import SessionWorkspace
from app.shared.config import settings

logger = logging.getLogger(__name__)


def _root() -> Path:
    configured = (getattr(settings, "super_assistant_workspace_root", "") or "").strip()
    base = Path(configured) if configured else Path(settings.uploads_dir) / "super-assistant-sessions"
    return base.expanduser().resolve()


def session_workspace() -> SessionWorkspace:
    # 根目录每次现读 settings：测试经 monkeypatch 切换，禁止缓存单例
    return SessionWorkspace(_root())


def file_context_section(conversation_id: str, query: str = "") -> str:
    """组装注入 system prompt 的会话附件段；无文件时返回 ""。"""
    return session_workspace().context_block(conversation_id, query=query)


def remove_session_files(conversation_id: str) -> None:
    """删除会话时清理附件目录；文件系统故障只记日志，不阻断会话删除。"""
    try:
        session_workspace().remove_session(conversation_id)
    except Exception:
        logger.warning("会话附件目录清理失败: %s", conversation_id, exc_info=True)
