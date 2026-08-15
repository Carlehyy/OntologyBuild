"""API 性能监控热路径开销基准（信息性门禁）。

监控必须在普通请求路径上保持微秒级开销：进程内聚合一次请求、直方图
分桶与百分位估算都应是纯内存操作。本测试与业务测试分开运行
（pytest --ignore tests/v2/perf 之外单独执行）。
"""
import time

from app.platform.observability.collector import (
    bucket_index,
    percentile_from_buckets,
)

RECORD_100K_THRESHOLD_MS = 400
BUCKET_100K_THRESHOLD_MS = 80
PERCENTILE_1K_THRESHOLD_MS = 20


def test_collector_record_100k():
    """进程内聚合 10 万次请求（含锁与直方图分桶）总耗时 < 400ms。

    等价于单请求约 4µs；真实请求路径上这远小于一次 SQL 往返。
    """
    from app.platform.observability.collector import collector, minute_bucket, utc_now

    now = minute_bucket(utc_now())
    start = time.perf_counter()
    for i in range(100_000):
        collector.record(
            minute_ts=now,
            method="GET",
            route=f"/api/v2/test/{i % 20}",
            status_class="2xx",
            duration_ms=i % 5000,
        )
    elapsed_ms = (time.perf_counter() - start) * 1000
    collector._rollups.clear()
    assert elapsed_ms < RECORD_100K_THRESHOLD_MS, (
        f"聚合 100000 次请求耗时 {elapsed_ms:.1f}ms，"
        f"超过阈值 {RECORD_100K_THRESHOLD_MS}ms"
    )


def test_bucket_index_100k():
    start = time.perf_counter()
    for i in range(100_000):
        bucket_index(i % 60_000)
    elapsed_ms = (time.perf_counter() - start) * 1000
    assert elapsed_ms < BUCKET_100K_THRESHOLD_MS, (
        f"直方图分桶 100000 次耗时 {elapsed_ms:.1f}ms，"
        f"超过阈值 {BUCKET_100K_THRESHOLD_MS}ms"
    )


def test_percentile_from_buckets_1k():
    counts = [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]
    start = time.perf_counter()
    for _ in range(1_000):
        percentile_from_buckets(counts, 95)
    elapsed_ms = (time.perf_counter() - start) * 1000
    assert elapsed_ms < PERCENTILE_1K_THRESHOLD_MS, (
        f"百分位估算 1000 次耗时 {elapsed_ms:.1f}ms，"
        f"超过阈值 {PERCENTILE_1K_THRESHOLD_MS}ms"
    )

