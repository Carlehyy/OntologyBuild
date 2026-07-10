from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    environment: str = "development"
    database_url: str = "sqlite:////tmp/ontoprompt.db"
    redis_url: str = "redis://localhost:6379/0"
    secret_key: str = "dev-secret-key"
    encryption_key: str = ""
    cors_allowed_origins: str = "*"
    first_admin_user: str = "admin"
    first_admin_password: str = "admin123"
    uploads_dir: str = "./uploads"
    access_token_expire_minutes: int = 1440

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

settings = Settings()

if settings.environment == "production":
    _insecure = []
    if (
        settings.secret_key in {"dev-secret-key", "change-me-to-a-random-32-char-string"}
        or len(settings.secret_key) < 32
    ):
        _insecure.append("SECRET_KEY")
    if settings.first_admin_password in {"admin123", "change-me"} or len(settings.first_admin_password) < 12:
        _insecure.append("FIRST_ADMIN_PASSWORD")
    if settings.minio_access_key == "minioadmin" or settings.minio_secret_key == "minioadmin":
        _insecure.append("MINIO_ACCESS_KEY/MINIO_SECRET_KEY")
    if "ontoprompt:ontoprompt@" in settings.database_url:
        _insecure.append("DATABASE_URL credentials")
    if not settings.database_url.lower().startswith("postgresql"):
        _insecure.append("DATABASE_URL must use PostgreSQL")
    if settings.neo4j_password == "ontoprompt123":
        _insecure.append("NEO4J_PASSWORD")
    if not settings.encryption_key:
        _insecure.append("ENCRYPTION_KEY")
    else:
        try:
            from cryptography.fernet import Fernet
            Fernet(settings.encryption_key.encode())
        except Exception:
            _insecure.append("ENCRYPTION_KEY must be a valid Fernet key")
    origins = [item.strip() for item in settings.cors_allowed_origins.split(",") if item.strip()]
    if not origins or "*" in origins:
        _insecure.append("CORS_ALLOWED_ORIGINS")
    if settings.storage_local_fallback:
        _insecure.append("STORAGE_LOCAL_FALLBACK=false")
    if settings.allow_public_registration:
        _insecure.append("ALLOW_PUBLIC_REGISTRATION=false")
    if _insecure:
        raise RuntimeError(
            f"ENVIRONMENT=production 但以下配置仍为默认值, 必须通过环境变量注入: {', '.join(_insecure)}"
        )
