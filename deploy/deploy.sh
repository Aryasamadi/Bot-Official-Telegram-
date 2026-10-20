#!/usr/bin/env bash
# NewsBot deploy — run on the VPS by .github/workflows/deploy-vps.yml (or by hand):
#
#   deploy.sh deploy <git-sha>   online backup → sync to <sha> → install → gate → restart → health
#   deploy.sh rollback           restore the previously recorded SHA and restart
#
# Contract:
#   * failure before `restart`  → service never touched, code left at target only if sync
#     itself succeeded and gate caught it (then code is rolled back);
#   * failure after `restart`   → code rolled back to the previous SHA and the service
#     restarted again on the old code (real rollback — not just a claim);
#   * `.deployed_sha` / `.deployed_sha.prev` in APP_DIR record current/previous release.
#
# Overridable for tests: APP_DIR, BACKUP_DIR, SERVICE, KEEP_BACKUPS, HEALTH_TRIES.
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/bot-ai-automatic}"
BACKUP_DIR="${BACKUP_DIR:-/opt/backups}"
SERVICE="${SERVICE:-bot-ai-automatic}"
KEEP_BACKUPS="${KEEP_BACKUPS:-7}"
HEALTH_TRIES="${HEALTH_TRIES:-10}"
RESTARTED=0

log() { printf '[deploy] %s\n' "$*"; }
err() { printf '[deploy][error] %s\n' "$*" >&2; }
die() { err "$*"; exit 1; }

preflight() {
  command -v git >/dev/null 2>&1 || die "git not found in PATH"
  command -v python3 >/dev/null 2>&1 || die "python3 not found in PATH (needed for online backup)"
  command -v systemctl >/dev/null 2>&1 || die "systemctl not found in PATH"
  [ -d "$APP_DIR/.git" ] || die "not a git checkout: $APP_DIR"
  [ -f "$APP_DIR/.env" ] || die ".env missing in $APP_DIR"
  [ -x "$APP_DIR/.venv/bin/python" ] || die ".venv/bin/python missing in $APP_DIR"
  [ -x "$APP_DIR/.venv/bin/pip" ] || die ".venv/bin/pip missing in $APP_DIR"
}

# Online SQLite backup (safe under WAL — `cp` of a live DB is not).
backup_db() {
  local db="$APP_DIR/newsbot.db"
  if [ ! -f "$db" ]; then
    log "no database yet — skipping backup"
    return 0
  fi
  mkdir -p "$BACKUP_DIR" || return 1
  local dest="$BACKUP_DIR/newsbot-$(date +%Y%m%d-%H%M%S).$$.db"
  python3 - "$db" "$dest" <<'PY' || return 1
import sqlite3, sys
src = sqlite3.connect(sys.argv[1], timeout=30)
dst = sqlite3.connect(sys.argv[2])
try:
    src.backup(dst)
finally:
    dst.close()
    src.close()
PY
  chmod 600 "$dest" || return 1
  # فقط KEEP_BACKUPS بک‌اپ اخیر نگه داشته می‌شود
  ls -1t "$BACKUP_DIR"/newsbot-*.db 2>/dev/null | tail -n +$((KEEP_BACKUPS + 1)) | xargs -r rm --
  log "backup written: $dest"
  return 0
}

sync_to() {
  local sha="$1"
  cd "$APP_DIR" || return 1
  git fetch -q origin main || return 1
  git cat-file -e "${sha}^{commit}" 2>/dev/null || return 1
  git reset -q --hard "$sha" || return 1
  # OPS-02: HEAD باید عیناً همان SHA محرک deploy باشد
  [ "$(git rev-parse HEAD)" = "$sha" ] || return 1
  log "code synced to $sha"
}

install_deps() {
  cd "$APP_DIR" || return 1
  log "installing pinned requirements"
  .venv/bin/pip install --quiet -r requirements.txt || return 1
  return 0
}

# دروازهٔ پیش از restart — شکست یعنی سرویس قدیمی دست‌نخورده می‌ماند
gate() {
  cd "$APP_DIR" || return 1
  .venv/bin/python -m compileall -q newsbot run.py || return 1
  .venv/bin/python run.py --check || return 1
  return 0
}

start_service() {
  RESTARTED=1
  systemctl restart "$SERVICE" || die "systemctl restart failed"
}

# سلامت واقعی: فعال‌شدن با صبر، نبودِ crash-loop، نبود traceback تازه در ژورنال
health() {
  local i
  for i in $(seq 1 "$HEALTH_TRIES"); do
    if systemctl is-active --quiet "$SERVICE"; then
      break
    fi
    if [ "$i" -eq "$HEALTH_TRIES" ]; then
      err "service '$SERVICE' still not active after ${HEALTH_TRIES}s"
      return 1
    fi
    sleep 1
  done
  local nrestarts
  nrestarts="$(systemctl show -p NRestarts --value "$SERVICE" 2>/dev/null || echo 0)"
  case "$nrestarts" in ''|*[!0-9]*) nrestarts=0 ;; esac
  if [ "$nrestarts" -ne 0 ]; then
    err "service '$SERVICE' is crash-looping (NRestarts=$nrestarts)"
    return 1
  fi
  if journalctl -u "$SERVICE" --since "@$(($(date +%s) - 120))" --no-pager 2>/dev/null | grep -Eiq 'traceback|fatal error'; then
    err "fresh traceback/fatal error found in journal for '$SERVICE'"
    return 1
  fi
  log "health check passed"
  return 0
}

# شکست پس از sync/install/gate → کد به SHA قبلی برمی‌گردد؛ اگر restart شده، دوباره روی کد قبلی بالا می‌آید
rollback_and_die() {
  err "$1 — rolling code back to $prev"
  cd "$APP_DIR" 2>/dev/null || true
  git reset -q --hard "$prev" || true
  if [ "$RESTARTED" -eq 1 ]; then
    systemctl restart "$SERVICE" || true
    log "service restarted on previous code $prev"
  fi
  exit 1
}

do_deploy() {
  local target="$1"
  preflight
  cd "$APP_DIR" || die "cannot cd into $APP_DIR"
  local prev
  prev="$(git rev-parse HEAD)" || die "cannot read HEAD of $APP_DIR"
  if [ "$prev" = "$target" ]; then
    log "already at $target — nothing to do"
    exit 0
  fi
  printf '%s\n' "$prev" > "$APP_DIR/.deployed_sha.prev" || die "cannot record previous sha"
  backup_db || die "database backup failed — aborting before any change"
  sync_to "$target" || die "code sync to $target failed — aborting (service not touched)"
  install_deps || rollback_and_die "dependency install failed"
  gate || rollback_and_die "pre-restart gate failed"
  start_service
  health || rollback_and_die "post-restart health check failed"
  printf '%s\n' "$target" > "$APP_DIR/.deployed_sha" || die "cannot record deployed sha"
  log "deploy complete: $prev -> $target"
}

do_rollback() {
  preflight
  cd "$APP_DIR" || die "cannot cd into $APP_DIR"
  [ -f "$APP_DIR/.deployed_sha.prev" ] || die "no previous sha recorded (.deployed_sha.prev) — nothing to roll back to"
  local back
  back="$(tr -d '[:space:]' < "$APP_DIR/.deployed_sha.prev")"
  [ -n "$back" ] || die "recorded previous sha is empty"
  git cat-file -e "${back}^{commit}" 2>/dev/null || die "previous sha $back not present in repository"
  git reset -q --hard "$back" || die "git reset to $back failed"
  [ "$(git rev-parse HEAD)" = "$back" ] || die "rollback verification failed (HEAD != $back)"
  start_service
  health || die "health check failed after rollback — inspect manually"
  printf '%s\n' "$back" > "$APP_DIR/.deployed_sha" || die "cannot record deployed sha"
  log "rolled back to $back"
}

case "${1:-}" in
  deploy)
    [ $# -eq 2 ] || die "usage: deploy.sh deploy <git-sha>"
    do_deploy "$2"
    ;;
  rollback)
    do_rollback
    ;;
  *)
    die "usage: deploy.sh {deploy <git-sha> | rollback}"
    ;;
esac
