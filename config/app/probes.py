from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass

import httpx
import psycopg2
import redis as redis_client
import urllib3
from minio import Minio
from neo4j import GraphDatabase, Query

from .http_transport import loopback_httpx_mounts
from .models import ConfigProfile


@dataclass(frozen=True)
class ProbeResult:
    ok: bool
    message: str
    detail: str
    duration_ms: int


def run_probe(service: str, profile: ConfigProfile) -> ProbeResult:
    probe = PROBES.get(service)
    if probe is None:
        return ProbeResult(False, "未知的测试项目", "请刷新页面后重试", 0)
    started = time.monotonic()
    try:
        message, detail = probe(profile)
    except Exception as exc:  # noqa: BLE001
        duration_ms = int((time.monotonic() - started) * 1000)
        return ProbeResult(
            ok=False,
            message="连接失败",
            detail=_safe_error(exc, profile),
            duration_ms=duration_ms,
        )
    duration_ms = int((time.monotonic() - started) * 1000)
    return ProbeResult(True, message, detail, duration_ms)


def probe_postgres(profile: ConfigProfile) -> tuple[str, str]:
    config = profile.postgres
    connection = psycopg2.connect(
        host=config.host,
        port=config.port,
        dbname=config.database,
        user=config.username,
        password=config.password,
        sslmode=config.ssl_mode,
        connect_timeout=6,
        options="-c statement_timeout=6000",
    )
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            row = cursor.fetchone()
            if row != (1,):
                raise RuntimeError("数据库没有返回预期结果")
    finally:
        connection.close()
    return "PostgreSQL 连接正常", "已登录数据库并成功执行只读查询"


def probe_redis(profile: ConfigProfile) -> tuple[str, str]:
    config = profile.redis
    client = redis_client.Redis(
        host=config.host,
        port=config.port,
        db=config.database,
        username=config.username or None,
        password=config.password,
        ssl=config.use_tls,
        socket_connect_timeout=6,
        socket_timeout=6,
        decode_responses=True,
    )
    try:
        if client.ping() is not True:
            raise RuntimeError("Redis 没有返回 PONG")
    finally:
        client.close()
    return "Redis 连接正常", "Celery 使用的消息通道已通过 PING 测试"


def probe_neo4j(profile: ConfigProfile) -> tuple[str, str]:
    config = profile.neo4j
    driver = GraphDatabase.driver(
        config.uri,
        auth=(config.username, config.password),
        connection_timeout=6,
        connection_acquisition_timeout=6,
        max_transaction_retry_time=0,
        liveness_check_timeout=6,
    )
    try:
        driver.verify_connectivity()
        with driver.session() as session:
            value = session.run(
                Query("RETURN 1 AS ok", timeout=6)
            ).single()
            if value is None or value["ok"] != 1:
                raise RuntimeError("Neo4j 没有返回预期结果")
    finally:
        driver.close()
    return "Neo4j 连接正常", "已完成身份验证和只读图查询"


def probe_minio(profile: ConfigProfile) -> tuple[str, str]:
    config = profile.minio
    http = urllib3.PoolManager(
        timeout=urllib3.Timeout(connect=6, read=6),
        retries=urllib3.Retry(
            total=0,
            connect=0,
            read=0,
            redirect=0,
            status=0,
        ),
    )
    try:
        client = Minio(
            config.endpoint,
            access_key=config.access_key,
            secret_key=config.secret_key,
            secure=config.secure,
            http_client=http,
        )
        buckets = client.list_buckets()
    finally:
        http.clear()
    return "MinIO 连接正常", f"凭据可读取存储桶列表，当前可见 {len(buckets)} 个桶"


def probe_browser(profile: ConfigProfile) -> tuple[str, str]:
    url = f"{profile.browser.cdp_url.rstrip('/')}/json/version"
    with httpx.Client(
        timeout=6,
        follow_redirects=False,
        mounts=loopback_httpx_mounts(url),
    ) as client:
        response = client.get(url)
        response.raise_for_status()
        payload = response.json()
    websocket_url = payload.get("webSocketDebuggerUrl")
    if not isinstance(websocket_url, str) or not websocket_url.startswith(("ws://", "wss://")):
        raise RuntimeError("CDP 返回中缺少 WebSocket 调试地址")
    browser_name = str(payload.get("Browser") or "Chromium")
    return "浏览器控制接口正常", f"已识别 {browser_name}"


def probe_n8n(profile: ConfigProfile) -> tuple[str, str]:
    config = profile.n8n
    url = f"{config.api_url.rstrip('/')}/api/v1/workflows"
    with httpx.Client(
        timeout=config.timeout_seconds,
        follow_redirects=False,
        mounts=loopback_httpx_mounts(url),
    ) as client:
        response = client.get(
            url,
            params={"limit": 1},
            headers={"X-N8N-API-KEY": config.api_key},
        )
        response.raise_for_status()
        payload = response.json()
    if not isinstance(payload, (dict, list)):
        raise RuntimeError("n8n 返回格式无法识别")
    return "n8n 连接正常", "API Key 可读取工作流列表"


PROBES: dict[str, Callable[[ConfigProfile], tuple[str, str]]] = {
    "postgres": probe_postgres,
    "redis": probe_redis,
    "neo4j": probe_neo4j,
    "minio": probe_minio,
    "browser": probe_browser,
    "n8n": probe_n8n,
}


def _safe_error(exc: Exception, profile: ConfigProfile) -> str:
    raw = str(exc).strip() or exc.__class__.__name__
    for secret in _secret_values(profile):
        if secret:
            raw = raw.replace(secret, "***")
    raw = re.sub(r"://([^/@\s]+)@", "://***@", raw)
    raw = re.sub(r"password\s*=\s*[^,\s]+", "password=***", raw, flags=re.IGNORECASE)
    if len(raw) > 400:
        raw = raw[:397] + "..."
    return raw


def _secret_values(profile: ConfigProfile) -> list[str]:
    return [
        profile.platform.first_admin_password,
        profile.platform.secret_key,
        profile.platform.encryption_key,
        profile.postgres.password,
        profile.redis.password,
        profile.neo4j.password,
        profile.minio.access_key,
        profile.minio.secret_key,
        profile.n8n.api_key,
        profile.advanced.w3_password,
        profile.advanced.api_hub_mcp_token,
        profile.advanced.api_hub_system_mcp_token,
        profile.advanced.api_hub_internal_proxy_token,
    ]
