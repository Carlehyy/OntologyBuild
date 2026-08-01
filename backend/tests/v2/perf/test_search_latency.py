"""
搜索延迟基准测试

目标：
- 已退役语义搜索必须在 50ms 内显式拒绝
- StorageService URI 解析 10万次 < 100ms
"""
import time
import pytest

URI_PARSE_THRESHOLD_MS = 100
SEARCH_FALLBACK_THRESHOLD_MS = 50


def test_storage_uri_parse_100k():
    """StorageService._parse_uri 解析 10 万次，总耗时 < 100ms"""
    from app.services.storage_service import StorageService

    uris = [f"s3://raw-datasets/path/to/file_{i}.csv" for i in range(100_000)]

    start = time.perf_counter()
    for uri in uris:
        StorageService._parse_uri(uri)
    elapsed_ms = (time.perf_counter() - start) * 1000

    assert elapsed_ms < URI_PARSE_THRESHOLD_MS, (
        f"URI 解析 100000 次耗时 {elapsed_ms:.1f}ms，超过阈值 {URI_PARSE_THRESHOLD_MS}ms"
    )


def test_retired_semantic_search_response_time():
    """Retired semantic search fails explicitly without external I/O."""
    from fastapi import HTTPException
    from app.data_channel.datasets.search_router import (
        _raise_semantic_search_unsupported,
    )

    start = time.perf_counter()
    with pytest.raises(HTTPException) as raised:
        _raise_semantic_search_unsupported()
    elapsed_ms = (time.perf_counter() - start) * 1000

    assert raised.value.status_code == 501
    assert raised.value.detail["code"] == "semantic_search_unsupported"
    assert elapsed_ms < SEARCH_FALLBACK_THRESHOLD_MS, (
        f"语义搜索退役响应 {elapsed_ms:.1f}ms，超过阈值 {SEARCH_FALLBACK_THRESHOLD_MS}ms"
    )


def test_cron_validate_1k():
    """Cron 表达式验证 1000 次，总耗时 < 100ms"""
    from app.services.v2.scheduler.cron_service import CronService

    expressions = ["0 8 * * *", "*/5 * * * *", "0 0 1 * *", "invalid"] * 250
    svc = CronService()

    start = time.perf_counter()
    for expr in expressions:
        svc.validate_cron(expr)
    elapsed_ms = (time.perf_counter() - start) * 1000

    assert elapsed_ms < 100, f"Cron 验证 1000 次耗时 {elapsed_ms:.1f}ms"
