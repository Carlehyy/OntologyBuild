#!/usr/bin/env bash
set -Eeuo pipefail
APP_DIR="${APP_DIR:-/opt/ontologybuild}"
BRANCH="${BRANCH:-nano-ontoprompt}"
REPO_URL="${REPO_URL:-https://github.com/Carlehyy/OntologyBuild.git}"
COMPOSE_FILE="docker-compose.prod.yml"
HEALTH_URL="${HEALTH_URL:-http://127.0.0.1:${PUBLIC_PORT:-80}/}"
READINESS_URL="${READINESS_URL:-${HEALTH_URL%/}/api/health}"
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
  set_env_value STORAGE_LOCAL_FALLBACK false
  set_env_value ALLOW_PUBLIC_REGISTRATION false
  set_env_value API_HUB_SYSTEM_MCP_TOKEN "$(random_hex 32)"
  set_env_value STRICT_PRODUCTION_CONFIG false
  chmod 600 .env
  log "generated runtime secrets were stored in ${APP_DIR}/.env (values are not printed to CI logs)"
}
[ -f .env ] || bootstrap_production_env
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
log "running database migrations"
# Never rewrite Alembic history during a normal deploy.  If a legacy database
# needs a one-off baseline stamp it must be an explicit, audited operation.
log "  validating migration graph"
MIGRATION_HEADS="$(compose run --rm backend alembic heads)"
printf '%s\n' "$MIGRATION_HEADS"
HEAD_COUNT="$(printf '%s\n' "$MIGRATION_HEADS" | grep -c '(head)' || true)"
if [ "$HEAD_COUNT" -ne 1 ]; then
  log "migration graph must have exactly one head, found ${HEAD_COUNT}"
  exit 1
fi
log "  upgrading to head"
run_with_retry compose run --rm backend alembic upgrade head
log "starting services"
run_with_retry compose up -d --remove-orphans
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
compose logs --tail=200 backend browser frontend || true
exit 1
