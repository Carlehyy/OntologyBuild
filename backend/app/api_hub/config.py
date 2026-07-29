"""API-Hub configuration inside the OntologyBuild backend.

The original environment variable names stay supported so an existing API-Hub
configuration can be copied across without translation. Runtime files live in
``backend/data/api_hub`` by default and never touch the platform database.
"""
import os
from pathlib import Path
from urllib.parse import urlsplit

from app.shared.env_files import BACKEND_DIR, load_backend_dotenv

load_backend_dotenv()


def _env(key: str, default: str = "") -> str:
    return (os.getenv(key, default) or "").strip()


def _csv_env(key: str, default: str = "") -> tuple[str, ...]:
    return tuple(
        item.strip()
        for item in _env(key, default).split(",")
        if item.strip()
    )


def _bool_env(key: str, default: bool) -> bool:
    value = _env(key)
    if not value:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


def _resolve_data_dir(raw: str) -> Path:
    if not raw:
        return (BACKEND_DIR / "data" / "api_hub").resolve()
    configured = Path(raw).expanduser()
    return (
        configured
        if configured.is_absolute()
        else BACKEND_DIR / configured
    ).resolve()


DATA_DIR = _resolve_data_dir(_env("API_HUB_DATA_DIR"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "app.db"
SESSION_PATH = DATA_DIR / "w3_session.json"
SESSION_LOCK_PATH = DATA_DIR / "w3_session.lock"

W3_USERNAME = _env("W3_USERNAME")
W3_PASSWORD = os.getenv("W3_PASSWORD", "")
W3_LOGIN_URL = _env(
    "W3_LOGIN_URL",
    "https://login.huawei.com/login1/rest/hwidcenter/login",
)
_default_w3_host = urlsplit(W3_LOGIN_URL).hostname or "login.huawei.com"
W3_LOGIN_ALLOWED_HOSTS = _csv_env(
    "API_HUB_W3_LOGIN_ALLOWED_HOSTS",
    _default_w3_host,
)


def _app_host_and_port() -> tuple[str, int]:
    """Return informational ASGI metadata with local backend precedence."""

    host = _env("LOCAL_BACKEND_HOST", _env("APP_HOST", "0.0.0.0"))
    port = int(
        _env(
            "LOCAL_BACKEND_PORT",
            _env("DEPLOY_RUN_PORT", _env("APP_PORT", "8000")),
        )
    )
    return host, port


# Host and port are informational here: OntologyBuild owns the ASGI server.
APP_HOST, APP_PORT = _app_host_and_port()
DEFAULT_CRON = _env("API_HUB_DEFAULT_CRON", _env("DEFAULT_CRON", "0 */2 * * *"))
HTTP_TIMEOUT = int(_env("API_HUB_HTTP_TIMEOUT", _env("HTTP_TIMEOUT", "30")))
MAX_RUNS_PER_INTERFACE = int(
    _env("API_HUB_MAX_RUNS_PER_INTERFACE", _env("MAX_RUNS_PER_INTERFACE", "20"))
)
TLS_CA_BUNDLE = _env("API_HUB_TLS_CA_BUNDLE")
OUTBOUND_MAX_REDIRECTS = max(0, int(_env("API_HUB_OUTBOUND_MAX_REDIRECTS", "5")))
# Block accidental access to the platform's loopback/private control plane.  A
# deliberate intranet integration can be permitted by its exact hostname/IP;
# W3 targets have their own narrow allowlist below.
OUTBOUND_BLOCK_PRIVATE_NETWORKS = _bool_env(
    "API_HUB_OUTBOUND_BLOCK_PRIVATE_NETWORKS", True
)
OUTBOUND_TRUSTED_HOSTS = _csv_env("API_HUB_OUTBOUND_TRUSTED_HOSTS")
W3_OUTBOUND_TRUSTED_HOSTS = _csv_env(
    "API_HUB_W3_OUTBOUND_TRUSTED_HOSTS",
    ",".join(("his.huawei.com", *W3_LOGIN_ALLOWED_HOSTS)),
)

# Lightweight, single-worker protection until the deployment moves to
# PostgreSQL / multi-worker infrastructure.  Requests fail fast when the
# process is saturated instead of letting threads and SQLite writes pile up.
MAX_INFLIGHT_REQUESTS = max(1, int(_env("API_HUB_MAX_INFLIGHT_REQUESTS", "24")))
REQUEST_QUEUE_TIMEOUT = max(0.0, float(_env("API_HUB_REQUEST_QUEUE_TIMEOUT", "0.25")))
SQLITE_BUSY_TIMEOUT_MS = max(100, int(_env("API_HUB_SQLITE_BUSY_TIMEOUT_MS", "8000")))

# Public HTTP proxy publishing. Management APIs stay under the platform JWT
# boundary; only /proxy/<slug> is public and every call requires a proxy key.
PROXY_PATH = _env("API_HUB_PROXY_PATH", "/proxy") or "/proxy"
if not PROXY_PATH.startswith("/"):
    PROXY_PATH = "/" + PROXY_PATH
PROXY_PATH = PROXY_PATH.rstrip("/") or "/proxy"
PROXY_KEY_HEADER = (
    _env("API_HUB_PROXY_KEY_HEADER", "X-API-Hub-Key") or "X-API-Hub-Key"
)
PROXY_MAX_REQUEST_BYTES = max(
    1,
    int(_env("API_HUB_PROXY_MAX_REQUEST_BYTES", str(10 * 1024 * 1024))),
)

MCP_TOKEN = _env("API_HUB_MCP_TOKEN", _env("MCP_TOKEN"))
SYSTEM_MCP_TOKEN = _env("API_HUB_SYSTEM_MCP_TOKEN", _env("SYSTEM_MCP_TOKEN"))
# n8n only needs invocation authority.  Keep it separate from the system MCP
# management token; fall back during upgrades so existing deployments keep
# working until they rotate to API_HUB_INTERNAL_PROXY_TOKEN.
INTERNAL_PROXY_TOKEN = _env("API_HUB_INTERNAL_PROXY_TOKEN") or SYSTEM_MCP_TOKEN
MCP_PATH = "/api-hub/mcp"
SYSTEM_MCP_PATH = "/api-hub/mcp/system"
MCP_SERVER_NAME = _env("API_HUB_MCP_SERVER_NAME", _env("MCP_SERVER_NAME", "api-hub"))
MCP_MAX_BODY_CHARS = int(
    _env("API_HUB_MCP_MAX_BODY_CHARS", _env("MCP_MAX_BODY_CHARS", "20000"))
)
MCP_ALLOWED_HOSTS = _csv_env(
    "API_HUB_MCP_ALLOWED_HOSTS",
    "localhost:*,127.0.0.1:*,[::1]:*",
)
MCP_ALLOWED_ORIGINS = _csv_env(
    "API_HUB_MCP_ALLOWED_ORIGINS",
    "http://localhost:*,https://localhost:*,http://127.0.0.1:*,https://127.0.0.1:*",
)


def is_w3_configured() -> bool:
    return bool(W3_USERNAME and W3_PASSWORD)


def is_lan_exposed() -> bool:
    return APP_HOST not in ("127.0.0.1", "localhost", "::1")
