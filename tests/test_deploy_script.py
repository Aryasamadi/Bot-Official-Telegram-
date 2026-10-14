# -*- coding: utf-8 -*-
"""فاز ۲ — تمرین واقعی deploy/deploy.sh در قرنطینه (بدون دست زدن به تولید).

سناریوها:
  1) deploy موفق: sync به SHA + بکاپ آنلاین + ثبت نشانگرها + restart
  2) شکست دروازه (کد خراب): کد به SHA قبلی برمی‌گردد، سرویس هرگز restart نمی‌شود
  3) شکست سلامت پس از restart: کد برمی‌گردد و سرویس دوباره روی کد قبلی بالا می‌آید
  4) زیرفرمان rollback: بازگشت داوطلبانه به SHA قبلی + restart + سلامت

هر سناریو یک origin موقت محلی می‌سازد (clone از همین مخزن) و deploy.sh واقعی را
با PATH استاب‌شدهٔ systemctl/journalctl/python3 و venv قلابی اجرا می‌کند.
"""
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DEPLOY_SH = ROOT / "deploy" / "deploy.sh"


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
    reason="deploy rehearsal needs bash + git (Git for Windows)",
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


class Sandbox:
    def __init__(self, tmp, app, env, origin):
        self.tmp = tmp
        self.app = app
        self.env = env
        self.origin = origin
        self.head0 = _git(app, "rev-parse", "HEAD").strip()
        self.backups = Path(env["BACKUP_DIR"])
        self.stub_log = Path(env["STUB_LOG"])

    def commit_push(self, name, content, subdir=None):
        """یک commit جدید روی origin موقت می‌سازد و SHAاش را برمی‌گرداند."""
        d = self.app / (subdir or "")
        d.mkdir(parents=True, exist_ok=True)
        (d / name).write_text(content, encoding="utf-8")
        env = dict(self.env)
        env.update({"GIT_AUTHOR_NAME": "sandbox", "GIT_AUTHOR_EMAIL": "sb@t",
                    "GIT_COMMITTER_NAME": "sandbox", "GIT_COMMITTER_EMAIL": "sb@t"})
        _git(self.app, "add", "-A", env=env)
        _git(self.app, "commit", "-q", "-m", f"sandbox: {name}", env=env)
        _run(["git", "-C", str(self.app), "push", "-q", "origin", "HEAD:main"], cwd=self.app, env=env)
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

    def rollback_files(self):
        head = _git(self.app, "rev-parse", "HEAD").strip()
        prev = (self.app / ".deployed_sha.prev").read_text().strip()
        dep_path = self.app / ".deployed_sha"
        dep = dep_path.read_text().strip() if dep_path.exists() else None
        return head, prev, dep


@pytest.fixture()
def sandbox(tmp_path):
    origin = tmp_path / "origin.git"
    _run(["git", "clone", "--quiet", "--bare", str(ROOT), str(origin)], cwd=tmp_path)
    _run(["git", "-C", str(origin), "symbolic-ref", "HEAD", "refs/heads/main"], cwd=tmp_path)

    app = tmp_path / "app"
    _run(["git", "clone", "--quiet", str(origin), str(app)], cwd=tmp_path)

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
        '#!/bin/sh\necho "$@" >> "$(dirname "$0")/../../pip.log"\nexit 0\n',
        encoding="utf-8",
    )

    # دیتابیس خالی تا مسیر بکاپ آنلاین واقعاً اجرا شود
    sqlite3.connect(str(app / "newsbot.db")).close()

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


def test_deploy_success_backs_up_and_records(sandbox):
    """سناریو ۱: deploy موفق = sync + بکاپ + نشانگرها + restart."""
    sha = sandbox.commit_push("feature.txt", "hello\n")
    r = sandbox.deploy(sha, check=True)

    head, prev, dep = sandbox.rollback_files()
    assert head == sha, "HEAD باید عیناً SHA محرک deploy باشد (OPS-02)"
    assert prev == sandbox.head0
    assert dep == sha

    backups = list(sandbox.backups.glob("newsbot-*.db"))
    assert len(backups) == 1, "دقیقاً یک بکاپ آنلاین باید ساخته شود"
    assert backups[0].stat().st_size > 0, "بکاپ نباید خالی باشد"

    pip_log = sandbox.app / "pip.log"
    assert pip_log.exists() and "requirements.txt" in pip_log.read_text()
    assert sandbox.restart_count() == 1
    assert "deploy complete" in r.stdout
    assert "health check passed" in r.stdout


def test_gate_failure_rolls_code_back_without_restart(sandbox):
    """سناریو ۲: کد خراب هرگز به سرویس نمی‌رسد و کد هم برمی‌گردد (OPS-03/D7)."""
    sha = sandbox.commit_push("broken.py", "def broken(:\n", subdir="newsbot")
    r = sandbox.deploy(sha)

    assert r.returncode != 0, "کد خراب باید deploy را شکست دهد"
    head, prev, _ = sandbox.rollback_files()
    assert head == sandbox.head0, "کد باید به SHA قبلی برگردد"
    assert prev == sandbox.head0
    assert sandbox.restart_count() == 0, "سرویس نباید هرگز restart شود"
    assert "rolling code back" in r.stderr
    assert not (sandbox.app / ".deployed_sha").exists(), "نشانگر deploy نباید ثبت شود"


def test_health_failure_scenario(sandbox):
    """سناریو ۳ (واقعی): فلگ inactive → سلامت شکست می‌خورد → rollback + restart دوم."""
    sha = sandbox.commit_push("feature.txt", "v2\n")
    # فلگ عدم فعالیت را قبل از deploy بگذار تا is-active همیشه 3 برگرداند
    Path(sandbox.env["STUB_DIR"], "inactive").write_text("1")
    r = sandbox.deploy(sha)

    assert r.returncode != 0, "سلامت شکست‌خورده باید deploy را شکست دهد"
    head, prev, _ = sandbox.rollback_files()
    assert head == sandbox.head0, "کد باید به SHA قبلی برگردد"
    assert prev == sandbox.head0
    assert sandbox.restart_count() == 2, "restart اول + restart کد قبلی بعد از شکست"
    assert not (sandbox.app / ".deployed_sha").exists()
    assert "post-restart health check failed" in r.stderr


def test_rollback_subcommand_restores_previous_sha(sandbox):
    """سناریو ۴: rollback داوطلبانه — بازگشت به SHA قبلی با تأیید سلامت."""
    sha = sandbox.commit_push("feature.txt", "v2\n")
    sandbox.deploy(sha, check=True)
    sandbox.stub_log.write_text("")  # شمارش restartهای rollback از صفر

    r = sandbox.rollback(check=True)
    head, prev, dep = sandbox.rollback_files()
    assert head == sandbox.head0, "rollback باید HEAD را به SHA قبلی ببرد"
    assert dep == sandbox.head0
    assert prev == sandbox.head0  # rollback idempotent است
    assert sandbox.restart_count() == 1
    assert "rolled back to" in r.stdout
    assert "health check passed" in r.stdout
