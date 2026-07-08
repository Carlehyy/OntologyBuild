#!/usr/bin/env bash
set -Eeuo pipefail
APP_DIR="${APP_DIR:-/opt/ontologybuild}"
BRANCH="${BRANCH:-nano-ontoprompt}"
REPO_URL="${REPO_URL:-https://github.com/Carlehyy/OntologyBuild.git}"
COMPOSE_FILE="docker-compose.prod.yml"
HEALTH_URL="${HEALTH_URL:-http://127.0.0.1:${PUBLIC_PORT:-80}/}"
RETRIES="${DEPLOY_RETRIES:-3}"
SLEEP_SECONDS="${DEPLOY_RETRY_SLEEP:-10}"
log() { printf '[%s] %s\n' "$(date -Is)" "$*"; }
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
command -v docker >/dev/null 2>&1 || { log "docker is not installed"; exit 1; }
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
[ -f .env ] || cp .env.example .env
log "building images"
run_with_retry compose build --pull
log "running database migrations"
# 先 stamp 到已知已应用的基准版本（跳过可能已手动执行但未记录的 migration），
# 再 upgrade head 应用新的 migration。stamp 失败（如已是最新）不阻塞部署。
log "  stamping to base revision (ignore errors if already stamped)"
compose run --rm backend alembic stamp 0006_element_source_lineage 2>&1 || true
log "  upgrading to head"
run_with_retry compose run --rm backend alembic upgrade head
log "starting services"
run_with_retry compose up -d --remove-orphans
log "waiting for public health endpoint: ${HEALTH_URL}"
for i in $(seq 1 30); do
  if curl -fsS --connect-timeout 5 --max-time 10 "$HEALTH_URL" >/dev/null && check_frontend_assets; then
    log "deployment succeeded"
    compose ps
    exit 0
  fi
  log "health check not ready (${i}/30)"
  sleep 10
done
log "deployment health check failed"
compose ps || true
compose logs --tail=200 backend frontend || true
exit 1
