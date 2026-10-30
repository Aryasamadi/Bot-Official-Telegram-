#!/usr/bin/env bash
# NewsBot deploy — run on the VPS by .github/workflows/deploy-vps.yml (or by hand):
#
#   deploy.sh deploy <git-sha>   online backup → sync to <sha> → install → gate → restart → health
#   deploy.sh rollback           restore the previously recorded SHA and restart
#
# Contract:
#   * failure before `restart`  → service never touched, code left at target only if sync
#     itself succeeded and gate caught it (then code is rolled back);
#   * failure after `restart`   → code rolled back to the previous SHA, the *dependencies*
#     restored to the previous release, and the service restarted again on the old code
#     (real rollback — not just a claim);
#   * `.deployed_sha` / `.deployed_sha.prev` in APP_DIR record current/previous release.
#
# PH-04 hardening (2026-10-20):
#   OPS-02  the health budget now matches the unit's TimeoutStartSec (180s default) and, for
#            `Type=notify`, waits for READY=1 (SubState=running) instead of a 10s guess;
#   OPS-03  the journal window is anchored to *this* restart and looks for real crash markers
#            only — the word "traceback" is not a marker (the app logs handled exceptions with
#            log.exception, and those were false-failing a good release);
#   OPS-04  the deploy script itself is taken from TARGET_SHA (see deploy-vps.yml);
#   OPS-06  a rollback restores the previous dependency set as well as the previous code;
#   OPS-09  every backup is verified (reopen + quick_check) before the deploy continues;
#   OPS-10  the article-archive folder next to the database is included in the backup;
#   OPS-25  re-deploying the SHA that is already deployed still backs up and health-checks;
#   OPS-26  a failed `systemctl restart` triggers the real rollback instead of exiting.
#
# Deploy safety (2026-10-28):
#   OPS-27  the database path/backend come from the application's own configuration
#            (newsbot/config.py) — never from a guess;
#   OPS-28  the mirrored article-archive folder is pruned like the database backups;
#   OPS-29  a rollback takes a verified backup of the current database first and
#            changes nothing when that backup fails.
#
# Overridable for tests: APP_DIR, BACKUP_DIR, SERVICE, KEEP_BACKUPS, HEALTH_TRIES.
# DB resolution (OPS-27): the deploy never guesses between data/newsbot.db and
# newsbot.db. DB_FILE/DB_BACKEND are read from $APP_DIR/.env with the application's
# own configuration module (newsbot/config.py — python-dotenv), a relative DB_FILE
# is resolved against the service's real WorkingDirectory, and a missing configured
# database aborts the deploy unless ALLOW_MISSING_DB=1 is set explicitly.
#   DEPLOY_DB_FILE       explicit DB path, overrides DB_FILE from .env
#   DEPLOY_DB_BACKEND    explicit backend, overrides DB_BACKEND from .env
#   DEPLOY_WORKING_DIR   explicit service cwd, replaces the systemd WorkingDirectory lookup
#   ALLOW_MISSING_DB=1   allow a first deploy with no database (skips the backup, loudly)
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/Bot-Official-Telegram}"   # مسیرِ واقعیِ Production (نامِ پوشه با حروفِ بزرگ)
BACKUP_DIR="${BACKUP_DIR:-/opt/backups}"
SERVICE="${SERVICE:-newsbot.service}"
KEEP_BACKUPS="${KEEP_BACKUPS:-7}"
# ── Runtime: systemd یا docker ───────────────────────────────────────────────────
# Production واقعی یک *کانتینر* است (نه یونیتِ systemd): سرویس با `python run.py` بالا
# می‌آید، کدِ میزبان در APP_DIR است و به /app داخلِ کانتینر bind-mount می‌شود.
# حالتِ systemd دست‌نخورده می‌ماند و «auto» خودش را تشخیص می‌دهد.
RUNTIME="${RUNTIME:-auto}"          # auto | systemd | docker
CONTAINER="${CONTAINER:-bot-official-telegram}"
CONTAINER_WORKDIR=""

# بودجهٔ سلامت: برابرِ TimeoutStartSec یونیت (پیش‌فرض ۱۸۰ ثانیه). با Type=notify
# سرویس تا دریافتِ READY=1 در حالتِ activating می‌ماند، پس ۱۰ ثانیه کافی نبود.
HEALTH_TRIES="${HEALTH_TRIES:-180}"
# Support the live VPS layout (venv/ + data/newsbot.db) while retaining legacy sandbox compatibility.
VENV_DIR="${VENV_DIR:-$APP_DIR/venv}"
if [ ! -x "$VENV_DIR/bin/python" ] && [ -x "$APP_DIR/.venv/bin/python" ]; then VENV_DIR="$APP_DIR/.venv"; fi
DB_PATH_EFFECTIVE=""
DB_BACKEND_EFFECTIVE=""
DB_ALLOW_MISSING=0
RESTARTED=0
RESTART_EPOCH=0

log() { printf '[deploy] %s\n' "$*"; }
err() { printf '[deploy][error] %s\n' "$*" >&2; }
die() { err "$*"; exit 1; }

# ── Runtime ─────────────────────────────────────────────────────────────────────
detect_runtime() {
  if [ "$RUNTIME" != "auto" ]; then return 0; fi
  if command -v docker >/dev/null 2>&1 && docker inspect "$CONTAINER" >/dev/null 2>&1; then
    RUNTIME=docker
  else
    RUNTIME=systemd
  fi
}

container_workdir() { docker inspect "$CONTAINER" --format '{{.Config.WorkingDir}}' 2>/dev/null | head -n 1; }

# مسیرِ داخلِ کانتینر ⇒ همان مسیر روی میزبان (بر اساسِ bind-mountها)
host_path_for() {
  local inside="$1" src dest
  while IFS="$(printf '\t')" read -r src dest; do
    [ -n "$dest" ] || continue
    if [ "$inside" = "$dest" ]; then printf '%s\n' "$src"; return 0; fi
    case "$inside" in
      "$dest"/*) printf '%s\n' "${src}${inside#$dest}"; return 0 ;;
    esac
  done < <(docker inspect "$CONTAINER" --format '{{range .Mounts}}{{.Source}}{{"\t"}}{{.Destination}}{{"\n"}}{{end}}' 2>/dev/null)
  return 1
}

# ── خواندنِ خصوصیاتِ یونیت (یک فراخوانی، بدونِ فرضِ خروجیِ خاص) ──
unit_info() {
  if [ "$RUNTIME" = "docker" ]; then
    local status restarts workdir
    status="$(docker inspect "$CONTAINER" --format '{{.State.Status}}' 2>/dev/null || true)"
    restarts="$(docker inspect "$CONTAINER" --format '{{.RestartCount}}' 2>/dev/null || true)"
    workdir="$(container_workdir)"
    local active=inactive sub=dead
    if [ "$status" = "running" ]; then active=active; sub=running; fi
    printf 'ActiveState=%s\nSubState=%s\nType=simple\nNRestarts=%s\nWorkingDir=%s\nFragmentPath=docker:%s\n' \
      "$active" "$sub" "${restarts:-0}" "$workdir" "$CONTAINER"
    return 0
  fi
  systemctl show "$SERVICE" 2>/dev/null || true
}

prop() { printf '%s\n' "$1" | sed -n "s/^$2=//p" | head -n 1; }

# OPS-27: resolve DB_BACKEND/DB_FILE from the application's own configuration.
# The deploy asks the service's own interpreter for `newsbot.config` — the very
# module the bot imports — so the .env is read by the same parser (python-dotenv),
# the same defaults apply (DB_FILE unset ⇒ newsbot.db, DB_BACKEND ⇒ sqlite) and
# process-environment values keep their precedence, exactly like config.py.
# The .env is never sourced or eval'd, and no value other than the resolved
# database path/backend ever leaves this function.
# مفسرِ برنامه در همان محیطی که سرویس اجرا می‌شود (systemd: venv · docker: داخل کانتینر)
service_python() {
  if [ "$RUNTIME" = "docker" ]; then
    docker exec -i -w "$CONTAINER_WORKDIR" "$CONTAINER" python "$@"
  else
    "$VENV_DIR/bin/python" "$@"
  fi
}

# مسیرِ کدِ برنامه از دیدِ همان محیط (داخلِ کانتینر: /app)
service_app_dir() {
  if [ "$RUNTIME" = "docker" ]; then printf '%s\n' "$CONTAINER_WORKDIR"; else printf '%s\n' "$APP_DIR"; fi
}

resolve_db_config() {
  local workdir="${DEPLOY_WORKING_DIR:-}"
  if [ -z "$workdir" ]; then
    if [ "$RUNTIME" = "docker" ]; then
      workdir="$CONTAINER_WORKDIR"
    else
      workdir="$(systemctl show -p WorkingDirectory "$SERVICE" 2>/dev/null | sed -n 's/^WorkingDirectory=//p' | head -n 1 || true)"
      workdir="${workdir#-}"
    fi
  fi
  # The unit's own environment is not visible to this shell; if it injects DB_*
  # or reads another EnvironmentFile, the deploy cannot know the precedence —
  # say so instead of guessing, and let DEPLOY_DB_FILE/DEPLOY_DB_BACKEND win.
  local unit_env unit_env_files
  if [ "$RUNTIME" = "docker" ]; then
    # کانتینر: محیط از همان .envِ برنامه خوانده می‌شود ⇒ همان parser (python-dotenv)
    unit_env="$(docker inspect "$CONTAINER" --format '{{range .Config.Env}}{{println .}}{{end}}' 2>/dev/null | grep -E '(^|[[:space:]])DB_(FILE|BACKEND)=' || true)"
    if [ -n "$unit_env" ]; then
      err "WARNING: the container sets DB_FILE/DB_BACKEND in its environment — set DEPLOY_DB_FILE / DEPLOY_DB_BACKEND"
      err "         so the deploy and the service use exactly the same value."
    fi
  else
  unit_env="$(systemctl show -p Environment "$SERVICE" 2>/dev/null || true)"
  if printf '%s\n' "$unit_env" | grep -qE '(^|[[:space:]])DB_(FILE|BACKEND)='; then
    err "WARNING: the systemd unit sets DB_FILE/DB_BACKEND in Environment= — the deploy reads the app config only;"
    err "         set DEPLOY_DB_FILE / DEPLOY_DB_BACKEND so both use exactly the same value."
  fi
  unit_env_files="$(systemctl show -p EnvironmentFiles "$SERVICE" 2>/dev/null || true)"
  if [ -n "$unit_env_files" ] && ! printf '%s\n' "$unit_env_files" | grep -qF "$APP_DIR/.env"; then
    err "WARNING: the unit's EnvironmentFiles do not include $APP_DIR/.env — the service may read different DB settings;"
    err "         set DEPLOY_DB_FILE / DEPLOY_DB_BACKEND explicitly."
  fi
  fi
  local status="" backend="" path="" message=""
  {
    IFS= read -r -d '' status || true
    IFS= read -r -d '' backend || true
    IFS= read -r -d '' path || true
    IFS= read -r -d '' message || true
  } < <(cd "$APP_DIR" && service_python - "$(service_app_dir)" "$workdir" "${DEPLOY_DB_FILE:-}" "${DEPLOY_DB_BACKEND:-}" <<'PY'
import os
import sys


def emit(status, backend, path, message):
    sys.stdout.buffer.write(
        b"\0".join(v.encode("utf-8") for v in (status, backend, path, message)) + b"\0"
    )


app_dir, workdir, dep_file, dep_backend = sys.argv[1:5]
sys.path.insert(0, app_dir)
# The service's environment comes from systemd, not from this SSH shell; a DB_FILE
# that happens to be exported in the deploy shell must not silently win over the
# unit's own configuration. systemd-injected DB_* values are reported as a warning
# by the caller and can be pinned with DEPLOY_DB_FILE / DEPLOY_DB_BACKEND.
for _key in ("DB_FILE", "DB_BACKEND"):
    os.environ.pop(_key, None)
try:
    from newsbot import config
except Exception:
    emit("error", "", "", "could not import newsbot.config from the service venv")
    raise SystemExit(0)

backend = (dep_backend or config.DB_BACKEND or "sqlite").strip().lower()

if dep_file:
    raw = dep_file
    raw_set = True
else:
    # config.DB_FILE already carries the app's effective value; the presence check
    # in os.environ tells "unset" (⇒ the app default) from "explicitly empty".
    raw = config.DB_FILE
    raw_set = "DB_FILE" in os.environ

if backend == "d1":
    emit("d1", backend, "", "backend=d1 — the database lives in Cloudflare D1; skipping the local SQLite backup")
    raise SystemExit(0)
if not raw_set:
    raw = "newsbot.db"
elif raw is None or raw == "":
    emit("error", backend, "", "DB_FILE is set but empty in .env — refusing to guess the database path")
    raise SystemExit(0)

if os.path.isabs(raw):
    path = os.path.normpath(raw)
else:
    if not workdir:
        emit("error", backend, "",
             "DB_FILE is relative but the service WorkingDirectory is unknown — "
             "set DEPLOY_WORKING_DIR (or an absolute DEPLOY_DB_FILE) explicitly")
        raise SystemExit(0)
    path = os.path.normpath(os.path.join(workdir, raw))

emit("ok" if os.path.isfile(path) else "missing", backend, path, "")
PY
)
  case "$status" in
    d1)
      DB_BACKEND_EFFECTIVE="d1"
      log "$message"
      ;;
    ok)
      DB_BACKEND_EFFECTIVE="$backend"
      DB_PATH_EFFECTIVE="$path"
      if [ "$RUNTIME" = "docker" ]; then
        local host_path
        case "$path" in
          "$APP_DIR"/*) DB_PATH_EFFECTIVE="$path" ;;   # از قبل مسیرِ میزبان است
          *)
            if host_path="$(host_path_for "$path")" && [ -n "$host_path" ]; then
              DB_PATH_EFFECTIVE="$host_path"
            else
              die "cannot map the container path $path to a host path (no matching bind mount) — refusing to guess"
            fi
            ;;
        esac
      fi
      log "database: $DB_PATH_EFFECTIVE (backend=$backend)"
      ;;
    missing)
      DB_BACKEND_EFFECTIVE="$backend"
      DB_PATH_EFFECTIVE="$path"
      if [ "${ALLOW_MISSING_DB:-0}" = "1" ]; then
        DB_ALLOW_MISSING=1
        err "WARNING: database not found at $DB_PATH_EFFECTIVE — continuing WITHOUT A BACKUP because ALLOW_MISSING_DB=1"
      else
        die "database not found at $DB_PATH_EFFECTIVE (backend=$backend) — refusing to deploy without a backup; fix DB_FILE, or set DEPLOY_DB_FILE (or ALLOW_MISSING_DB=1 for an intentional first deploy)"
      fi
      ;;
    *)
      die "cannot resolve the database configuration: ${message:-no output from the app config}"
      ;;
  esac
}

preflight() {
  detect_runtime
  command -v git >/dev/null 2>&1 || die "git not found in PATH"
  command -v python3 >/dev/null 2>&1 || die "python3 not found in PATH (needed for online backup)"
  [ -d "$APP_DIR/.git" ] || die "not a git checkout: $APP_DIR"
  [ -f "$APP_DIR/.env" ] || die ".env missing in $APP_DIR"
  if [ "$RUNTIME" = "docker" ]; then
    command -v docker >/dev/null 2>&1 || die "docker not found in PATH"
    docker inspect "$CONTAINER" >/dev/null 2>&1 || die "container '$CONTAINER' not found (RUNTIME=docker)"
    CONTAINER_WORKDIR="$(container_workdir)"
    [ -n "$CONTAINER_WORKDIR" ] || CONTAINER_WORKDIR="/app"
    log "runtime: docker ($CONTAINER, workdir=$CONTAINER_WORKDIR)"
    return 0
  fi
  command -v systemctl >/dev/null 2>&1 || die "systemctl not found in PATH"
  [ -x "$VENV_DIR/bin/python" ] || die "venv/bin/python missing in $VENV_DIR"
  [ -x "$VENV_DIR/bin/pip" ] || die "venv/bin/pip missing in $VENV_DIR"
  # OPS-08: یونیت باید وجود داشته باشد و EnvironmentFile آن *اختیاری* نباشد.
  # (با «-» اختیاری است و نبودِ .env ⇒ حلقهٔ ری‌استارت تا ابد.)
  local info frag
  info="$(unit_info)"; frag="$(prop "$info" FragmentPath)"
  [ -n "$frag" ] || die "systemd unit '$SERVICE' not found (is the service name right?)"
  log "unit: $frag"
  if systemctl cat "$SERVICE" 2>/dev/null | grep -Eq '^[[:space:]]*EnvironmentFile=-'; then
    err "WARNING: the unit declares an OPTIONAL EnvironmentFile ('-'); a missing .env will not stop the"
    err "         service and will cause an endless restart loop. Remove the '-' or create the file."
  fi
}

# Online SQLite backup (safe under WAL — `cp` of a live DB is not) + verification.
backup_db() {
  if [ "$DB_BACKEND_EFFECTIVE" = "d1" ]; then
    return 0
  fi
  local db="$DB_PATH_EFFECTIVE"
  if [ ! -f "$db" ]; then
    if [ "$DB_ALLOW_MISSING" -eq 1 ]; then
      log "no database yet — skipping backup (ALLOW_MISSING_DB=1)"
      return 0
    fi
    err "database file not found: $db"
    return 1
  fi
  mkdir -p "$BACKUP_DIR" || return 1
  local dest="$BACKUP_DIR/newsbot-$(date +%Y%m%d-%H%M%S).$$.db"
  local rc=0
  python3 - "$db" "$dest" <<'PY' || rc=$?
import sqlite3, sys
src = sqlite3.connect(sys.argv[1], timeout=30)
dst = sqlite3.connect(sys.argv[2])
try:
    src.backup(dst)
finally:
    dst.close()
    src.close()
# OPS-09: a backup is only worth having if it can be opened AND it holds what the source held.
# (A database with no tables yet is legitimate - the bot may not have written anything.)
def _objects(conn):
    return conn.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()[0]
s = sqlite3.connect(sys.argv[1]); d = sqlite3.connect(sys.argv[2])
try:
    row = d.execute("PRAGMA quick_check").fetchone()
    ok = row[0] if row else "no-result"
    d_objs = _objects(d); s_objs = _objects(s)
finally:
    d.close(); s.close()
if ok != "ok" or d_objs != s_objs:
    raise SystemExit("backup verification failed: quick_check=%r backup_objects=%r source_objects=%r"
                     % (ok, d_objs, s_objs))
PY
  if [ "$rc" -ne 0 ]; then
    # a failed/unverified backup must not stay behind as if it were a good one
    rm -f "$dest"
    err "database backup failed (sqlite backup/verification error) — no backup file kept"
    return 1
  fi
  chmod 600 "$dest" || return 1
  # OPS-10: پوشهٔ آرشیوِ مقالاتِ قدیمی (کنارِ دیتابیس) هم باید پشتیبانی شود
  mirror_article_archive "$db" || return 1
  # فقط KEEP_BACKUPS بکاپ اخیر نگه داشته می‌شود
  ls -1t "$BACKUP_DIR"/newsbot-*.db 2>/dev/null | tail -n +$((KEEP_BACKUPS + 1)) | xargs -r rm --
  prune_archive_backups
  log "backup written and verified: $dest"
  return 0
}

# OPS-10: mirror the article-archive folder that sits next to the database.
# A missing archive folder is legitimate (no archived article yet); a failing copy
# is not — the deploy must stop before any code or service change.
mirror_article_archive() {
  local db="$1"
  local archive_dir
  archive_dir="$(dirname "$db")/backups"
  if [ ! -d "$archive_dir" ]; then
    log "no article archive folder at $archive_dir — nothing to mirror"
    return 0
  fi
  mkdir -p "$BACKUP_DIR/archive" || return 1
  local copied=0 f
  for f in "$archive_dir"/*.db; do
    [ -e "$f" ] || continue
    if ! cp -p "$f" "$BACKUP_DIR/archive/"; then
      err "failed to copy the article archive $f into $BACKUP_DIR/archive — refusing to deploy with an incomplete backup set"
      return 1
    fi
    copied=$((copied + 1))
  done
  if [ "$copied" -gt 0 ]; then
    log "archive folder mirrored to $BACKUP_DIR/archive ($copied file(s))"
  else
    log "article archive folder $archive_dir has no .db snapshot to mirror"
  fi
  return 0
}

# OPS-28: keep the mirrored archive bounded like the database backups.
# Only files that match the application's archive naming (newsbot-archive-*.db)
# are ever removed; anything else in the folder is left untouched.
prune_archive_backups() {
  local dir="$BACKUP_DIR/archive"
  [ -d "$dir" ] || return 0
  local list f
  list="$(ls -1t "$dir"/newsbot-archive-*.db 2>/dev/null | tail -n +$((KEEP_BACKUPS + 1)) || true)"
  [ -n "$list" ] || return 0
  while IFS= read -r f; do
    [ -n "$f" ] || continue
    rm -f -- "$f" || err "WARNING: could not prune the old archive backup $f"
  done <<< "$list"
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
  if [ "$RUNTIME" = "docker" ]; then
    docker exec -w "$CONTAINER_WORKDIR" "$CONTAINER" pip install --quiet -r requirements.txt || return 1
    return 0
  fi
  "$VENV_DIR/bin/pip" install --quiet -r requirements.txt || return 1
  return 0
}

# دروازهٔ پیش از restart — شکست یعنی سرویس قدیمی دست‌نخورده می‌ماند
gate() {
  cd "$APP_DIR" || return 1
  if [ "$RUNTIME" = "docker" ]; then
    docker exec -w "$CONTAINER_WORKDIR" "$CONTAINER" python -m compileall -q newsbot run.py || return 1
    docker exec -w "$CONTAINER_WORKDIR" "$CONTAINER" python run.py --check || return 1
    return 0
  fi
  "$VENV_DIR/bin/python" -m compileall -q newsbot run.py || return 1
  "$VENV_DIR/bin/python" run.py --check || return 1
  return 0
}

start_service() {
  RESTART_EPOCH="$(date +%s)"
  if [ "$RUNTIME" = "docker" ]; then
    if ! docker restart "$CONTAINER" >/dev/null; then
      err "docker restart failed for '$CONTAINER'"
      return 1
    fi
    RESTARTED=1
    return 0
  fi
  if ! systemctl restart "$SERVICE"; then
    err "systemctl restart failed for '$SERVICE'"
    return 1
  fi
  RESTARTED=1
  return 0
}

# ژورنالِ *همین* اجرا: فقط نشانه‌هایِ مرگبارِ سرویس. کلمهٔ «traceback» معیار نیست،
# چون برنامه برای استثناهایِ مدیریت‌شده log.exception می‌نویسد (و آن خطا نیست).
journal_failed_since() {
  local since="$1"
  if [ "$RUNTIME" = "docker" ]; then
    docker logs --since "$since" "$CONTAINER" 2>&1 \
      | grep -Eiq 'failed with result|main process exited|core dumped|segmentation fault|watchdog timeout'
    return $?
  fi
  journalctl -u "$SERVICE" --since "@$since" -p err --no-pager 2>/dev/null \
    | grep -Eiq 'failed with result|main process exited|core dumped|segmentation fault|watchdog timeout'
}

# سلامت واقعی: (۱) سرویس به حالتِ running برسد — برای Type=notify یعنی READY=1 رسیده،
# (۲) crash-loop نباشد، (۳) ژورنالِ همین اجرا خطای مرگبار نداشته باشد.
health() {
  local i info active subst type nrestarts
  for i in $(seq 1 "$HEALTH_TRIES"); do
    info="$(unit_info)"
    active="$(prop "$info" ActiveState)"; subst="$(prop "$info" SubState)"; type="$(prop "$info" Type)"
    if [ "$active" = "active" ] && { [ "$type" != "notify" ] || [ "$subst" = "running" ]; }; then
      break
    fi
    if [ "$i" -eq "$HEALTH_TRIES" ]; then
      err "service '$SERVICE' not ready after ${HEALTH_TRIES}s (ActiveState=$active SubState=$subst Type=$type)"
      return 1
    fi
    sleep 1
  done
  nrestarts="$(prop "$(unit_info)" NRestarts)"; case "$nrestarts" in ''|*[!0-9]*) nrestarts=0 ;; esac
  if [ "$nrestarts" -ne 0 ]; then
    err "service '$SERVICE' is crash-looping (NRestarts=$nrestarts)"
    return 1
  fi
  if journal_failed_since "$RESTART_EPOCH"; then
    err "a crash marker was logged for '$SERVICE' since this restart"
    return 1
  fi
  log "health check passed"
  return 0
}

# شکست پس از sync/install/gate → کد و وابستگی‌ها به SHA قبلی برمی‌گردند؛ اگر restart شده،
# دوباره روی کد قبلی بالا می‌آید.
rollback_and_die() {
  err "$1 — rolling code back to $prev"
  cd "$APP_DIR" 2>/dev/null || true
  git reset -q --hard "$prev" || true
  # OPS-06: فقط برگرداندنِ کد کافی نیست — کتابخانه‌ها هم باید نسخهٔ قبلی باشند،
  # وگرنه کدِ قدیمی روی وابستگی‌هایِ جدید اجرا می‌شود.
  install_deps || err "WARNING: could not restore the previous dependency set"
  if [ "$RESTARTED" -eq 1 ]; then
    if [ "$RUNTIME" = "docker" ]; then
      docker restart "$CONTAINER" >/dev/null || true
    else
      systemctl restart "$SERVICE" || true
    fi
    log "service restarted on previous code $prev"
  fi
  exit 1
}

do_deploy() {
  local target="$1"
  preflight
  resolve_db_config
  cd "$APP_DIR" || die "cannot cd into $APP_DIR"
  local prev
  prev="$(git rev-parse HEAD)" || die "cannot read HEAD of $APP_DIR"
  if [ "$prev" = "$target" ]; then
    # OPS-25: «سبز بودنِ deploy» باید شاهدِ سلامتِ سرویس باشد، نه فقط «کد از قبل همان است».
    log "already at $target — running backup and health check (no code change)"
    backup_db || die "database backup failed"
    health || die "health check failed on the already-deployed SHA"
    printf '%s\n' "$target" > "$APP_DIR/.deployed_sha" || die "cannot record deployed sha"
    log "deploy complete (no change): $prev"
    exit 0
  fi
  printf '%s\n' "$prev" > "$APP_DIR/.deployed_sha.prev" || die "cannot record previous sha"
  backup_db || die "database backup failed — aborting before any change"
  sync_to "$target" || die "code sync to $target failed — aborting (service not touched)"
  install_deps || rollback_and_die "dependency install failed"
  gate || rollback_and_die "pre-restart gate failed"
  # OPS-26: شکستِ restart هم باید rollback واقعی بسازد، نه خروجِ خام
  start_service || rollback_and_die "service restart failed"
  health || rollback_and_die "post-restart health check failed"
  printf '%s\n' "$target" > "$APP_DIR/.deployed_sha" || die "cannot record deployed sha"
  log "deploy complete: $prev -> $target"
}

do_rollback() {
  preflight
  resolve_db_config
  cd "$APP_DIR" || die "cannot cd into $APP_DIR"
  [ -f "$APP_DIR/.deployed_sha.prev" ] || die "no previous sha recorded (.deployed_sha.prev) — nothing to roll back to"
  local back
  back="$(tr -d '[:space:]' < "$APP_DIR/.deployed_sha.prev")"
  [ -n "$back" ] || die "recorded previous sha is empty"
  git cat-file -e "${back}^{commit}" 2>/dev/null || die "previous sha $back not present in repository"
  # OPS-29: the rollback swaps the running version — take a verified backup of the
  # current database first (from the same resolved path as the deploy), so a failed
  # backup leaves both the database and the active version untouched.
  backup_db || die "database backup failed — refusing to roll back without a backup"
  git reset -q --hard "$back" || die "git reset to $back failed"
  [ "$(git rev-parse HEAD)" = "$back" ] || die "rollback verification failed (HEAD != $back)"
  install_deps || die "could not restore the previous dependency set"
  start_service || die "service restart failed during rollback"
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
