# -*- coding: utf-8 -*-
"""فاز ۶.۲ / F1 — سطح دسترسی Deploy/Rollback و تضمین restart پس از rollback.

این تست‌ها سه چیز را نگهبانی می‌کنند:

1. مستندات `docs/VPS_DEPLOYMENT.md` نباید اجرای `deploy.sh` را با
   `sudo -u botapp` تجویز کند؛ چون اسکریپت در پایان `systemctl restart`
   (نیازمند root) اجرا می‌کند.
2. اگر کنترل سرویس توسط کاربر غیرمجاز رد شود (همان چیزی که با
   `sudo -u botapp` رخ می‌دهد)، deploy باید شکست بخورد، کد را برگرداند و
   هیچ‌وقت «موفق» اعلام نشود.
3. rollback باید کد قبلی را برگرداند، سپس سرویس را restart کند و اگر
   restart شکست خورد، با خطای روشن و کد خروج غیرصفر متوقف شود.

هیچ اتصالی به VPS یا سرویس واقعی وجود ندارد؛ systemctl/journalctl با
استاب‌های همین فایل شبیه‌سازی می‌شوند. هارنس عمداً مستقل از
`tests/test_deploy_script.py` است تا import یک ماژول تستِ خواهر، نگهبان
وابستگی‌ها (`tests/test_dependencies.py`) را به‌اشتباه به‌عنوان وابستگیِ
شخصِ ثالث علامت نزند.
"""
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DEPLOY_SH = ROOT / "deploy" / "deploy.sh"
DEPLOY_DOC = ROOT / "docs" / "VPS_DEPLOYMENT.md"


def _find_bash():
    # Git Bash مقدم است؛ bash سیستمی WSL (system32) مسیرهای ویندوزی را نمی‌فهمد
    for cand in (r"C:\Program Files\Git\bin\bash.exe",
                 r"C:\Program Files\Git\usr\bin\bash.exe",
                 shutil.which("bash")):
        if cand and Path(cand).exists() and "system32" not in cand.lower():
            return cand
    return None


BASH = _find_bash()

pytestmark = pytest.mark.skipif(
    BASH is None or shutil.which("git") is None,
    reason="deploy privilege regression needs bash + git (Git for Windows)",
)


def _run(args, cwd, env=None, check=True):
    r = subprocess.run(args, cwd=str(cwd), env=env, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise AssertionError(
            f"cmd failed ({r.returncode}): {' '.join(map(str, args))}\n"
            f"stdout: {r.stdout[-600:]}\nstderr: {r.stderr[-600:]}"
        )
    return r


def _git(app, *args, env=None):
    return _run(["git", "-C", str(app), *args], cwd=app, env=env).stdout


def _make_db(path, marker="sandbox"):
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY)")
        conn.execute("CREATE TABLE deploy_marker (v TEXT)")
        conn.execute("INSERT INTO deploy_marker VALUES (?)", (marker,))
        conn.commit()
    finally:
        conn.close()


class Sandbox:
    def __init__(self, tmp, app, env, origin):
        self.tmp = tmp
        self.app = app
        self.env = env
        self.origin = origin
        self.head0 = _git(app, "rev-parse", "HEAD").strip()
        self.backups = Path(env["BACKUP_DIR"])
        self.stub_log = Path(env["STUB_LOG"])

    def commit_push(self, name, content):
        """یک commit جدید روی origin موقت می‌سازد و SHAاش را برمی‌گرداند."""
        (self.app / name).write_text(content, encoding="utf-8")
        env = dict(self.env)
        env.update({"GIT_AUTHOR_NAME": "sandbox", "GIT_AUTHOR_EMAIL": "sb@t",
                    "GIT_COMMITTER_NAME": "sandbox", "GIT_COMMITTER_EMAIL": "sb@t"})
        _git(self.app, "add", "-A", env=env)
        _git(self.app, "commit", "-q", "-m", f"sandbox: {name}", env=env)
        _run(["git", "-C", str(self.app), "push", "-q", "origin", "HEAD:main"],
             cwd=self.app, env=env)
        sha = _git(self.app, "rev-parse", "HEAD").strip()
        # VPS واقعی روی کد قدیمی می‌ماند تا fetch+reset کار کند — همین‌جا شبیه‌سازی می‌شود
        _git(self.app, "reset", "-q", "--hard", self.head0, env=env)
        return sha

    def deploy(self, sha, extra=None, check=False):
        env = dict(self.env)
        if extra:
            env.update(extra)
        # Git Bash مسیرهای با اسلش مورب می‌پذیرد؛ بک‌اسلش را نه
        return _run([BASH, str(DEPLOY_SH).replace("\\", "/"), "deploy", sha],
                    cwd=self.tmp, env=env, check=check)

    def rollback(self, check=False):
        return _run([BASH, str(DEPLOY_SH).replace("\\", "/"), "rollback"],
                    cwd=self.tmp, env=self.env, check=check)

    def restart_count(self):
        if not self.stub_log.exists():
            return 0
        return sum(1 for line in self.stub_log.read_text().splitlines()
                   if line.startswith("systemctl restart"))


@pytest.fixture()
def sandbox(tmp_path):
    # مستقل از وضعیت git مخزن میزبان (در CI checkout کم‌عمق/detached است) —
    # فقط فایل‌های کاری کپی و یک repo تازه ساخته می‌شود
    app = tmp_path / "app"
    shutil.copytree(ROOT, app, ignore=shutil.ignore_patterns(
        ".git", ".venv", "venv", "__pycache__", "*.pyc", "$py.class",
        ".pytest_cache", ".mypy_cache", ".ruff_cache", ".idea", ".vscode",
        "*.db", "*.db-*", "*.db-journal", ".env", ".env.*", "*.log",
        "*.bak", "*.tmp", "_audit_tmp", "probe_*.py", "_probe*.py",
        ".deployed_sha", ".deployed_sha.prev",
    ))
    origin = tmp_path / "origin.git"
    seed_env = dict(os.environ)
    seed_env.update({"GIT_AUTHOR_NAME": "sandbox", "GIT_AUTHOR_EMAIL": "sb@t",
                     "GIT_COMMITTER_NAME": "sandbox", "GIT_COMMITTER_EMAIL": "sb@t"})
    _run(["git", "init", "-q", "-b", "main", str(app)], cwd=tmp_path, env=seed_env)
    _run(["git", "init", "-q", "--bare", str(origin)], cwd=tmp_path, env=seed_env)
    _git(app, "add", "-A", env=seed_env)
    _git(app, "commit", "-q", "-m", "seed", env=seed_env)
    _run(["git", "-C", str(app), "remote", "add", "origin", str(origin)], cwd=app, env=seed_env)
    _run(["git", "-C", str(app), "push", "-q", "origin", "main"], cwd=app, env=seed_env)

    # .env قلابی — config.py آن را خودش بارگذاری می‌کند
    (app / ".env").write_text(
        "BOT_TOKEN=123456789:AAsandbox_dummy_token_0000000000000\n"
        "SUPER_ADMIN_IDS=1\n"
        "DB_FILE=newsbot.db\n",
        encoding="utf-8",
    )

    # venv قلابی: python واقعی (برای compileall/--check) + pip بی‌اثرِ ثبت‌کننده
    py = Path(sys.executable).as_posix()
    vbin = app / ".venv" / "bin"
    vbin.mkdir(parents=True)
    (vbin / "python").write_text(f'#!/bin/sh\nexec "{py}" "$@"\n', encoding="utf-8")
    (vbin / "pip").write_text(
        '#!/bin/sh\necho "$@" >> "$(dirname "$0")/../../pip.log"\n'
        'if [ "${FAIL_PIP:-0}" = 1 ]; then exit 1; fi\nexit 0\n',
        encoding="utf-8",
    )

    _make_db(app / "newsbot.db")

    # استاب‌های PATH: systemctl/journalctl قلابی + python3 واقعی
    sbin = tmp_path / "sbin"
    sbin.mkdir()
    (sbin / "python3").write_text(f'#!/bin/sh\nexec "{py}" "$@"\n', encoding="utf-8")
    (sbin / "systemctl").write_text(
        "#!/bin/sh\n"
        'echo "systemctl $*" >> "$STUB_LOG"\n'
        "case \"$1\" in\n"
        '  is-active) [ -f "$STUB_DIR/inactive" ] && exit 3 || exit 0 ;;\n'
        '  show) echo "${STUB_NRESTARTS:-0}" ;;\n'
        '  restart) if [ -f "$STUB_DIR/fail-restart" ]; then exit 1; fi ;;\n'
        "  *) exit 0 ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    (sbin / "journalctl").write_text(
        '#!/bin/sh\necho "journalctl $*" >> "$STUB_LOG"\nexit 0\n',
        encoding="utf-8",
    )

    for f in [vbin / "python", vbin / "pip", sbin / "python3",
              sbin / "systemctl", sbin / "journalctl"]:
        os.chmod(str(f), 0o755)

    env = dict(os.environ)
    env.update({
        "PATH": str(sbin) + os.pathsep + env.get("PATH", ""),
        "APP_DIR": str(app).replace("\\", "/"),
        "BACKUP_DIR": str(tmp_path / "backups").replace("\\", "/"),
        "SERVICE": "sandbox-service",
        "HEALTH_TRIES": "3",
        "STUB_LOG": str(tmp_path / "stub.log").replace("\\", "/"),
        "STUB_DIR": str(sbin).replace("\\", "/"),
    })
    return Sandbox(tmp_path, app, env, origin)


def _doc_section(text, start, end):
    begin = text.index(start)
    finish = text.index(end, begin)
    return text[begin:finish]


def _add_systemctl_wrapper(sandbox, name, body):
    """یک systemctl سفارشی جلوی استابِ اصلی می‌گذارد (بدون لمس سرویس واقعی)."""
    wrapper_dir = sandbox.tmp / name
    wrapper_dir.mkdir()
    wrapper = wrapper_dir / "systemctl"
    wrapper.write_text(body, encoding="utf-8")
    os.chmod(str(wrapper), 0o755)
    return str(wrapper_dir).replace("\\", "/")


# ── قرارداد مستندات ───────────────────────────────────────────────────────────
def test_deploy_and_rollback_docs_require_root_and_reject_sudo_u_botapp():
    """دستورهای Deploy/Rollback باید root باشند و `sudo -u botapp` را رد کنند."""
    text = DEPLOY_DOC.read_text(encoding="utf-8")
    deploy = _doc_section(text, "## ۴.", "## ۵.")
    rollback = _doc_section(text, "## ۵.", "## ۶.")

    for line in text.splitlines():
        if "deploy.sh" in line:
            assert not re.search(r"sudo\s+-u\s+botapp", line), (
                "docs/VPS_DEPLOYMENT.md نباید deploy.sh را با sudo -u botapp اجرا کند: "
                f"{line!r}"
            )

    assert re.search(r"sudo\s+\./deploy/deploy\.sh\s+deploy\b", deploy), \
        "دستور Deploy مستندشده باید با sudo اجرا شود"
    assert re.search(r"sudo\s+\./deploy/deploy\.sh\s+rollback\b", rollback), \
        "دستور Rollback مستندشده باید با sudo اجرا شود"
    assert "ssh root@<VPS_HOST>" in deploy, \
        "روش SSH برای اجرای Deploy باید صریح باشد (ssh root@<VPS_HOST>)"

    for section, label in ((deploy, "Deploy"), (rollback, "Rollback")):
        assert "systemctl restart newsbot" in section, label
        assert "root" in section.lower(), label
        assert "sudo -u botapp" in section, label


def test_rollback_doc_describes_restart_health_and_nonzero_failure():
    """مستند Rollback باید ترتیب restart/health و رفتار شکست را صادقانه بگوید."""
    text = DEPLOY_DOC.read_text(encoding="utf-8")
    rollback = _doc_section(text, "## ۵.", "## ۶.")

    assert ".deployed_sha.prev" in rollback
    assert "systemctl restart newsbot" in rollback
    assert "غیرصفر" in rollback, "باید صریح بگوید شکست restart/health کد خروج غیرصفر دارد"
    assert "سلامت" in rollback, "باید دروازهٔ سلامت پس از restart را ذکر کند"
    assert "systemctl status newsbot" in rollback
    assert "journalctl -u newsbot" in rollback


# ── رفتار دسترسی: سیستم غیرمجاز نمی‌تواند deploy را موفق کند ───────────────────
def test_restart_permission_denied_fails_deploy_and_restores_code(sandbox):
    """شبیه‌سازی `sudo -u botapp`: systemctl restart با «Access denied» رد می‌شود.

    deploy باید در همان مرحله شکست بخورد، کد را به SHA قبلی برگرداند و
    نشانگر `.deployed_sha` را ننویسد — نه اینکه شکست را نادیده بگیرد.
    """
    sha = sandbox.commit_push("feature.txt", "v2\n")
    denied = _add_systemctl_wrapper(
        sandbox,
        "denied-systemctl",
        "#!/bin/sh\n"
        'if [ "$1" = restart ]; then\n'
        '  echo "systemctl $*" >> "$STUB_LOG"\n'
        '  echo "Failed to restart $SERVICE: Interactive authentication required." >&2\n'
        "  exit 1\n"
        "fi\n"
        'exec "$STUB_DIR/systemctl" "$@"\n',
    )

    r = sandbox.deploy(sha, extra={"PATH": denied + os.pathsep + sandbox.env["PATH"]})

    assert r.returncode != 0, "restart غیرمجاز باید deploy را شکست دهد"
    assert "Interactive authentication required" in r.stderr, r.stderr
    assert "service restart failed" in r.stderr, r.stderr
    assert "service restart on previous code" in r.stderr, r.stderr
    assert "deploy complete" not in r.stdout, "نباید موفقیت اعلام شود"
    assert _git(sandbox.app, "rev-parse", "HEAD").strip() == sandbox.head0
    assert not (sandbox.app / ".deployed_sha").exists()
    assert sandbox.restart_count() == 2, "restart اول + تلاش برای restart نسخهٔ قبلی"


def test_rollback_restart_permission_denied_is_nonzero_and_code_is_restored(sandbox):
    """rollback دستی با restart غیرمجاز: کد برمی‌گردد ولی «موفق» اعلام نمی‌شود."""
    sha = sandbox.commit_push("feature.txt", "v2\n")
    sandbox.deploy(sha, check=True)
    sandbox.stub_log.write_text("")

    denied = _add_systemctl_wrapper(
        sandbox,
        "rollback-denied-systemctl",
        "#!/bin/sh\n"
        'if [ "$1" = restart ]; then\n'
        '  echo "systemctl $*" >> "$STUB_LOG"\n'
        '  echo "Failed to restart $SERVICE: Interactive authentication required." >&2\n'
        "  exit 1\n"
        "fi\n"
        'exec "$STUB_DIR/systemctl" "$@"\n',
    )
    sandbox.env["PATH"] = denied + os.pathsep + sandbox.env["PATH"]

    r = sandbox.rollback()

    assert r.returncode != 0, "restart ناموفق در rollback باید کد خروج غیرصفر بدهد"
    assert "systemctl restart failed during rollback" in r.stderr, r.stderr
    assert "rolled back to" not in r.stdout, "نباید rollback موفق اعلام شود"
    assert "health check passed" not in r.stdout
    assert _git(sandbox.app, "rev-parse", "HEAD").strip() == sandbox.head0
    assert sandbox.restart_count() == 1, "فقط یک تلاش restart، بدون ادعای موفقیت"


# ── نتیجهٔ Rollback: کد قبل از restart برگشته باشد ─────────────────────────────
def test_rollback_restarts_service_after_restoring_previous_code(sandbox):
    """در لحظهٔ restart، HEAD باید همان SHA قبلی باشد (نه SHA هدف)."""
    sha = sandbox.commit_push("feature.txt", "v2\n")
    sandbox.deploy(sha, check=True)
    assert (sandbox.app / "feature.txt").exists(), "precondition: فایل نسخهٔ هدف"

    restart_heads = sandbox.tmp / "restart-heads.log"
    sandbox.env["RESTART_HEADS"] = str(restart_heads).replace("\\", "/")
    recorder = _add_systemctl_wrapper(
        sandbox,
        "recording-systemctl",
        "#!/bin/sh\n"
        'if [ "$1" = restart ]; then\n'
        '  git -C "$APP_DIR" rev-parse HEAD >> "$RESTART_HEADS"\n'
        "fi\n"
        'exec "$STUB_DIR/systemctl" "$@"\n',
    )
    sandbox.env["PATH"] = recorder + os.pathsep + sandbox.env["PATH"]
    sandbox.stub_log.write_text("")

    r = sandbox.rollback(check=True)

    assert _git(sandbox.app, "rev-parse", "HEAD").strip() == sandbox.head0
    assert not (sandbox.app / "feature.txt").exists(), "کد نسخهٔ هدف باید حذف شده باشد"
    assert restart_heads.read_text(encoding="utf-8").split() == [sandbox.head0], \
        "restart باید بعد از بازگرداندن کد و روی SHA قبلی انجام شده باشد"
    assert sandbox.restart_count() == 1
    assert (sandbox.app / ".deployed_sha").read_text(encoding="utf-8").strip() == sandbox.head0
    assert "rolled back to" in r.stdout
    assert "health check passed" in r.stdout
