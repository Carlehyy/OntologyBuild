"""Fail-closed production configuration and deployment gates."""

import os
from pathlib import Path
import shutil
import stat
import subprocess
import tomllib

import pytest
import yaml

from app.shared.config import Settings, production_config_errors
from app.settings.workflows.n8n_client import enforce_n8n_url_policy


ROOT = Path(__file__).resolve().parents[4]


def _production_settings(**updates):
    values = {
        "environment": "production",
        "database_url": "postgresql://app:strong-password@db:5432/app",
        "secret_key": "0123456789abcdef0123456789abcdef",
        "encryption_key": "",
        "cors_allowed_origins": "",
        "first_admin_password": "strong-admin-password",
        "redis_url": "redis://:strong-password@redis:6379/0",
        "neo4j_uri": "bolt://neo4j:7687",
        "neo4j_password": "strong-neo4j-password",
        "minio_endpoint": "minio:9000",
        "minio_access_key": "ontology-minio",
        "minio_secret_key": "strong-minio-password",
        "steward_browser_cdp_url": "http://browser:9222",
        "n8n_api_url": "https://n8n.example.com/api/v1",
        "n8n_api_key": "strong-n8n-api-key",
        "pipeline_file_public_app_base_url": "https://platform.example.com",
        "pipeline_file_public_api_base_url": "https://api.example.com",
        "allow_public_registration": False,
    }
    values.update(updates)
    return Settings(**values)


def test_existing_production_can_keep_secret_key_derived_encryption():
    assert production_config_errors(_production_settings()) == []


@pytest.mark.parametrize(
    "scheme",
    ["postgres", "postgresql+psycopg2"],
)
def test_production_rejects_noncanonical_postgres_schemes(scheme):
    settings = _production_settings(
        database_url=f"{scheme}://app:strong-password@db:5432/app"
    )

    assert any(
        "canonical postgresql://" in error
        for error in production_config_errors(settings)
    )


def test_explicit_encryption_key_must_still_be_valid_fernet():
    errors = production_config_errors(
        _production_settings(encryption_key="not-a-fernet-key"))
    assert "ENCRYPTION_KEY must be a valid Fernet key" in errors


def test_wildcard_cors_remains_blocked_but_empty_is_same_origin():
    errors = production_config_errors(
        _production_settings(cors_allowed_origins="*"))
    assert "CORS_ALLOWED_ORIGINS" in errors


def test_production_rejects_non_public_or_malformed_file_link_origins():
    defaults = production_config_errors(_production_settings(
        pipeline_file_public_app_base_url="http://localhost:5173",
        pipeline_file_public_api_base_url="http://127.0.0.1:8000",
    ))
    malformed = production_config_errors(_production_settings(
        pipeline_file_public_app_base_url="https://user@example.com",
        pipeline_file_public_api_base_url="https://api.example.com/files?x=1",
    ))
    with_path = production_config_errors(_production_settings(
        pipeline_file_public_app_base_url="https://platform.example.com/app",
    ))

    assert sum("browser-reachable public host" in error
               for error in defaults) == 2
    assert any("APP_BASE_URL must be an absolute" in error
               for error in malformed)
    assert any("API_BASE_URL must be an absolute" in error
               for error in malformed)
    assert any("APP_BASE_URL must be an absolute" in error
               for error in with_path)


def test_production_compose_requires_real_stack_without_chroma_or_fallbacks():
    compose = yaml.safe_load((ROOT / "docker-compose.prod.yml").read_text())
    backend = compose["services"]["backend"]
    worker = compose["services"]["celery_worker"]

    assert "chromadb" not in compose["services"]
    assert "chroma_data" not in compose["volumes"]
    for service in (backend, worker):
        environment = service["environment"]
        assert "STORAGE_LOCAL_FALLBACK" not in environment
        assert "DATASET_IMPORT_USE_CELERY" not in environment
        assert "REQUIRE_EXTERNAL_DEPENDENCIES" not in environment
        assert "STRICT_PRODUCTION_CONFIG" not in environment
    assert backend["depends_on"]["minio"]["condition"] == "service_healthy"
    assert backend["depends_on"]["browser"]["condition"] == "service_started"
    assert "webSocketDebuggerUrl" in compose["services"]["browser"][
        "healthcheck"
    ]["test"][-1]
    assert backend["environment"]["STORAGE_LOCAL_DIR"] == "/uploads/object-storage"
    assert worker["environment"]["STORAGE_LOCAL_DIR"] == "/uploads/object-storage"
    assert worker["environment"]["STEWARD_BROWSER_CDP_URL"] == (
        "http://browser:9222"
    )
    assert backend["environment"]["N8N_TIMEOUT_SECONDS"] == (
        "${N8N_TIMEOUT_SECONDS:-30}"
    )
    assert worker["environment"]["N8N_TIMEOUT_SECONDS"] == (
        "${N8N_TIMEOUT_SECONDS:-30}"
    )
    assert "--requirepass" in compose["services"]["redis"]["command"][-1]
    assert "PIPELINE_FILE_PUBLIC_APP_BASE_URL" in backend["environment"]
    assert "PIPELINE_FILE_PUBLIC_API_BASE_URL" in backend["environment"]
    assert "PIPELINE_FILE_PUBLIC_APP_BASE_URL" in worker["environment"]
    assert "PIPELINE_FILE_PUBLIC_API_BASE_URL" in worker["environment"]
    assert "uploads:/uploads" in backend["volumes"]
    assert "uploads:/uploads" in worker["volumes"]


def test_removed_graph_fallback_dependencies_are_not_packaged():
    project = tomllib.loads(
        (ROOT / "backend" / "pyproject.toml").read_text(encoding="utf-8")
    )
    dependencies = project["project"]["dependencies"]

    assert not any(item.startswith("chromadb") for item in dependencies)
    assert not any(item.startswith("networkx") for item in dependencies)
    assert not (
        ROOT / "backend" / "app" / "ontologies" / "graph"
        / "networkx_service.py"
    ).exists()
    assert not (
        ROOT / "backend" / "app" / "shared" / "chroma_service.py"
    ).exists()


def test_local_compose_requires_real_stack_without_chroma():
    compose = yaml.safe_load((ROOT / "docker-compose.local.yml").read_text())
    backend = compose["services"]["backend"]
    worker = compose["services"]["celery_worker"]
    migrate = compose["services"]["migrate"]

    assert "chromadb" not in compose["services"]
    assert "chroma_data" not in compose["volumes"]
    for dependency in ("db", "redis", "neo4j", "minio"):
        assert backend["depends_on"][dependency]["condition"] == (
            "service_healthy"
        )
        assert worker["depends_on"][dependency]["condition"] == (
            "service_healthy"
        )
    assert backend["depends_on"]["browser"]["condition"] == "service_started"
    assert migrate["command"] == "alembic upgrade head"
    assert migrate["depends_on"]["db"]["condition"] == "service_healthy"
    assert backend["depends_on"]["migrate"]["condition"] == (
        "service_completed_successfully"
    )
    assert worker["depends_on"]["migrate"]["condition"] == (
        "service_completed_successfully"
    )
    assert "webSocketDebuggerUrl" in compose["services"]["browser"][
        "healthcheck"
    ]["test"][-1]
    assert "--requirepass" in compose["services"]["redis"]["command"][-1]


def test_deploy_probes_dependencies_after_start_and_before_migrations():
    script = (ROOT / "scripts" / "deploy-prod.sh").read_text(
        encoding="utf-8"
    )

    assert "compose up -d --wait" in script
    start = script.index("run_with_retry start_required_dependency_services")
    probe = script.index("backend python -m app.shared.dependency_probe")
    migrate = script.index('log "running database migrations"')
    assert start < probe < migrate
    assert "compose up -d browser" in script
    wait_block = script[script.index("compose up -d --wait"):script.index(
        "else", script.index("compose up -d --wait"))]
    assert "db redis neo4j minio" in wait_block
    assert "db redis neo4j minio browser" not in wait_block


def test_operator_configured_public_http_n8n_is_allowed_in_production():
    assert enforce_n8n_url_policy(
        "http://n8n.example.com:5678/api/v1", environment="production"
    ) == "http://n8n.example.com:5678/api/v1"


def test_private_http_and_public_https_n8n_are_allowed_in_production():
    assert enforce_n8n_url_policy(
        "http://10.0.0.8:5678", environment="production"
    ) == "http://10.0.0.8:5678/api/v1"
    assert enforce_n8n_url_policy(
        "https://n8n.example.com/api/v1", environment="production"
    ) == "https://n8n.example.com/api/v1"


def test_n8n_global_config_test_requires_admin(client, editor_user):
    login = client.post(
        "/api/v1/auth/login",
        json={"username": "editor", "password": "editor123"},
    )
    token = login.json()["data"]["access_token"]
    response = client.post(
        "/api/v1/settings/workflow-config/test",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "enabled": True,
            "api_url": "http://127.0.0.1:5678/api/v1",
            "api_key": "not-sent-because-authz-runs-first",
            "timeout_seconds": 1,
        },
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "Admin required"


def test_secret_key_derived_encryption_remains_decryptable(monkeypatch):
    from app.shared import encryption

    monkeypatch.setattr(encryption.settings, "encryption_key", "")
    monkeypatch.setattr(
        encryption.settings, "secret_key",
        "0123456789abcdef0123456789abcdef")
    ciphertext = encryption.encrypt("existing-connection-password")
    assert encryption.decrypt(ciphertext) == "existing-connection-password"


def _run_deploy_validation(app_dir: Path, *, health_url: str | None = None):
    manifest = app_dir / "production.dependencies.env"
    if not manifest.exists():
        _write_valid_dependency_manifest(manifest)
    env = os.environ.copy()
    env.update({
        "APP_DIR": str(app_dir),
        "SKIP_GIT": "1",
        "DEPLOY_VALIDATE_ONLY": "1",
    })
    if health_url is not None:
        env["HEALTH_URL"] = health_url
    return subprocess.run(
        ["bash", str(ROOT / "scripts" / "deploy-prod.sh")],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=20,
    )


def _write_valid_dependency_manifest(
    path: Path,
    *,
    public_port: str = "80",
    redis_url: str = (
        "redis://:synthetic-redis-password@redis.example.com:6379/0"
    ),
    database_url: str = (
        "postgresql://ontology:synthetic-postgres-password"
        "@pg.example.com:5432/ontology"
    ),
    neo4j_uri: str = "bolt+s://graph.example.com:7687",
    neo4j_auth: str = "neo4j/synthetic-neo4j-password",
    postgres_user: str = "ontology",
    postgres_password: str = "synthetic-postgres-password",
    postgres_db: str = "ontology",
) -> None:
    path.write_text(
        "ENVIRONMENT=production\n"
        f"PUBLIC_PORT={public_port}\n"
        f"POSTGRES_DB={postgres_db}\n"
        f"POSTGRES_USER={postgres_user}\n"
        f"POSTGRES_PASSWORD={postgres_password}\n"
        f"DATABASE_URL={database_url}\n"
        f"REDIS_URL={redis_url}\n"
        f"NEO4J_URI={neo4j_uri}\n"
        "NEO4J_USER=neo4j\n"
        "NEO4J_PASSWORD=synthetic-neo4j-password\n"
        f"NEO4J_AUTH={neo4j_auth}\n"
        "MINIO_ENDPOINT=objects.example.com:9000\n"
        "MINIO_ACCESS_KEY=synthetic-minio-access\n"
        "MINIO_SECRET_KEY=synthetic-minio-secret\n"
        "MINIO_USE_SSL=true\n"
        "N8N_API_URL=https://n8n.example.com/api/v1\n"
        "N8N_API_KEY=synthetic-n8n-api-key\n"
        "N8N_TIMEOUT_SECONDS=30\n",
        encoding="utf-8",
    )


def _read_env(path: Path) -> dict[str, str]:
    values = {}
    for line in path.read_text().splitlines():
        if line and not line.lstrip().startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value
    return values


def test_deploy_bootstraps_server_env_without_more_github_secrets(tmp_path):
    shutil.copy(ROOT / ".env.example", tmp_path / ".env.example")

    result = _run_deploy_validation(tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    generated_path = tmp_path / ".env"
    generated = _read_env(generated_path)
    assert generated["ENVIRONMENT"] == "production"
    assert len(generated["SECRET_KEY"]) == 64
    assert len(generated["FIRST_ADMIN_PASSWORD"]) == 48
    assert generated["POSTGRES_PASSWORD"] in generated["DATABASE_URL"]
    assert generated["NEO4J_AUTH"] == f"neo4j/{generated['NEO4J_PASSWORD']}"
    assert generated["MINIO_ACCESS_KEY"] != "minioadmin"
    assert generated["MINIO_SECRET_KEY"] != "minioadmin"
    assert generated["REDIS_PASSWORD"] != "ontopromptredis123"
    assert generated["STORAGE_LOCAL_DIR"] == "/uploads/object-storage"
    assert generated["STEWARD_BROWSER_CDP_URL"] == "http://browser:9222"
    assert generated["PIPELINE_FILE_GATEWAY_BASE_URL"] == (
        "http://127.0.0.1:80/api/v2/file-transfer")
    assert generated["PIPELINE_FILE_PUBLIC_APP_BASE_URL"] == (
        "http://127.0.0.1:80")
    assert generated["PIPELINE_FILE_PUBLIC_API_BASE_URL"] == (
        "http://127.0.0.1:80")
    assert generated["SECRET_KEY"] not in result.stdout
    assert stat.S_IMODE(generated_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(
        (tmp_path / "production.dependencies.env").stat().st_mode
    ) == 0o600


def test_deploy_upgrades_exact_legacy_bundled_redis_url(tmp_path):
    shutil.copy(ROOT / ".env.example", tmp_path / ".env.example")
    _write_valid_dependency_manifest(
        tmp_path / "production.dependencies.env",
        redis_url="redis://redis:6379/0",
    )

    result = _run_deploy_validation(tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    generated = _read_env(tmp_path / ".env")
    password = generated["REDIS_PASSWORD"]
    assert password != "ontopromptredis123"
    assert generated["REDIS_URL"] == f"redis://:{password}@redis:6379/0"
    assert "upgraded the legacy bundled Redis URL" in result.stdout
    assert password not in result.stdout + result.stderr


def test_deploy_derives_bundled_redis_requirepass_from_client_url(tmp_path):
    shutil.copy(ROOT / ".env.example", tmp_path / ".env.example")
    password = "synthetic-bundled-redis-password"
    redis_url = f"redis://:{password}@redis:6379/0"
    _write_valid_dependency_manifest(
        tmp_path / "production.dependencies.env",
        redis_url=redis_url,
    )

    result = _run_deploy_validation(tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    generated = _read_env(tmp_path / ".env")
    assert generated["REDIS_PASSWORD"] == password
    assert generated["REDIS_URL"] == redis_url
    assert "synchronized the bundled Redis service password" in result.stdout
    assert password not in result.stdout + result.stderr


def test_deploy_preserves_external_redis_url_without_binding_local_password(
    tmp_path,
):
    shutil.copy(ROOT / ".env.example", tmp_path / ".env.example")
    redis_url = (
        "rediss://:synthetic-provider-password@cache.example.com:6380/0"
    )
    _write_valid_dependency_manifest(
        tmp_path / "production.dependencies.env",
        redis_url=redis_url,
    )

    result = _run_deploy_validation(tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    generated = _read_env(tmp_path / ".env")
    assert generated["REDIS_URL"] == redis_url
    assert generated["REDIS_PASSWORD"] not in redis_url
    assert "preserved the external Redis URL" in result.stdout
    assert "synthetic-provider-password" not in result.stdout + result.stderr


def test_deploy_validates_bundled_postgres_and_neo4j_authorities(tmp_path):
    shutil.copy(ROOT / ".env.example", tmp_path / ".env.example")
    manifest = tmp_path / "production.dependencies.env"
    _write_valid_dependency_manifest(
        manifest,
        database_url=(
            "postgresql://ontology:synthetic-postgres-password"
            "@db:5432/ontology"
        ),
        neo4j_uri="bolt://neo4j:7687",
    )

    result = _run_deploy_validation(tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "validated bundled PostgreSQL" in result.stdout
    assert "validated bundled Neo4j" in result.stdout


def test_deploy_compares_decoded_bundled_postgres_authority(tmp_path):
    shutil.copy(ROOT / ".env.example", tmp_path / ".env.example")
    _write_valid_dependency_manifest(
        tmp_path / "production.dependencies.env",
        database_url=(
            "postgres://onto%40logy:synthetic%3Apostgres-password"
            "@db:5432/onto%2Flogy"
        ),
        postgres_user="onto@logy",
        postgres_password="synthetic:postgres-password",
        postgres_db="onto/logy",
    )

    result = _run_deploy_validation(tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "validated bundled PostgreSQL" in result.stdout


@pytest.mark.parametrize(
    ("database_url", "neo4j_uri", "neo4j_auth", "message"),
    [
        (
            "postgresql://ontology:wrong-password@db:5432/ontology",
            "bolt+s://graph.example.com:7687",
            "neo4j/synthetic-neo4j-password",
            "bundled DATABASE_URL must exactly match",
        ),
        (
            "postgresql://ontology:synthetic-postgres-password@pg.example.com:5432/ontology",
            "bolt://neo4j:7687",
            "neo4j/wrong-password",
            "bundled NEO4J_AUTH must match",
        ),
        (
            "postgresql://ontology:synthetic-postgres-password@pg.example.com:5432/ontology",
            "bolt+s://graph.example.com:7687",
            "none",
            "NEO4J_AUTH must enable authentication",
        ),
    ],
)
def test_deploy_rejects_mismatched_or_disabled_bundled_auth(
    tmp_path,
    database_url,
    neo4j_uri,
    neo4j_auth,
    message,
):
    shutil.copy(ROOT / ".env.example", tmp_path / ".env.example")
    _write_valid_dependency_manifest(
        tmp_path / "production.dependencies.env",
        database_url=database_url,
        neo4j_uri=neo4j_uri,
        neo4j_auth=neo4j_auth,
    )

    result = _run_deploy_validation(tmp_path)

    assert result.returncode != 0
    assert message in result.stdout + result.stderr


def test_deploy_rejects_encoded_password_for_bundled_redis(tmp_path):
    shutil.copy(ROOT / ".env.example", tmp_path / ".env.example")
    _write_valid_dependency_manifest(
        tmp_path / "production.dependencies.env",
        redis_url="redis://:encoded%40password@redis:6379/0",
    )

    result = _run_deploy_validation(tmp_path)

    assert result.returncode != 0
    output = result.stdout + result.stderr
    assert "bundled Redis URL must use an unescaped" in output
    assert "encoded%40password" not in output


def test_existing_insecure_example_env_is_rejected(tmp_path):
    shutil.copy(ROOT / ".env.example", tmp_path / ".env.example")
    shutil.copy(ROOT / ".env.example", tmp_path / ".env")

    result = _run_deploy_validation(tmp_path)

    assert result.returncode != 0
    assert "SECRET_KEY is missing or still uses an example credential" in (
        result.stdout + result.stderr
    )


def test_deploy_requires_dependency_manifest(tmp_path):
    shutil.copy(ROOT / ".env.example", tmp_path / ".env.example")
    env = os.environ.copy()
    env.update({
        "APP_DIR": str(tmp_path),
        "SKIP_GIT": "1",
        "DEPLOY_VALIDATE_ONLY": "1",
        "DEPENDENCY_CONFIG_FILE": "missing-production-dependencies.env",
    })

    result = subprocess.run(
        ["bash", str(ROOT / "scripts" / "deploy-prod.sh")],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
    )

    assert result.returncode != 0
    assert "is required for production deployment" in result.stdout


def test_deploy_ignores_legacy_manifest_switches_without_reenabling_them(
    tmp_path,
):
    shutil.copy(ROOT / ".env.example", tmp_path / ".env.example")
    manifest = tmp_path / "production.dependencies.env"
    _write_valid_dependency_manifest(manifest)
    with manifest.open("a", encoding="utf-8") as stream:
        stream.write(
            "POSTGRES_HOST=ignored.example.com\n"
            "POSTGRES_PORT=5432\n"
            "MINIO_CONSOLE_URL=https://ignored.example.com\n"
            "STRICT_PRODUCTION_CONFIG=false\n"
            "REQUIRE_EXTERNAL_DEPENDENCIES=false\n"
            "DATASET_IMPORT_USE_CELERY=false\n"
            "STORAGE_LOCAL_FALLBACK=true\n"
            "N8N_EMAIL=\n"
            "N8N_PASSWORD=\n"
        )

    result = _run_deploy_validation(tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    generated = _read_env(tmp_path / ".env")
    for key in (
        "POSTGRES_HOST",
        "POSTGRES_PORT",
        "MINIO_CONSOLE_URL",
        "STRICT_PRODUCTION_CONFIG",
        "REQUIRE_EXTERNAL_DEPENDENCIES",
        "DATASET_IMPORT_USE_CELERY",
        "STORAGE_LOCAL_FALLBACK",
        "N8N_EMAIL",
        "N8N_PASSWORD",
    ):
        assert key not in generated
        assert f"ignored deprecated production dependency setting: {key}" in (
            result.stdout
        )


def test_deploy_backfills_n8n_timeout_for_existing_env_and_old_manifest(
    tmp_path,
):
    shutil.copy(ROOT / ".env.example", tmp_path / ".env.example")
    first = _run_deploy_validation(tmp_path)
    assert first.returncode == 0, first.stdout + first.stderr

    for filename in (".env", "production.dependencies.env"):
        path = tmp_path / filename
        path.write_text(
            "\n".join(
                line for line in path.read_text(encoding="utf-8").splitlines()
                if not line.startswith("N8N_TIMEOUT_SECONDS=")
            ) + "\n",
            encoding="utf-8",
        )

    result = _run_deploy_validation(tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    assert _read_env(tmp_path / ".env")["N8N_TIMEOUT_SECONDS"] == "30"


def test_deploy_derives_pipeline_file_gateway_from_external_health_url(tmp_path):
    shutil.copy(ROOT / ".env.example", tmp_path / ".env.example")

    result = _run_deploy_validation(
        tmp_path, health_url="https://platform.example.com/")

    assert result.returncode == 0, result.stdout + result.stderr
    generated = _read_env(tmp_path / ".env")
    assert generated["PIPELINE_FILE_GATEWAY_BASE_URL"] == (
        "https://platform.example.com/api/v2/file-transfer")
    assert generated["PIPELINE_FILE_PUBLIC_APP_BASE_URL"] == (
        "https://platform.example.com")
    assert generated["PIPELINE_FILE_PUBLIC_API_BASE_URL"] == (
        "https://platform.example.com")


def test_deploy_uses_manifest_public_port_for_default_health_origin(tmp_path):
    shutil.copy(ROOT / ".env.example", tmp_path / ".env.example")
    _write_valid_dependency_manifest(
        tmp_path / "production.dependencies.env", public_port="8123"
    )

    result = _run_deploy_validation(tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    generated = _read_env(tmp_path / ".env")
    assert generated["PUBLIC_PORT"] == "8123"
    assert generated["PIPELINE_FILE_GATEWAY_BASE_URL"] == (
        "http://127.0.0.1:8123/api/v2/file-transfer")
    assert generated["PIPELINE_FILE_PUBLIC_APP_BASE_URL"] == (
        "http://127.0.0.1:8123")
    assert generated["PIPELINE_FILE_PUBLIC_API_BASE_URL"] == (
        "http://127.0.0.1:8123")


def _deploy_workflow_script() -> str:
    workflow = yaml.safe_load(
        (ROOT / ".github" / "workflows" /
         "deploy-nano-ontoprompt.yml").read_text())
    deploy_step = next(
        step
        for step in workflow["jobs"]["deploy"]["steps"]
        if step.get("name") == "Deploy over SSH"
    )
    return deploy_step["run"]


def _run_deploy_workflow_step(
    tmp_path: Path,
    *,
    dependency_config: str,
    health_url: str = "",
):
    (tmp_path / "production.dependencies.env").write_text(
        dependency_config,
        encoding="utf-8",
    )
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_sshpass = fake_bin / "sshpass"
    fake_sshpass.write_text(
        "#!/usr/bin/env bash\nprintf '%s\\n' \"$*\"\n",
        encoding="utf-8",
    )
    fake_sshpass.chmod(0o755)
    env = os.environ.copy()
    env.update({
        "PATH": f"{fake_bin}:{env['PATH']}",
        "GITHUB_WORKSPACE": str(ROOT),
        "DEPLOY_HOST": "203.0.113.8",
        "DEPLOY_USER": "deploy-user",
        "DEPLOY_PASSWORD": "test-password",
        "DEPLOY_APP_DIR": "/srv/ontologybuild",
        "DEPLOY_HEALTH_URL": health_url,
    })
    return subprocess.run(
        ["bash", "-e", "-c", _deploy_workflow_script()],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )


@pytest.mark.parametrize(
    ("public_port", "expected_url"),
    [
        ("8088", "http://203.0.113.8:8088/"),
        ("80", "http://203.0.113.8/"),
    ],
)
def test_deploy_workflow_defaults_health_url_to_manifest_public_port(
    tmp_path, public_port, expected_url,
):
    result = _run_deploy_workflow_step(
        tmp_path,
        dependency_config=f"PUBLIC_PORT={public_port}\n",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert f"HEALTH_URL='{expected_url}'" in result.stdout


def test_deploy_workflow_preserves_explicit_health_url(tmp_path):
    result = _run_deploy_workflow_step(
        tmp_path,
        dependency_config="PUBLIC_PORT=not-used\n",
        health_url="https://platform.example.com/health-root/",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert (
        "HEALTH_URL='https://platform.example.com/health-root/'"
        in result.stdout
    )


@pytest.mark.parametrize("public_port", ["not-a-port", "0", "65536"])
def test_deploy_workflow_rejects_invalid_manifest_public_port(
    tmp_path, public_port,
):
    result = _run_deploy_workflow_step(
        tmp_path,
        dependency_config=f"PUBLIC_PORT={public_port}\n",
    )

    assert result.returncode != 0
    assert "PUBLIC_PORT must be an integer between 1 and 65535" in result.stdout
    assert "ssh -o StrictHostKeyChecking" not in result.stdout


def test_deploy_ignores_empty_exported_public_port_and_uses_validated_env(
    tmp_path, monkeypatch,
):
    shutil.copy(ROOT / ".env.example", tmp_path / ".env.example")
    _write_valid_dependency_manifest(
        tmp_path / "production.dependencies.env", public_port="8123"
    )
    monkeypatch.setenv("PUBLIC_PORT", "")

    result = _run_deploy_validation(tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    generated = _read_env(tmp_path / ".env")
    assert generated["PUBLIC_PORT"] == "8123"
    assert generated["PIPELINE_FILE_GATEWAY_BASE_URL"] == (
        "http://127.0.0.1:8123/api/v2/file-transfer")
    assert generated["PIPELINE_FILE_PUBLIC_APP_BASE_URL"] == (
        "http://127.0.0.1:8123")
    assert generated["PIPELINE_FILE_PUBLIC_API_BASE_URL"] == (
        "http://127.0.0.1:8123")


def test_deploy_preserves_explicit_pipeline_file_gateway(tmp_path):
    content = (ROOT / ".env.example").read_text().replace(
        "PIPELINE_FILE_GATEWAY_BASE_URL=http://backend:8000/api/v2/file-transfer",
        "PIPELINE_FILE_GATEWAY_BASE_URL=https://platform.example.com/api/v2/file-transfer",
    )
    (tmp_path / ".env.example").write_text(content)

    result = _run_deploy_validation(tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    generated = _read_env(tmp_path / ".env")
    assert generated["PIPELINE_FILE_GATEWAY_BASE_URL"] == (
        "https://platform.example.com/api/v2/file-transfer")


def test_deploy_preserves_explicit_public_file_origins(tmp_path):
    content = (ROOT / ".env.example").read_text()
    content = content.replace(
        "PIPELINE_FILE_PUBLIC_APP_BASE_URL=http://localhost:5173",
        "PIPELINE_FILE_PUBLIC_APP_BASE_URL=https://app.example.com",
    ).replace(
        "PIPELINE_FILE_PUBLIC_API_BASE_URL=http://localhost:8000",
        "PIPELINE_FILE_PUBLIC_API_BASE_URL=https://files.example.com",
    )
    (tmp_path / ".env.example").write_text(content)

    result = _run_deploy_validation(
        tmp_path, health_url="https://platform.example.com/")

    assert result.returncode == 0, result.stdout + result.stderr
    generated = _read_env(tmp_path / ".env")
    assert generated["PIPELINE_FILE_PUBLIC_APP_BASE_URL"] == (
        "https://app.example.com")
    assert generated["PIPELINE_FILE_PUBLIC_API_BASE_URL"] == (
        "https://files.example.com")


def test_deploy_rejects_malformed_public_file_origin(tmp_path):
    content = (ROOT / ".env.example").read_text().replace(
        "PIPELINE_FILE_PUBLIC_API_BASE_URL=http://localhost:8000",
        "PIPELINE_FILE_PUBLIC_API_BASE_URL=https://user@example.com/files?x=1",
    )
    (tmp_path / ".env.example").write_text(content)

    result = _run_deploy_validation(tmp_path)

    assert result.returncode != 0
    assert "PIPELINE_FILE_PUBLIC_API_BASE_URL must be an absolute" in result.stdout


def test_deploy_pins_legacy_storage_read_root_to_shared_volume(tmp_path):
    content = (ROOT / ".env.example").read_text().replace(
        "STORAGE_LOCAL_DIR=storage",
        "STORAGE_LOCAL_DIR=relative/object-storage",
    )
    (tmp_path / ".env.example").write_text(content)

    result = _run_deploy_validation(tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    assert _read_env(tmp_path / ".env")["STORAGE_LOCAL_DIR"] == (
        "/uploads/object-storage")
