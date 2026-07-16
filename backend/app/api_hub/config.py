"""API-Hub configuration inside the OntologyBuild backend.

The original environment variable names stay supported so an existing API-Hub
configuration can be copied across without translation. Runtime files live in
``backend/data/api_hub`` by default and never touch the platform database.
"""
import os
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parents[2]
load_dotenv(BACKEND_DIR / ".env")


def _env(key: str, default: str = "") -> str:
    return (os.getenv(key, default) or "").strip()


def _csv_env(key: str, default: str = "") -> tuple[str, ...]:
    return tuple(
        item.strip()
        for item in _env(key, default).split(",")
        if item.strip()
    )


_data_dir = _env("API_HUB_DATA_DIR")
DATA_DIR = Path(_data_dir) if _data_dir else BACKEND_DIR / "data" / "api_hub"
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

# Host and port are informational here: OntologyBuild owns the ASGI server.
APP_HOST = _env("APP_HOST", "0.0.0.0")
APP_PORT = int(os.getenv("DEPLOY_RUN_PORT", "") or _env("APP_PORT", "8000"))
DEFAULT_CRON = _env("API_HUB_DEFAULT_CRON", _env("DEFAULT_CRON", "0 */2 * * *"))
HTTP_TIMEOUT = int(_env("API_HUB_HTTP_TIMEOUT", _env("HTTP_TIMEOUT", "30")))
MAX_RUNS_PER_INTERFACE = int(
    _env("API_HUB_MAX_RUNS_PER_INTERFACE", _env("MAX_RUNS_PER_INTERFACE", "20"))
)
TLS_CA_BUNDLE = _env("API_HUB_TLS_CA_BUNDLE")
OUTBOUND_ALLOWED_HOSTS = _csv_env("API_HUB_OUTBOUND_ALLOWED_HOSTS")
OUTBOUND_MAX_REDIRECTS = max(0, int(_env("API_HUB_OUTBOUND_MAX_REDIRECTS", "5")))

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
