#!/usr/bin/env bash
set -Eeuo pipefail
APP_DIR="${APP_DIR:-/opt/ontologybuild}"
BRANCH="${BRANCH:-nano-ontoprompt}"
REPO_URL="${REPO_URL:-https://github.com/Carlehyy/OntologyBuild.git}"
COMPOSE_FILE="docker-compose.prod.yml"
DEPENDENCY_CONFIG_FILE="${DEPENDENCY_CONFIG_FILE:-production.dependencies.env}"
# Preserve explicit operator overrides, but defer defaults until the persistent
# .env and production.dependencies.env have been merged. PUBLIC_PORT from that
# manifest must drive the same endpoint that Compose will expose.
HEALTH_URL="${HEALTH_URL:-}"
READINESS_URL="${READINESS_URL:-}"
RETRIES="${DEPLOY_RETRIES:-3}"
SLEEP_SECONDS="${DEPLOY_RETRY_SLEEP:-10}"
log() { printf '[%s] %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$*"; }
run_with_retry() {
  local attempt=1
  until "$@"; do
    if [ "$attempt" -ge "$RETRIES" ]; then
      log "command failed after ${attempt} attempts: $*"
      return 1
    fi
    log "command failed, retrying in ${SLEEP_SECONDS}s (${attempt}/${RETRIES}): $*"
    attempt=$((attempt + 1))
    sleep "$SLEEP_SECONDS"
  done
}
compose() {
  if docker compose version >/dev/null 2>&1; then
    docker compose -f "$COMPOSE_FILE" "$@"
  elif command -v docker-compose >/dev/null 2>&1; then
    docker-compose -f "$COMPOSE_FILE" "$@"
  else
    log "Docker Compose is not installed"
    return 1
  fi
}
assert_asset() {
  local url="$1"
  local expected="$2"
  local headers
  headers="$(mktemp)"
  if ! curl -fsS --connect-timeout 5 --max-time 20 -D "$headers" -o /dev/null "$url"; then
    rm -f "$headers"
    return 1
  fi
  if ! grep -Eiq "^content-type:.*${expected}" "$headers"; then
    log "asset content type mismatch: $url"
    tr -d '\r' < "$headers" | awk 'BEGIN{IGNORECASE=1}/^HTTP|^Content-Type|^Content-Length/{print "  " $0}' >&2
    rm -f "$headers"
    return 1
  fi
  rm -f "$headers"
}
check_frontend_assets() {
  local base="${HEALTH_URL%/}"
  local index_file main_file
  local entry css asset expected
  index_file="$(mktemp)"
  main_file="$(mktemp)"
  curl -fsS --connect-timeout 5 --max-time 20 "$base/" -o "$index_file"

  entry="$(grep -oE 'src="/assets/[^"]+\.js"' "$index_file" | head -n1 | sed -E 's/src="([^"]+)"/\1/')"
  css="$(grep -oE 'href="/assets/[^"]+\.css"' "$index_file" | head -n1 | sed -E 's/href="([^"]+)"/\1/')"
  [ -n "$entry" ] || { log "frontend entry script not found in index.html"; rm -f "$index_file" "$main_file"; return 1; }

  assert_asset "$base$entry" "javascript"
  [ -z "$css" ] || assert_asset "$base$css" "text/css"
  curl -fsS --connect-timeout 5 --max-time 30 "$base$entry" -o "$main_file"

  grep -aoE 'assets/[-A-Za-z0-9_./]+\.(js|css)' "$main_file" | sort -u | while read -r asset; do
    case "$asset" in
      *.js) expected="javascript" ;;
      *.css) expected="text/css" ;;
      *) continue ;;
    esac
    assert_asset "$base/$asset" "$expected"
  done

  rm -f "$index_file" "$main_file"
}
check_action_worker() {
  compose exec -T celery_worker \
    celery -A app.tasks.celery_app:celery_app inspect ping --timeout=5 \
    >/dev/null
}
if [ "${SKIP_GIT:-0}" != "1" ]; then
  command -v git >/dev/null 2>&1 || { log "git is not installed"; exit 1; }
  mkdir -p "$APP_DIR"
  if [ ! -d "$APP_DIR/.git" ]; then
    rm -rf "$APP_DIR"
    run_with_retry git clone --branch "$BRANCH" --single-branch "$REPO_URL" "$APP_DIR"
  fi
  cd "$APP_DIR"
  run_with_retry git fetch origin "$BRANCH"
  git checkout "$BRANCH"
  git reset --hard "origin/$BRANCH"
else
  cd "$APP_DIR"
fi
random_hex() {
  local bytes="${1:-32}"
  od -An -N "$bytes" -tx1 /dev/urandom | tr -d ' \n'
}
set_env_value() {
  local key="$1"
  local value="$2"
  local tmp
  tmp="$(mktemp)"
  awk -v key="$key" -v value="$value" '
    BEGIN { found=0 }
    $0 ~ "^[[:space:]]*" key "=" {
      print key "=" value; found=1; next
    }
    { print }
    END { if (!found) print key "=" value }
  ' .env > "$tmp"
  mv "$tmp" .env
}
bootstrap_production_env() {
  log "production .env is missing; creating a persistent server-side runtime configuration"
  cp .env.example .env
  local db_password neo4j_password minio_access minio_secret
  db_password="$(random_hex 24)"
  neo4j_password="$(random_hex 24)"
  minio_access="onto$(random_hex 10)"
  minio_secret="$(random_hex 24)"
  set_env_value ENVIRONMENT production
  set_env_value SECRET_KEY "$(random_hex 32)"
  set_env_value ENCRYPTION_KEY ""
  set_env_value CORS_ALLOWED_ORIGINS ""
  set_env_value FIRST_ADMIN_PASSWORD "$(random_hex 24)"
  set_env_value POSTGRES_PASSWORD "$db_password"
  set_env_value DATABASE_URL "postgresql://ontoprompt:${db_password}@db:5432/ontoprompt"
  set_env_value NEO4J_PASSWORD "$neo4j_password"
  set_env_value NEO4J_AUTH "neo4j/${neo4j_password}"
  set_env_value MINIO_ACCESS_KEY "$minio_access"
  set_env_value MINIO_SECRET_KEY "$minio_secret"
  set_env_value STORAGE_LOCAL_FALLBACK true
  set_env_value STORAGE_LOCAL_DIR /uploads/object-storage
  set_env_value ALLOW_PUBLIC_REGISTRATION false
  set_env_value API_HUB_SYSTEM_MCP_TOKEN "$(random_hex 32)"
  set_env_value STRICT_PRODUCTION_CONFIG false
  chmod 600 .env
  log "generated runtime secrets were stored in ${APP_DIR}/.env (values are not printed to CI logs)"
}
[ -f .env ] || bootstrap_production_env

dependency_key_allowed() {
  case "$1" in
    ENVIRONMENT|PUBLIC_PORT|STRICT_PRODUCTION_CONFIG|REQUIRE_EXTERNAL_DEPENDENCIES|\
    POSTGRES_HOST|POSTGRES_PORT|POSTGRES_DB|POSTGRES_USER|POSTGRES_PASSWORD|\
    DATABASE_URL|REDIS_URL|DATASET_IMPORT_USE_CELERY|\
    NEO4J_URI|NEO4J_USER|NEO4J_PASSWORD|NEO4J_AUTH|\
    MINIO_CONSOLE_URL|MINIO_ENDPOINT|MINIO_ACCESS_KEY|MINIO_SECRET_KEY|\
    MINIO_USE_SSL|STORAGE_LOCAL_FALLBACK|\
    N8N_API_URL|N8N_EMAIL|N8N_PASSWORD|N8N_API_KEY)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}
apply_production_dependency_config() {
  local raw key value applied=0 current_required
  if [ ! -f "$DEPENDENCY_CONFIG_FILE" ]; then
    current_required="$(
      awk -F= '$1 == "REQUIRE_EXTERNAL_DEPENDENCIES" {
        print substr($0, index($0, "=") + 1)
      }' .env | tail -n1
    )"
    case "$current_required" in
      1|true|TRUE|yes|YES)
        log "$DEPENDENCY_CONFIG_FILE is required by the current production .env"
        exit 1
        ;;
      *)
        return
        ;;
    esac
  fi

  while IFS= read -r raw || [ -n "$raw" ]; do
    raw="${raw%$'\r'}"
    case "$raw" in
      ""|\#*) continue ;;
    esac
    if [[ "$raw" != *=* ]]; then
      log "$DEPENDENCY_CONFIG_FILE contains an invalid line"
      exit 1
    fi
    key="${raw%%=*}"
    value="${raw#*=}"
    if [[ ! "$key" =~ ^[A-Z][A-Z0-9_]*$ ]] \
        || ! dependency_key_allowed "$key"; then
      log "$DEPENDENCY_CONFIG_FILE contains unsupported key: $key"
      exit 1
    fi
    if [ -z "$value" ]; then
      log "$DEPENDENCY_CONFIG_FILE contains an empty required value: $key"
      exit 1
    fi
    set_env_value "$key" "$value"
    applied=$((applied + 1))
  done < "$DEPENDENCY_CONFIG_FILE"
  chmod 600 "$DEPENDENCY_CONFIG_FILE"
  log "applied ${applied} production dependency settings (values are not printed)"
}
apply_production_dependency_config
chmod 600 .env
if [ -z "$(awk -F= '$1 == "API_HUB_SYSTEM_MCP_TOKEN" {print substr($0, index($0,"=")+1)}' .env | tail -n1)" ]; then
  set_env_value API_HUB_SYSTEM_MCP_TOKEN "$(random_hex 32)"
fi
env_value() {
  local key="$1"
  awk -v key="$key" '
    $0 ~ "^[[:space:]]*" key "=" {
      line=$0; sub("^[[:space:]]*" key "=", "", line); value=line
    }
    END { gsub(/\r$/, "", value); print value }
  ' .env
}
if [ "${PUBLIC_PORT+x}" = "x" ]; then
  # Match Compose's `${PUBLIC_PORT:-80}` interpolation exactly: an explicitly
  # exported empty value selects the default instead of the project .env.
  effective_public_port="${PUBLIC_PORT:-80}"
else
  effective_public_port="$(env_value PUBLIC_PORT)"
  effective_public_port="${effective_public_port:-80}"
fi
HEALTH_URL="${HEALTH_URL:-http://127.0.0.1:${effective_public_port}/}"
READINESS_URL="${READINESS_URL:-${HEALTH_URL%/}/api/health}"
redact_compose_logs() {
  local key value line tail_lines
  local -a sensitive_values=()
  local -a services=("$@")
  tail_lines="${COMPOSE_LOG_TAIL:-200}"
  for key in \
    SECRET_KEY ENCRYPTION_KEY FIRST_ADMIN_PASSWORD POSTGRES_PASSWORD \
    DATABASE_URL REDIS_URL NEO4J_PASSWORD NEO4J_AUTH \
    MINIO_ACCESS_KEY MINIO_SECRET_KEY API_HUB_SYSTEM_MCP_TOKEN \
    N8N_PASSWORD N8N_API_KEY; do
    value="$(env_value "$key")"
    [ -z "$value" ] || sensitive_values+=("$value")
  done
  compose logs --tail="$tail_lines" "${services[@]}" 2>&1 | while IFS= read -r line; do
    for value in "${sensitive_values[@]}"; do
      line="${line//"$value"/<redacted>}"
    done
    printf '%s\n' "$line"
  done
}
redact_backend_failure_logs() {
  COMPOSE_LOG_TAIL=2000 redact_compose_logs backend | awk '
    /Traceback \(most recent call last\)/ ||
    /The above exception was the direct cause/ ||
    /During handling of the above exception/ ||
    /File "\/app\/app\// ||
    /ERROR:/ ||
    /WARNING:/ ||
    /Error:/ ||
    /Exception:/ ||
    /索引创建失败/ ||
    /Neo4j unavailable/ {
      print
    }
  '
}
configure_storage_fallback() {
  local current enabled
  enabled="$(env_value STORAGE_LOCAL_FALLBACK)"
  case "$enabled" in
    0|false|FALSE|no|NO)
      set_env_value STORAGE_LOCAL_FALLBACK false
      log "local object-storage fallback is disabled"
      return
      ;;
    ""|1|true|TRUE|yes|YES) ;;
    *)
      log "STORAGE_LOCAL_FALLBACK must be true or false"
      exit 1
      ;;
  esac
  current="$(env_value STORAGE_LOCAL_DIR)"
  case "$current" in
    ""|storage|./storage)
      set_env_value STORAGE_LOCAL_DIR /uploads/object-storage
      log "configured STORAGE_LOCAL_DIR on the shared persistent uploads volume"
      ;;
    /uploads/object-storage) ;;
    /*)
      set_env_value STORAGE_LOCAL_DIR /uploads/object-storage
      log "normalized STORAGE_LOCAL_DIR to the production Compose shared volume"
      ;;
    *)
      log "STORAGE_LOCAL_DIR must be an absolute persistent path in production"
      exit 1
      ;;
  esac
  # The production containers share the uploads volume, so fallback is durable
  # and is safe to enable even when MinIO is temporarily or permanently absent.
  set_env_value STORAGE_LOCAL_FALLBACK true
}
configure_storage_fallback
configure_pipeline_file_gateway() {
  local current default_url public_url
  current="$(env_value PIPELINE_FILE_GATEWAY_BASE_URL)"
  default_url="http://backend:8000/api/v2/file-transfer"
  public_url="${HEALTH_URL%/}/api/v2/file-transfer"
  if [ -z "$current" ] || [ "$current" = "$default_url" ]; then
    set_env_value PIPELINE_FILE_GATEWAY_BASE_URL "$public_url"
    current="$public_url"
    log "configured PIPELINE_FILE_GATEWAY_BASE_URL from the deployment health URL"
  fi
  case "$current" in
    http://127.0.0.1/*|http://127.0.0.1:*|http://localhost/*|http://localhost:*|http://backend:*|https://*) ;;
    http://*)
      log "warning: PIPELINE_FILE_GATEWAY_BASE_URL uses public plain HTTP; configure HTTPS before production file transfers"
      ;;
    *)
      log "PIPELINE_FILE_GATEWAY_BASE_URL must be an absolute HTTP(S) URL"
      exit 1
      ;;
  esac
}
configure_pipeline_file_gateway
validate_pipeline_file_public_base() {
  local key="$1"
  local value="$2"
  local port
  if [[ ! "$value" =~ ^https?://(\[[0-9A-Fa-f:]+\]|[A-Za-z0-9.-]+)(:([0-9]{1,5}))?/?$ ]]; then
    log "$key must be an absolute HTTP(S) origin without credentials, path, query, or fragment"
    exit 1
  fi
  port="${BASH_REMATCH[3]:-}"
  if [ -n "$port" ] && (( 10#$port > 65535 )); then
    log "$key contains an invalid TCP port"
    exit 1
  fi
  case "$value" in
    http://*)
      log "warning: $key uses plain HTTP; configure HTTPS before sharing attachment links"
      ;;
  esac
}
configure_pipeline_file_public_base() {
  local key="$1"
  local old_default="$2"
  local current public_base
  current="$(env_value "$key")"
  public_base="${HEALTH_URL%/}"
  if [ -z "$current" ] || [ "$current" = "$old_default" ]; then
    set_env_value "$key" "$public_base"
    current="$public_base"
    log "configured $key from the deployment health URL"
  fi
  validate_pipeline_file_public_base "$key" "$current"
}
configure_pipeline_file_public_base \
  PIPELINE_FILE_PUBLIC_APP_BASE_URL http://localhost:5173
configure_pipeline_file_public_base \
  PIPELINE_FILE_PUBLIC_API_BASE_URL http://localhost:8000
require_secret() {
  local key="$1"
  local value
  value="$(env_value "$key")"
  [ -n "$value" ] || { log "$key is missing or empty in production .env"; exit 1; }
}
check_secret() {
  local key="$1"
  local insecure="$2"
  local value
  value="$(env_value "$key")"
  if [ -z "$value" ] || [ "$value" = "$insecure" ]; then
    case "${STRICT_PRODUCTION_CONFIG:-$(env_value STRICT_PRODUCTION_CONFIG)}" in
      1|true|TRUE|yes|YES)
        log "$key is missing or still uses an example credential"
        exit 1
        ;;
      *)
        log "warning: $key is missing or still uses an example credential; deployment continues in compatibility mode"
        ;;
    esac
  fi
}
check_secret SECRET_KEY dev-secret-key
check_secret SECRET_KEY change-me-to-a-random-32-char-string
check_secret FIRST_ADMIN_PASSWORD admin123
check_secret POSTGRES_PASSWORD ontoprompt
check_secret DATABASE_URL postgresql://ontoprompt:ontoprompt@db:5432/ontoprompt
check_secret NEO4J_PASSWORD ontoprompt123
check_secret NEO4J_AUTH neo4j/ontoprompt123
check_secret MINIO_ACCESS_KEY minioadmin
check_secret MINIO_SECRET_KEY minioadmin
validate_required_external_dependencies() {
  local key
  case "$(env_value REQUIRE_EXTERNAL_DEPENDENCIES)" in
    1|true|TRUE|yes|YES) ;;
    *) return ;;
  esac
  for key in \
    DATABASE_URL REDIS_URL NEO4J_URI NEO4J_USER NEO4J_PASSWORD \
    MINIO_ENDPOINT MINIO_ACCESS_KEY MINIO_SECRET_KEY; do
    require_secret "$key"
  done
  case "$(env_value ENVIRONMENT)" in
    production) ;;
    *) log "ENVIRONMENT=production is required"; exit 1 ;;
  esac
  case "$(env_value STORAGE_LOCAL_FALLBACK)" in
    0|false|FALSE|no|NO) ;;
    *) log "STORAGE_LOCAL_FALLBACK=false is required"; exit 1 ;;
  esac
  case "$(env_value DATASET_IMPORT_USE_CELERY)" in
    1|true|TRUE|yes|YES) ;;
    *) log "DATASET_IMPORT_USE_CELERY=true is required"; exit 1 ;;
  esac
  case "$(env_value DATABASE_URL)" in
    postgresql://*|postgres://*) ;;
    *) log "DATABASE_URL must use PostgreSQL"; exit 1 ;;
  esac
  case "$(env_value REDIS_URL)" in
    redis://:*@*|rediss://:*@*) ;;
    *) log "REDIS_URL must use single-password authentication"; exit 1 ;;
  esac
  case "$(env_value NEO4J_URI)" in
    bolt://*|bolt+s://*|neo4j://*|neo4j+s://*) ;;
    *) log "NEO4J_URI must use a supported Neo4j scheme"; exit 1 ;;
  esac
  case "$(env_value MINIO_ENDPOINT)" in
    *://*) log "MINIO_ENDPOINT must not include a URL scheme"; exit 1 ;;
    *:9001) log "MINIO_ENDPOINT must use the S3 API port, not console port 9001"; exit 1 ;;
    *:*) ;;
    *) log "MINIO_ENDPOINT must include the S3 API port"; exit 1 ;;
  esac
}
validate_required_external_dependencies
if [ -z "$(env_value ENCRYPTION_KEY)" ]; then
  log "ENCRYPTION_KEY is empty; preserving the existing SECRET_KEY-derived encryption key"
fi
check_image_digest() {
  local key="$1"
  local value
  value="$(env_value "$key")"
  if [[ ! "$value" =~ @sha256:[0-9a-fA-F]{64}$ ]]; then
    if [ "${STRICT_IMAGE_DIGESTS:-0}" = "1" ]; then
      log "$key must be pinned to an immutable @sha256 digest"
      exit 1
    fi
    log "warning: $key is not digest-pinned; set STRICT_IMAGE_DIGESTS=1 after populating immutable image references"
  fi
}
for image_key in \
  POSTGRES_IMAGE REDIS_IMAGE NEO4J_IMAGE MINIO_IMAGE CHROMA_IMAGE BROWSER_IMAGE \
  PYTHON_BASE_IMAGE NODE_BASE_IMAGE NGINX_BASE_IMAGE; do
  check_image_digest "$image_key"
done
if [ "${DEPLOY_VALIDATE_ONLY:-0}" = "1" ]; then
  log "production environment validation succeeded"
  exit 0
fi
command -v docker >/dev/null 2>&1 || { log "docker is not installed"; exit 1; }
log "building images"
run_with_retry compose build --pull
if case "$(env_value REQUIRE_EXTERNAL_DEPENDENCIES)" in
    1|true|TRUE|yes|YES) true ;;
    *) false ;;
  esac; then
  log "verifying required production dependency connectivity"
  run_with_retry compose run --rm --no-deps \
    backend python -m app.shared.dependency_probe
fi
log "running database migrations"
# Never rewrite Alembic history during a normal deploy.  If a legacy database
# needs a one-off baseline stamp it must be an explicit, audited operation.
log "  validating migration graph"
MIGRATION_HEADS="$(compose run --rm --no-deps backend alembic heads)"
printf '%s\n' "$MIGRATION_HEADS"
HEAD_COUNT="$(printf '%s\n' "$MIGRATION_HEADS" | grep -c '(head)' || true)"
if [ "$HEAD_COUNT" -ne 1 ]; then
  log "migration graph must have exactly one head, found ${HEAD_COUNT}"
  exit 1
fi
log "  upgrading to head"
run_with_retry compose run --rm --no-deps backend alembic upgrade head
log "starting services"
if ! run_with_retry compose up -d --remove-orphans; then
  log "service startup failed; printing redacted backend diagnostics"
  compose ps || true
  redact_backend_failure_logs || true
  exit 1
fi
log "waiting for backend, action worker and frontend readiness: ${READINESS_URL}"
for i in $(seq 1 30); do
  if curl -fsS --connect-timeout 5 --max-time 10 "$READINESS_URL" >/dev/null \
      && check_action_worker \
      && check_frontend_assets; then
    log "deployment succeeded"
    compose ps
    exit 0
  fi
  log "health check not ready (${i}/30)"
  sleep 10
done
log "deployment health check failed"
compose ps || true
redact_compose_logs backend browser frontend || true
exit 1
