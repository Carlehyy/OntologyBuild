#!/usr/bin/env bash
set -Eeuo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="${APP_DIR:-/opt/ontologybuild}"
bash "$SCRIPT_DIR/ci/validate-deploy-app-dir.sh" "$APP_DIR"
BRANCH="${BRANCH:-nano-ontoprompt}"
REPO_URL="${REPO_URL:-https://github.com/Carlehyy/OntologyBuild.git}"
COMPOSE_FILE="docker-compose.prod.yml"
DEPENDENCY_CONFIG_FILE="${DEPENDENCY_CONFIG_FILE:-production.dependencies.env}"
# Preserve explicit operator overrides, but defer defaults until the persistent
# .env and the production dependency manifest have been merged. The dependency
# manifest is mandatory and must configure every external runtime service.
# PUBLIC_PORT from that manifest drives the endpoint Compose will expose.
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
compose_environment() {
  # Docker Compose gives exported shell variables precedence over the project
  # .env file. The manifest has already been validated and persisted into
  # .env, so clear every interpolation/config authority that could otherwise
  # make the containers differ from what this script validated. Values are
  # deliberately named here rather than printed or dynamically expanded.
  env \
    -u ENVIRONMENT \
    -u PUBLIC_PORT \
    -u POSTGRES_DB \
    -u POSTGRES_USER \
    -u POSTGRES_PASSWORD \
    -u DATABASE_URL \
    -u REDIS_PASSWORD \
    -u REDIS_URL \
    -u NEO4J_URI \
    -u NEO4J_USER \
    -u NEO4J_PASSWORD \
    -u NEO4J_AUTH \
    -u MINIO_ENDPOINT \
    -u MINIO_ACCESS_KEY \
    -u MINIO_SECRET_KEY \
    -u MINIO_USE_SSL \
    -u N8N_API_URL \
    -u N8N_API_KEY \
    -u N8N_TIMEOUT_SECONDS \
    -u STEWARD_BROWSER_CDP_URL \
    -u CORS_ALLOWED_ORIGINS \
    -u UPLOADS_DIR \
    -u PIPELINE_FILE_GATEWAY_BASE_URL \
    -u PIPELINE_FILE_PUBLIC_APP_BASE_URL \
    -u PIPELINE_FILE_PUBLIC_API_BASE_URL \
    -u STEWARD_BROWSER_HTTP_LEASE_SECONDS \
    -u STEWARD_BROWSER_HTTP_FRAME_INTERVAL_MS \
    -u STEWARD_BROWSER_MAX_SESSIONS \
    -u STEWARD_BROWSER_MAX_SESSIONS_PER_USER \
    -u STEWARD_BROWSER_IDLE_TIMEOUT_SECONDS \
    -u STEWARD_BROWSER_REAPER_INTERVAL_SECONDS \
    -u POSTGRES_IMAGE \
    -u REDIS_IMAGE \
    -u NEO4J_IMAGE \
    -u MINIO_IMAGE \
    -u BROWSER_IMAGE \
    -u PYTHON_BASE_IMAGE \
    -u NODE_BASE_IMAGE \
    -u NGINX_BASE_IMAGE \
    -u STRICT_IMAGE_DIGESTS \
    -u COMPOSE_DISABLE_ENV_FILE \
    -u COMPOSE_ENV_FILES \
    -u COMPOSE_FILE \
    -u COMPOSE_PATH_SEPARATOR \
    -u COMPOSE_PROFILES \
    -u COMPOSE_PROJECT_NAME \
    "$@"
}
compose() {
  if compose_environment docker compose version >/dev/null 2>&1; then
    compose_environment docker compose -f "$COMPOSE_FILE" "$@"
  elif command -v docker-compose >/dev/null 2>&1; then
    compose_environment docker-compose -f "$COMPOSE_FILE" "$@"
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
start_required_dependency_services() {
  # CDP must be configured and is checked by deep readiness, but an unhealthy
  # browser must not prevent the API process from starting for diagnostics.
  compose up -d browser
  if compose up --help 2>&1 | grep -q -- '--wait'; then
    compose up -d --wait \
      --wait-timeout "${DEPENDENCY_WAIT_TIMEOUT:-180}" \
      db redis neo4j minio
  else
    # Legacy Compose has no --wait. The mandatory connectivity probe below is
    # retried and remains the authoritative availability gate.
    compose up -d db redis neo4j minio
  fi
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
  local db_password redis_password neo4j_password minio_access minio_secret
  db_password="$(random_hex 24)"
  redis_password="$(random_hex 24)"
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
  set_env_value REDIS_PASSWORD "$redis_password"
  set_env_value REDIS_URL "redis://:${redis_password}@redis:6379/0"
  set_env_value NEO4J_PASSWORD "$neo4j_password"
  set_env_value NEO4J_AUTH "neo4j/${neo4j_password}"
  set_env_value MINIO_ACCESS_KEY "$minio_access"
  set_env_value MINIO_SECRET_KEY "$minio_secret"
  # Keep the historical local:// root readable during migration. New writes
  # are always sent to MinIO and never fall back to this directory.
  set_env_value STORAGE_LOCAL_DIR /uploads/object-storage
  set_env_value ALLOW_PUBLIC_REGISTRATION false
  set_env_value API_HUB_SYSTEM_MCP_TOKEN "$(random_hex 32)"
  chmod 600 .env
  log "generated runtime secrets were stored in ${APP_DIR}/.env (values are not printed to CI logs)"
}
[ -f .env ] || bootstrap_production_env

dependency_key_allowed() {
  case "$1" in
    ENVIRONMENT|PUBLIC_PORT|\
    POSTGRES_DB|POSTGRES_USER|POSTGRES_PASSWORD|DATABASE_URL|REDIS_URL|\
    NEO4J_URI|NEO4J_USER|NEO4J_PASSWORD|NEO4J_AUTH|\
    MINIO_ENDPOINT|MINIO_ACCESS_KEY|MINIO_SECRET_KEY|MINIO_USE_SSL|\
    N8N_API_URL|N8N_API_KEY|N8N_TIMEOUT_SECONDS)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}
dependency_key_ignored_legacy() {
  # The protected production manifest may still contain these historical
  # fields. Accept them read-only during the migration, but never write them
  # into .env and never let them control runtime behavior.
  case "$1" in
    POSTGRES_HOST|POSTGRES_PORT|MINIO_CONSOLE_URL|\
    STRICT_PRODUCTION_CONFIG|REQUIRE_EXTERNAL_DEPENDENCIES|\
    DATASET_IMPORT_USE_CELERY|STORAGE_LOCAL_FALLBACK|\
    N8N_EMAIL|N8N_PASSWORD)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}
apply_production_dependency_config() {
  local raw key value applied=0
  if [ ! -f "$DEPENDENCY_CONFIG_FILE" ]; then
    log "$DEPENDENCY_CONFIG_FILE is required for production deployment"
    exit 1
  fi
  # The tracked manifest is a temporary compatibility exception containing
  # real connection credentials. Restrict the unpacked copy before reading it.
  chmod 600 "$DEPENDENCY_CONFIG_FILE"

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
    if [[ ! "$key" =~ ^[A-Z][A-Z0-9_]*$ ]]; then
      log "$DEPENDENCY_CONFIG_FILE contains unsupported key: $key"
      exit 1
    fi
    if dependency_key_ignored_legacy "$key"; then
      log "ignored deprecated production dependency setting: $key"
      continue
    fi
    if ! dependency_key_allowed "$key"; then
      log "$DEPENDENCY_CONFIG_FILE contains unsupported key: $key"
      exit 1
    fi
    if [ -z "$value" ]; then
      log "$DEPENDENCY_CONFIG_FILE contains an empty required value: $key"
      exit 1
    fi
    if [ "$key" = "DATABASE_URL" ]; then
      case "$value" in
        postgres://*)
          value="postgresql://${value#postgres://}"
          log "normalized legacy postgres:// DATABASE_URL to postgresql://"
          ;;
        postgresql+psycopg2://*)
          value="postgresql://${value#postgresql+psycopg2://}"
          log "normalized driver-qualified DATABASE_URL to postgresql://"
          ;;
      esac
    fi
    set_env_value "$key" "$value"
    applied=$((applied + 1))
  done < "$DEPENDENCY_CONFIG_FILE"
  log "applied ${applied} production dependency settings (values are not printed)"
}
apply_production_dependency_config
chmod 600 .env
# Chromium is bundled with the production Compose stack. Pin the internal CDP
# URL so existing server-side .env files receive the newly required setting.
set_env_value STEWARD_BROWSER_CDP_URL http://browser:9222
if [ -z "$(awk -F= '$1 == "N8N_TIMEOUT_SECONDS" {print substr($0, index($0,"=")+1)}' .env | tail -n1)" ]; then
  set_env_value N8N_TIMEOUT_SECONDS 30
fi
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
redis_password_is_compose_safe() {
  local value="$1"
  [ "$value" != "ontopromptredis123" ] \
    && [[ "$value" =~ ^[A-Za-z0-9._~-]{16,}$ ]]
}
configure_redis_auth() {
  local redis_url service_password url_password
  redis_url="$(env_value REDIS_URL)"
  service_password="$(env_value REDIS_PASSWORD)"

  case "$redis_url" in
    redis://redis:6379/0)
      # Compatibility for the exact historical bundled URL. Reuse a strong
      # persistent service secret when present; otherwise create one once.
      if ! redis_password_is_compose_safe "$service_password"; then
        service_password="$(random_hex 24)"
      fi
      set_env_value REDIS_PASSWORD "$service_password"
      set_env_value REDIS_URL \
        "redis://:${service_password}@redis:6379/0"
      log "upgraded the legacy bundled Redis URL to authenticated mode"
      ;;
    redis://:*@redis:6379/[0-9]|redis://:*@redis:6379/[0-9][0-9])
      # REDIS_URL is the manifest authority. For the bundled service, derive
      # requirepass from that same URL so client and server cannot diverge.
      url_password="${redis_url#redis://:}"
      url_password="${url_password%@redis:6379/*}"
      if ! redis_password_is_compose_safe "$url_password"; then
        log "bundled Redis URL must use an unescaped 16+ character password containing only letters, digits, '.', '_', '~' or '-'"
        exit 1
      fi
      set_env_value REDIS_PASSWORD "$url_password"
      log "synchronized the bundled Redis service password from REDIS_URL"
      ;;
    redis://*@redis:*|redis://*@redis/*|redis://redis:*|redis://redis/*|\
    rediss://*@redis:*|rediss://*@redis/*|rediss://redis:*|rediss://redis/*)
      log "bundled Redis URL must use redis://:<password>@redis:6379/<numeric-db>; use an external host for TLS or other connection modes"
      exit 1
      ;;
    *)
      # An external REDIS_URL remains untouched. The bundled container still
      # receives its own strong password, but backend/worker do not use it.
      if ! redis_password_is_compose_safe "$service_password"; then
        set_env_value REDIS_PASSWORD "$(random_hex 24)"
      fi
      log "preserved the external Redis URL; bundled Redis credentials are not used by clients"
      ;;
  esac
}
configure_redis_auth
percent_decode_url_component() {
  local encoded="$1" decoded="" prefix hex byte
  while [[ "$encoded" == *%* ]]; do
    prefix="${encoded%%\%*}"
    decoded+="$prefix"
    encoded="${encoded#*%}"
    if [ "${#encoded}" -lt 2 ]; then
      return 1
    fi
    hex="${encoded:0:2}"
    if [[ ! "$hex" =~ ^[0-9A-Fa-f]{2}$ ]]; then
      return 1
    fi
    printf -v byte '%b' "\\x${hex}"
    decoded+="$byte"
    encoded="${encoded:2}"
  done
  printf '%s' "${decoded}${encoded}"
}
bundled_postgres_url_matches_service() {
  local database_url="$1" remainder authority encoded_database
  local userinfo hostport encoded_user encoded_password
  local decoded_user decoded_password decoded_database

  remainder="${database_url#*://}"
  if [ "$remainder" = "$database_url" ] || [[ "$remainder" != */* ]]; then
    return 1
  fi
  authority="${remainder%%/*}"
  encoded_database="${remainder#*/}"
  if [[ "$encoded_database" == *\?* ]] \
      || [[ "$encoded_database" == *\#* ]] \
      || [[ "$encoded_database" == */* ]]; then
    return 1
  fi
  hostport="${authority##*@}"
  userinfo="${authority%@*}"
  if [ "$userinfo" = "$authority" ] || [ "$hostport" != "db:5432" ] \
      || [[ "$userinfo" != *:* ]]; then
    return 1
  fi
  encoded_user="${userinfo%%:*}"
  encoded_password="${userinfo#*:}"
  decoded_user="$(percent_decode_url_component "$encoded_user")" \
    || return 1
  decoded_password="$(percent_decode_url_component "$encoded_password")" \
    || return 1
  decoded_database="$(percent_decode_url_component "$encoded_database")" \
    || return 1
  [ "$decoded_user" = "$(env_value POSTGRES_USER)" ] \
    && [ "$decoded_password" = "$(env_value POSTGRES_PASSWORD)" ] \
    && [ "$decoded_database" = "$(env_value POSTGRES_DB)" ]
}
validate_bundled_service_authority() {
  local database_url neo4j_uri expected_neo4j_auth
  database_url="$(env_value DATABASE_URL)"
  case "$database_url" in
    postgresql://*'@db:5432/'*)
      if ! bundled_postgres_url_matches_service "$database_url"; then
        log "bundled DATABASE_URL must exactly match decoded POSTGRES_USER/POSTGRES_PASSWORD/POSTGRES_DB and db:5432"
        exit 1
      fi
      log "validated bundled PostgreSQL service credentials against DATABASE_URL"
      ;;
    postgresql://*'@db:'*|postgresql://*'@db/'*)
      log "bundled DATABASE_URL must use the canonical db:5432 endpoint"
      exit 1
      ;;
  esac

  case "$(env_value NEO4J_AUTH)" in
    ""|none|NONE|None)
      log "NEO4J_AUTH must enable authentication for the bundled Neo4j service"
      exit 1
      ;;
    */*) ;;
    *) log "NEO4J_AUTH must use username/password format"; exit 1 ;;
  esac
  neo4j_uri="$(env_value NEO4J_URI)"
  case "$neo4j_uri" in
    bolt://neo4j:7687|neo4j://neo4j:7687)
      expected_neo4j_auth="$(env_value NEO4J_USER)/$(env_value NEO4J_PASSWORD)"
      if [ "$(env_value NEO4J_AUTH)" != "$expected_neo4j_auth" ]; then
        log "bundled NEO4J_AUTH must match NEO4J_USER/NEO4J_PASSWORD"
        exit 1
      fi
      log "validated bundled Neo4j service credentials against client settings"
      ;;
    bolt://neo4j:*|neo4j://neo4j:*|bolt+s://neo4j:*|neo4j+s://neo4j:*|\
    bolt+ssc://neo4j:*|neo4j+ssc://neo4j:*)
      log "bundled NEO4J_URI must use bolt://neo4j:7687 or neo4j://neo4j:7687"
      exit 1
      ;;
  esac
}
validate_bundled_service_authority
effective_public_port="$(env_value PUBLIC_PORT)"
effective_public_port="${effective_public_port:-80}"
HEALTH_URL="${HEALTH_URL:-http://127.0.0.1:${effective_public_port}/}"
READINESS_URL="${READINESS_URL:-${HEALTH_URL%/}/api/health}"
redact_compose_logs() {
  local key value line tail_lines
  local -a sensitive_values=()
  local -a services=("$@")
  tail_lines="${COMPOSE_LOG_TAIL:-200}"
  for key in \
    SECRET_KEY ENCRYPTION_KEY FIRST_ADMIN_PASSWORD POSTGRES_PASSWORD \
    DATABASE_URL REDIS_PASSWORD REDIS_URL NEO4J_PASSWORD NEO4J_AUTH \
    MINIO_ACCESS_KEY MINIO_SECRET_KEY API_HUB_SYSTEM_MCP_TOKEN \
    N8N_API_KEY; do
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
set_env_value STORAGE_LOCAL_DIR /uploads/object-storage
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
    log "$key is missing or still uses an example credential"
    exit 1
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
validate_required_runtime_dependencies() {
  local key
  for key in \
    DATABASE_URL REDIS_URL NEO4J_URI NEO4J_USER NEO4J_PASSWORD \
    MINIO_ENDPOINT MINIO_ACCESS_KEY MINIO_SECRET_KEY \
    N8N_API_URL N8N_API_KEY STEWARD_BROWSER_CDP_URL; do
    require_secret "$key"
  done
  case "$(env_value ENVIRONMENT)" in
    production) ;;
    *) log "ENVIRONMENT=production is required"; exit 1 ;;
  esac
  case "$(env_value DATABASE_URL)" in
    postgresql://*) ;;
    *) log "DATABASE_URL must use the canonical postgresql:// scheme"; exit 1 ;;
  esac
  case "$(env_value REDIS_URL)" in
    redis://:*@*|rediss://:*@*) ;;
    *) log "REDIS_URL must use single-password authentication"; exit 1 ;;
  esac
  case "$(env_value NEO4J_URI)" in
    bolt://*|bolt+s://*|bolt+ssc://*|neo4j://*|neo4j+s://*|neo4j+ssc://*) ;;
    *) log "NEO4J_URI must use a supported Neo4j scheme"; exit 1 ;;
  esac
  case "$(env_value MINIO_ENDPOINT)" in
    *://*) log "MINIO_ENDPOINT must not include a URL scheme"; exit 1 ;;
    *:9001) log "MINIO_ENDPOINT must use the S3 API port, not console port 9001"; exit 1 ;;
    *:*) ;;
    *) log "MINIO_ENDPOINT must include the S3 API port"; exit 1 ;;
  esac
  case "$(env_value N8N_API_URL)" in
    http://*|https://*) ;;
    *) log "N8N_API_URL must be an absolute HTTP(S) URL"; exit 1 ;;
  esac
  case "$(env_value N8N_TIMEOUT_SECONDS)" in
    ""|*[!0-9]*)
      log "N8N_TIMEOUT_SECONDS must be an integer between 3 and 120"
      exit 1
      ;;
    *)
      if [ "$(env_value N8N_TIMEOUT_SECONDS)" -lt 3 ] \
          || [ "$(env_value N8N_TIMEOUT_SECONDS)" -gt 120 ]; then
        log "N8N_TIMEOUT_SECONDS must be an integer between 3 and 120"
        exit 1
      fi
      ;;
  esac
  case "$(env_value STEWARD_BROWSER_CDP_URL)" in
    http://*|https://*) ;;
    *) log "STEWARD_BROWSER_CDP_URL must be an absolute HTTP(S) URL"; exit 1 ;;
  esac
}
validate_required_runtime_dependencies
if [ -z "$(env_value ENCRYPTION_KEY)" ]; then
  log "ENCRYPTION_KEY is empty; preserving the existing SECRET_KEY-derived encryption key"
fi
strict_image_digests_value="$(env_value STRICT_IMAGE_DIGESTS)"
case "$strict_image_digests_value" in
  ""|0|false|FALSE|no|NO)
    strict_image_digests_enabled=0
    ;;
  1|true|TRUE|yes|YES)
    strict_image_digests_enabled=1
    ;;
  *)
    log "STRICT_IMAGE_DIGESTS must be true or false"
    exit 1
    ;;
esac
check_image_digest() {
  local key="$1"
  local value
  value="$(env_value "$key")"
  if [[ ! "$value" =~ @sha256:[0-9a-fA-F]{64}$ ]]; then
    if [ "$strict_image_digests_enabled" = "1" ]; then
      log "$key must be pinned to an immutable @sha256 digest"
      exit 1
    fi
    log "warning: $key is not digest-pinned; set STRICT_IMAGE_DIGESTS=1 after populating immutable image references"
  fi
}
for image_key in \
  POSTGRES_IMAGE REDIS_IMAGE NEO4J_IMAGE MINIO_IMAGE BROWSER_IMAGE \
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
log "starting required dependency services"
run_with_retry start_required_dependency_services
log "verifying required production dependency connectivity"
run_with_retry compose run --rm --no-deps \
  backend python -m app.shared.dependency_probe
log "stopping backend and Celery worker before database migrations"
# Revision 0055 changes the projection readiness contract. Old application
# processes do not understand that fence, so no API/worker writer may remain
# active while Alembic upgrades the database.
run_with_retry compose stop -t 30 backend celery_worker
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
