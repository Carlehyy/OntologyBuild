from __future__ import annotations

from types import SimpleNamespace

import app.probes as probes
from app.models import ConfigProfile, default_profile


def _profile() -> ConfigProfile:
    payload = default_profile().model_dump()
    payload["postgres"]["password"] = "postgres-password"
    payload["redis"]["password"] = "redis-password"
    payload["neo4j"]["password"] = "neo4j-password"
    payload["minio"]["access_key"] = "minio-access"
    payload["minio"]["secret_key"] = "minio-password"
    payload["n8n"]["api_key"] = "n8n-key"
    payload["llm"]["api_key"] = "llm-key"
    return ConfigProfile.model_validate(payload)


class FakeResponse:
    def __init__(self, payload, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def test_postgres_probe_runs_select_one_and_closes(monkeypatch) -> None:
    events: list[str] = []
    connection_options = {}

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def execute(self, sql):
            events.append(sql)

        def fetchone(self):
            return (1,)

    class Connection:
        def cursor(self):
            return Cursor()

        def close(self):
            events.append("closed")

    monkeypatch.setattr(
        probes.psycopg2,
        "connect",
        lambda **kwargs: (
            connection_options.update(kwargs)
            or events.append(f"{kwargs['host']}:{kwargs['port']}")
            or Connection()
        ),
    )

    message, _ = probes.probe_postgres(_profile())

    assert message == "PostgreSQL 连接正常"
    assert events == ["127.0.0.1:5432", "SELECT 1", "closed"]
    assert connection_options["connect_timeout"] == 6
    assert "statement_timeout=6000" in connection_options["options"]


def test_redis_probe_authenticates_pings_and_closes(monkeypatch) -> None:
    seen = {}

    class Client:
        def __init__(self, **kwargs):
            seen.update(kwargs)

        def ping(self):
            return True

        def close(self):
            seen["closed"] = True

    monkeypatch.setattr(probes.redis_client, "Redis", Client)
    message, _ = probes.probe_redis(_profile())

    assert message == "Redis 连接正常"
    assert seen["password"] == "redis-password"
    assert seen["closed"] is True


def test_neo4j_probe_verifies_connectivity_and_query(monkeypatch) -> None:
    events: list[str] = []

    class Result:
        def single(self):
            return {"ok": 1}

    class Session:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def run(self, query):
            events.append(str(query))
            assert query.timeout == 6
            return Result()

    class Driver:
        def verify_connectivity(self):
            events.append("verified")

        def session(self):
            return Session()

        def close(self):
            events.append("closed")

    monkeypatch.setattr(
        probes.GraphDatabase,
        "driver",
        lambda *_args, **_kwargs: Driver(),
    )
    message, _ = probes.probe_neo4j(_profile())

    assert message == "Neo4j 连接正常"
    assert events == ["verified", "RETURN 1 AS ok", "closed"]


def test_minio_probe_is_read_only_and_reports_bucket(monkeypatch) -> None:
    seen = {}

    class Client:
        def __init__(self, endpoint, **kwargs):
            seen["endpoint"] = endpoint
            seen.update(kwargs)

        def list_buckets(self):
            return [SimpleNamespace(name="ontology-build")]

    monkeypatch.setattr(probes, "Minio", Client)
    message, detail = probes.probe_minio(_profile())

    assert message == "MinIO 连接正常"
    assert "当前可见 1 个桶" in detail
    assert seen["endpoint"] == "127.0.0.1:9000"
    assert seen["secret_key"] == "minio-password"


def test_chroma_probe_supports_v1_fallback(monkeypatch) -> None:
    requested: list[str] = []

    class Client:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def get(self, url, **_kwargs):
            requested.append(url)
            return FakeResponse(
                {},
                404 if url.endswith("/api/v2/heartbeat") else 200,
            )

    monkeypatch.setattr(probes.httpx, "Client", Client)
    message, detail = probes.probe_chroma(_profile())

    assert message == "Chroma 连接正常"
    assert detail.endswith("/api/v1/heartbeat 返回成功")
    assert len(requested) == 2


def test_browser_probe_requires_websocket_debugger_url(monkeypatch) -> None:
    class Client:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def get(self, url):
            assert url == "http://127.0.0.1:9222/json/version"
            return FakeResponse(
                {
                    "Browser": "Chrome/140",
                    "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/browser/id",
                }
            )

    monkeypatch.setattr(probes.httpx, "Client", Client)
    message, detail = probes.probe_browser(_profile())

    assert message == "浏览器控制接口正常"
    assert detail == "已识别 Chrome/140"


def test_n8n_probe_uses_api_key_header(monkeypatch) -> None:
    seen = {}

    class Client:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def get(self, url, *, params, headers):
            seen["url"] = url
            seen["params"] = params
            seen["headers"] = headers
            return FakeResponse({"data": []})

    monkeypatch.setattr(probes.httpx, "Client", Client)
    message, _ = probes.probe_n8n(_profile())

    assert message == "n8n 连接正常"
    assert seen["url"].endswith("/api/v1/workflows")
    assert seen["params"] == {"limit": 1}
    assert seen["headers"]["X-N8N-API-KEY"] == "n8n-key"


def test_openai_compatible_probe_sends_minimal_request(monkeypatch) -> None:
    seen = {}

    class Client:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def post(self, url, *, headers, json):
            seen["url"] = url
            seen["headers"] = headers
            seen["json"] = json
            return FakeResponse({"choices": [{"message": {"content": "P"}}]})

    monkeypatch.setattr(probes.httpx, "Client", Client)
    message, detail = probes.probe_llm(_profile())

    assert message == "默认模型连接正常"
    assert "极少量费用" in detail
    assert seen["url"] == "https://api.openai.com/v1/chat/completions"
    assert seen["headers"]["Authorization"] == "Bearer llm-key"
    assert seen["json"]["max_tokens"] == 1


def test_llm_probe_rejects_unrelated_success_json(monkeypatch) -> None:
    class Client:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def post(self, *_args, **_kwargs):
            return FakeResponse({"status": "ok"})

    monkeypatch.setattr(probes.httpx, "Client", Client)

    try:
        probes.probe_llm(_profile())
    except RuntimeError as exc:
        assert "choices" in str(exc)
    else:
        raise AssertionError("无模型响应内容时不能误判连接成功")
