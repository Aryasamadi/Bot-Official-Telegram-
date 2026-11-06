#!/usr/bin/env bash
# NewsBot — forced command for the restricted `deploy` SSH user (DEVOPS-01).
#
# The key of the deploy user is pinned in ~/.ssh/authorized_keys with
#   command="…/deploy/ssh-forced-command.sh",no-port-forwarding,no-X11-forwarding,
#   no-agent-forwarding,no-pty
# ⇒ sshd hands every session to this script and passes the requested command in
# $SSH_ORIGINAL_COMMAND. This script allows exactly three things and rejects
# everything else — the account can never open a shell on the VPS.
#
# Allowed:
#   deploy <40-hex-sha>   run the deploy script for that commit (through sudo -n)
#   rollback              roll back to the previously recorded release
#   preflight             read-only health/inspection output (no root needed)
#   health                read-only health output (no root needed)
#
# Everything else ⇒ exit 126 with an explicit message.
#
# Environment overrides (used by the tests and by a custom rollout):
#   DEPLOY_SCRIPT   path of deploy.sh          (default: /opt/Bot-Official-Telegram/deploy/deploy.sh)
#   SUDO            how to escalate             (default: sudo -n)
#   APP_DIR         the checkout on the host   (default: /opt/Bot-Official-Telegram)
#   UPLOADED_SCRIPT the copy scp'ed by CI      (default: /tmp/newsbot-deploy.sh)
set -euo pipefail

DEPLOY_SCRIPT="${DEPLOY_SCRIPT:-/opt/Bot-Official-Telegram/deploy/deploy.sh}"
SUDO="${SUDO:-sudo -n}"
APP_DIR="${APP_DIR:-/opt/Bot-Official-Telegram}"
ALLOW_PREFLIGHT="${ALLOW_PREFLIGHT:-1}"

log() { printf '[forced-command] %s\n' "$*" >&2; }

audit() {
  # audit trail without failing the command when syslog is unavailable
  if command -v logger >/dev/null 2>&1; then
    logger -t newsbot-deploy -- "${1:-}" >/dev/null 2>&1 || true
  fi
}

usage() {
  cat >&2 <<'EOF'
Allowed commands:
  deploy <40-hex-sha>   deploy that commit (via the restricted sudoers rule)
  rollback              roll back to the previously recorded release
  preflight             read-only inspection output
  health                read-only health output
EOF
}

command_line="${SSH_ORIGINAL_COMMAND:-}"

if [ -z "$command_line" ]; then
  log "no command given"
  usage
  exit 1
fi

# ── فقط فرمان‌هایِ دقیقِ زیر مجازند (هیچ پوسته‌ای باز نمی‌شود) ───────────────────
read -r -a argv <<< "$command_line"
verb="${argv[0]:-}"

# اسکریپتِ بارگذاری‌شده توسط CI (scp) در /tmp — فقط اگر *محتوایش* دقیقاً همان
# «deploy/deploy.sh»ِ کامیتِ هدف باشد اجرا می‌شود (بررسی با git hash-object).
# این‌طوری نسخهٔ جدیدِ اسکریپت (با دروازهٔ ضدِ عقب‌گرد و تخلیهٔ ایمن) اجرا می‌شود،
# بدونِ این‌که به کاربر اجازه داده شود فایلِ دلخواه را به‌عنوان root اجرا کند.
UPLOADED_SCRIPT="${UPLOADED_SCRIPT:-/tmp/newsbot-deploy.sh}"

verify_uploaded_script() {
  local sha="$1" expected actual
  command -v git >/dev/null 2>&1 || return 1
  [ -r "$UPLOADED_SCRIPT" ] || return 1
  expected="$(git -C "$APP_DIR" rev-parse --verify -q "$sha:deploy/deploy.sh" 2>/dev/null || true)"
  [ -n "$expected" ] || return 1
  actual="$(git hash-object "$UPLOADED_SCRIPT" 2>/dev/null || true)"
  [ -n "$actual" ] || return 1
  [ "$expected" = "$actual" ] || return 1
  return 0
}

resolve_deploy_script() {
  local sha="$1"
  if verify_uploaded_script "$sha"; then
    printf '%s\n' "$UPLOADED_SCRIPT"
    return 0
  fi
  if [ -x "$DEPLOY_SCRIPT" ]; then
    printf '%s\n' "$DEPLOY_SCRIPT"
    return 0
  fi
  return 1
}

case "$verb" in
  deploy)
    if [ "${#argv[@]}" -ne 2 ]; then
      log "deploy needs exactly one commit SHA"
      usage
      exit 2
    fi
    sha="${argv[1]}"
    if ! printf '%s' "$sha" | grep -Eq '^[0-9a-f]{40}$'; then
      log "refusing a malformed commit SHA: ${sha:0:64}"
      exit 2
    fi
    audit "deploy $sha"
    script="$(resolve_deploy_script "$sha" || true)"
    if [ -z "$script" ]; then
      log "no usable deploy script (neither the uploaded nor the checked-out one)"
      exit 1
    fi
    exec $SUDO "$script" deploy "$sha"
    ;;
  rollback)
    if [ "${#argv[@]}" -ne 1 ]; then
      log "rollback takes no arguments"
      usage
      exit 2
    fi
    audit "rollback"
    exec $SUDO "$DEPLOY_SCRIPT" rollback
    ;;
  preflight)
    [ "${#argv[@]}" -eq 1 ] || { log "preflight takes no arguments"; usage; exit 2; }
    audit "preflight"
    echo "== git HEAD =="
    git -C "$APP_DIR" rev-parse HEAD 2>/dev/null || echo "unknown"
    echo "== deployed sha =="
    cat "$APP_DIR/.deployed_sha" 2>/dev/null || echo "unknown"
    echo "== container =="
    docker ps --filter "name=bot-official-telegram" --format '{{.Names}} {{.Status}}' 2>/dev/null || echo "docker unavailable"
    exit 0
    ;;
  health)
    [ "${#argv[@]}" -eq 1 ] || { log "health takes no arguments"; usage; exit 2; }
    audit "health"
    if docker ps --filter "name=bot-official-telegram" --format '{{.Names}}' 2>/dev/null | grep -q .; then
      docker exec bot-official-telegram python -m newsbot.healthcheck 2>/dev/null || true
      exit 0
    fi
    systemctl is-active newsbot.service 2>/dev/null || true
    exit 0
    ;;
  *)
    log "command not allowed: ${verb:0:64}"
    usage
    exit 126
    ;;
esac
