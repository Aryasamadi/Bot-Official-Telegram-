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
# Overridable for tests: APP_DIR, BACKUP_DIR, SERVICE, KEEP_BACKUPS, HEALTH_TRIES.
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/bot-ai-automatic}"
BACKUP_DIR="${BACKUP_DIR:-/opt/backups}"
SERVICE="${SERVICE:-bot-ai-automatic}"
KEEP_BACKUPS="${KEEP_BACKUPS:-7}"
# بودجهٔ سلامت: برابرِ TimeoutStartSec یونیت (پیش‌فرض ۱۸۰ ثانیه). با Type=notify
# سرویس تا دریافتِ READY=1 در حالتِ activating می‌ماند، پس ۱۰ ثانیه کافی نبود.
HEALTH_TRIES="${HEALTH_TRIES:-180}"
RESTARTED=0
RESTART_EPOCH=0

log() { printf '[deploy] %s\n' "$*"; }
err() { printf '[deploy][error] %s\n' "$*" >&2; }
die() { err "$*"; exit 1; }

# ── خواندنِ خصوصیاتِ یونیت (یک فراخوانی، بدونِ فرضِ خروجیِ خاص) ──
unit_info() { systemctl show "$SERVICE" 2>/dev/null || true; }

prop() { printf '%s\n' "$1" | sed -n "s/^$2=//p" | head -n 1; }

preflight() {
  command -v git >/dev/null 2>&1 || die "git not found in PATH"
  command -v python3 >/dev/null 2>&1 || die "python3 not found in PATH (needed for online backup)"
  command -v systemctl >/dev/null 2>&1 || die "systemctl not found in PATH"
  [ -d "$APP_DIR/.git" ] || die "not a git checkout: $APP_DIR"
  [ -f "$APP_DIR/.env" ] || die ".env missing in $APP_DIR"
  [ -x "$APP_DIR/.venv/bin/python" ] || die ".venv/bin/python missing in $APP_DIR"
  [ -x "$APP_DIR/.venv/bin/pip" ] || die ".venv/bin/pip missing in $APP_DIR"
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
  chmod 600 "$dest" || return 1
  # OPS-10: پوشهٔ آرشیوِ مقالاتِ قدیمی (کنارِ دیتابیس) هم باید پشتیبانی شود
  if [ -d "$APP_DIR/backups" ]; then
    mkdir -p "$BACKUP_DIR/archive" || return 1
    cp -p "$APP_DIR"/backups/*.db "$BACKUP_DIR/archive/" 2>/dev/null || true
    log "archive folder mirrored to $BACKUP_DIR/archive"
  fi
  # فقط KEEP_BACKUPS بکاپ اخیر نگه داشته می‌شود
  ls -1t "$BACKUP_DIR"/newsbot-*.db 2>/dev/null | tail -n +$((KEEP_BACKUPS + 1)) | xargs -r rm --
  log "backup written and verified: $dest"
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
  RESTART_EPOCH="$(date +%s)"
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
  cd "$APP_DIR" || die "cannot cd into $APP_DIR"
  [ -f "$APP_DIR/.deployed_sha.prev" ] || die "no previous sha recorded (.deployed_sha.prev) — nothing to roll back to"
  local back
  back="$(tr -d '[:space:]' < "$APP_DIR/.deployed_sha.prev")"
  [ -n "$back" ] || die "recorded previous sha is empty"
  git cat-file -e "${back}^{commit}" 2>/dev/null || die "previous sha $back not present in repository"
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
