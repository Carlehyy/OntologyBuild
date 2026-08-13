"""工具权限规则：逗号分隔的工具名 glob（fnmatch 语义），deny 优先。

规则示例：``mcp__minio__delete_*``、``web_*``、``*``。deny 命中即拒；
allow 非空时未命中即拒；两者全空默认放行。匹配大小写敏感
（fnmatchcase），与操作系统无关。
"""
from __future__ import annotations

from fnmatch import fnmatchcase

from app.shared.config import settings


class ToolPermissionChecker:
    """按 allow/deny 两组逗号分隔 glob 判定工具是否可执行。"""

    def __init__(self, allow_csv: str = "", deny_csv: str = "") -> None:
        self.allow = self._parse(allow_csv)
        self.deny = self._parse(deny_csv)

    @staticmethod
    def _parse(csv: str) -> list[str]:
        return [item.strip() for item in (csv or "").split(",") if item.strip()]

    @classmethod
    def from_settings(cls) -> "ToolPermissionChecker":
        return cls(settings.super_assistant_tool_allow, settings.super_assistant_tool_deny)

    def is_allowed(self, tool_name: str) -> bool:
        if any(fnmatchcase(tool_name, pattern) for pattern in self.deny):
            return False
        if self.allow and not any(fnmatchcase(tool_name, pattern) for pattern in self.allow):
            return False
        return True
