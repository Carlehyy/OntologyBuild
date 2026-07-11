from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    environment: str = "development"
    database_url: str = "sqlite:////tmp/ontoprompt.db"
    redis_url: str = "redis://localhost:6379/0"
    secret_key: str = "dev-secret-key"
    encryption_key: str = ""
    cors_allowed_origins: str = "*"
    # Compatibility-first rollout: existing installations may still have the
    # historical example credentials. Enable only after rotating server .env
    # values and, when applicable, re-encrypting stored connector credentials.
    strict_production_config: bool = False
    first_admin_user: str = "admin"
    first_admin_password: str = "admin123"
    uploads_dir: str = "./uploads"
    access_token_expire_minutes: int = 1440

    # Data-steward conversation workspace and its isolated browser runtime.
    # Empty workspace root resolves to <uploads_dir>/steward-sessions.
    steward_workspace_root: str = ""
    steward_browser_cdp_url: str = "http://localhost:9222"
    steward_browser_timeout_seconds: int = 30
    steward_browser_max_captures: int = 300
    steward_browser_frame_interval_ms: int = 250
    steward_browser_allow_private_networks: bool = True
    # URL used inside generated n8n workflows to reach this backend.
    steward_proxy_base_url: str = "http://backend:8000/api-hub/proxy"

    max_upload_mb: int = 200
    allowed_upload_extensions: str = "csv,xlsx,xls,json,xml,pdf,docx,doc,pptx,ppt,md,txt"
    # 数据集版本保留数（每个版本都是全量快照，不清理会 O(N²) 膨胀）；0 = 不清理
    dataset_version_keep: int = 20
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
    # Development/test convenience only.  Production must fail closed when the
    # shared object store is unavailable; per-container local files are not a
    # durable or shared substitute for MinIO.
    storage_local_fallback: bool = True
    # Relative paths are resolved against the backend project root.
    storage_local_dir: str = "storage"

    # Development can expose self-registration. Production must disable it and
    # provision users through authenticated administrative flows.
    allow_public_registration: bool = True

    chroma_host: str = "localhost"
    chroma_port: int = 8001

    model_config = {"env_file": ".env"}

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
        _insecure.append("STORAGE_LOCAL_FALLBACK=false")
    if current.allow_public_registration:
        _insecure.append("ALLOW_PUBLIC_REGISTRATION=false")
    return _insecure


settings = Settings()

if settings.environment == "production":
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
