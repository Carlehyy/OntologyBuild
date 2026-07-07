"""
AI HOT Connector — 把 https://aihot.virxact.com 公开 API 接入数据流水线

让 AI HOT 作为一等数据源进入「连接 → 数据集 → 转换 → 治理 → 映射 → 正规本体」
全流程，与 file / sql / mongo / rest 等连接器并列。

config 示例:
{
    "mode": "selected",       # selected | all | latest 等 AI HOT mode
    "category": "ai-models",  # 可选分类 slug
    "take": 100,              # 每个资源拉取条数 (1~100)
    "q": "GPT"                # 可选搜索关键词 (>=2 字符)
}

资源 (list_resources) 固定两个端点：
  - items   资讯条目
  - daily   最新日报

复用 app.services.collectors.aihot 的真实抓取逻辑（含浏览器 UA，绕过 403）。
"""
from __future__ import annotations

import logging
from typing import Any

from app.services.connection.base import ConnectorBase
from app.services.collectors import aihot

logger = logging.getLogger(__name__)


class AihotConnector(ConnectorBase):
    """AI HOT 公开 API 连接器。"""

    RESOURCES = ["items", "daily"]

    def __init__(self, config: dict | None = None):
        self._config = config or {}

    # ── 配置解析 ───────────────────────────────────────────────
    def _mode(self) -> str:
        return self._config.get("mode") or "selected"

    def _category(self) -> str | None:
        return self._config.get("category") or None

    def _take(self) -> int:
        try:
            return min(max(int(self._config.get("take", 50)), 1), 100)
        except (ValueError, TypeError):
            return 50

    def _q(self) -> str | None:
        q = self._config.get("q")
        return q if q and len(str(q)) >= 2 else None

    # ── ConnectorBase 接口 ────────────────────────────────────
    def test_connection(self) -> bool:
        """连接测试 — 拉一条 items 验证可达性 / UA 是否被接受。"""
        try:
            data = aihot.fetch_items(mode=self._mode(), category=self._category(),
                                     take=1, q=self._q())
            return isinstance(data, dict)
        except Exception as e:
            logger.warning(f"AI HOT 连接测试失败: {e}")
            return False

    def list_resources(self) -> list[str]:
        return list(self.RESOURCES)

    def pull_sample(self, resource: str, limit: int = 100) -> list[dict]:
        return self._pull(resource, limit=limit)

    def pull_full(self, resource: str) -> list[dict]:
        return self._pull(resource, limit=self._take())

    # ── 内部抓取 + 扁平化 ──────────────────────────────────────
    def _pull(self, resource: str, limit: int = 100) -> list[dict]:
        resource = (resource or "items").strip().lower()
        try:
            if resource == "daily":
                return self._daily_rows()
            return self._item_rows(limit=limit)
        except Exception as e:
            logger.warning(f"AI HOT pull '{resource}' 失败: {e}")
            return []

    def _item_rows(self, limit: int = 100) -> list[dict]:
        take = min(max(limit, 1), 100) if limit else self._take()
        data = aihot.fetch_items(mode=self._mode(), category=self._category(),
                                 take=take, q=self._q())
        items = data.get("items", []) if isinstance(data, dict) else []
        rows: list[dict] = []
        for it in items:
            props = aihot.item_to_properties(it)
            # 保留稳定外部 id，供下游做主键 / 去重
            props["id"] = it.get("id") or it.get("permalink") or it.get("url")
            rows.append({k: v for k, v in props.items() if v is not None})
        return rows[:take]

    def _daily_rows(self) -> list[dict]:
        data = aihot.fetch_daily()
        if not isinstance(data, dict):
            return []
        # daily 可能是单条日报，也可能含 items；统一扁平化为行
        if isinstance(data.get("items"), list):
            rows = []
            for it in data["items"]:
                props = aihot.item_to_properties(it)
                props["id"] = it.get("id") or it.get("permalink") or it.get("url")
                rows.append({k: v for k, v in props.items() if v is not None})
            return rows
        # 单条日报 → 单行
        flat = {k: v for k, v in data.items() if not isinstance(v, (list, dict))}
        if flat:
            flat.setdefault("id", data.get("date") or data.get("id") or "daily")
            return [flat]
        return []
