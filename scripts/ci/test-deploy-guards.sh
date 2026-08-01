#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd)"
APP_DIR_VALIDATOR="$SCRIPT_DIR/validate-deploy-app-dir.sh"
DEPLOY_SCRIPT="$REPO_ROOT/scripts/deploy-prod.sh"
DEPLOY_WORKFLOW="$REPO_ROOT/.github/workflows/deploy-nano-ontoprompt.yml"
ARCHIVE_SCRIPT="$SCRIPT_DIR/create-deployment-archive.sh"

assert_accepted() {
  local value="$1"
  bash "$APP_DIR_VALIDATOR" "$value" >/dev/null
}

assert_rejected() {
  local value="${1-}"
  if bash "$APP_DIR_VALIDATOR" "$value" >/dev/null 2>&1; then
    printf 'expected DEPLOY_APP_DIR to be rejected\n' >&2
    exit 1
  fi
}

assert_accepted /opt/ontologybuild
assert_accepted /srv/apps/ontology-build_1

assert_rejected ""
assert_rejected /
assert_rejected /opt
assert_rejected opt/ontologybuild
assert_rejected /opt/../ontologybuild
assert_rejected /opt//ontologybuild
assert_rejected /opt/ontologybuild/
assert_rejected "/opt/ontology build"
assert_rejected "/opt/ontology'build"
assert_rejected $'/opt/ontologybuild\nunexpected'
assert_rejected "/opt/ontologybuild;unexpected"

if grep -q 'StrictHostKeyChecking=no' "$DEPLOY_WORKFLOW"; then
  printf 'deployment workflow must not bypass SSH host-key verification\n' >&2
  exit 1
fi
sshpass_command_count="$(grep -c 'sshpass -p' "$DEPLOY_WORKFLOW")"
strict_sshpass_command_count="$(
  grep -c 'sshpass -p.*StrictHostKeyChecking=yes' "$DEPLOY_WORKFLOW"
)"
if [ "$sshpass_command_count" -eq 0 ] ||
   [ "$sshpass_command_count" -ne "$strict_sshpass_command_count" ]; then
  printf 'every sshpass command must enforce the scanned host key\n' >&2
  exit 1
fi
if ! grep -Fq \
  'bash scripts/ci/create-deployment-archive.sh /tmp/ontologybuild.tar.gz' \
  "$DEPLOY_WORKFLOW"; then
  printf 'deployment workflow must use the tested runtime archive builder\n' >&2
  exit 1
fi
if ! git -C "$REPO_ROOT" ls-files --error-unmatch \
    production.dependencies.env >/dev/null 2>&1 \
    || [ ! -s "$REPO_ROOT/production.dependencies.env" ]; then
  printf 'the current deployment contract requires the tracked production dependency manifest\n' >&2
  exit 1
fi
if grep -Eq '^[[:space:]]+environment:[[:space:]]+production[[:space:]]*$' \
    "$DEPLOY_WORKFLOW" \
    || grep -Fq '${{ vars.' "$DEPLOY_WORKFLOW" \
    || grep -Fq 'materialize-production-dependencies.sh' "$DEPLOY_WORKFLOW"; then
  printf 'deployment configuration source changed without an explicit migration\n' >&2
  exit 1
fi

test_dir="$(mktemp -d /tmp/ontologybuild-deploy-guards.XXXXXX)"
archive_source="$(mktemp -d /tmp/ontologybuild-archive-source.XXXXXX)"
archive_output="$(mktemp /tmp/ontologybuild-archive.XXXXXX.tar.gz)"
trap 'rm -rf -- "$test_dir" "$archive_source"; rm -f -- "$archive_output"' EXIT

archive_fixture_paths=(
  ".env.example"
  "docker-compose.prod.yml"
  "production.dependencies.env"
  "backend/.dockerignore"
  "backend/Dockerfile"
  "backend/alembic.ini"
  "backend/alembic/env.py"
  "backend/app/main.py"
  "backend/pyproject.toml"
  "backend/uv.lock"
  "backend/scripts/maintenance/reset_admin_password.py"
  "frontend/.dockerignore"
  "frontend/Dockerfile.prod"
  "frontend/index.html"
  "frontend/nginx/default.conf"
  "frontend/package.json"
  "frontend/package-lock.json"
  "frontend/postcss.config.js"
  "frontend/public/favicon.svg"
  "frontend/src/main.tsx"
  "frontend/src/test/e2e/should-not-deploy.spec.ts"
  "frontend/tailwind.config.ts"
  "frontend/tsconfig.app.json"
  "frontend/tsconfig.json"
  "frontend/tsconfig.node.json"
  "frontend/vite.config.ts"
  "docker/browser/Dockerfile"
  "scripts/deploy-prod.sh"
  "scripts/ci/validate-deploy-app-dir.sh"
  "docs/should-not-deploy.md"
  ".artifacts/should-not-deploy.json"
)
for archive_path in "${archive_fixture_paths[@]}"; do
  mkdir -p "$archive_source/$(dirname "$archive_path")"
  : > "$archive_source/$archive_path"
done
DEPLOY_SOURCE_ROOT="$archive_source" \
  bash "$ARCHIVE_SCRIPT" "$archive_output" >/dev/null
archive_listing="$(tar -tzf "$archive_output")"
for required_member in \
  ".env.example" \
  "backend/app/main.py" \
  "backend/scripts/maintenance/reset_admin_password.py" \
  "frontend/src/main.tsx" \
  "docker/browser/Dockerfile" \
  "production.dependencies.env"; do
  if ! grep -Fxq "$required_member" <<<"$archive_listing"; then
    printf 'deployment archive is missing required member: %s\n' \
      "$required_member" >&2
    exit 1
  fi
done
for forbidden_member in \
  "frontend/src/test/e2e/should-not-deploy.spec.ts" \
  "docs/should-not-deploy.md" \
  ".artifacts/should-not-deploy.json"; do
  if grep -Fxq "$forbidden_member" <<<"$archive_listing"; then
    printf 'deployment archive contains non-runtime member: %s\n' \
      "$forbidden_member" >&2
    exit 1
  fi
done

cp "$REPO_ROOT/.env.example" "$test_dir/.env"

set_test_env_value() {
  local key="$1"
  local value="$2"
  local output="$test_dir/.env.next"
  awk -v key="$key" -v value="$value" '
    BEGIN { found=0 }
    $0 ~ "^[[:space:]]*" key "=" {
      print key "=" value
      found=1
      next
    }
    { print }
    END { if (!found) print key "=" value }
  ' "$test_dir/.env" > "$output"
  mv "$output" "$test_dir/.env"
}

digest="example.invalid/ontologybuild@sha256:$(printf 'a%.0s' {1..64})"
for image_key in \
  POSTGRES_IMAGE REDIS_IMAGE NEO4J_IMAGE MINIO_IMAGE BROWSER_IMAGE \
  PYTHON_BASE_IMAGE NODE_BASE_IMAGE NGINX_BASE_IMAGE; do
  set_test_env_value "$image_key" "$digest"
done
set_test_env_value SECRET_KEY 0123456789abcdef0123456789abcdef
set_test_env_value FIRST_ADMIN_PASSWORD synthetic-admin-password
set_test_env_value STRICT_IMAGE_DIGESTS true

if (
  cd "$test_dir"
  APP_DIR="$test_dir" \
    SKIP_GIT=1 \
    DEPLOY_VALIDATE_ONLY=1 \
    DEPENDENCY_CONFIG_FILE=missing-production-dependencies.env \
    bash "$DEPLOY_SCRIPT"
) >"$test_dir/missing-manifest.log" 2>&1; then
  printf 'expected a missing production dependency manifest to be rejected\n' >&2
  exit 1
fi
grep -q 'is required for production deployment' \
  "$test_dir/missing-manifest.log"

env \
  PROD_PUBLIC_PORT=8080 \
  PROD_POSTGRES_DB=ontology \
  PROD_POSTGRES_USER=ontology \
  PROD_POSTGRES_PASSWORD=synthetic-postgres-password \
  PROD_DATABASE_URL=postgresql://ontology:synthetic-postgres-password@pg.example.com:5432/ontology \
  PROD_REDIS_URL=redis://:synthetic-redis-password@redis.example.com:6379/0 \
  PROD_NEO4J_URI=bolt+s://graph.example.com:7687 \
  PROD_NEO4J_USER=neo4j \
  PROD_NEO4J_PASSWORD=synthetic-neo4j-password \
  PROD_NEO4J_AUTH=neo4j/synthetic-neo4j-password \
  PROD_MINIO_ENDPOINT=objects.example.com:9000 \
  PROD_MINIO_ACCESS_KEY=synthetic-minio-access \
  PROD_MINIO_SECRET_KEY=synthetic-minio-secret \
  PROD_MINIO_USE_SSL=true \
  PROD_N8N_API_URL=https://n8n.example.com \
  PROD_N8N_API_KEY=synthetic-n8n-api-key \
  PROD_N8N_TIMEOUT_SECONDS=30 \
  bash "$REPO_ROOT/scripts/ci/materialize-production-dependencies.sh" \
    "$test_dir/test-production-dependencies.env" >/dev/null

grep -Fxq 'N8N_TIMEOUT_SECONDS=30' \
  "$test_dir/test-production-dependencies.env"

(
  cd "$test_dir"
  env -u STRICT_IMAGE_DIGESTS \
    APP_DIR="$test_dir" \
    SKIP_GIT=1 \
    DEPLOY_VALIDATE_ONLY=1 \
  DEPENDENCY_CONFIG_FILE=test-production-dependencies.env \
    bash "$DEPLOY_SCRIPT" >/dev/null
)

external_redis_url='redis://:synthetic-redis-password@redis.example.com:6379/0'
grep -Fxq "REDIS_URL=${external_redis_url}" "$test_dir/.env"
if grep -Fxq 'REDIS_PASSWORD=synthetic-redis-password' "$test_dir/.env"; then
  printf 'external Redis credentials must not configure bundled Redis\n' >&2
  exit 1
fi

set_manifest_value() {
  local key="$1"
  local value="$2"
  local manifest="$test_dir/test-production-dependencies.env"
  local output="$test_dir/test-production-dependencies.next"
  awk -v key="$key" -v value="$value" '
    BEGIN { found=0 }
    $0 ~ "^[[:space:]]*" key "=" {
      print key "=" value
      found=1
      next
    }
    { print }
    END { if (!found) print key "=" value }
  ' "$manifest" > "$output"
  mv "$output" "$manifest"
}

canonical_database_url='postgresql://ontology:synthetic-postgres-password@pg.example.com:5432/ontology'
for legacy_database_url in \
  'postgres://ontology:synthetic-postgres-password@pg.example.com:5432/ontology' \
  'postgresql+psycopg2://ontology:synthetic-postgres-password@pg.example.com:5432/ontology'; do
  set_manifest_value DATABASE_URL "$legacy_database_url"
  (
    cd "$test_dir"
    env -u STRICT_IMAGE_DIGESTS \
      APP_DIR="$test_dir" \
      SKIP_GIT=1 \
      DEPLOY_VALIDATE_ONLY=1 \
      DEPENDENCY_CONFIG_FILE=test-production-dependencies.env \
      bash "$DEPLOY_SCRIPT" >database-url-normalization.log
  )
  grep -Fxq "DATABASE_URL=${canonical_database_url}" "$test_dir/.env"
  if grep -Fq 'synthetic-postgres-password' \
      "$test_dir/database-url-normalization.log"; then
    printf 'normalized PostgreSQL credential leaked into deploy validation logs\n' >&2
    exit 1
  fi
done
set_manifest_value DATABASE_URL "$canonical_database_url"

set_manifest_value REDIS_URL redis://redis:6379/0
(
  cd "$test_dir"
  env -u STRICT_IMAGE_DIGESTS \
    APP_DIR="$test_dir" \
    SKIP_GIT=1 \
    DEPLOY_VALIDATE_ONLY=1 \
    DEPENDENCY_CONFIG_FILE=test-production-dependencies.env \
    bash "$DEPLOY_SCRIPT" >legacy-redis.log
)
legacy_redis_password="$(
  awk -F= '$1 == "REDIS_PASSWORD" {
    print substr($0, index($0, "=") + 1)
  }' "$test_dir/.env" | tail -n1
)"
if [ -z "$legacy_redis_password" ] \
    || [ "$legacy_redis_password" = 'ontopromptredis123' ]; then
  printf 'legacy bundled Redis URL was not upgraded securely\n' >&2
  exit 1
fi
grep -Fxq \
  "REDIS_URL=redis://:${legacy_redis_password}@redis:6379/0" \
  "$test_dir/.env"
if grep -Fq "$legacy_redis_password" "$test_dir/legacy-redis.log"; then
  printf 'generated Redis password leaked into deploy validation logs\n' >&2
  exit 1
fi

bundled_redis_password='synthetic-bundled-redis-password'
set_manifest_value REDIS_URL \
  "redis://:${bundled_redis_password}@redis:6379/0"
(
  cd "$test_dir"
  env -u STRICT_IMAGE_DIGESTS \
    APP_DIR="$test_dir" \
    SKIP_GIT=1 \
    DEPLOY_VALIDATE_ONLY=1 \
    DEPENDENCY_CONFIG_FILE=test-production-dependencies.env \
    bash "$DEPLOY_SCRIPT" >bundled-redis.log
)
grep -Fxq "REDIS_PASSWORD=${bundled_redis_password}" "$test_dir/.env"
if grep -Fq "$bundled_redis_password" "$test_dir/bundled-redis.log"; then
  printf 'bundled Redis password leaked into deploy validation logs\n' >&2
  exit 1
fi

set_manifest_value REDIS_URL redis://:encoded%40password@redis:6379/0
if (
  cd "$test_dir"
  env -u STRICT_IMAGE_DIGESTS \
    APP_DIR="$test_dir" \
    SKIP_GIT=1 \
    DEPLOY_VALIDATE_ONLY=1 \
    DEPENDENCY_CONFIG_FILE=test-production-dependencies.env \
    bash "$DEPLOY_SCRIPT"
) >"$test_dir/encoded-redis.log" 2>&1; then
  printf 'encoded bundled Redis password must be rejected\n' >&2
  exit 1
fi
grep -q 'bundled Redis URL must use an unescaped' \
  "$test_dir/encoded-redis.log"
if grep -Fq 'encoded%40password' "$test_dir/encoded-redis.log"; then
  printf 'rejected Redis credential leaked into validation logs\n' >&2
  exit 1
fi
set_manifest_value REDIS_URL "$external_redis_url"

set_test_env_value POSTGRES_IMAGE postgres:16-alpine
if (
  cd "$test_dir"
  env -u STRICT_IMAGE_DIGESTS \
    APP_DIR="$test_dir" \
    SKIP_GIT=1 \
    DEPLOY_VALIDATE_ONLY=1 \
    DEPENDENCY_CONFIG_FILE=test-production-dependencies.env \
    bash "$DEPLOY_SCRIPT"
) >"$test_dir/strict-failure.log" 2>&1; then
  printf 'expected .env STRICT_IMAGE_DIGESTS=true to reject a floating image\n' >&2
  exit 1
fi
grep -q 'POSTGRES_IMAGE must be pinned' "$test_dir/strict-failure.log"

if (
  cd "$test_dir"
  STRICT_IMAGE_DIGESTS=0 \
    APP_DIR="$test_dir" \
    SKIP_GIT=1 \
    DEPLOY_VALIDATE_ONLY=1 \
    DEPENDENCY_CONFIG_FILE=test-production-dependencies.env \
    bash "$DEPLOY_SCRIPT" >/dev/null
); then
  printf 'exported STRICT_IMAGE_DIGESTS must not override the validated .env\n' >&2
  exit 1
fi

set_test_env_value STRICT_IMAGE_DIGESTS false
(
  cd "$test_dir"
  STRICT_IMAGE_DIGESTS=1 \
    APP_DIR="$test_dir" \
    SKIP_GIT=1 \
    DEPLOY_VALIDATE_ONLY=1 \
    DEPENDENCY_CONFIG_FILE=test-production-dependencies.env \
    bash "$DEPLOY_SCRIPT" >/dev/null
)

fake_bin="$test_dir/fake-bin"
fake_docker_log="$test_dir/fake-docker.log"
mkdir -p "$fake_bin"
cat >"$fake_bin/docker" <<'FAKE_DOCKER'
#!/usr/bin/env bash
set -Eeuo pipefail

for key in \
  ENVIRONMENT PUBLIC_PORT POSTGRES_DB POSTGRES_USER POSTGRES_PASSWORD \
  DATABASE_URL REDIS_PASSWORD REDIS_URL NEO4J_URI NEO4J_USER \
  NEO4J_PASSWORD NEO4J_AUTH MINIO_ENDPOINT MINIO_ACCESS_KEY \
  MINIO_SECRET_KEY MINIO_USE_SSL N8N_API_URL N8N_API_KEY \
  N8N_TIMEOUT_SECONDS STEWARD_BROWSER_CDP_URL CORS_ALLOWED_ORIGINS \
  UPLOADS_DIR PIPELINE_FILE_GATEWAY_BASE_URL \
  PIPELINE_FILE_PUBLIC_APP_BASE_URL PIPELINE_FILE_PUBLIC_API_BASE_URL \
  STEWARD_BROWSER_HTTP_LEASE_SECONDS \
  STEWARD_BROWSER_HTTP_FRAME_INTERVAL_MS STEWARD_BROWSER_MAX_SESSIONS \
  STEWARD_BROWSER_MAX_SESSIONS_PER_USER \
  STEWARD_BROWSER_IDLE_TIMEOUT_SECONDS \
  STEWARD_BROWSER_REAPER_INTERVAL_SECONDS POSTGRES_IMAGE REDIS_IMAGE \
  NEO4J_IMAGE MINIO_IMAGE BROWSER_IMAGE PYTHON_BASE_IMAGE NODE_BASE_IMAGE \
  NGINX_BASE_IMAGE STRICT_IMAGE_DIGESTS COMPOSE_DISABLE_ENV_FILE \
  COMPOSE_ENV_FILES COMPOSE_FILE COMPOSE_PATH_SEPARATOR COMPOSE_PROFILES \
  COMPOSE_PROJECT_NAME; do
  if [ "${!key+x}" = "x" ]; then
    printf 'compose environment leaked authority key: %s\n' "$key" \
      >>"$FAKE_DOCKER_LOG"
    exit 88
  fi
done

printf '%s\n' "$*" >>"$FAKE_DOCKER_LOG"
if [ "${1-}" = compose ] && [ "${2-}" = version ]; then
  exit 0
fi
case "$*" in
  *' up --help')
    printf '%s\n' '      --wait'
    ;;
  *' alembic heads')
    printf '%s\n' '0055_projection_readiness (head)'
    ;;
  *' up -d --remove-orphans')
    exit 42
    ;;
esac
FAKE_DOCKER
chmod +x "$fake_bin/docker"

if (
  cd "$test_dir"
  PATH="$fake_bin:$PATH" \
    FAKE_DOCKER_LOG="$fake_docker_log" \
    ENVIRONMENT=development \
    PUBLIC_PORT=65535 \
    POSTGRES_DB=shell_override \
    POSTGRES_USER=shell_override \
    POSTGRES_PASSWORD=shell-override-password \
    DATABASE_URL=postgres://shell-override.invalid/wrong \
    REDIS_PASSWORD=shell-override-password \
    REDIS_URL=redis://:wrong@shell-override.invalid:6379/0 \
    NEO4J_URI=bolt://shell-override.invalid:7687 \
    NEO4J_USER=shell_override \
    NEO4J_PASSWORD=shell-override-password \
    NEO4J_AUTH=shell_override/shell-override-password \
    MINIO_ENDPOINT=shell-override.invalid:9000 \
    MINIO_ACCESS_KEY=shell-override-access \
    MINIO_SECRET_KEY=shell-override-secret \
    MINIO_USE_SSL=false \
    N8N_API_URL=https://shell-override.invalid \
    N8N_API_KEY=shell-override-key \
    N8N_TIMEOUT_SECONDS=3 \
    STEWARD_BROWSER_CDP_URL=http://shell-override.invalid:9222 \
    CORS_ALLOWED_ORIGINS=https://shell-override.invalid \
    UPLOADS_DIR=/tmp/shell-override-uploads \
    PIPELINE_FILE_GATEWAY_BASE_URL=https://shell-override.invalid/gateway \
    PIPELINE_FILE_PUBLIC_APP_BASE_URL=https://shell-override.invalid \
    PIPELINE_FILE_PUBLIC_API_BASE_URL=https://shell-override.invalid \
    STEWARD_BROWSER_HTTP_LEASE_SECONDS=1 \
    STEWARD_BROWSER_HTTP_FRAME_INTERVAL_MS=1 \
    STEWARD_BROWSER_MAX_SESSIONS=1 \
    STEWARD_BROWSER_MAX_SESSIONS_PER_USER=1 \
    STEWARD_BROWSER_IDLE_TIMEOUT_SECONDS=1 \
    STEWARD_BROWSER_REAPER_INTERVAL_SECONDS=1 \
    POSTGRES_IMAGE=example.invalid/unvalidated:latest \
    REDIS_IMAGE=example.invalid/unvalidated:latest \
    NEO4J_IMAGE=example.invalid/unvalidated:latest \
    MINIO_IMAGE=example.invalid/unvalidated:latest \
    BROWSER_IMAGE=example.invalid/unvalidated:latest \
    PYTHON_BASE_IMAGE=example.invalid/unvalidated:latest \
    NODE_BASE_IMAGE=example.invalid/unvalidated:latest \
    NGINX_BASE_IMAGE=example.invalid/unvalidated:latest \
    STRICT_IMAGE_DIGESTS=true \
    COMPOSE_DISABLE_ENV_FILE=1 \
    COMPOSE_ENV_FILES=/tmp/unvalidated-compose.env \
    COMPOSE_FILE=/tmp/unvalidated-compose.yml \
    COMPOSE_PATH_SEPARATOR=: \
    COMPOSE_PROFILES=unvalidated-profile \
    COMPOSE_PROJECT_NAME=unvalidated-project \
    APP_DIR="$test_dir" \
    SKIP_GIT=1 \
    DEPLOY_RETRIES=1 \
    DEPENDENCY_CONFIG_FILE=test-production-dependencies.env \
    bash "$DEPLOY_SCRIPT"
) >"$test_dir/fake-deploy.log" 2>&1; then
  printf 'fake deployment must stop at the intentional final Compose failure\n' >&2
  exit 1
fi

if grep -Fq 'compose environment leaked authority key:' "$fake_docker_log"; then
  printf 'Compose inherited an exported shell authority\n' >&2
  exit 1
fi
probe_line="$(grep -nF \
  'compose -f docker-compose.prod.yml run --rm --no-deps backend python -m app.shared.dependency_probe' \
  "$fake_docker_log" | head -n1 | cut -d: -f1)"
stop_line="$(grep -nF \
  'compose -f docker-compose.prod.yml stop -t 30 backend celery_worker' \
  "$fake_docker_log" | head -n1 | cut -d: -f1)"
heads_line="$(grep -nF \
  'compose -f docker-compose.prod.yml run --rm --no-deps backend alembic heads' \
  "$fake_docker_log" | head -n1 | cut -d: -f1)"
upgrade_line="$(grep -nF \
  'compose -f docker-compose.prod.yml run --rm --no-deps backend alembic upgrade head' \
  "$fake_docker_log" | head -n1 | cut -d: -f1)"
if [ -z "$probe_line" ] || [ -z "$stop_line" ] || [ -z "$heads_line" ] \
    || [ -z "$upgrade_line" ] \
    || [ "$probe_line" -ge "$stop_line" ] \
    || [ "$stop_line" -ge "$heads_line" ] \
    || [ "$heads_line" -ge "$upgrade_line" ]; then
  printf 'backend and Celery worker must stop before Alembic validation/upgrade\n' >&2
  exit 1
fi

printf 'deployment guard self-tests passed\n'
