#!/usr/bin/env bash
# NewsBot — ساختِ کاربرِ اختصاصیِ استقرار روی VPS (DEVOPS-01)
#
# هدف: استقرار از راهِ SSH با *کمترین* سطحِ دسترسی انجام شود:
#   * به‌جای root ⇒ یک کاربرِ سیستمیِ `deploy` بدونِ رمز و بدونِ پوستهٔ آزاد؛
#   * هر نشستِ SSH به یک *فرمانِ اجباری* (forced command) می‌رود ⇒ نه پوسته،
#     نه port-forwarding، نه X11، نه agent-forwarding، نه PTY؛
#   * فقط اجرای اسکریپتِ استقرار از طریق یک قاعدهٔ محدودِ sudoers مجاز است
#     (پارامترها هم در forced command اعتبارسنجی می‌شوند).
#
# استفاده (روی VPS، با root):
#   bash deploy/setup-deploy-user.sh --key-file /root/newsbot_deploy_ed25519.pub
#   bash deploy/setup-deploy-user.sh --key-file ... --dry-run     # فقط نمایش
#
# گزینه‌ها:
#   --user <name>          نامِ کاربر (پیش‌فرض: deploy)
#   --app-dir <path>       مسیرِ checkout روی میزبان (پیش‌فرض: /opt/Bot-Official-Telegram)
#   --key-file <path>      کلیدِ عمومیِ CI (محتوای خطِ اول به authorized_keys اضافه می‌شود)
#   --docker-group <name>  گروهِ docker (پیش‌فرض: docker)
#   --dry-run             هیچ تغییری در سیستم اعمال نمی‌شود
#   -h, --help             همین راهنما
#
# متغیرهای محیطی (هم‌ارزِ گزینه‌ها، برای استفاده در تست):
#   DEPLOY_USER, DEPLOY_APP_DIR, DEPLOY_KEY_FILE, DEPLOY_DOCKER_GROUP,
#   DEPLOY_HOME, DEPLOY_SUDOERS_FILE, DEPLOY_FORCED_COMMAND
set -euo pipefail

DEPLOY_USER="${DEPLOY_USER:-deploy}"
APP_DIR="${DEPLOY_APP_DIR:-/opt/Bot-Official-Telegram}"
KEY_FILE="${DEPLOY_KEY_FILE:-}"
DOCKER_GROUP="${DEPLOY_DOCKER_GROUP:-docker}"
HOME_DIR="${DEPLOY_HOME:-}"
SUDOERS_FILE="${DEPLOY_SUDOERS_FILE:-/etc/sudoers.d/newsbot-deploy}"
FORCED_COMMAND="${DEPLOY_FORCED_COMMAND:-}"
DRY_RUN=0

# ── خروجی ──────────────────────────────────────────────────────────────────────
log()  { printf '[setup-deploy-user] %s\n' "$*"; }
warn() { printf '[setup-deploy-user] WARNING: %s\n' "$*" >&2; }
die()  { printf '[setup-deploy-user] ERROR: %s\n' "$*" >&2; exit 1; }

usage() {
  cat <<'EOF'
NewsBot — ساختِ کاربرِ اختصاصیِ استقرار روی VPS (DEVOPS-01)

استفاده (روی VPS، با root):
  bash deploy/setup-deploy-user.sh --key-file /root/newsbot_deploy_ed25519.pub
  bash deploy/setup-deploy-user.sh --key-file ... --dry-run     # فقط نمایش

گزینه‌ها:
  --user <name>          نامِ کاربر (پیش‌فرض: deploy)
  --app-dir <path>       مسیرِ checkout روی میزبان (پیش‌فرض: /opt/Bot-Official-Telegram)
  --key-file <path>      کلیدِ عمومیِ CI (خطِ اول به authorized_keys اضافه می‌شود)
  --docker-group <name>  گروهِ docker (پیش‌فرض: docker)
  --dry-run              هیچ تغییری در سیستم اعمال نمی‌شود
  -h, --help             همین راهنما

متغیرهای محیطی (هم‌ارزِ گزینه‌ها، برای استفاده در تست):
  DEPLOY_USER, DEPLOY_APP_DIR, DEPLOY_KEY_FILE, DEPLOY_DOCKER_GROUP,
  DEPLOY_HOME, DEPLOY_SUDOERS_FILE, DEPLOY_FORCED_COMMAND
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --user) DEPLOY_USER="${2:?--user needs a value}"; shift 2 ;;
    --app-dir) APP_DIR="${2:?--app-dir needs a value}"; shift 2 ;;
    --key-file) KEY_FILE="${2:?--key-file needs a value}"; shift 2 ;;
    --docker-group) DOCKER_GROUP="${2:?--docker-group needs a value}"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown argument: $1 (try --help)" ;;
  esac
done

[ "$(id -u)" = "0" ] || [ "$DRY_RUN" = "1" ] || die "this script must run as root (or with --dry-run)"
[ -n "$FORCED_COMMAND" ] || FORCED_COMMAND="$APP_DIR/deploy/ssh-forced-command.sh"

# ── ۱) کاربرِ سیستمی ───────────────────────────────────────────────────────────
if id "$DEPLOY_USER" >/dev/null 2>&1; then
  log "the user '$DEPLOY_USER' already exists — it will be adjusted, not recreated"
  USER_EXISTS=1
else
  USER_EXISTS=0
  log "creating the system user '$DEPLOY_USER'"
  if [ "$DRY_RUN" = "1" ]; then
    log "would run: useradd --system --create-home --shell /bin/bash $DEPLOY_USER"
  else
    # --system ⇒ بدونِ expiry و بدونِ کارتِ UID قدیمی؛ پوستهٔ bash لازم است چون
    # sshd فرمانِ اجباری را با آن اجرا می‌کند (nologin آن را می‌شکند).
    useradd --system --create-home --home-dir "/home/$DEPLOY_USER" --shell /bin/bash "$DEPLOY_USER"
  fi
fi

if [ -z "$HOME_DIR" ]; then
  HOME_DIR="$(getent passwd "$DEPLOY_USER" | cut -d: -f6 || true)"
  [ -n "$HOME_DIR" ] || HOME_DIR="/home/$DEPLOY_USER"
fi
log "home directory: $HOME_DIR"

# ── ۲) گروهِ docker (برای اجرای فرمان‌های docker بدونِ root) ───────────────────
if getent group "$DOCKER_GROUP" >/dev/null 2>&1; then
  log "the group '$DOCKER_GROUP' exists"
else
  log "creating the group '$DOCKER_GROUP'"
  if [ "$DRY_RUN" = "1" ]; then
    log "would run: groupadd $DOCKER_GROUP"
  else
    groupadd "$DOCKER_GROUP"
  fi
fi
if id -nG "$DEPLOY_USER" 2>/dev/null | tr ' ' '\n' | grep -qx "$DOCKER_GROUP"; then
  log "the user '$DEPLOY_USER' is already a member of '$DOCKER_GROUP'"
else
  log "adding '$DEPLOY_USER' to the group '$DOCKER_GROUP'"
  if [ "$DRY_RUN" = "1" ]; then
    log "would run: usermod -aG $DOCKER_GROUP $DEPLOY_USER"
  else
    usermod -aG "$DOCKER_GROUP" "$DEPLOY_USER"
  fi
fi

# ── ۳) قاعدهٔ sudoers (فقط اجرای اسکریپتِ استقرار) ─────────────────────────────
# پارامترها در forced command اعتبارسنجی می‌شوند (SHA باید ۴۰ رقمِ hex باشد)،
# ولی قاعدهٔ sudoers هم به همان مسیرِ ثابت محدود است تا کاربر نتواند
# فرمانِ دلخواه را sudo کند.
SUDOERS_CONTENT="# NewsBot deployment rule (DEVOPS-01) — generated by deploy/setup-deploy-user.sh
# The deploy user may run ONLY the deployment scripts, as root, without a password.
# The arguments are validated again inside deploy/ssh-forced-command.sh (40-hex SHA)
# and inside deploy/deploy.sh itself (the commit must exist in the repository).
${DEPLOY_USER} ALL=(root) NOPASSWD: ${APP_DIR}/deploy/deploy.sh deploy *, ${APP_DIR}/deploy/deploy.sh rollback, /tmp/newsbot-deploy.sh deploy *
"

if [ "$DRY_RUN" = "1" ]; then
  log "would write $SUDOERS_FILE:"
  printf '%s\n' "$SUDOERS_CONTENT" | sed 's/^/    /'
else
  # اعتبارسنجی *قبل* از نصب ⇒ یک قاعدهٔ خراب هرگز sudo را نمی‌شکند.
  TMP_SUDOERS="$(mktemp)"
  printf '%s' "$SUDOERS_CONTENT" > "$TMP_SUDOERS"
  if ! visudo -c -f "$TMP_SUDOERS" >/dev/null 2>&1; then
    visudo -c -f "$TMP_SUDOERS" || true
    rm -f "$TMP_SUDOERS"
    die "the generated sudoers rule is invalid — nothing was installed"
  fi
  rm -f "$TMP_SUDOERS"
  log "the sudoers rule is valid (visudo -c)"
  TMP_INSTALL="$(mktemp)"
  printf '%s' "$SUDOERS_CONTENT" > "$TMP_INSTALL"
  install -m 0440 -o root -g root "$TMP_INSTALL" "$SUDOERS_FILE"
  rm -f "$TMP_INSTALL"
  log "installed $SUDOERS_FILE"
fi

# ── ۴) کلیدِ عمومی + فرمانِ اجباری ────────────────────────────────────────────
SSH_DIR="$HOME_DIR/.ssh"
AUTH_KEYS="$SSH_DIR/authorized_keys"
PUBKEY=""
if [ -n "$KEY_FILE" ]; then
  [ -r "$KEY_FILE" ] || die "the key file '$KEY_FILE' is not readable"
  PUBKEY="$(grep -vE '^\s*(#|$)' "$KEY_FILE" | head -n 1)"
  [ -n "$PUBKEY" ] || die "the key file '$KEY_FILE' has no key"
fi

AUTH_LINE=""
if [ -n "$PUBKEY" ]; then
  # هر گزینهٔ محدودکننده جدا از فرمانِ اجباری: بدونِ PTY، بدونِ forwarding،
  # بدونِ agent. نتیجه: یک نشستِ SSH می‌تواند فقط یکی از چهار فرمان را اجرا کند.
  AUTH_LINE="command=\"${FORCED_COMMAND}\",no-port-forwarding,no-X11-forwarding,no-agent-forwarding,no-pty ${PUBKEY}"
else
  warn "no --key-file given — the authorized_keys file will be left untouched"
fi

if [ "$DRY_RUN" = "1" ]; then
  log "would prepare $SSH_DIR (0700) and $AUTH_KEYS (0600)"
  [ -n "$AUTH_LINE" ] && log "would write the key line: $AUTH_LINE"
else
  install -d -m 0700 -o "$DEPLOY_USER" -g "$DEPLOY_USER" "$SSH_DIR"
  if [ -n "$AUTH_LINE" ]; then
    # کلیدِ قبلیِ همان کلید را جایگزین می‌کند (idempotent)، بقیه دست‌نخورده می‌مانند.
    KEY_BLOB="$(printf '%s' "$PUBKEY" | awk '{print $2}')"
    TMP_AUTH="$(mktemp)"
    if [ -f "$AUTH_KEYS" ]; then
      grep -v "$KEY_BLOB" "$AUTH_KEYS" > "$TMP_AUTH" || true
    else
      : > "$TMP_AUTH"
    fi
    printf '%s\n' "$AUTH_LINE" >> "$TMP_AUTH"
    install -m 0600 -o "$DEPLOY_USER" -g "$DEPLOY_USER" "$TMP_AUTH" "$AUTH_KEYS"
    rm -f "$TMP_AUTH"
    log "installed the public key in $AUTH_KEYS"
  fi
  chown -R "$DEPLOY_USER":"$DEPLOY_USER" "$SSH_DIR"
  chmod 0700 "$SSH_DIR"
  chmod 0600 "$AUTH_KEYS" 2>/dev/null || true
fi

# ── ۵) اسکریپتِ فرمانِ اجباری باید فقط برای root خواندنی-نوشتنی باشد ──────────
FORCED_PATH="$FORCED_COMMAND"
if [ -e "$FORCED_PATH" ]; then
  log "hardening $FORCED_PATH (root-owned, not writable by the deploy user)"
  if [ "$DRY_RUN" = "1" ]; then
    log "would run: chown root:root $FORCED_PATH && chmod 0755 $FORCED_PATH"
  else
    chown root:root "$FORCED_PATH"
    chmod 0755 "$FORCED_PATH"
  fi
else
  warn "the forced-command script '$FORCED_PATH' does not exist yet — copy the repository first"
fi

# ── ۶) حساب کاربری: بدونِ رمز، بدونِ نشستِ تعاملی ───────────────────────────────
if [ "$DRY_RUN" = "1" ]; then
  log "would lock the password of '$DEPLOY_USER' and set an expired shell-independent expiry"
else
  passwd -l "$DEPLOY_USER" >/dev/null 2>&1 || warn "could not lock the password of '$DEPLOY_USER'"
  usermod --expiredate -1 "$DEPLOY_USER" 2>/dev/null || true
  # بدونِ کلیدِ ssh ⇒ حتی با فرمانِ اجباری هم نشستی باز نمی‌شود.
  if [ -n "$AUTH_LINE" ]; then
    log "the account is restricted to the forced command (no shell, no forwarding, no PTY)"
  fi
fi

log "done. Test it with: ssh -i <private-key> ${DEPLOY_USER}@<host> 'deploy <40-hex-sha>'"
