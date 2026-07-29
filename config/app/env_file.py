from __future__ import annotations

import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

from dotenv import dotenv_values

from .models import (
    AdvancedConfig,
    BrowserConfig,
    ChromaConfig,
    ConfigProfile,
    LlmConfig,
    MinioConfig,
    N8nConfig,
    Neo4jConfig,
    PlatformConfig,
    PostgresConfig,
    RedisConfig,
    default_profile,
)


SECRET_FIELDS: dict[str, tuple[str, str]] = {
    "platform.first_admin_password": ("platform", "first_admin_password"),
    "platform.secret_key": ("platform", "secret_key"),
    "platform.encryption_key": ("platform", "encryption_key"),
    "postgres.password": ("postgres", "password"),
    "redis.password": ("redis", "password"),
    "neo4j.password": ("neo4j", "password"),
    "minio.access_key": ("minio", "access_key"),
    "minio.secret_key": ("minio", "secret_key"),
    "n8n.api_key": ("n8n", "api_key"),
    "llm.api_key": ("llm", "api_key"),
    "advanced.w3_password": ("advanced", "w3_password"),
    "advanced.api_hub_mcp_token": ("advanced", "api_hub_mcp_token"),
    "advanced.api_hub_system_mcp_token": (
        "advanced",
        "api_hub_system_mcp_token",
    ),
    "advanced.api_hub_internal_proxy_token": (
        "advanced",
        "api_hub_internal_proxy_token",
    ),
}


@dataclass(frozen=True)
class WriteResult:
    path: Path
    backup_path: Path | None


class LocalEnvStore:
    def __init__(self, path: Path):
        self.path = path

    @property
    def exists(self) -> bool:
        return self.path.is_file()

    def read_values(self) -> dict[str, str]:
        if not self.exists:
            return {}
        return {
            key: str(value)
            for key, value in dotenv_values(self.path, interpolate=False).items()
            if value is not None
        }

    def load_profile(self) -> ConfigProfile:
        values = self.read_values()
        if not values:
            return default_profile()

        defaults = default_profile()
        database = _parse_database_url(values.get("DATABASE_URL", ""))
        redis = _parse_redis_url(values.get("REDIS_URL", ""))

        return ConfigProfile(
            platform=PlatformConfig(
                backend_host=values.get(
                    "LOCAL_BACKEND_HOST", defaults.platform.backend_host
                ),
                backend_port=_int_value(
                    values, "LOCAL_BACKEND_PORT", defaults.platform.backend_port
                ),
                frontend_host=values.get(
                    "LOCAL_FRONTEND_HOST", defaults.platform.frontend_host
                ),
                frontend_port=_int_value(
                    values, "LOCAL_FRONTEND_PORT", defaults.platform.frontend_port
                ),
                first_admin_user=values.get(
                    "FIRST_ADMIN_USER", defaults.platform.first_admin_user
                ),
                first_admin_password=values.get("FIRST_ADMIN_PASSWORD", ""),
                secret_key=values.get("SECRET_KEY", ""),
                encryption_key=values.get("ENCRYPTION_KEY", ""),
            ),
            postgres=PostgresConfig(
                host=database.get("host", defaults.postgres.host),
                port=int(database.get("port", defaults.postgres.port)),
                database=database.get("database", defaults.postgres.database),
                username=database.get("username", defaults.postgres.username),
                password=database.get("password", ""),
                ssl_mode=database.get("ssl_mode", defaults.postgres.ssl_mode),
            ),
            redis=RedisConfig(
                host=redis.get("host", defaults.redis.host),
                port=int(redis.get("port", defaults.redis.port)),
                database=int(redis.get("database", defaults.redis.database)),
                username=redis.get("username", defaults.redis.username),
                password=redis.get("password", ""),
                use_tls=redis.get("use_tls", defaults.redis.use_tls),
            ),
            neo4j=Neo4jConfig(
                uri=values.get("NEO4J_URI", defaults.neo4j.uri),
                username=values.get("NEO4J_USER", defaults.neo4j.username),
                password=values.get("NEO4J_PASSWORD", ""),
            ),
            minio=MinioConfig(
                endpoint=values.get("MINIO_ENDPOINT", defaults.minio.endpoint),
                access_key=values.get("MINIO_ACCESS_KEY", ""),
                secret_key=values.get("MINIO_SECRET_KEY", ""),
                secure=_bool_value(
                    values, "MINIO_USE_SSL", defaults.minio.secure
                ),
            ),
            chroma=ChromaConfig(
                host=values.get("CHROMA_HOST", defaults.chroma.host),
                port=_int_value(values, "CHROMA_PORT", defaults.chroma.port),
            ),
            browser=BrowserConfig(
                cdp_url=values.get(
                    "STEWARD_BROWSER_CDP_URL", defaults.browser.cdp_url
                )
            ),
            n8n=N8nConfig(
                api_url=values.get("LOCAL_N8N_API_URL", defaults.n8n.api_url),
                api_key=values.get("LOCAL_N8N_API_KEY", ""),
                timeout_seconds=_int_value(
                    values,
                    "LOCAL_N8N_TIMEOUT_SECONDS",
                    defaults.n8n.timeout_seconds,
                ),
            ),
            llm=LlmConfig(
                name=values.get("LOCAL_LLM_NAME", defaults.llm.name),
                provider=values.get("LOCAL_LLM_PROVIDER", defaults.llm.provider),
                api_base=values.get("LOCAL_LLM_API_BASE", defaults.llm.api_base),
                api_key=values.get("LOCAL_LLM_API_KEY", ""),
                model=values.get("LOCAL_LLM_MODEL", defaults.llm.model),
            ),
            advanced=AdvancedConfig(
                uploads_dir=values.get(
                    "UPLOADS_DIR", defaults.advanced.uploads_dir
                ),
                storage_local_dir=values.get(
                    "STORAGE_LOCAL_DIR", defaults.advanced.storage_local_dir
                ),
                api_hub_data_dir=values.get(
                    "API_HUB_DATA_DIR", defaults.advanced.api_hub_data_dir
                ),
                super_assistant_skill_root=values.get(
                    "SUPER_ASSISTANT_SKILL_ROOT",
                    defaults.advanced.super_assistant_skill_root,
                ),
                steward_workspace_root=values.get(
                    "STEWARD_WORKSPACE_ROOT",
                    defaults.advanced.steward_workspace_root,
                ),
                w3_username=values.get("W3_USERNAME", ""),
                w3_password=values.get("W3_PASSWORD", ""),
                w3_login_url=values.get(
                    "W3_LOGIN_URL", defaults.advanced.w3_login_url
                ),
                api_hub_mcp_token=values.get("API_HUB_MCP_TOKEN", ""),
                api_hub_system_mcp_token=values.get(
                    "API_HUB_SYSTEM_MCP_TOKEN", ""
                ),
                api_hub_internal_proxy_token=values.get(
                    "API_HUB_INTERNAL_PROXY_TOKEN", ""
                ),
            ),
        )

    def public_profile(
        self,
    ) -> tuple[ConfigProfile, dict[str, bool], str | None]:
        warning: str | None = None
        try:
            profile = self.load_profile()
        except (UnicodeError, ValueError):
            profile = default_profile()
            warning = (
                "现有本地配置无法安全读取，已加载全新的默认值。"
                "旧密码和密钥不会被沿用，请重新填写全部凭据后生成配置；"
                "原文件会备份为 .env.bak。"
            )
        payload = profile.model_dump()
        present: dict[str, bool] = {}
        for field, (section, key) in SECRET_FIELDS.items():
            present[field] = warning is None and bool(payload[section][key])
            if self.exists and warning is None:
                payload[section][key] = ""
        return ConfigProfile.model_validate(payload), present, warning

    def _existing_profile_payload(self) -> dict[str, object]:
        if not self.exists:
            return {}
        try:
            return self.load_profile().model_dump()
        except (UnicodeError, ValueError):
            # An invalid existing file must never leak partial secrets or make
            # the repair path impossible. The user must re-enter credentials.
            return {}

    def resolve_secrets(self, submitted: ConfigProfile) -> ConfigProfile:
        payload = submitted.model_dump()
        existing = self._existing_profile_payload()
        missing: list[str] = []

        for field, (section, key) in SECRET_FIELDS.items():
            if payload[section][key]:
                continue
            old_value = existing.get(section, {}).get(key, "")
            if old_value:
                payload[section][key] = old_value
                continue
            if field == "advanced.w3_password" and not payload["advanced"]["w3_username"]:
                continue
            missing.append(field)

        if missing:
            labels = ", ".join(missing)
            raise ValueError(f"以下完整功能凭据尚未填写: {labels}")
        return ConfigProfile.model_validate(payload)

    def resolve_service_secrets(
        self,
        submitted: ConfigProfile,
        service: str,
    ) -> ConfigProfile:
        """Resolve only credentials required by one connectivity probe."""
        payload = submitted.model_dump()
        existing = self._existing_profile_payload()
        missing: list[str] = []

        for field, (section, key) in SECRET_FIELDS.items():
            if section != service:
                continue
            if payload[section][key]:
                continue
            old_value = existing.get(section, {}).get(key, "")
            if old_value:
                payload[section][key] = old_value
            else:
                missing.append(field)

        if missing:
            raise ValueError(
                "当前测试所需凭据尚未填写: " + ", ".join(missing)
            )
        return ConfigProfile.model_validate(payload)

    def write(self, profile: ConfigProfile) -> WriteResult:
        resolved = self.resolve_secrets(profile)
        content = render_env(resolved)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        backup: Path | None = None

        if self.path.exists():
            backup = self.path.with_name(".env.bak")
            shutil.copy2(self.path, backup)
            _restrict_permissions(backup)

        file_descriptor, temporary_name = tempfile.mkstemp(
            prefix=".env.",
            suffix=".tmp",
            dir=self.path.parent,
            text=True,
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(file_descriptor, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            _restrict_permissions(temporary_path)
            os.replace(temporary_path, self.path)
            _restrict_permissions(self.path)
        except Exception:
            temporary_path.unlink(missing_ok=True)
            raise

        return WriteResult(path=self.path, backup_path=backup)


def render_env(profile: ConfigProfile) -> str:
    pg = profile.postgres
    redis = profile.redis
    backend_origin = (
        f"http://{_origin_host(profile.platform.backend_host)}:"
        f"{profile.platform.backend_port}"
    )
    frontend_origin = (
        f"http://{_origin_host(profile.platform.frontend_host)}:"
        f"{profile.platform.frontend_port}"
    )
    database_url = (
        "postgresql+psycopg2://"
        f"{quote(pg.username, safe='')}:{quote(pg.password, safe='')}@"
        f"{_url_host(pg.host)}:{pg.port}/{quote(pg.database, safe='')}"
        f"?sslmode={quote(pg.ssl_mode, safe='')}"
    )
    redis_scheme = "rediss" if redis.use_tls else "redis"
    redis_userinfo = (
        f"{quote(redis.username, safe='')}:{quote(redis.password, safe='')}"
        if redis.username
        else f":{quote(redis.password, safe='')}"
    )
    redis_url = (
        f"{redis_scheme}://{redis_userinfo}@{_url_host(redis.host)}:"
        f"{redis.port}/{redis.database}"
    )

    sections: list[tuple[str, list[tuple[str, object]]]] = [
        (
            "本地配置中心标记。生产环境不会生成或依赖此文件",
            [
                ("LOCAL_CONFIG_SCHEMA_VERSION", "1"),
                ("LOCAL_CONFIG_MANAGED", True),
                ("ENVIRONMENT", "development"),
                ("STRICT_PRODUCTION_CONFIG", False),
                ("REQUIRE_EXTERNAL_DEPENDENCIES", True),
            ],
        ),
        (
            "前后端本地监听与代理。只监听本机，避免开发服务暴露到局域网",
            [
                ("LOCAL_BACKEND_HOST", profile.platform.backend_host),
                ("LOCAL_BACKEND_PORT", profile.platform.backend_port),
                ("LOCAL_FRONTEND_HOST", profile.platform.frontend_host),
                ("LOCAL_FRONTEND_PORT", profile.platform.frontend_port),
                ("APP_HOST", profile.platform.backend_host),
                ("APP_PORT", profile.platform.backend_port),
                ("VITE_API_PROXY_TARGET", backend_origin),
            ],
        ),
        (
            "平台身份与加密。已有数据库使用后不要随意更换密钥",
            [
                ("SECRET_KEY", profile.platform.secret_key),
                ("ENCRYPTION_KEY", profile.platform.encryption_key),
                ("FIRST_ADMIN_USER", profile.platform.first_admin_user),
                ("FIRST_ADMIN_PASSWORD", profile.platform.first_admin_password),
                ("ACCESS_TOKEN_EXPIRE_MINUTES", 1440),
                ("CORS_ALLOWED_ORIGINS", ""),
                ("ALLOW_PUBLIC_REGISTRATION", True),
            ],
        ),
        (
            "PostgreSQL。完整本地模式不允许回退到 SQLite",
            [("DATABASE_URL", database_url)],
        ),
        (
            "Redis 与 Celery。请另外启动 Celery worker",
            [
                ("REDIS_URL", redis_url),
                ("DATASET_IMPORT_USE_CELERY", True),
            ],
        ),
        (
            "Neo4j 图数据库",
            [
                ("NEO4J_URI", profile.neo4j.uri),
                ("NEO4J_USER", profile.neo4j.username),
                ("NEO4J_PASSWORD", profile.neo4j.password),
            ],
        ),
        (
            "MinIO 对象存储。9000 通常是 API 端口，9001 通常是管理页面",
            [
                ("MINIO_ENDPOINT", profile.minio.endpoint),
                ("MINIO_ACCESS_KEY", profile.minio.access_key),
                ("MINIO_SECRET_KEY", profile.minio.secret_key),
                ("MINIO_USE_SSL", profile.minio.secure),
                ("STORAGE_LOCAL_FALLBACK", False),
                ("STORAGE_LOCAL_DIR", profile.advanced.storage_local_dir),
            ],
        ),
        (
            "Chroma 向量数据库",
            [
                ("CHROMA_HOST", profile.chroma.host),
                ("CHROMA_PORT", profile.chroma.port),
            ],
        ),
        (
            "数据管家浏览器与项目相对数据目录",
            [
                ("STEWARD_BROWSER_CDP_URL", profile.browser.cdp_url),
                ("STEWARD_BROWSER_ALLOW_PRIVATE_NETWORKS", True),
                ("STEWARD_WORKSPACE_ROOT", profile.advanced.steward_workspace_root),
                ("UPLOADS_DIR", profile.advanced.uploads_dir),
                (
                    "SUPER_ASSISTANT_SKILL_ROOT",
                    profile.advanced.super_assistant_skill_root,
                ),
            ],
        ),
        (
            "本地托管的 n8n 配置。后端启动后加密写入平台数据库",
            [
                ("LOCAL_N8N_API_URL", profile.n8n.api_url),
                ("LOCAL_N8N_API_KEY", profile.n8n.api_key),
                ("LOCAL_N8N_TIMEOUT_SECONDS", profile.n8n.timeout_seconds),
            ],
        ),
        (
            "本地托管的默认模型。后端启动后加密写入平台数据库",
            [
                ("LOCAL_LLM_NAME", profile.llm.name),
                ("LOCAL_LLM_PROVIDER", profile.llm.provider),
                ("LOCAL_LLM_API_BASE", profile.llm.api_base),
                ("LOCAL_LLM_API_KEY", profile.llm.api_key),
                ("LOCAL_LLM_MODEL", profile.llm.model),
            ],
        ),
        (
            "API Hub 本地数据与独立权限令牌",
            [
                ("API_HUB_DATA_DIR", profile.advanced.api_hub_data_dir),
                ("API_HUB_MCP_TOKEN", profile.advanced.api_hub_mcp_token),
                (
                    "API_HUB_SYSTEM_MCP_TOKEN",
                    profile.advanced.api_hub_system_mcp_token,
                ),
                (
                    "API_HUB_INTERNAL_PROXY_TOKEN",
                    profile.advanced.api_hub_internal_proxy_token,
                ),
                ("API_HUB_MCP_ALLOWED_HOSTS", "localhost:*,127.0.0.1:*"),
                (
                    "API_HUB_MCP_ALLOWED_ORIGINS",
                    "http://localhost:*,http://127.0.0.1:*",
                ),
            ],
        ),
        (
            "可选 W3 登录。留空账号时不会启用",
            [
                ("W3_USERNAME", profile.advanced.w3_username),
                ("W3_PASSWORD", profile.advanced.w3_password),
                ("W3_LOGIN_URL", profile.advanced.w3_login_url),
            ],
        ),
        (
            "n8n 文件网关和浏览器可见地址",
            [
                (
                    "PIPELINE_FILE_GATEWAY_BASE_URL",
                    f"{backend_origin}/api/v2/file-transfer",
                ),
                ("PIPELINE_FILE_PUBLIC_APP_BASE_URL", frontend_origin),
                ("PIPELINE_FILE_PUBLIC_API_BASE_URL", backend_origin),
                ("STEWARD_PROXY_BASE_URL", f"{backend_origin}/api-hub/proxy"),
                (
                    "STEWARD_INTERNAL_PROXY_BASE_URL",
                    f"{backend_origin}/api-hub/internal/interfaces",
                ),
            ],
        ),
    ]

    output = [
        "# 由 OpenOntology 本地配置中心生成，请通过 config/start.bat 或 start.sh 修改",
        "# 文件编码为 UTF-8，配置值不会同步到 GitHub",
        "",
    ]
    for comment, items in sections:
        output.append(f"# {comment}")
        for key, value in items:
            output.append(f"{key}={_dotenv_value(value)}")
        output.append("")
    return "\n".join(output).rstrip() + "\n"


def _dotenv_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    raw = str(value)
    if "\x00" in raw or "\n" in raw or "\r" in raw:
        raise ValueError("配置值不能包含换行或空字符")
    if "${" in raw:
        raise ValueError("配置值不能包含 ${，以免环境变量解析产生歧义")
    return json.dumps(raw, ensure_ascii=False)


def _parse_database_url(value: str) -> dict[str, object]:
    if not value:
        return {}
    parsed = urlsplit(value)
    if not parsed.scheme.startswith("postgresql") and parsed.scheme != "postgres":
        return {}
    query = {}
    for item in parsed.query.split("&"):
        key, separator, raw = item.partition("=")
        if separator:
            query[key] = unquote(raw)
    return {
        "host": parsed.hostname or "",
        "port": parsed.port or 5432,
        "database": unquote(parsed.path.lstrip("/")),
        "username": unquote(parsed.username or ""),
        "password": unquote(parsed.password or ""),
        "ssl_mode": query.get("sslmode", "prefer"),
    }


def _parse_redis_url(value: str) -> dict[str, object]:
    if not value:
        return {}
    parsed = urlsplit(value)
    if parsed.scheme not in {"redis", "rediss"}:
        return {}
    try:
        database = int(parsed.path.lstrip("/") or 0)
    except ValueError:
        database = 0
    return {
        "host": parsed.hostname or "",
        "port": parsed.port or 6379,
        "database": database,
        "username": unquote(parsed.username or ""),
        "password": unquote(parsed.password or ""),
        "use_tls": parsed.scheme == "rediss",
    }


def _int_value(values: dict[str, str], key: str, default: int) -> int:
    try:
        return int(values.get(key, str(default)))
    except (TypeError, ValueError):
        return default


def _bool_value(values: dict[str, str], key: str, default: bool) -> bool:
    raw = values.get(key)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _url_host(host: str) -> str:
    return f"[{host}]" if ":" in host and not host.startswith("[") else host


def _origin_host(host: str) -> str:
    return _url_host(host)


def _restrict_permissions(path: Path) -> None:
    if os.name != "nt":
        path.chmod(0o600)
