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
# Session 3 / فاز C (2026-11-06):
#   DEVOPS-03  the container is rebuilt from an *immutable* image tagged with the commit
#              SHA (docker-compose.yml at the repository root). No source bind-mount, no
#              `pip install` into a live container, and the database moves to the
#              standard persistent volume /opt/newsbot-data ⇒ /app/data/newsbot.db;
#   DEVOPS-06  an anti-downgrade gate refuses a SHA that is older than what is already
#              deployed (the workflow_run queue race), and the local changes of the
#              server are saved as a patch before `git reset --hard`;
#   DEVOPS-08  before the container is stopped the bot is put into "graceful drain"
#              mode: it stops starting new publications and waits for the in-flight
#              ones, so nothing lands in `delivery_unknown`.
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
#
# Session-3 knobs:
#   ALLOW_OLDER_SHA=1    bypass the anti-downgrade gate (explicit operator override)
#   IMAGE_REPO           docker image repository (default: newsbot)
#   NEWSBOT_DATA_DIR     persistent data directory on the host (default: /opt/newsbot-data)
#   NEWSBOT_ENV_FILE     env file for compose (default: /etc/newsbot/env)
#   COMPOSE_FILE         compose file (default: $APP_DIR/docker-compose.yml)
#   DRAIN_TIMEOUT        seconds to wait for the in-flight publications (default: 30)
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/Bot-Official-Telegram}"   # مسیرِ واقعیِ Production (نامِ پوشه با حروفِ بزرگ)
BACKUP_DIR="${BACKUP_DIR:-/opt/backups}"
SERVICE="${SERVICE:-newsbot.service}"
KEEP_BACKUPS="${KEEP_BACKUPS:-7}"
# ── Runtime: systemd یا docker ───────────────────────────────────────────────────
# Production واقعی یک *کانتینر* است (نه یونیتِ systemd). از فاز C کانتینر از روی
# یک ایمیجِ immutable با تگِ SHA اجرا می‌شود (docker-compose.yml) و دیتابیس در یک
# حجمِ ماندگار روی میزبان است. حالتِ systemd دست‌نخورده می‌ماند و «auto» خودش را
# تشخیص می‌دهد.
RUNTIME="${RUNTIME:-auto}"          # auto | systemd | docker
CONTAINER="${CONTAINER:-bot-official-telegram}"
CONTAINER_WORKDIR=""

# ── Session 3: ایمیجِ immutable، حجمِ داده و تخلیهٔ ایمن ─────────────────────────
IMAGE_REPO="${IMAGE_REPO:-newsbot}"
DATA_DIR="${NEWSBOT_DATA_DIR:-/opt/newsbot-data}"
ENV_FILE="${NEWSBOT_ENV_FILE:-/etc/newsbot/env}"
COMPOSE_FILE="${COMPOSE_FILE:-$APP_DIR/docker-compose.yml}"
DRAIN_TIMEOUT="${DRAIN_TIMEOUT:-30}"
DRAIN_FLAG="${DRAIN_FLAG:-/tmp/newsbot_draining}"
DRAINED_FLAG="${DRAINED_FLAG:-/tmp/newsbot_drained}"
IMAGE_CURRENT=""                   # تگِ ایمیجی که قرار است اجرا شود (docker)

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

# ── Session 3 (DEVOPS-03): ابزارهای ایمیجِ immutable ────────────────────────────
# آیا پلاگینِ compose روی میزبان هست؟ (خروجیِ واقعی باید شاملِ «version» باشد،
# وگرنه یک استابِ بی‌خروجی هم «موجود» تلقی می‌شود.)
compose_available() {
  local out
  out="$(docker compose version 2>/dev/null || true)"
  printf '%s' "$out" | grep -qi 'version'
}

# محیطِ واقعیِ کانتینرِ در حال اجرا → یک فایلِ env موقت (برای اجرای دروازه روی ایمیجِ تازه)
container_env_file() {
  local tmp
  tmp="$(mktemp "${TMPDIR:-/tmp}/newsbot-env.XXXXXX")" || return 1
  chmod 600 "$tmp" || return 1
  docker inspect "$CONTAINER" --format '{{range .Config.Env}}{{println .}}{{end}}' 2>/dev/null \
    | grep -vE '^(PATH|HOME|HOSTNAME|PYTHON_VERSION|PYTHON_SHA256|LANG)=' > "$tmp" || true
  printf '%s\n' "$tmp"
}

# ساختِ ایمیجِ immutable با تگِ SHA. وابستگی‌ها داخلِ ایمیج پخته می‌شوند؛
# نصبِ وابستگی در کانتینرِ زنده دیگر انجام نمی‌شود (DEVOPS-03).
build_image() {
  local sha="$1"
  [ -f "$APP_DIR/deploy/Dockerfile" ] || { err "missing $APP_DIR/deploy/Dockerfile"; return 1; }
  log "building the immutable image $IMAGE_REPO:$sha"
  docker build \
    --build-arg "NEWSBOT_VERSION=$sha" \
    -t "$IMAGE_REPO:$sha" -t "$IMAGE_REPO:latest" \
    -f "$APP_DIR/deploy/Dockerfile" "$APP_DIR" || return 1
  docker image inspect "$IMAGE_REPO:$sha" >/dev/null 2>&1 || {
    err "the image $IMAGE_REPO:$sha was not created"; return 1; }
  IMAGE_CURRENT="$IMAGE_REPO:$sha"
  log "image ready: $IMAGE_REPO:$sha"
  return 0
}

# آیا ایمیجِ این تگ روی میزبان موجود است؟ (برای rollback سریع)
image_exists() {
  docker image inspect "$1" >/dev/null 2>&1
}

# ── DEVOPS-03/06: اجرای اسکریپتِ متناظر با کامیتِ هدف ──────────────────────────
# اگر اسکریپتِ در حالِ اجرا با «deploy/deploy.sh»ِ کامیتِ هدف یکی نباشد، نسخهٔ هدف
# در یک فایلِ موقت (که root آن را ساخته ⇒ قابل‌دستکاری از سوی کاربر نیست) نوشته
# و همان اجرا می‌شود. نتیجه: همیشه *همان* منطقِ کامیتِ هدف اجرا می‌شود، حتی اگر
# اسکریپتِ روی سرور از نسخهٔ قبلی باشد.
reexec_target_script() {
  local sha="$1" tmp expected current
  expected="$(git rev-parse --verify -q "${sha}:deploy/deploy.sh" 2>/dev/null || true)"
  if [ -z "$expected" ]; then
    log "the target commit has no deploy/deploy.sh — keeping the running script"
    return 0
  fi
  current="$(git hash-object "$0" 2>/dev/null || true)"
  if [ -n "$current" ] && [ "$current" = "$expected" ]; then
    return 0   # همین حالا نسخهٔ درست اجرا می‌شود
  fi
  tmp="$(mktemp "${TMPDIR:-/tmp}/newsbot-deploy-XXXXXX.sh")" || return 1
  if ! git show "${sha}:deploy/deploy.sh" > "$tmp"; then
    rm -f "$tmp"
    return 1
  fi
  chmod 0700 "$tmp"
  log "re-executing the deploy script of $sha (the running copy is a different revision)"
  exec bash "$tmp" deploy "$sha"
}

# ── DEVOPS-06: دروازهٔ ضدِ عقب‌گرد (Anti-Downgrade Gate) ─────────────────────────
# صفِ workflow_run ترتیبِ زمانیِ کامیت‌ها را تضمین نمی‌کند ⇒ یک CIِ قدیمی‌تر که
# دیرتر تمام می‌شود می‌تواند نسخهٔ جدیدتر را روی سرور بنویسد. این دروازه چنین
# استقراری را رد می‌کند (Escape hatch صریح: ALLOW_OLDER_SHA=1).
anti_downgrade_gate() {
  local target="$1" current="$2" origin_main="$3"
  if [ "${ALLOW_OLDER_SHA:-0}" = "1" ]; then
    log "WARNING: ALLOW_OLDER_SHA=1 — the anti-downgrade gate is DISABLED (target=$target)"
    return 0
  fi
  if [ -n "$origin_main" ] && [ "$target" = "$origin_main" ]; then
    return 0   # هدف، نوکِ main است ⇒ به‌روزترین نسخه
  fi
  if [ -n "$origin_main" ] && git merge-base --is-ancestor "$target" "$origin_main" 2>/dev/null; then
    err "REFUSED: $target is older than origin/main ($origin_main) — deploying it would roll the server back."
    err "         Re-run the deploy for the newest green SHA, or set ALLOW_OLDER_SHA=1 explicitly."
    return 1
  fi
  if git merge-base --is-ancestor "$target" "$current" 2>/dev/null; then
    err "REFUSED: $target is an ancestor of the deployed commit ($current) — deploying it would roll the server back."
    err "         Re-run the deploy for the newest green SHA, or set ALLOW_OLDER_SHA=1 explicitly."
    return 1
  fi
  if ! git merge-base --is-ancestor "$current" "$target" 2>/dev/null; then
    err "REFUSED: $target does not descend from the deployed commit ($current) — the history diverged."
    err "         Resolve the history on the server first, or set ALLOW_OLDER_SHA=1 explicitly."
    return 1
  fi
  return 0
}

# ── DEVOPS-06: حفظِ تغییراتِ محلیِ سرور پیش از `git reset --hard` ───────────────
# یک hotfixِ دستیِ اپراتور نباید برای همیشه پاک شود: قبل از reset، وضعیت و patch
# در /opt/backups/dirty-tree-<timestamp>.patch نگه داشته می‌شود.
preserve_dirty_tree() {
  cd "$APP_DIR" || return 1
  local status
  status="$(git status --porcelain 2>/dev/null || true)"
  [ -n "$status" ] || return 0
  mkdir -p "$BACKUP_DIR" || return 1
  local patch="$BACKUP_DIR/dirty-tree-$(date +%Y%m%d-%H%M%S).patch"
  {
    echo "# NewsBot dirty working tree captured before a deploy ($(date -u +%Y-%m-%dT%H:%M:%SZ))"
    echo "# HEAD=$(git rev-parse HEAD 2>/dev/null || echo unknown)"
    echo "# git status --porcelain:"
    printf '%s\n' "$status"
    echo
  } >> "$patch" || return 1
  git diff >> "$patch" 2>/dev/null || true
  git diff --cached >> "$patch" 2>/dev/null || true
  local untracked
  untracked="$(git ls-files --others --exclude-standard 2>/dev/null || true)"
  if [ -n "$untracked" ]; then
    {
      echo "# untracked files:"
      printf '%s\n' "$untracked"
    } >> "$patch"
  fi
  chmod 600 "$patch" || return 1
  log "WARNING: the working tree has local changes — they were saved to $patch"
  return 0
}

# ── DEVOPS-08: تخلیهٔ ایمن پیش از توقفِ سرویس ─────────────────────────────────
# پرچمِ «در حالِ تخلیه» را در همان محیطی می‌سازد که ربات در آن اجرا می‌شود و
# منتظرِ پرچمِ «تخلیه کامل شد» می‌ماند (یعنی: هیچ انتشاری در حالِ پرواز نمانده).
drain_service() {
  local budget="$DRAIN_TIMEOUT"
  if [ "$RUNTIME" = "docker" ]; then
    local flag="$DRAIN_FLAG" drained="$DRAINED_FLAG"
    # مسیرِ نشانگر از محیطِ خودِ کانتینر خوانده می‌شود تا با برنامه یکی باشد.
    local env_dump
    env_dump="$(docker inspect "$CONTAINER" --format '{{range .Config.Env}}{{println .}}{{end}}' 2>/dev/null || true)"
    case "$env_dump" in
      *DRAIN_FLAG_FILE=*) flag="${env_dump##*DRAIN_FLAG_FILE=}"; flag="${flag%%$'\n'*}" ;;
    esac
    case "$env_dump" in
      *DRAINED_FLAG_FILE=*) drained="${env_dump##*DRAINED_FLAG_FILE=}"; drained="${drained%%$'\n'*}" ;;
    esac
    log "graceful drain: asking the container to stop starting new publications"
    docker exec "$CONTAINER" python -c \
      'import pathlib,sys; pathlib.Path(sys.argv[1]).write_text("drain")' "$flag" \
      || err "WARNING: could not create the drain flag inside the container"
    local i
    for i in $(seq 1 "$budget"); do
      if docker exec "$CONTAINER" python -c \
          'import pathlib,sys; sys.exit(0 if pathlib.Path(sys.argv[1]).exists() else 1)' "$drained"; then
        log "graceful drain: the bot reports no publication in flight"
        return 0
      fi
      sleep 1
    done
    err "WARNING: the graceful drain did not finish within ${budget}s — a publication may be interrupted"
    return 0
  fi
  # systemd: ربات روی خودِ میزبان اجرا می‌شود ⇒ نشانگر در همان FS است.
  log "graceful drain: asking the service to stop starting new publications"
  printf 'drain\n' > "$DRAIN_FLAG" 2>/dev/null \
    || err "WARNING: could not create the drain flag at $DRAIN_FLAG"
  local i
  for i in $(seq 1 "$budget"); do
    if [ -f "$DRAINED_FLAG" ]; then
      log "graceful drain: the bot reports no publication in flight"
      return 0
    fi
    sleep 1
  done
  err "WARNING: the graceful drain did not finish within ${budget}s — a publication may be interrupted"
  return 0
}

# پاک‌کردنِ نشانگرِ تخلیه (بهترین‌تلاش): اگر ربات هنوز بالا باشد نباید در حالتِ
# «همیشه در حالِ تخلیه» گیر کند. در حالتِ docker نشانگر داخلِ کانتینر است.
clear_drain_flag() {
  if [ "$RUNTIME" = "docker" ]; then
    docker exec "$CONTAINER" python -c \
      'import os,sys
for p in sys.argv[1:]:
    try: os.unlink(p)
    except OSError: pass' "$DRAIN_FLAG" "$DRAINED_FLAG" >/dev/null 2>&1 || true
    return 0
  fi
  rm -f "$DRAIN_FLAG" "$DRAINED_FLAG" 2>/dev/null || true
  return 0
}

# توقفِ کنترل‌شدهٔ سرویس (قبل از جابه‌جاییِ دیتابیس و ساختِ نسخهٔ جدید)
stop_service() {
  if [ "$RUNTIME" = "docker" ]; then
    log "stopping the container '$CONTAINER' (grace period ${DOCKER_STOP_TIMEOUT:-30}s)"
    docker stop --time "${DOCKER_STOP_TIMEOUT:-30}" "$CONTAINER" >/dev/null \
      || { err "docker stop failed for '$CONTAINER'"; return 1; }
    return 0
  fi
  return 0
}

# ── DEVOPS-03: مهاجرتِ امنِ مسیرِ دیتابیس به حجمِ استاندارد ─────────────────────
# مسیرِ استاندارد: /opt/newsbot-data (میزبان) ⇒ /app/data/newsbot.db (کانتینر).
# فایل‌های ‎-wal/-shm هم همراهِ دیتابیس منتقل می‌شوند و مالکیت (uid 10001) حفظ می‌شود.
migrate_db_path() {
  [ "$RUNTIME" = "docker" ] || return 0
  [ "$DB_BACKEND_EFFECTIVE" = "d1" ] && return 0
  local current="${DB_PATH_EFFECTIVE:-}"
  local std_db="$DATA_DIR/newsbot.db"
  [ -n "$current" ] || return 0
  if [ "$current" = "$std_db" ]; then
    log "database path is already standard: $std_db"
    return 0
  fi
  mkdir -p "$DATA_DIR" || return 1
  chown 10001:10001 "$DATA_DIR" 2>/dev/null || true
  local suffix src dst moved=0
  for suffix in "" "-wal" "-shm"; do
    src="${current}${suffix}"; dst="${std_db}${suffix}"
    [ -e "$src" ] || continue
    if [ -e "$dst" ]; then
      log "database migration: $dst already exists — the source $src is left untouched"
      continue
    fi
    if ! mv -f "$src" "$dst"; then
      err "failed to move $src to $dst — refusing to continue (no data may be lost)"
      return 1
    fi
    moved=1
  done
  # پوشهٔ آرشیوِ مقاله‌ها هم کنارِ دیتابیس است ⇒ همان‌جا منتقل می‌شود.
  local src_archive dst_archive
  src_archive="$(dirname "$current")/backups"; dst_archive="$(dirname "$std_db")/backups"
  if [ -d "$src_archive" ] && [ ! -e "$dst_archive" ]; then
    mv -f "$src_archive" "$dst_archive" || {
      err "failed to move the article archive $src_archive"; return 1; }
    log "article archive moved: $src_archive -> $dst_archive"
  fi
  if [ "$moved" -eq 0 ]; then
    log "no database file to migrate at $current"
    return 0
  fi
  chown 10001:10001 "$std_db" 2>/dev/null || true
  chmod 600 "$std_db" 2>/dev/null || true
  log "database migrated: $current -> $std_db"
  DB_PATH_EFFECTIVE="$std_db"
  return 0
}

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
      log "the container environment defines DB_FILE/DB_BACKEND — the deploy follows exactly those values"
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
  # در حالتِ docker، محیطِ خودِ کانتینر *همان* محیطِ برنامه است ⇒ DB_FILE/DB_BACKEND
  # نباید حذف شوند (وگرنه مقدارِ واقعیِ کانتینر نادیده گرفته می‌شود). در حالتِ
  # systemd، محیطِ این شل با محیطِ یونیت فرق دارد ⇒ باید حذف شود تا حدس نزنیم.
  local pop_env=1
  [ "$RUNTIME" = "docker" ] && pop_env=0
  {
    IFS= read -r -d '' status || true
    IFS= read -r -d '' backend || true
    IFS= read -r -d '' path || true
    IFS= read -r -d '' message || true
  } < <(cd "$APP_DIR" && service_python - "$(service_app_dir)" "$workdir" "${DEPLOY_DB_FILE:-}" "${DEPLOY_DB_BACKEND:-}" "$pop_env" <<'PY'
import os
import sys


def emit(status, backend, path, message):
    sys.stdout.buffer.write(
        b"\0".join(v.encode("utf-8") for v in (status, backend, path, message)) + b"\0"
    )


app_dir, workdir, dep_file, dep_backend, pop_env = sys.argv[1:6]
sys.path.insert(0, app_dir)
# The service's environment comes from systemd, not from this SSH shell; a DB_FILE
# that happens to be exported in the deploy shell must not silently win over the
# unit's own configuration. systemd-injected DB_* values are reported as a warning
# by the caller and can be pinned with DEPLOY_DB_FILE / DEPLOY_DB_BACKEND.
# In docker mode the container's own environment IS the application's environment
# (pop_env=0) ⇒ its DB_FILE/DB_BACKEND are the source of truth.
if pop_env == "1":
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
  # fetch کامل (نه فقط main) تا merge-base بتواند تاریخچه را مقایسه کند.
  git fetch -q --prune origin || return 1
  git cat-file -e "${sha}^{commit}" 2>/dev/null || return 1
  # DEVOPS-06: تغییراتِ محلیِ سرور (hotfixِ دستی) پیش از reset حفظ می‌شوند.
  preserve_dirty_tree || return 1
  git reset -q --hard "$sha" || return 1
  # OPS-02: HEAD باید عیناً همان SHA محرک deploy باشد
  [ "$(git rev-parse HEAD)" = "$sha" ] || return 1
  log "code synced to $sha"
}

install_deps() {
  cd "$APP_DIR" || return 1
  if [ "$RUNTIME" = "docker" ]; then
    # DEVOPS-03: در حالتِ docker هیچ چیزی داخلِ کانتینرِ زنده نصب نمی‌شود؛
    # وابستگی‌هایِ پین‌شده داخلِ ایمیج پخته شده‌اند (build_image).
    log "dependencies are baked into the image ($IMAGE_REPO) — nothing is installed in the live container"
    return 0
  fi
  log "installing pinned requirements"
  "$VENV_DIR/bin/pip" install --quiet -r requirements.txt || return 1
  return 0
}

# دروازهٔ پیش از restart — شکست یعنی سرویس قدیمی دست‌نخورده می‌ماند
gate() {
  cd "$APP_DIR" || return 1
  if [ "$RUNTIME" = "docker" ]; then
    # دروازه روی *ایمیجِ تازه* اجرا می‌شود (نه روی کانتینرِ قدیمی که هنوز
    # کدِ قبلی را اجرا می‌کند) ⇒ همان چیزی که واقعاً بالا خواهد آمد آزمایش می‌شود.
    local image="$IMAGE_CURRENT" env_file
    [ -n "$image" ] || { err "no image was built — cannot run the pre-restart gate"; return 1; }
    env_file="$(container_env_file)" || { err "cannot snapshot the container environment"; return 1; }
    docker run --rm --env-file "$env_file" --entrypoint python "$image" \
      -m compileall -q newsbot run.py || { rm -f "$env_file"; return 1; }
    docker run --rm --env-file "$env_file" --entrypoint python "$image" \
      run.py --check || { rm -f "$env_file"; return 1; }
    rm -f "$env_file"
    return 0
  fi
  "$VENV_DIR/bin/python" -m compileall -q newsbot run.py || return 1
  "$VENV_DIR/bin/python" run.py --check || return 1
  return 0
}

# بالا آوردنِ سرویس از روی ایمیجِ SHA-tagged (DEVOPS-03) یا ری‌استارتِ یونیت.
# compose اگر هست ⇒ کانتینر از روی همان ایمیج بازساخته می‌شود؛ وگرنه fallback
# با `docker run` (همان محدودیت‌های امنیتی، بدونِ bind-mountِ کد).
recreate_container() {
  local image="$1"
  [ -n "$image" ] || { err "no image tag given"; return 1; }
  if compose_available; then
    log "recreating the container from $image (docker compose)"
    NEWSBOT_IMAGE="$image" \
    NEWSBOT_DATA_DIR="$DATA_DIR" \
    NEWSBOT_ENV_FILE="$ENV_FILE" \
    NEWSBOT_BACKUP_DIR="$BACKUP_DIR" \
      docker compose -f "$COMPOSE_FILE" up -d --force-recreate --no-build || return 1
    return 0
  fi
  log "recreating the container from $image (docker run — the compose plugin is not available)"
  docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
  docker run -d --name "$CONTAINER" --restart unless-stopped \
    --read-only --tmpfs /tmp:rw,noexec,nosuid,nodev,size=64m \
    --cap-drop ALL --security-opt no-new-privileges:true \
    --memory 512m --cpus 1.0 --pids-limit 256 \
    --env-file "$ENV_FILE" \
    -e DB_FILE=/app/data/newsbot.db \
    -e PYTHONUNBUFFERED=1 \
    -v "$DATA_DIR:/app/data" \
    -v "$BACKUP_DIR:/opt/backups:ro" \
    --health-cmd "python -m newsbot.healthcheck" \
    --health-interval 60s --health-timeout 10s --health-retries 3 --health-start-period 30s \
    "$image" || return 1
  return 0
}

start_service() {
  RESTART_EPOCH="$(date +%s)"
  if [ "$RUNTIME" = "docker" ]; then
    if ! recreate_container "$IMAGE_CURRENT"; then
      err "failed to start the container from $IMAGE_CURRENT"
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
  if [ "$RUNTIME" = "docker" ]; then
    # کانتینر درست پس از exec «running» می‌شود؛ اگر برنامه در ثانیه‌های اول کرش
    # کند، وضعیت به exited می‌رود ⇒ یک مکث و بازبینیِ دوباره لازم است.
    sleep "${DOCKER_SETTLE_SECONDS:-5}"
    active="$(prop "$(unit_info)" ActiveState)"
    if [ "$active" != "active" ]; then
      err "container '$CONTAINER' is not running ${DOCKER_SETTLE_SECONDS}s after the restart (ActiveState=$active)"
      return 1
    fi
  fi
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
  if [ "$RUNTIME" = "docker" ]; then
    # DEVOPS-03: rollback به تگِ ایمیجِ قبلی (بدون ساختِ دوباره اگر موجود باشد).
    local prev_image="$IMAGE_REPO:$prev"
    if ! image_exists "$prev_image"; then
      log "the previous image $prev_image is not on the host — rebuilding it from $prev"
      build_image "$prev" || err "WARNING: could not rebuild the previous image"
      prev_image="$IMAGE_REPO:$prev"
    fi
    recreate_container "$prev_image" || err "WARNING: could not restart the container on the previous image"
    log "service restarted on previous image $prev_image"
    exit 1
  fi
  if [ "$RESTARTED" -eq 1 ]; then
    systemctl restart "$SERVICE" || true
    log "service restarted on previous code $prev"
  fi
  clear_drain_flag
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
  # ── DEVOPS-06: دروازهٔ ضدِ عقب‌گرد (پیش از هر تغییری روی سرور) ────────────────
  git fetch -q --prune origin || die "cannot fetch from origin"
  # ── همیشه با اسکریپتِ *همان کامیتِ هدف* ادامه بده ─────────────────────────────
  # اسکریپتی که در حالِ اجراست ممکن است نسخهٔ قبلی باشد (مثلاً در اولین استقرارِ
  # این نسخه). اگر نسخهٔ هدف فرق دارد، همان را (به‌صورت یک فایلِ موقتِ root-owned)
  # اجرا می‌کنیم ⇒ دروازهٔ ضدِ عقب‌گرد و تخلیهٔ ایمنِ همین نسخه واقعاً اجرا می‌شوند.
  reexec_target_script "$target" || die "cannot extract the deploy script of $target"
  local origin_main=""
  origin_main="$(git rev-parse --verify -q origin/main 2>/dev/null || true)"
  anti_downgrade_gate "$target" "$prev" "$origin_main" \
    || die "anti-downgrade gate refused the deployment (ALLOW_OLDER_SHA=1 overrides it)"
  printf '%s\n' "$prev" > "$APP_DIR/.deployed_sha.prev" || die "cannot record previous sha"
  backup_db || die "database backup failed — aborting before any change"
  sync_to "$target" || die "code sync to $target failed — aborting (service not touched)"
  # ── DEVOPS-03: ایمیجِ immutable با تگِ SHA ───────────────────────────────────
  if [ "$RUNTIME" = "docker" ]; then
    build_image "$target" || rollback_and_die "image build failed"
  fi
  install_deps || rollback_and_die "dependency install failed"
  gate || rollback_and_die "pre-restart gate failed"
  # ── DEVOPS-08: تخلیهٔ ایمن ⇒ هیچ انتشارِ در حالِ پروازی قطع نمی‌شود ───────────
  if [ "$RUNTIME" = "docker" ]; then
    drain_service || err "WARNING: the graceful drain failed"
    stop_service || die "could not stop the container"
    migrate_db_path || die "database path migration failed — aborting before the new container starts"
  fi
  # OPS-26: شکستِ restart هم باید rollback واقعی بسازد، نه خروجِ خام
  start_service || rollback_and_die "service restart failed"
  clear_drain_flag
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
  preserve_dirty_tree || die "cannot preserve the local changes of the working tree"
  git reset -q --hard "$back" || die "git reset to $back failed"
  [ "$(git rev-parse HEAD)" = "$back" ] || die "rollback verification failed (HEAD != $back)"
  # DEVOPS-03: نسخهٔ قبلی باید از روی همان ایمیجِ SHA-tagged بالا بیاید.
  if [ "$RUNTIME" = "docker" ]; then
    local prev_image="$IMAGE_REPO:$back"
    if ! image_exists "$prev_image"; then
      log "the previous image $prev_image is not on the host — rebuilding it from $back"
      build_image "$back" || die "could not rebuild the previous image $back"
    fi
    IMAGE_CURRENT="$prev_image"
    drain_service || err "WARNING: the graceful drain failed"
    stop_service || die "could not stop the container during the rollback"
  fi
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
