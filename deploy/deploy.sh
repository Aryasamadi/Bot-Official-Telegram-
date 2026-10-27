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
# Overridable for tests: APP_DIR, BACKUP_DIR, SERVICE, KEEP_BACKUPS, HEALTH_TRIES,
#                        VENV_DIR, DEPLOY_DB_FILE.
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/bot-official-telegram}"
BACKUP_DIR="${BACKUP_DIR:-/opt/backups}"
SERVICE="${SERVICE:-newsbot.service}"
KEEP_BACKUPS="${KEEP_BACKUPS:-7}"
HEALTH_TRIES="${HEALTH_TRIES:-10}"
VENV_DIR="${VENV_DIR:-}"
DEPLOY_DB_FILE="${DEPLOY_DB_FILE:-}"
RESTARTED=0
DB_BACKEND_EFFECTIVE=""
DB_PATH_EFFECTIVE=""

log() { printf '[deploy] %s\n' "$*"; }
err() { printf '[deploy][error] %s\n' "$*" >&2; }
die() { err "$*"; exit 1; }

# ── خواندن پیکربندی واقعی برنامه ──────────────────────────────────────────────
# newsbot/config.py پیکربندی را از <ریشهٔ پروژه>/.env می‌خواند (load_dotenv) و
# DB_FILE/DB_BACKEND را از همان‌جا می‌گیرد. اینجا دقیقاً همان منبع خوانده می‌شود
# تا backup روی همان دیتابیسی برود که سرویس واقعاً از آن استفاده می‌کند — نه روی
# یک مسیر حدسی. متغیرهای محیطِ فرایندِ deploy عمداً نادیده گرفته می‌شوند (می‌توانند
# از یک نشست کاملاً نامرتبط آمده باشند)؛ برای انتخاب صریح، DEPLOY_DB_FILE هست.
read_app_env() {   # APP_ENV_VALUE و APP_ENV_FOUND را می‌گذارد (بدون زیرپوسته)
  local key="$1" file="$2" line value
  APP_ENV_VALUE=""
  APP_ENV_FOUND=0
  [ -f "$file" ] || return 0
  while IFS= read -r line || [ -n "$line" ]; do
    line="${line%$'\r'}"
    line="${line#"${line%%[![:space:]]*}"}"
    case "$line" in
      "$key="*) value="${line#*=}" ;;
      "export $key="*) value="${line#export $key=}" ;;
      *) continue ;;
    esac
    value="${value%"${value##*[![:space:]]}"}"
    case "$value" in
      \"*\") value="${value#\"}"; value="${value%\"}" ;;
      \'*\') value="${value#\'}"; value="${value%\'}" ;;
    esac
    APP_ENV_VALUE="$value"
    APP_ENV_FOUND=1
  done < "$file"
  return 0
}

# مسیر دیتابیس و بک‌اند، دقیقاً با همان قواعدی که newsbot/config.py دارد
resolve_app_db() {
  local raw backend
  read_app_env DB_FILE "$APP_DIR/.env"
  raw="$APP_ENV_VALUE"
  local raw_found="$APP_ENV_FOUND"
  read_app_env DB_BACKEND "$APP_DIR/.env"
  backend="$APP_ENV_VALUE"
  if [ "$raw_found" -eq 1 ] && [ -z "$raw" ]; then
    die "DB_FILE is present but empty in $APP_DIR/.env — refusing to guess the database path"
  fi
  [ -n "$raw" ] || raw="newsbot.db"            # newsbot/config.py:83
  [ -n "$backend" ] || backend="sqlite"        # newsbot/config.py:84
  DB_BACKEND_EFFECTIVE="$(printf '%s' "$backend" | tr '[:upper:]' '[:lower:]' | tr -d '[:space:]')"
  if [ -n "$DEPLOY_DB_FILE" ]; then
    raw="$DEPLOY_DB_FILE"
  fi
  case "$raw" in
    /*|[A-Za-z]:[\\/]*) DB_PATH_EFFECTIVE="$raw" ;;   # مسیر مطلق — مثل هاست واقعی
    *)  DB_PATH_EFFECTIVE="$APP_DIR/$raw" ;;          # نسبی ⇒ نسبت به WorkingDirectory سرویس
  esac
}

# محیط مجازی: صریح (VENV_DIR) یا خودکار با ترتیب قطعی؛ ابهام ⇒ خطا، نه حدس
resolve_venv_dir() {
  if [ -n "$VENV_DIR" ]; then
    case "$VENV_DIR" in /*|[A-Za-z]:[\\/]*) ;; *) VENV_DIR="$APP_DIR/$VENV_DIR" ;; esac
    VENV_DIR="${VENV_DIR%/}"
    [ -d "$VENV_DIR" ] || die "VENV_DIR does not exist: $VENV_DIR"
    return 0
  fi
  local dot_venv="$APP_DIR/.venv" plain_venv="$APP_DIR/venv" has_dot=0 has_plain=0
  if [ -d "$dot_venv" ]; then has_dot=1; fi
  if [ -d "$plain_venv" ]; then has_plain=1; fi
  if [ "$has_dot" -eq 1 ] && [ "$has_plain" -eq 1 ]; then
    die "ambiguous virtualenv in $APP_DIR: both .venv and venv exist — set VENV_DIR to the one the service uses"
  elif [ "$has_dot" -eq 1 ]; then
    VENV_DIR="$dot_venv"
  elif [ "$has_plain" -eq 1 ]; then
    VENV_DIR="$plain_venv"
  else
    die "no virtualenv found in $APP_DIR (expected .venv or venv) — set VENV_DIR explicitly"
  fi
}

preflight() {
  command -v git >/dev/null 2>&1 || die "git not found in PATH"
  command -v python3 >/dev/null 2>&1 || die "python3 not found in PATH (needed for online backup)"
  command -v systemctl >/dev/null 2>&1 || die "systemctl not found in PATH"
  [ -d "$APP_DIR/.git" ] || die "not a git checkout: $APP_DIR"
  [ -f "$APP_DIR/.env" ] || die ".env missing in $APP_DIR"
  resolve_venv_dir
  [ -x "$VENV_DIR/bin/python" ] || die "python interpreter missing: $VENV_DIR/bin/python"
  [ -x "$VENV_DIR/bin/pip" ] || die "pip missing: $VENV_DIR/bin/pip"
  resolve_app_db
}

# Online SQLite backup (safe under WAL — `cp` of a live DB is not).
# مسیر دیتابیس از پیکربندی خود برنامه خوانده می‌شود؛ نبود یا ابهام ⇒ توقف صریح
# (بکاپ هرگز بی‌صدا رد نمی‌شود، چون «بکاپ نبودن» یعنی ریسک از‌دست‌رفتن داده).
backup_db() {
  local db="$DB_PATH_EFFECTIVE"
  if [ "$DB_BACKEND_EFFECTIVE" = "d1" ]; then
    log "backend=d1 — the database lives in Cloudflare D1; there is no local SQLite file to back up"
    return 0
  fi
  if [ ! -f "$db" ]; then
    err "database file not found: $db"
    err "  configured by DB_FILE in $APP_DIR/.env (backend=$DB_BACKEND_EFFECTIVE)"
    local alt found=""
    for alt in "$APP_DIR/newsbot.db" "$APP_DIR/data/newsbot.db"; do
      if [ -f "$alt" ]; then found="$found $alt"; fi
    done
    if [ -n "$found" ]; then
      err "  other database files exist:$found"
      err "  DB_FILE does not point at the live database — fix DB_FILE in .env (or set DEPLOY_DB_FILE) before deploying"
    fi
    err "refusing to deploy without a database backup — no code was changed"
    return 1
  fi
  mkdir -p "$BACKUP_DIR" || return 1
  local dest tmp
  dest="$BACKUP_DIR/newsbot-$(date +%Y%m%d-%H%M%S).$$.db"
  tmp="$dest.tmp"
  if ! python3 - "$db" "$tmp" <<'PY'
import sqlite3, sys
from pathlib import Path

src_path, dst_path = sys.argv[1], sys.argv[2]


def snapshot(readonly):
    if readonly:
        # فقط‌خواندنی ⇒ فایل دیتابیس دست‌نخورده می‌ماند؛ روی WAL به‌دلیل قفل -shm
        # ممکن است ممکن نباشد، پس در آن حالت به حالت معمولی برمی‌گردیم.
        src = sqlite3.connect(Path(src_path).resolve().as_uri() + "?mode=ro", uri=True, timeout=30)
    else:
        src = sqlite3.connect(src_path, timeout=30)
    dst = sqlite3.connect(dst_path)
    try:
        pages_before = int(src.execute("PRAGMA page_count").fetchone()[0])
        src.backup(dst)
        dst.commit()
        changes = src.total_changes
    finally:
        dst.close()
        src.close()
    return pages_before, changes


try:
    pages_before, changes = snapshot(True)
except sqlite3.Error as exc:
    print(f"note: read-only snapshot unavailable ({exc}); retrying read-write", file=sys.stderr)
    pages_before, changes = snapshot(False)

if changes:
    print(f"source database was modified during the backup (total_changes={changes})", file=sys.stderr)
    raise SystemExit(1)

check = sqlite3.connect(dst_path)
try:
    status = check.execute("PRAGMA integrity_check").fetchone()[0]
    pages = int(check.execute("PRAGMA page_count").fetchone()[0])
finally:
    check.close()

if str(status).strip().lower() != "ok":
    print(f"backup integrity check failed: {status}", file=sys.stderr)
    raise SystemExit(1)
if pages < pages_before:
    print(f"backup is incomplete: {pages} pages copied, source had {pages_before}", file=sys.stderr)
    raise SystemExit(1)
PY
  then
    err "database backup failed — no backup file was kept and no code was changed"
    rm -f "$tmp"
    return 1
  fi
  chmod 600 "$tmp" || { rm -f "$tmp"; return 1; }
  mv -f "$tmp" "$dest" || { rm -f "$tmp"; return 1; }
  # فقط KEEP_BACKUPS بکاپ اخیر نگه داشته می‌شود
  ls -1t "$BACKUP_DIR"/newsbot-*.db 2>/dev/null | tail -n +$((KEEP_BACKUPS + 1)) | xargs -r rm -- || log "warning: pruning old backups failed"
  log "backup written: $dest"
  return 0
}

require_clean_worktree() {
  local action="$1"
  local status
  if ! status="$(git status --porcelain=v1 --untracked-files=normal 2>&1)"; then
    err "cannot inspect Git working tree in $APP_DIR: $status"
    return 1
  fi
  if [ -n "$status" ]; then
    err "working tree in $APP_DIR has uncommitted changes (tracked, staged, or untracked) — refusing $action."
    printf '%s\n' "$status" >&2
    err "Commit, stash, or remove them explicitly, then re-run."
    return 1
  fi
}

assert_no_untracked_overwrites() {
  local sha="$1"
  if ! python3 - "$sha" <<'PY'
import subprocess
import sys

sha = sys.argv[1]

def paths(args):
    data = subprocess.check_output(args)
    return [p.decode("utf-8", "surrogateescape") for p in data.split(b"\0") if p]

try:
    target = set(paths(["git", "ls-tree", "-rz", "--name-only", "-r", sha]))
    local_paths = set(paths(["git", "ls-files", "-z", "--others"]))
except (OSError, subprocess.CalledProcessError) as exc:
    print(f"cannot compare untracked files with target commit: {exc}", file=sys.stderr)
    raise SystemExit(1)

target_parents = set()
for path in target:
    parent = path
    while "/" in parent:
        parent = parent.rsplit("/", 1)[0]
        target_parents.add(parent)

conflicts = []
for path in local_paths:
    if path in target:
        conflicts.append(path)
        continue
    parent = path
    while "/" in parent:
        parent = parent.rsplit("/", 1)[0]
        if parent in target:
            conflicts.append(path)
            break
    else:
        if path in target_parents:
            conflicts.append(path)

if conflicts:
    for path in sorted(conflicts):
        print(f"untracked path would be overwritten by target commit: {path}", file=sys.stderr)
    raise SystemExit(1)
PY
  then
    err "target commit $sha conflicts with local untracked files; no code was changed."
    return 1
  fi
}

reset_to_sha() {
  local sha="$1"
  assert_no_untracked_overwrites "$sha" || return 1
  if ! git reset -q --keep "$sha"; then
    err "git reset to $sha failed; inspect the working tree before retrying."
    return 1
  fi
  local head
  if ! head="$(git rev-parse HEAD 2>&1)"; then
    err "cannot verify HEAD after reset to $sha: $head"
    return 1
  fi
  [ "$head" = "$sha" ] || {
    err "reset verification failed (HEAD=$head, expected=$sha)."
    return 1
  }
}

sync_to() {
  local sha="$1"
  cd "$APP_DIR" || return 1
  require_clean_worktree "deploy to $sha" || return 1
  if ! git fetch -q origin main; then
    err "git fetch origin main failed; code was not changed."
    return 1
  fi
  if ! git cat-file -e "${sha}^{commit}" 2>/dev/null; then
    err "target commit $sha is not present in the repository after fetch."
    return 1
  fi
  reset_to_sha "$sha" || return 1
  log "code synced to $sha"
}

install_deps() {
  cd "$APP_DIR" || return 1
  log "installing pinned requirements"
  "$VENV_DIR/bin/pip" install --quiet -r requirements.txt || return 1
  return 0
}

# دروازهٔ پیش از restart — شکست یعنی سرویس قدیمی دست‌نخورده می‌ماند
gate() {
  cd "$APP_DIR" || return 1
  "$VENV_DIR/bin/python" -m compileall -q newsbot run.py || return 1
  # DB_FILE/DB_BACKEND از env تصادفیِ فرایندِ deploy پاک می‌شوند تا خودِ برنامه آن‌ها را
  # از .env بخواند — دقیقاً مثل سرویس (systemd EnvironmentFile + load_dotenv). وگرنه دروازه
  # ممکن است دیتابیسی را بسنجد که سرویس اصلاً از آن استفاده نمی‌کند.
  ( unset DB_FILE DB_BACKEND; "$VENV_DIR/bin/python" run.py --check ) || return 1
  return 0
}

start_service() {
  RESTARTED=1
  systemctl restart "$SERVICE" || {
    err "systemctl restart failed"
    return 1
  }
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
  if reset_to_sha "$prev"; then
    if [ "$RESTARTED" -eq 1 ]; then
      if systemctl restart "$SERVICE"; then
        log "service restarted on previous code $prev"
      else
        err "service restart on previous code $prev failed"
      fi
    fi
  else
    err "automatic rollback to $prev failed; no service restart was attempted."
  fi
  exit 1
}

do_deploy() {
  local target="$1"
  preflight
  cd "$APP_DIR" || die "cannot cd into $APP_DIR"
  require_clean_worktree "deploy to $target" || die "deploy refused because the working tree has local changes"
  local prev
  prev="$(git rev-parse HEAD)" || die "cannot read HEAD of $APP_DIR"
  if [ "$prev" = "$target" ]; then
    log "already at $target — nothing to do"
    exit 0
  fi
  backup_db || die "database backup failed — aborting before any change"
  printf '%s\n' "$prev" > "$APP_DIR/.deployed_sha.prev" || die "cannot record previous sha"
  sync_to "$target" || die "code sync to $target failed — aborting (service not touched)"
  install_deps || rollback_and_die "dependency install failed"
  gate || rollback_and_die "pre-restart gate failed"
  start_service || rollback_and_die "service restart failed"
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
  require_clean_worktree "rollback to $back" || die "rollback refused because the working tree has local changes"
  reset_to_sha "$back" || die "git reset to $back failed"
  start_service || die "systemctl restart failed during rollback"
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
