# tests/v2/infra/test_health.py
"""
Infrastructure health-check tests for the /health endpoint.

These tests run against the FastAPI TestClient (no real Docker services
needed). External services (Neo4j, MinIO, ChromaDB) will be reported as
"unavailable" in a pure-unit context — that is the expected, safe fallback.
"""
from fastapi.testclient import TestClient
from app.main import app, _probe_http_service

client = TestClient(app)

VALID_DB_STATES = ("ok", "error", "unknown")
VALID_SERVICE_STATES = ("ok", "unavailable", "unknown")


def test_liveness_endpoint_is_dependency_independent():
    response = client.get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_endpoint_returns_200():
    response = client.get("/health")
    assert response.status_code == 200


def test_health_endpoint_returns_ok():
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert "status" in data
    assert "db" in data


def test_health_db_key_present():
    response = client.get("/health")
    data = response.json()
    assert data["db"] in VALID_DB_STATES


def test_health_all_service_keys_present():
    """All four service keys must be present in the response."""
    response = client.get("/health")
    data = response.json()
    for key in (
        "db", "redis", "neo4j", "minio", "chroma",
        "sentinel_scheduler", "data_scheduler",
        "ontology_projection",
    ):
        assert key in data, f"Missing key: {key}"


def test_health_service_states_are_valid():
    """Each service reports a known state string."""
    response = client.get("/health")
    data = response.json()
    assert data["db"] in VALID_DB_STATES
    for key in (
        "redis", "neo4j", "minio", "chroma",
        "sentinel_scheduler", "data_scheduler",
        "ontology_projection",
    ):
        assert data[key] in VALID_SERVICE_STATES, (
            f"{key} has unexpected state: {data[key]}"
        )


def test_health_status_reflects_dependencies():
    """Readiness may be degraded in development; liveness is a separate route."""
    response = client.get("/health")
    data = response.json()
    assert data["status"] in ("ok", "degraded")
    assert data["status"] == ("ok" if not data["unavailable"] else "degraded")


def test_production_readiness_fails_closed(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "environment", "production")
    response = client.get("/api/health")
    data = response.json()
    if data["unavailable"]:
        assert response.status_code == 503
        assert data["status"] == "error"
    else:
        assert response.status_code == 200
        assert data["status"] == "ok"


def test_http_probe_closes_every_response(monkeypatch):
    """Regression: repeated readiness probes must release every HTTP socket."""
    responses = []

    class FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.closed = True

        def read(self, _limit):
            return b'{"ok": true}'

    def fake_urlopen(request, timeout):
        response = FakeResponse()
        response.closed = False
        response.request = request
        response.timeout = timeout
        responses.append(response)
        return response

    monkeypatch.setattr("app.main.urllib.request.urlopen", fake_urlopen)

    for _ in range(1100):
        _probe_http_service("http://chroma:8000/api/v1/heartbeat")

    assert len(responses) == 1100
    assert all(response.closed for response in responses)
    assert all(response.timeout == 3.0 for response in responses)
    assert all(response.request.get_header("Connection") == "close"
               for response in responses)
