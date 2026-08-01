import ipaddress
from urllib.parse import urlsplit

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.shared.env_files import (
    LEGACY_BACKEND_ENV_FILE,
    LOCAL_CONFIG_ENV_FILE,
)


class Settings(BaseSettings):
    environment: str = "development"
    # Local launch settings are deliberately separate from deployment ports.
    # Docker and production continue to own their Uvicorn command line.
    local_backend_host: str = "127.0.0.1"
    local_backend_port: int = Field(default=8000, ge=1, le=65535)
    local_frontend_host: str = "127.0.0.1"
    local_frontend_port: int = Field(default=5173, ge=1, le=65535)

    # The configuration center provisions the required n8n integration into
    # its database-backed runtime record during startup. LLM providers are
    # intentionally configured later through the model-management UI.
    n8n_api_url: str = ""
    n8n_api_key: str = ""
    n8n_timeout_seconds: int = Field(default=30, ge=3, le=120)

    # SQLite is retained only for the explicit test environment. Every normal
    # application startup validates PostgreSQL before importing the app.
    database_url: str = "sqlite:////tmp/ontoprompt.db"
    redis_url: str = "redis://localhost:6379/0"
    secret_key: str = "dev-secret-key"
    encryption_key: str = ""
    cors_allowed_origins: str = "*"
    first_admin_user: str = "admin"
    first_admin_password: str = "admin123"
    uploads_dir: str = "./uploads"
    access_token_expire_minutes: int = 1440

    # Formal-ontology actions can call an external webhook.  Keep the request
    # budget small because actions execute on the API request/worker thread;
    # the dispatcher retries only transient failures and sends an idempotency
    # key with every attempt.
    formal_action_webhook_timeout_seconds: int = 15
    formal_action_webhook_max_attempts: int = 2
    formal_action_webhook_max_body_bytes: int = 1_000_000

    # Super Assistant is intentionally isolated from ontology/exploration
    # runtimes. Skills are real directories rooted below this path; an empty
    # value resolves to <uploads_dir>/super-assistant/skills.
    super_assistant_skill_root: str = ""
    super_assistant_max_skill_archive_mb: int = 20
    super_assistant_max_skill_files: int = 500
    super_assistant_max_skill_file_mb: int = 5
    super_assistant_max_tool_rounds: int = 8
    super_assistant_tool_result_chars: int = 30000
    super_assistant_approval_timeout_seconds: int = 180
    # stdio launches a process inside the backend container. Keep it opt-in and
    # require an explicit executable allowlist because this is equivalent to
    # granting server-side code execution to assistant configurators.
    super_assistant_mcp_stdio_enabled: bool = False
    super_assistant_mcp_stdio_allowed_commands: str = ""

    # Data-steward conversation workspace and its isolated browser runtime.
    # Empty workspace root resolves to <uploads_dir>/steward-sessions.
    steward_workspace_root: str = ""
    steward_browser_cdp_url: str = "http://localhost:9222"
    steward_browser_timeout_seconds: int = 30
    steward_browser_max_captures: int = 300
    steward_browser_frame_interval_ms: int = 250
    # WebSocket-blocked clients fall back to authenticated HTTP frame polling.
    # A short renewable lease preserves manual-takeover semantics without
    # leaving the Agent paused forever when a tab or network disappears.
    steward_browser_http_lease_seconds: int = 30
    steward_browser_http_frame_interval_ms: int = 500
    # Human input in the shared live browser temporarily takes priority.  The
    # Agent waits for this short quiet window instead of treating an observer
    # connection as a permanent manual takeover.
    steward_browser_user_activity_seconds: int = 3
    # One Chromium process is shared, while every active conversation owns an
    # isolated BrowserContext.  Bound those contexts and suspend idle ones so a
    # user cannot exhaust the sidecar simply by creating conversations.
    steward_browser_max_sessions: int = 8
    steward_browser_max_sessions_per_user: int = 3
    steward_browser_idle_timeout_seconds: int = 900
    steward_browser_reaper_interval_seconds: int = 30
    steward_browser_allow_private_networks: bool = True
    # URL used inside generated n8n workflows to reach this backend.
    steward_proxy_base_url: str = "http://backend:8000/api-hub/proxy"
    steward_internal_proxy_base_url: str = "http://backend:8000/api-hub/internal/interfaces"
    # Header Auth credential already configured in n8n.  The data steward only
    # stores its id/name reference in workflow JSON, never the secret value.
    steward_proxy_credential_name: str = "API Hub Internal Proxy"

    max_upload_mb: int = 200
    allowed_upload_extensions: str = "csv,xlsx,xls,json,xml,pdf,docx,doc,pptx,ppt,md,txt"
    # 事件附件只做安全落盘与下载，不进入文档解析链路，因此默认兼容任意扩展名。
    # 如部署方需要收紧，可通过 EVENT_ATTACHMENT_EXTENSIONS 配置逗号分隔白名单。
    event_attachment_extensions: str = "*"
    # 可选 OfficeCLI 适配器。核心会话空间不依赖它；配置后才向探索 Agent 暴露
    # docx/xlsx/pptx 的结构化增删改工具，避免生产镜像隐式下载第三方二进制。
    exploration_officecli_path: str = ""
    # 数据集版本保留数（每个版本都是全量快照，不清理会 O(N²) 膨胀）；0 = 不清理
    dataset_version_keep: int = 20
    # Immutable DatasetVersion outbox poll interval.  The worker uses database
    # claims, so multiple API replicas may poll safely.
    dataset_event_poll_seconds: int = 2
    dataset_event_claim_timeout_seconds: int = 3600
    dataset_event_batch_size: int = 20
    # Trial materialization runs synchronously but persists its running claim
    # first.  Expired claims are terminalized and can no longer block retry or
    # deletion; late completion is fenced by the per-run claim token.
    ontology_trial_lease_seconds: int = 3600
    # Current transform engine is list-based.  Refuse oversized production
    # inputs explicitly instead of risking process OOM; raise as deployments
    # gain memory or replace with a streaming executor.
    pipeline_max_in_memory_rows: int = 500_000

    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "ontoprompt123"

    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"
    minio_use_ssl: bool = False
    # Retained only so legacy local:// objects can be migrated/read. New writes
    # always require MinIO and never select this directory as a fallback.
    storage_local_dir: str = "storage"

    # n8n never receives long-lived MinIO credentials.  Every invocation gets
    # a short-lived, pipeline-scoped upload token and calls this platform file
    # gateway instead.  The URL must be reachable from the n8n runtime (the
    # Docker service name is the production-compose default).
    pipeline_file_gateway_base_url: str = "http://backend:8000/api/v2/file-transfer"
    # Browser-visible origins are intentionally independent from the n8n file
    # gateway: n8n may use a private network address while people opening a
    # FileRef need public frontend and API addresses.
    pipeline_file_public_app_base_url: str = "http://localhost:5173"
    pipeline_file_public_api_base_url: str = "http://localhost:8000"
    pipeline_file_upload_token_minutes: int = 15
    pipeline_file_max_upload_mb: int = 100
    pipeline_file_preview_retention_hours: int = 24
    pipeline_file_pending_retention_hours: int = 2
    # Opportunistic cleanup also runs during uploads and startup.  This worker
    # bounds physical-object retention even when a quiet deployment receives
    # no new pipeline traffic for a long time.
    pipeline_file_cleanup_interval_seconds: int = 300
    pipeline_file_allowed_extensions: str = (
        "csv,xlsx,xls,json,xml,pdf,docx,doc,pptx,ppt,md,txt,"
        "png,jpg,jpeg,gif,webp,svg,zip,gz,tar"
    )

    # Development can expose self-registration. Production must disable it and
    # provision users through authenticated administrative flows.
    allow_public_registration: bool = True

    model_config = SettingsConfigDict(
        # Later dotenv files override earlier ones.  Real process environment
        # variables still have the highest pydantic-settings priority.
        env_file=(LEGACY_BACKEND_ENV_FILE, LOCAL_CONFIG_ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
    )


def required_dependency_config_errors(current: Settings) -> list[str]:
    """Validate the required runtime stack for every non-test startup."""
    errors: list[str] = []
    try:
        database = urlsplit(current.database_url)
        database.port
    except ValueError:
        database = None
    if (
        database is None
        or database.scheme != "postgresql"
        or not database.hostname
        or not database.username
        or not database.password
        or database.path in {"", "/"}
    ):
        errors.append(
            "DATABASE_URL must use postgresql:// and reference an "
            "authenticated PostgreSQL database"
        )

    try:
        redis_url = urlsplit(current.redis_url)
        redis_url.port
    except ValueError:
        redis_url = None
    if (
        redis_url is None
        or redis_url.scheme not in {"redis", "rediss"}
        or not redis_url.hostname
        or redis_url.port is None
        or not redis_url.password
    ):
        errors.append(
            "REDIS_URL must reference authenticated Redis with an explicit port"
        )

    try:
        neo4j = urlsplit(current.neo4j_uri)
        neo4j.port
    except ValueError:
        neo4j = None
    if (
        neo4j is None
        or neo4j.scheme not in {
            "bolt", "bolt+s", "bolt+ssc", "neo4j", "neo4j+s", "neo4j+ssc"
        }
        or not neo4j.hostname
        or neo4j.port is None
        or not current.neo4j_user
        or not current.neo4j_password
    ):
        errors.append("NEO4J_URI/NEO4J credentials must reference Neo4j")

    raw_minio = str(current.minio_endpoint or "").strip()
    try:
        minio = urlsplit(f"//{raw_minio}")
        minio_port = minio.port
    except ValueError:
        minio = None
        minio_port = None
    if (
        not raw_minio
        or "://" in raw_minio
        or minio is None
        or not minio.hostname
        or minio_port is None
        or minio_port == 9001
        or not current.minio_access_key
        or not current.minio_secret_key
    ):
        errors.append(
            "MINIO_ENDPOINT/MINIO credentials must reference the S3 API, "
            "not the browser console"
        )

    try:
        browser = urlsplit(str(current.steward_browser_cdp_url or "").strip())
        browser.port
    except ValueError:
        browser = None
    if (
        browser is None
        or browser.scheme not in {"http", "https"}
        or not browser.hostname
        or browser.username is not None
        or browser.password is not None
        or browser.query
        or browser.fragment
    ):
        errors.append(
            "STEWARD_BROWSER_CDP_URL must be an absolute HTTP(S) discovery URL"
        )

    try:
        n8n = urlsplit(str(current.n8n_api_url or "").strip())
        n8n.port
    except ValueError:
        n8n = None
    if (
        n8n is None
        or n8n.scheme not in {"http", "https"}
        or not n8n.hostname
        or n8n.username is not None
        or n8n.password is not None
        or n8n.query
        or n8n.fragment
        or not str(current.n8n_api_key or "").strip()
    ):
        errors.append("N8N_API_URL/N8N_API_KEY must configure n8n")
    return errors


def production_config_errors(current: Settings) -> list[str]:
    """Return fail-closed production configuration errors.

    ``ENCRYPTION_KEY`` intentionally remains optional for existing deployments:
    historical ciphertext was encrypted with a Fernet key derived from
    ``SECRET_KEY``. Requiring a new independent key without re-encrypting those
    rows makes every stored connection credential unreadable.
    """
    _insecure = []
    if (
        current.secret_key in {"dev-secret-key", "change-me-to-a-random-32-char-string"}
        or len(current.secret_key) < 32
    ):
        _insecure.append("SECRET_KEY")
    if current.first_admin_password in {"admin123", "change-me"} or len(current.first_admin_password) < 12:
        _insecure.append("FIRST_ADMIN_PASSWORD")
    if current.minio_access_key == "minioadmin" or current.minio_secret_key == "minioadmin":
        _insecure.append("MINIO_ACCESS_KEY/MINIO_SECRET_KEY")
    if "ontoprompt:ontoprompt@" in current.database_url:
        _insecure.append("DATABASE_URL credentials")
    if urlsplit(current.database_url).scheme.lower() != "postgresql":
        _insecure.append("DATABASE_URL must use canonical postgresql://")
    if current.neo4j_password == "ontoprompt123":
        _insecure.append("NEO4J_PASSWORD")
    if current.encryption_key:
        try:
            from cryptography.fernet import Fernet
            Fernet(current.encryption_key.encode())
        except Exception:
            _insecure.append("ENCRYPTION_KEY must be a valid Fernet key")
    origins = [item.strip() for item in current.cors_allowed_origins.split(",") if item.strip()]
    # Empty means same-origin only and is safe. Wildcard remains forbidden.
    if "*" in origins:
        _insecure.append("CORS_ALLOWED_ORIGINS")
    _insecure.extend(required_dependency_config_errors(current))
    for key, value in (
        ("PIPELINE_FILE_PUBLIC_APP_BASE_URL",
         current.pipeline_file_public_app_base_url),
        ("PIPELINE_FILE_PUBLIC_API_BASE_URL",
         current.pipeline_file_public_api_base_url),
    ):
        raw = str(value or "").strip()
        try:
            parsed = urlsplit(raw)
            hostname = parsed.hostname
            # Accessing ``port`` also validates malformed/non-numeric ports.
            parsed.port
        except ValueError:
            parsed = None
            hostname = None
        invalid = (
            parsed is None
            or parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or not hostname
            or parsed.username is not None
            or parsed.password is not None
            or bool(parsed.query)
            or bool(parsed.fragment)
            or parsed.path not in {"", "/"}
            or any(character.isspace() for character in raw)
        )
        if invalid:
            _insecure.append(
                f"{key} must be an absolute HTTP(S) origin without credentials, "
                "path, query, or fragment"
            )
            continue
        is_local = hostname.lower() in {"localhost", "backend"}
        try:
            address = ipaddress.ip_address(hostname)
            is_local = is_local or address.is_loopback or address.is_unspecified
        except ValueError:
            pass
        if is_local:
            _insecure.append(
                f"{key} must use a browser-reachable public host in production"
            )
    if current.allow_public_registration:
        _insecure.append("ALLOW_PUBLIC_REGISTRATION=false")
    return _insecure


settings = Settings()

if settings.environment != "test":
    _dependency_errors = required_dependency_config_errors(settings)
    if _dependency_errors:
        raise RuntimeError(
            "平台必需运行时依赖配置无效: "
            f"{', '.join(_dependency_errors)}"
        )

if settings.environment == "production":
    _insecure = production_config_errors(settings)
    if _insecure:
        raise RuntimeError(
            "ENVIRONMENT=production 检测到不安全或旧版配置: "
            f"{', '.join(_insecure)}"
        )
