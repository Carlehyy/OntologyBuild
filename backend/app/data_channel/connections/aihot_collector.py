"""
AI HOT 数据采集器

从 https://aihot.virxact.com 公开 API 采集真实 AI 资讯数据，
落地为本体的对象实例 (ObjectInstance)。

公开 API：
  GET /api/public/items   资讯条目 (id/title/source/category/score/selected/publishedAt/summary)
  GET /api/public/daily   最新日报

这是平台「数据采集 → 本体建模」全流程的真实数据源。
"""
from __future__ import annotations
import httpx
from typing import Any

BASE_URL = "https://aihot.virxact.com"
# 该站点对默认 UA 返回 403，必须带浏览器 UA
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Accept": "application/json",
}

# AI HOT 分类 slug → 中文
CATEGORY_LABELS = {
    "ai-models": "模型发布/更新",
    "ai-products": "产品发布/更新",
    "industry": "行业动态",
    "paper": "论文研究",
    "tip": "技巧与观点",
}


def fetch_items(mode: str = "selected", category: str | None = None,
                take: int = 50, q: str | None = None) -> dict[str, Any]:
    """拉取资讯条目。返回 {count, hasNext, nextCursor, items[]}"""
    params: dict[str, Any] = {"mode": mode, "take": min(max(take, 1), 100)}
    if category:
        params["category"] = category
    if q and len(q) >= 2:
        params["q"] = q
    from app.shared import perf_spans

    url = f"{BASE_URL}/api/public/items"
    span = perf_spans.begin_span("http", name="GET", target=perf_spans.http_target(url))
    request_status = "error"
    try:
        with httpx.Client(timeout=20, headers=_HEADERS) as client:
            resp = client.get(url, params=params)
            request_status = str(resp.status_code)
            resp.raise_for_status()
            return resp.json()
    finally:
        perf_spans.end_span(span, status=request_status)


def fetch_daily() -> dict[str, Any]:
    """拉取最新日报。"""
    from app.shared import perf_spans

    url = f"{BASE_URL}/api/public/daily"
    span = perf_spans.begin_span("http", name="GET", target=perf_spans.http_target(url))
    request_status = "error"
    try:
        with httpx.Client(timeout=20, headers=_HEADERS) as client:
            resp = client.get(url)
            request_status = str(resp.status_code)
            resp.raise_for_status()
            return resp.json()
    finally:
        perf_spans.end_span(span, status=request_status)


def item_to_properties(item: dict[str, Any]) -> dict[str, Any]:
    """把 AI HOT item 映射为「资讯条目」对象类型的属性。

    属性名与示例本体的 ObjectType.properties 对齐（见 sample_ontology）。
    """
    return {
        "title": item.get("title"),
        "titleEn": item.get("title_en"),
        "url": item.get("url"),
        "permalink": item.get("permalink"),
        "source": item.get("source"),
        "category": item.get("category"),
        "categoryLabel": CATEGORY_LABELS.get(item.get("category") or "", item.get("category")),
        "score": item.get("score"),
        "selected": bool(item.get("selected")),
        "publishedAt": item.get("publishedAt"),
        "summary": item.get("summary"),
    }
