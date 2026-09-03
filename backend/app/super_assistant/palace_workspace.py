"""记忆宫殿用户级文件工作区：与 steward 共用 SessionWorkspace 实现、独立根目录。

owner_id 经 uuid5 确定映射为工作区目录 id（SessionWorkspace 的目录隔离
只接受 UUID），同一用户的全部文件收敛到同一目录、同一把进程锁。
只依赖 workspace/config，禁止 import palace_service（防循环）。
"""
from __future__ import annotations

import uuid
from pathlib import Path

from app.data_channel.steward.workspace import SessionWorkspace
from app.shared.config import settings

_PALACE_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "super-assistant-palace")


def _root() -> Path:
    configured = (getattr(settings, "super_assistant_palace_workspace_root", "") or "").strip()
    base = Path(configured) if configured else Path(settings.uploads_dir) / "super-assistant-palace"
    return base.expanduser().resolve()


def user_dir_id(owner_id: str) -> str:
    """owner_id → 工作区目录 id：确定性映射，重建/并发路径稳定。"""
    return str(uuid.uuid5(_PALACE_NAMESPACE, str(owner_id)))


def user_workspace(owner_id: str) -> SessionWorkspace:
    # 根目录每次现读 settings：测试经 monkeypatch 切换，禁止缓存单例
    return SessionWorkspace(_root())
