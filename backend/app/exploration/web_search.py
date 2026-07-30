"""Compatibility facade for the shared web-search capability."""

from app.shared.web_search import (
    WEB_SEARCH_TOOL,
    WebSearchError,
    parse_bing_results,
    parse_bing_rss,
    parse_duck_results,
    search_context,
    search_web,
)

__all__ = [
    "WEB_SEARCH_TOOL",
    "WebSearchError",
    "parse_bing_results",
    "parse_bing_rss",
    "parse_duck_results",
    "search_context",
    "search_web",
]
