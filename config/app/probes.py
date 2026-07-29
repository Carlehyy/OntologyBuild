from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx
import psycopg2
import redis as redis_client
import urllib3
from minio import Minio
from neo4j import GraphDatabase, Query

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


def probe_chroma(profile: ConfigProfile) -> tuple[str, str]:
    config = profile.chroma
    origin = f"http://{_http_host(config.host)}:{config.port}"
    attempted: list[str] = []
    with httpx.Client(timeout=6, follow_redirects=False) as client:
        for path in ("/api/v2/heartbeat", "/api/v1/heartbeat", "/api/v1/version"):
            attempted.append(path)
            response = client.get(f"{origin}{path}")
            if response.status_code == 200:
                return "Chroma 连接正常", f"健康接口 {path} 返回成功"
            if response.status_code not in {404, 405}:
                response.raise_for_status()
    raise RuntimeError(f"未找到兼容的 Chroma 健康接口: {', '.join(attempted)}")


def probe_browser(profile: ConfigProfile) -> tuple[str, str]:
    url = f"{profile.browser.cdp_url.rstrip('/')}/json/version"
    with httpx.Client(timeout=6, follow_redirects=False) as client:
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
    with httpx.Client(timeout=config.timeout_seconds, follow_redirects=False) as client:
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


def probe_llm(profile: ConfigProfile) -> tuple[str, str]:
    config = profile.llm
    base = config.api_base.rstrip("/")
    with httpx.Client(timeout=30, follow_redirects=False) as client:
        if config.provider == "anthropic":
            response = client.post(
                f"{base}/messages",
                headers={
                    "x-api-key": config.api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": config.model,
                    "max_tokens": 1,
                    "messages": [{"role": "user", "content": "Reply PONG"}],
                },
            )
        else:
            response = client.post(
                f"{base}/chat/completions",
                headers={
                    "Authorization": f"Bearer {config.api_key}",
                    "content-type": "application/json",
                },
                json={
                    "model": config.model,
                    "max_tokens": 1,
                    "messages": [{"role": "user", "content": "Reply PONG"}],
                },
            )
        response.raise_for_status()
        payload: Any = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("模型服务返回格式无法识别")
    if payload.get("error"):
        raise RuntimeError("模型服务返回了错误信息")
    if config.provider == "anthropic":
        content = payload.get("content")
        if not isinstance(content, list) or not content:
            raise RuntimeError("模型服务未返回 Anthropic 消息内容")
    else:
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            raise RuntimeError("模型服务未返回兼容的 choices 内容")
    return "默认模型连接正常", "已完成一次最小输出测试，可能产生极少量费用"


PROBES: dict[str, Callable[[ConfigProfile], tuple[str, str]]] = {
    "postgres": probe_postgres,
    "redis": probe_redis,
    "neo4j": probe_neo4j,
    "minio": probe_minio,
    "chroma": probe_chroma,
    "browser": probe_browser,
    "n8n": probe_n8n,
    "llm": probe_llm,
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
        profile.llm.api_key,
        profile.advanced.w3_password,
        profile.advanced.api_hub_mcp_token,
        profile.advanced.api_hub_system_mcp_token,
        profile.advanced.api_hub_internal_proxy_token,
    ]


def _http_host(host: str) -> str:
    return f"[{host}]" if ":" in host and not host.startswith("[") else host
