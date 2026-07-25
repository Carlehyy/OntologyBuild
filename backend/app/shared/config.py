import ipaddress
from pathlib import Path
from urllib.parse import urlsplit

from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    environment: str = "development"
    database_url: str = "sqlite:////tmp/ontoprompt.db"
    redis_url: str = "redis://localhost:6379/0"
    # Redis/Celery is optional. Spreadsheet imports run in the API process by
    # default so a plain Uvicorn installation never waits for a broker timeout.
    # Set DATASET_IMPORT_USE_CELERY=true only when a worker is deployed.
    dataset_import_use_celery: bool = False
    secret_key: str = "dev-secret-key"
    encryption_key: str = ""
    cors_allowed_origins: str = "*"
    # Compatibility-first rollout: existing installations may still have the
    # historical example credentials. Enable only after rotating server .env
    # values and, when applicable, re-encrypting stored connector credentials.
    strict_production_config: bool = False
    # Dedicated production deployments can make PostgreSQL, Redis, Neo4j and
    # MinIO mandatory. In this mode startup and deployment fail closed instead
    # of silently selecting SQLite, synchronous jobs or local object storage.
    require_external_dependencies: bool = False
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
    # MinIO remains preferred.  When enabled, local fallback must point to a
    # durable shared volume in production (the production Compose default is
    # /uploads/object-storage, mounted by both backend and Celery).
    storage_local_fallback: bool = True
    # Relative paths are resolved against the backend project root.
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

    chroma_host: str = "localhost"
    chroma_port: int = 8001

    model_config = {"env_file": ".env"}


_LOCAL_DEPENDENCY_HOSTS = {
    "localhost",
    "db",
    "redis",
    "neo4j",
    "minio",
}


def _is_local_dependency_host(hostname: str | None) -> bool:
    if not hostname:
        return True
    host = hostname.strip().lower()
    if host in _LOCAL_DEPENDENCY_HOSTS:
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_loopback or address.is_unspecified


def required_dependency_config_errors(current: Settings) -> list[str]:
    """Return errors that would allow required services to degrade locally."""
    if not current.require_external_dependencies:
        return []

    errors: list[str] = []
    if current.storage_local_fallback:
        errors.append(
            "STORAGE_LOCAL_FALLBACK=false when "
            "REQUIRE_EXTERNAL_DEPENDENCIES=true"
        )
    if not current.dataset_import_use_celery:
        errors.append(
            "DATASET_IMPORT_USE_CELERY=true when "
            "REQUIRE_EXTERNAL_DEPENDENCIES=true"
        )

    try:
        database = urlsplit(current.database_url)
        database.port
    except ValueError:
        database = None
    if (
        database is None
        or database.scheme not in {"postgresql", "postgres"}
        or _is_local_dependency_host(database.hostname)
        or not database.username
        or database.password is None
        or database.path in {"", "/"}
    ):
        errors.append(
            "DATABASE_URL must reference an authenticated external "
            "PostgreSQL database"
        )

    try:
        redis_url = urlsplit(current.redis_url)
        redis_url.port
    except ValueError:
        redis_url = None
    if (
        redis_url is None
        or redis_url.scheme not in {"redis", "rediss"}
        or _is_local_dependency_host(redis_url.hostname)
        or redis_url.password is None
    ):
        errors.append(
            "REDIS_URL must reference an authenticated external Redis"
        )

    try:
        neo4j = urlsplit(current.neo4j_uri)
        neo4j.port
    except ValueError:
        neo4j = None
    if (
        neo4j is None
        or neo4j.scheme not in {"bolt", "bolt+s", "neo4j", "neo4j+s"}
        or _is_local_dependency_host(neo4j.hostname)
        or not current.neo4j_user
        or not current.neo4j_password
    ):
        errors.append(
            "NEO4J_URI/NEO4J credentials must reference an external Neo4j"
        )

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
        or _is_local_dependency_host(minio.hostname)
        or minio_port == 9001
        or not current.minio_access_key
        or not current.minio_secret_key
    ):
        errors.append(
            "MINIO_ENDPOINT/MINIO credentials must reference an external "
            "S3 API endpoint, not the browser console"
        )
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
    if not current.database_url.lower().startswith("postgresql"):
        _insecure.append("DATABASE_URL must use PostgreSQL")
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
    if current.storage_local_fallback:
        raw_local_dir = str(current.storage_local_dir or "").strip()
        local_dir = Path(raw_local_dir).expanduser()
        if not raw_local_dir or not local_dir.is_absolute():
            _insecure.append(
                "STORAGE_LOCAL_DIR must be an absolute persistent path "
                "when STORAGE_LOCAL_FALLBACK=true"
            )
        else:
            resolved = local_dir.resolve()
            temporary_roots = {
                Path("/tmp"),
                Path("/var/tmp"),
                Path("/private/tmp"),
            }
            if resolved == Path("/") or any(
                resolved == root or root in resolved.parents
                for root in temporary_roots
            ):
                _insecure.append(
                    "STORAGE_LOCAL_DIR must use a persistent non-temporary volume"
                )
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

if settings.environment == "production":
    _dependency_errors = required_dependency_config_errors(settings)
    if _dependency_errors:
        raise RuntimeError(
            "ENVIRONMENT=production 第三方依赖强制配置无效: "
            f"{', '.join(_dependency_errors)}"
        )
    _insecure = production_config_errors(settings)
    if _insecure:
        message = (
            "ENVIRONMENT=production 检测到不安全或旧版配置: "
            f"{', '.join(_insecure)}")
        if settings.strict_production_config:
            raise RuntimeError(message)
        import logging
        logging.getLogger(__name__).warning(
            "%s。当前以兼容模式启动；完成服务器 .env 轮换后设置 "
            "STRICT_PRODUCTION_CONFIG=true 可恢复强制门禁。", message)
