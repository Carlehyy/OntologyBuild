from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    environment: str = "development"
    database_url: str = "sqlite:////tmp/ontoprompt.db"
    redis_url: str = "redis://localhost:6379/0"
    secret_key: str = "dev-secret-key"
    encryption_key: str = ""
    first_admin_user: str = "admin"
    first_admin_password: str = "admin123"
    uploads_dir: str = "./uploads"
    access_token_expire_minutes: int = 1440

    max_upload_mb: int = 200
    allowed_upload_extensions: str = "csv,xlsx,xls,json,xml,pdf,docx,doc,pptx,ppt,md,txt"
    # 数据集版本保留数（每个版本都是全量快照，不清理会 O(N²) 膨胀）；0 = 不清理
    dataset_version_keep: int = 20

    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "ontoprompt123"

    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"
    minio_use_ssl: bool = False

    chroma_host: str = "localhost"
    chroma_port: int = 8001

    model_config = {"env_file": ".env"}

settings = Settings()

if settings.environment == "production":
    _insecure = []
    if settings.secret_key == "dev-secret-key":
        _insecure.append("SECRET_KEY")
    if settings.first_admin_password == "admin123":
        _insecure.append("FIRST_ADMIN_PASSWORD")
    if settings.minio_access_key == "minioadmin" or settings.minio_secret_key == "minioadmin":
        _insecure.append("MINIO_ACCESS_KEY/MINIO_SECRET_KEY")
    if not settings.encryption_key:
        _insecure.append("ENCRYPTION_KEY")
    if _insecure:
        raise RuntimeError(
            f"ENVIRONMENT=production 但以下配置仍为默认值, 必须通过环境变量注入: {', '.join(_insecure)}"
        )
