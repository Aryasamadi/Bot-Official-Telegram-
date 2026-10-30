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
        '#!/bin/sh\necho "$@" >> "$(dirname "$0")/../../pip.log"\nexit 0\n',
        encoding="utf-8",
    )

    # دیتابیس خالی تا مسیر بکاپ آنلاین واقعاً اجرا شود
    sqlite3.connect(str(app / "newsbot.db")).close()

    # استاب‌های PATH: systemctl/journalctl قلابی + python3 واقعی
    # PH-04: استابِ systemctl اکنون یک بلوکِ Key=Value می‌دهد (مثل systemctl show واقعی)
    # تا health بتواند ActiveState/SubState/Type/NRestarts را بخواند.
    sbin = tmp_path / "sbin"
    sbin.mkdir()
    (sbin / "python3").write_text(f'#!/bin/sh\nexec "{py}" "$@"\n', encoding="utf-8")
    (sbin / "systemctl").write_text(
        "#!/bin/sh\n"
        'echo "systemctl $*" >> "$STUB_LOG"\n'
        "case \"$1\" in\n"
        '  is-active) [ -f "$STUB_DIR/inactive" ] && exit 3 || exit 0 ;;\n'
        '  restart) [ -f "$STUB_DIR/restart-fails" ] && exit 1 || exit 0 ;;\n'
        "  show)\n"
        '    if [ -f "$STUB_DIR/inactive" ]; then echo "ActiveState=inactive"; else echo "ActiveState=active"; fi\n'
        '    echo "SubState=${STUB_SUBSTATE:-running}"\n'
        '    echo "Type=${STUB_TYPE:-simple}"\n'
        '    echo "NRestarts=${STUB_NRESTARTS:-0}"\n'
        '    echo "Result=success"\n'
        '    echo "ExecMainStatus=0"\n'
        '    echo "FragmentPath=/etc/systemd/system/${STUB_SERVICE_NAME}.service"\n'
        '    [ -f "$STUB_DIR/no-workdir" ] || echo "WorkingDirectory=${APP_DIR:-}"\n'
        "    exit 0 ;;\n"
        "  cat) cat \"$STUB_DIR/unit.conf\" 2>/dev/null; exit 0 ;;\n"
        "  *) exit 0 ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    (sbin / "journalctl").write_text(
        '#!/bin/sh\necho "journalctl $*" >> "$STUB_LOG"\n'
        'cat "$STUB_DIR/journal" 2>/dev/null\nexit 0\n',
        encoding="utf-8",
    )
    (sbin / "unit.conf").write_text(
        "[Service]\nEnvironmentFile=/opt/app/.env\n", encoding="utf-8"
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
        "STUB_SERVICE_NAME": "sandbox-service",
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


# ══════════════════════════════════════════════════════════════════════════════════════
# PH-04 — سناریوهای امنیتِ استقرار
# ══════════════════════════════════════════════════════════════════════════════════════
def test_failed_restart_triggers_a_real_rollback(sandbox):
    """OPS-26 — شکستِ `systemctl restart` باید rollback واقعی بسازد (نه خروجِ خام)."""
    sha = sandbox.commit_push("feature.txt", "v2\n")
    Path(sandbox.env["STUB_DIR"], "restart-fails").write_text("1")
    r = sandbox.deploy(sha)

    assert r.returncode != 0, "شکستِ restart باید deploy را ناموفق کند"
    head, prev, _ = sandbox.rollback_files()
    assert head == sandbox.head0, "کد باید به SHA قبلی برگردد"
    assert "rolling code back" in r.stderr, "باید مسیرِ rollback اجرا شود"
    assert not (sandbox.app / ".deployed_sha").exists(), "نشانگرِ deploy نباید ثبت شود"


def test_rollback_also_restores_the_dependency_set(sandbox):
    """OPS-06 — rollback فقط کد نیست: وابستگی‌ها هم باید به نسخهٔ قبلی برگردند."""
    sha = sandbox.commit_push("feature.txt", "v2\n")
    Path(sandbox.env["STUB_DIR"], "restart-fails").write_text("1")
    sandbox.deploy(sha)
    pip_log = sandbox.app / "pip.log"
    assert pip_log.exists()
    installs = [ln for ln in pip_log.read_text().splitlines() if "requirements.txt" in ln]
    assert len(installs) >= 2, "rollback باید دوباره وابستگی‌هایِ نسخهٔ قبلی را نصب کند"


def test_health_waits_for_ready_when_the_unit_is_type_notify(sandbox):
    """OPS-02 — با Type=notify، سرویس تا READY=1 در حالتِ activating می‌ماند ⇒ گیت باید منتظر بماند."""
    sha = sandbox.commit_push("feature.txt", "v2\n")
    r = sandbox.deploy(sha, extra={"STUB_TYPE": "notify", "STUB_SUBSTATE": "activating"})
    # گیت باید شکست بخورد چون سرویس «running» نشد
    assert r.returncode != 0, "با Type=notify و SubState=activating نباید deploy موفق شود"
    assert "not ready" in r.stderr or "health check failed" in r.stderr


def test_handled_traceback_in_the_journal_does_not_fail_the_release(sandbox):
    """OPS-03 — «traceback» معیارِ خرابی نیست: برنامه برای استثناهایِ مدیریت‌شده هم آن را می‌نویسد."""
    sha = sandbox.commit_push("feature.txt", "v2\n")
    Path(sandbox.env["STUB_DIR"], "journal").write_text(
        "2026-10-20 10:00:00 host bot[1]: Traceback (most recent call last):\n"
        "2026-10-20 10:00:00 host bot[1]: RuntimeError: handled by the app\n",
        encoding="utf-8",
    )
    r = sandbox.deploy(sha, check=True)
    assert "health check passed" in r.stdout
    assert sandbox.restart_count() == 1, "یک releaseِ سالم نباید rollback شود"


def test_crash_marker_in_the_journal_fails_the_release(sandbox):
    """OPS-03 — نشانهٔ مرگبارِ واقعی (Main process exited) باید انتشار را متوقف کند."""
    sha = sandbox.commit_push("feature.txt", "v2\n")
    Path(sandbox.env["STUB_DIR"], "journal").write_text(
        "2026-10-20 10:00:00 host systemd[1]: bot-ai-automatic.service: Main process exited, code=exited, status=1/FAILURE\n",
        encoding="utf-8",
    )
    r = sandbox.deploy(sha)
    assert r.returncode != 0, "نشانهٔ مرگبار باید deploy را ناموفق کند"
    head, prev, _ = sandbox.rollback_files()
    assert head == sandbox.head0, "کد باید به SHA قبلی برگردد"


def test_backup_is_verified_before_the_deploy_continues(sandbox):
    """OPS-09 — بکاپ باید باز و بررسی شود، نه فقط نوشته."""
    sha = sandbox.commit_push("feature.txt", "v2\n")
    r = sandbox.deploy(sha, check=True)
    assert "backup written and verified" in r.stdout
    backups = list(sandbox.backups.glob("newsbot-*.db"))
    assert len(backups) == 1 and backups[0].stat().st_size > 0


def test_redeploying_the_same_sha_still_backs_up_and_health_checks(sandbox):
    """OPS-25 — «سبز بودنِ deploy» باید شاهدِ سلامت باشد، نه فقط «کد عوض نشده»."""
    sha = sandbox.commit_push("feature.txt", "v2\n")
    sandbox.deploy(sha, check=True)
    before = len(list(sandbox.backups.glob("newsbot-*.db")))
    r = sandbox.deploy(sha, check=True)
    assert "already at" in r.stdout
    assert len(list(sandbox.backups.glob("newsbot-*.db"))) == before + 1, \
        "استقرارِ تکراری هم باید بکاپ بگیرد"
    assert "health check passed" in r.stdout


def test_optional_environment_file_is_reported(sandbox):
    """OPS-08 — اگر EnvironmentFile یونیت اختیاری باشد، هشدار داده می‌شود."""
    (Path(sandbox.env["STUB_DIR"]) / "unit.conf").write_text(
        "[Service]\nEnvironmentFile=-/opt/app/.env\n", encoding="utf-8"
    )
    sha = sandbox.commit_push("feature.txt", "v2\n")
    r = sandbox.deploy(sha, check=True)
    assert "OPTIONAL EnvironmentFile" in r.stderr


# ══════════════════════════════════════════════════════════════════════════════════════
# OPS-27 — مسیر دیتابیس باید از .env/سرویس حل شود، نه با حدس
# ══════════════════════════════════════════════════════════════════════════════════════
BASE_ENV = (
    "BOT_TOKEN=123456789:AAsandbox_dummy_token_0000000000000\n"
    "SUPER_ADMIN_IDS=1\n"
)


def _write_env(sandbox, extra=""):
    (sandbox.app / ".env").write_text(BASE_ENV + extra, encoding="utf-8")


def _make_db(path, marker):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path))
    try:
        con.execute("CREATE TABLE IF NOT EXISTS deploy_probe(marker TEXT)")
        con.execute("DELETE FROM deploy_probe")
        con.execute("INSERT INTO deploy_probe(marker) VALUES (?)", (marker,))
        con.commit()
    finally:
        con.close()


def _latest_backup(sandbox):
    files = sorted(sandbox.backups.glob("newsbot-*.db"), key=lambda p: p.stat().st_mtime_ns)
    assert files, "بکاپی نوشته نشده است"
    return files[-1]


def _backup_marker(path):
    con = sqlite3.connect(str(path))
    try:
        row = con.execute("SELECT marker FROM deploy_probe").fetchone()
        return row[0] if row else None
    finally:
        con.close()


def test_unset_db_file_defaults_to_the_working_directory(sandbox):
    """DB_FILE تعریف‌نشده ⇒ پیش‌فرض newsbot.db نسبت به WorkingDirectory سرویس."""
    _write_env(sandbox)
    _make_db(sandbox.app / "newsbot.db", "default-db")
    sha = sandbox.commit_push("feature.txt", "v2\n")
    r = sandbox.deploy(sha, check=True)
    assert _backup_marker(_latest_backup(sandbox)) == "default-db"
    assert "backend=sqlite" in r.stdout


def test_relative_db_file_is_resolved_against_the_service_working_directory(sandbox):
    """DB_FILE نسبی باید دقیقاً مثل برنامه (نسبت به cwd سرویس) حل شود."""
    _write_env(sandbox, "DB_FILE=data/live.db\n")
    _make_db(sandbox.app / "data" / "live.db", "relative-db")
    sha = sandbox.commit_push("feature.txt", "v2\n")
    sandbox.deploy(sha, check=True)
    assert _backup_marker(_latest_backup(sandbox)) == "relative-db"


def test_absolute_db_file_is_used_verbatim(sandbox):
    """DB_FILE مطلق نباید داخل APP_DIR بازنویسی شود."""
    db = sandbox.tmp / "outside" / "live.db"
    _write_env(sandbox, f"DB_FILE={db.as_posix()}\n")
    _make_db(db, "absolute-db")
    sha = sandbox.commit_push("feature.txt", "v2\n")
    sandbox.deploy(sha, check=True)
    assert _backup_marker(_latest_backup(sandbox)) == "absolute-db"


def test_configured_db_wins_over_a_stale_legacy_file(sandbox):
    """وجود newsbot.db کهنه نباید باعث بکاپ‌گرفتن از فایل اشتباه شود."""
    _write_env(sandbox, "DB_FILE=data/live.db\n")
    _make_db(sandbox.app / "data" / "live.db", "configured-db")
    _make_db(sandbox.app / "newsbot.db", "stale-db")
    sha = sandbox.commit_push("feature.txt", "v2\n")
    sandbox.deploy(sha, check=True)
    assert _backup_marker(_latest_backup(sandbox)) == "configured-db"


def test_missing_configured_db_aborts_the_deploy(sandbox):
    """نبودِ DB پیکربندی‌شده نباید «دیتابیس جدید» تعبیر شود."""
    _write_env(sandbox, "DB_FILE=data/missing.db\n")
    sha = sandbox.commit_push("feature.txt", "v2\n")
    r = sandbox.deploy(sha)
    assert r.returncode != 0
    assert "database not found" in r.stderr
    assert "refusing to deploy without a backup" in r.stderr
    assert _git(sandbox.app, "rev-parse", "HEAD").strip() == sandbox.head0, "هیچ کدی نباید تغییر کند"
    assert sandbox.restart_count() == 0, "سرویس نباید restart شود"
    assert not list(sandbox.backups.glob("newsbot-*.db"))


def test_missing_db_is_allowed_only_with_the_explicit_opt_in(sandbox):
    """استقرار اولیه فقط با ALLOW_MISSING_DB=1 مجاز است و بدون بکاپ ادامه می‌دهد."""
    _write_env(sandbox, "DB_FILE=data/missing.db\n")
    sha = sandbox.commit_push("feature.txt", "v2\n")
    r = sandbox.deploy(sha, check=True, extra={"ALLOW_MISSING_DB": "1"})
    assert "ALLOW_MISSING_DB=1" in r.stderr
    assert "no database yet" in r.stdout
    assert not list(sandbox.backups.glob("newsbot-*.db"))


def test_d1_backend_skips_the_local_sqlite_backup(sandbox):
    """DB_BACKEND=d1 یعنی دیتابیس محلی مرجع نیست؛ بکاپ SQLite نباید گرفته شود."""
    _write_env(sandbox,
               "DB_BACKEND=d1\nCF_ACCOUNT_ID=acct\nCF_D1_DATABASE_ID=dbid\nCF_D1_TOKEN=tok\n")
    _make_db(sandbox.app / "newsbot.db", "not-the-source-of-truth")
    sha = sandbox.commit_push("feature.txt", "v2\n")
    r = sandbox.deploy(sha, check=True)
    assert "backend=d1" in r.stdout
    assert not list(sandbox.backups.glob("newsbot-*.db"))


def test_archive_is_mirrored_from_the_configured_db_directory(sandbox):
    """آرشیو باید از پوشهٔ backups کنار همان DB انتخاب‌شده کپی شود، نه از APP_DIR/backups."""
    _write_env(sandbox, "DB_FILE=data/live.db\n")
    _make_db(sandbox.app / "data" / "live.db", "archive-db")
    _make_db(sandbox.app / "data" / "backups" / "newsbot-archive-right.db", "archive-right")
    _make_db(sandbox.app / "backups" / "newsbot-archive-stale.db", "archive-stale")
    sha = sandbox.commit_push("feature.txt", "v2\n")
    sandbox.deploy(sha, check=True)
    mirrored = sandbox.backups / "archive" / "newsbot-archive-right.db"
    assert mirrored.exists(), "آرشیوِ کنارِ DB انتخاب‌شده باید در بکاپ بیاید"
    assert _backup_marker(mirrored) == "archive-right"
    assert not (sandbox.backups / "archive" / "newsbot-archive-stale.db").exists()


def test_relative_db_file_fails_closed_without_a_known_working_directory(sandbox):
    """اگر WorkingDirectory قابل‌تعیین نباشد، مسیر نسبی نباید حدس زده شود."""
    (Path(sandbox.env["STUB_DIR"]) / "no-workdir").write_text("1")
    _write_env(sandbox, "DB_FILE=data/live.db\n")
    _make_db(sandbox.app / "data" / "live.db", "should-not-be-used")
    sha = sandbox.commit_push("feature.txt", "v2\n")
    r = sandbox.deploy(sha)
    assert r.returncode != 0
    assert "WorkingDirectory is unknown" in r.stderr
    assert not list(sandbox.backups.glob("newsbot-*.db"))


def test_explicit_deploy_working_dir_resolves_a_relative_db_file(sandbox):
    """DEPLOY_WORKING_DIR باید ابهام cwd را صریح حل کند."""
    (Path(sandbox.env["STUB_DIR"]) / "no-workdir").write_text("1")
    _write_env(sandbox, "DB_FILE=data/live.db\n")
    _make_db(sandbox.app / "data" / "live.db", "explicit-workdir")
    sha = sandbox.commit_push("feature.txt", "v2\n")
    sandbox.deploy(sha, check=True,
                   extra={"DEPLOY_WORKING_DIR": sandbox.app.as_posix()})
    assert _backup_marker(_latest_backup(sandbox)) == "explicit-workdir"


def test_empty_db_file_is_rejected(sandbox):
    """DB_FILE خالی با تعریف‌نشده یکی نیست و نباید حدس زده شود."""
    _write_env(sandbox, "DB_FILE=\n")
    sha = sandbox.commit_push("feature.txt", "v2\n")
    r = sandbox.deploy(sha)
    assert r.returncode != 0
    assert "DB_FILE is set but empty" in r.stderr


# ══════════════════════════════════════════════════════════════════════════════════════
# OPS-10/OPS-28/OPS-29 — آرشیو، نگهداری و بکاپِ پیش از Rollback
# ══════════════════════════════════════════════════════════════════════════════════════
def test_failing_archive_copy_aborts_the_deploy(sandbox):
    """کپی ناموفقِ آرشیو باید Deploy را متوقف کند (پوشهٔ آرشیو موجود ولی کپی خراب)."""
    _write_env(sandbox, "DB_FILE=newsbot.db\n")
    _make_db(sandbox.app / "newsbot.db", "archive-copy-failure")
    archive = sandbox.app / "backups"
    archive.mkdir(parents=True, exist_ok=True)
    # یک پوشه به‌جای فایلِ آرشیو ⇒ cp قطعاً شکست می‌خورد (بدون وابستگی به سطح دسترسی)
    (archive / "newsbot-archive-broken.db").mkdir()
    sha = sandbox.commit_push("feature.txt", "v2\n")
    r = sandbox.deploy(sha)
    assert r.returncode != 0, "کپی ناموفقِ آرشیو نباید اجازهٔ انتشار بدهد"
    assert "failed to copy the article archive" in r.stderr
    assert _git(sandbox.app, "rev-parse", "HEAD").strip() == sandbox.head0, "هیچ کدی نباید عوض شود"
    assert sandbox.restart_count() == 0, "سرویس نباید restart شود"


def test_missing_archive_folder_is_not_an_error(sandbox):
    """نبودِ پوشهٔ آرشیو مجاز است و با کپی ناموفق فرق دارد."""
    _write_env(sandbox, "DB_FILE=newsbot.db\n")
    _make_db(sandbox.app / "newsbot.db", "no-archive-folder")
    sha = sandbox.commit_push("feature.txt", "v2\n")
    r = sandbox.deploy(sha, check=True)
    assert "no article archive folder" in r.stdout
    assert _backup_marker(_latest_backup(sandbox)) == "no-archive-folder"


def test_rollback_takes_a_backup_before_switching_versions(sandbox):
    """OPS-29 — پیش از جایگزینی نسخه، از دیتابیس فعلی بکاپ معتبر گرفته می‌شود."""
    _write_env(sandbox, "DB_FILE=newsbot.db\n")
    _make_db(sandbox.app / "newsbot.db", "rollback-db")
    sha = sandbox.commit_push("feature.txt", "v2\n")
    sandbox.deploy(sha, check=True)
    assert len(list(sandbox.backups.glob("newsbot-*.db"))) == 1
    sandbox.rollback(check=True)
    backups = sorted(sandbox.backups.glob("newsbot-*.db"), key=lambda p: p.stat().st_mtime_ns)
    assert len(backups) == 2, "rollback هم باید بکاپ بگیرد"
    assert _backup_marker(backups[-1]) == "rollback-db"


def test_rollback_stops_when_the_database_backup_fails(sandbox):
    """OPS-29 — شکستِ بکاپ ⇒ نه دیتابیس و نه نسخهٔ فعال تغییر می‌کند."""
    _write_env(sandbox, "DB_FILE=newsbot.db\n")
    _make_db(sandbox.app / "newsbot.db", "rollback-backup-failure")
    sha = sandbox.commit_push("feature.txt", "v2\n")
    sandbox.deploy(sha, check=True)
    deployed_head = _git(sandbox.app, "rev-parse", "HEAD").strip()
    restarts_before = sandbox.restart_count()
    # دیتابیس را خراب می‌کنیم تا بکاپ حتماً شکست بخورد
    (sandbox.app / "newsbot.db").write_bytes(b"this is not a sqlite database")
    r = sandbox.rollback()
    assert r.returncode != 0, "rollback بدون بکاپ نباید ادامه یابد"
    assert "database backup failed" in r.stderr
    assert _git(sandbox.app, "rev-parse", "HEAD").strip() == deployed_head, "نسخهٔ فعال نباید عوض شود"
    assert (sandbox.app / ".deployed_sha").read_text(encoding="utf-8").strip() == sha
    assert sandbox.restart_count() == restarts_before, "سرویس نباید restart شود"
    assert len(list(sandbox.backups.glob("newsbot-*.db"))) == 1, "بکاپِ نیم‌بند نباید باقی بماند"


def test_archive_pruning_keeps_the_newest_snapshots_and_other_files(sandbox):
    """OPS-28 — پوشهٔ آرشیو محدود می‌شود، ولی فایل‌های نامرتبط دست‌نخورده می‌مانند."""
    _write_env(sandbox, "DB_FILE=newsbot.db\n")
    _make_db(sandbox.app / "newsbot.db", "prune-db")
    archive = sandbox.app / "backups"
    archive.mkdir(parents=True, exist_ok=True)
    names = ["newsbot-archive-a.db", "newsbot-archive-b.db",
             "newsbot-archive-c.db", "newsbot-archive-d.db"]
    for i, name in enumerate(names):
        snap = archive / name
        _make_db(snap, name)
        os.utime(snap, (1_600_000_000 + i * 60, 1_600_000_000 + i * 60))
    mirror = sandbox.backups / "archive"
    mirror.mkdir(parents=True, exist_ok=True)
    (mirror / "notes.txt").write_text("keep me", encoding="utf-8")
    (mirror / "manual.db").write_text("keep me too", encoding="utf-8")
    sha = sandbox.commit_push("feature.txt", "v2\n")
    sandbox.deploy(sha, check=True, extra={"KEEP_BACKUPS": "2"})
    kept = sorted(p.name for p in mirror.glob("newsbot-archive-*.db"))
    assert kept == ["newsbot-archive-c.db", "newsbot-archive-d.db"], "فقط تازه‌ترین آرشیوها می‌مانند"
    assert (mirror / "notes.txt").exists(), "فایل نامرتبط نباید حذف شود"
    assert (mirror / "manual.db").exists(), "فایل با نامِ متفاوت نباید حذف شود"


# ══════════════════════════════════════════════════════════════════════════════════════
# حالتِ docker — سرویسِ واقعی یک کانتینر است (نه یونیتِ systemd)
# ══════════════════════════════════════════════════════════════════════════════════════

DOCKER_STUB = '''#!/usr/bin/env python3
"""استابِ docker: کانتینری که /app آن bind-mountِ پوشهٔ برنامه روی میزبان است."""
import os
import subprocess
import sys

args = sys.argv[1:]
with open(os.environ["STUB_LOG"], "a", encoding="utf-8") as fh:
    fh.write("docker " + " ".join(args) + "\\n")

CONTAINER = os.environ.get("CONTAINER_NAME", "bot-official-telegram")
APP = os.environ["APP_DIR"]
MOUNT = "/app"


def host_path(value):
    if value == MOUNT:
        return APP
    if value.startswith(MOUNT + "/"):
        return APP + value[len(MOUNT):]
    return value


if args and args[0] == "inspect":
    if len(args) >= 2 and args[1] != CONTAINER:
        sys.exit(1)
    fmt = args[3] if len(args) >= 4 and args[2] == "--format" else ""
    if "{{.Config.WorkingDir}}" in fmt:
        print(MOUNT)
    elif "{{.State.Status}}" in fmt:
        stub_dir = os.environ["STUB_DIR"]
        if os.path.exists(os.path.join(stub_dir, "flaky")):
            counter = os.path.join(stub_dir, "flaky.count")
            seen = 0
            if os.path.exists(counter):
                seen = int(open(counter).read().strip() or "0")
            open(counter, "w").write(str(seen + 1))
            print("running" if seen == 0 else "inactive")
        elif os.path.exists(os.path.join(stub_dir, "inactive")):
            print("inactive")
        else:
            print("running")
    elif "{{.RestartCount}}" in fmt:
        print("0")
    elif ".Mounts" in fmt:
        print(APP + "\\t" + MOUNT)
    elif ".Config.Env" in fmt:
        print("BOT_TOKEN=stub")
    sys.exit(0)

if args and args[0] == "exec":
    rest = args[1:]
    while rest and rest[0] in ("-i", "-t", "-it", "-ti"):
        rest = rest[1:]
    if rest and rest[0] == "-w":
        rest = rest[2:]
    if not rest or rest[0] != CONTAINER:
        sys.exit(1)
    cmd = list(rest[1:])
    if not cmd:
        sys.exit(0)
    env = dict(os.environ)
    env["PYTHONPATH"] = APP
    if cmd[:2] == ["python", "-"]:
        cmd[0] = sys.executable
        # اسکریپتِ داخلِ کانتینر اجرا می‌شود: مسیرِ برنامه به میزبان نگاشت می‌شود،
        # ولی مسیرِ دیتابیس باید همان مسیرِ داخلِ کانتینر بماند (و بعداً نگاشت شود).
        cmd[2] = host_path(cmd[2])
        code = sys.stdin.read()
        helper = ("import os\\n\\n"
                  "def _host_path(p):\\n"
                  "    return p.replace('%s', '%s', 1) if p.startswith('%s') else p\\n" % (MOUNT, APP, MOUNT))
        code = code.replace("import os\\n", helper, 1)
        code = code.replace("os.path.isfile(path)", "os.path.isfile(_host_path(path))")
        sys.exit(subprocess.run(cmd, cwd=APP, env=env, input=code, text=True).returncode)
    if cmd[0] == "pip":
        sys.exit(0)
    if cmd[0] == "python":
        cmd[0] = sys.executable
    sys.exit(subprocess.run([host_path(a) if a.startswith(MOUNT) else a for a in cmd], cwd=APP, env=env).returncode)

if args and args[0] == "restart":
    sys.exit(0)

if args and args[0] == "logs":
    sys.exit(0)

sys.exit(0)
'''


@pytest.fixture()
def docker_sandbox(sandbox):
    """همان sandbox بالا، ولی سرویس یک کانتینر است (پیش‌فرضِ RUNTIME=auto)."""
    sbin = Path(sandbox.env["STUB_DIR"])
    stub = sbin / "docker"
    stub.write_text(DOCKER_STUB, encoding="utf-8")
    os.chmod(str(stub), 0o755)
    sandbox.env["CONTAINER_NAME"] = "bot-official-telegram"
    sandbox.env["DOCKER_SETTLE_SECONDS"] = "0"
    return sandbox


def _docker_calls(sandbox):
    log = Path(sandbox.env["STUB_LOG"])
    if not log.exists():
        return []
    return [line for line in log.read_text(encoding="utf-8").splitlines() if line.startswith("docker ")]


def test_docker_runtime_deploys_through_the_container(docker_sandbox):
    """سرویسِ کانتینری: بکاپ + gate + restart همه از راهِ docker."""
    sha = docker_sandbox.commit_push("feature.txt", "hello docker\n")
    r = docker_sandbox.deploy(sha, check=True)

    head, prev, dep = docker_sandbox.rollback_files()
    assert head == sha
    assert prev == docker_sandbox.head0
    assert dep == sha
    assert "deploy complete" in r.stdout

    calls = _docker_calls(docker_sandbox)
    assert any("pip install" in c for c in calls), "نیازمندی‌ها باید داخل کانتینر نصب شوند"
    assert any("run.py --check" in c for c in calls), "دروازه باید داخل کانتینر اجرا شود"
    assert sum(1 for c in calls if c.startswith("docker restart ")) == 1, "سرویس باید یک‌بار restart شود"
    assert not any(line.startswith("systemctl restart") for line in
                   Path(docker_sandbox.env["STUB_LOG"]).read_text(encoding="utf-8").splitlines()), \
        "در حالت docker نباید systemctl صدا زده شود"


def test_docker_runtime_maps_the_container_db_path_to_the_host_mount(docker_sandbox):
    """مسیرِ دیتابیس داخل کانتینر باید به همان فایل روی میزبان نگاشت شود."""
    _write_env(docker_sandbox, "DB_FILE=newsbot.db\n")
    _make_db(docker_sandbox.app / "newsbot.db", "docker-mode-row")
    sha = docker_sandbox.commit_push("feature.txt", "v2\n")
    docker_sandbox.deploy(sha, check=True)

    backups = list(docker_sandbox.backups.glob("newsbot-*.db"))
    assert len(backups) == 1, "بکاپ باید از مسیرِ نگاشت‌شدهٔ میزبان ساخته شود"
    with sqlite3.connect(str(backups[0])) as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert tables, "بکاپِ دیتابیسِ واقعی باید جدول داشته باشد"


def test_docker_runtime_refuses_to_deploy_without_the_container(docker_sandbox):
    """کانتینرِ غایب ⇒ پیش‌شرط fail-fast، بدون restart."""
    sha = docker_sandbox.commit_push("feature.txt", "v2\n")
    r = docker_sandbox.deploy(sha, extra={"RUNTIME": "docker", "CONTAINER": "other-container"})
    assert r.returncode != 0
    assert "not found" in r.stderr
    assert not any(c.startswith("docker restart ") for c in _docker_calls(docker_sandbox))


def test_docker_runtime_health_failure_rolls_back_the_container(docker_sandbox):
    """کانتینر پس از restart سالم بالا نیامد ⇒ rollback + restart دوم."""
    sha = docker_sandbox.commit_push("feature.txt", "v2\n")
    Path(docker_sandbox.env["STUB_DIR"], "inactive").write_text("1")
    r = docker_sandbox.deploy(sha)

    assert r.returncode != 0, "سلامتِ ناموفق باید deploy را شکست دهد"
    head, prev, _ = docker_sandbox.rollback_files()
    assert head == docker_sandbox.head0, "کد باید به SHA قبلی برگردد"
    assert prev == docker_sandbox.head0
    assert sum(1 for c in _docker_calls(docker_sandbox) if c.startswith("docker restart ")) == 2, \
        "restart اول + restart کد قبلی بعد از شکست"
    assert not (docker_sandbox.app / ".deployed_sha").exists()


def test_docker_runtime_catches_a_crash_right_after_the_restart(docker_sandbox):
    """کانتینر بعد از restart زودتر از «مکثِ سنجش» می‌میرد ⇒ سلامت شکست می‌خورد."""
    sha = docker_sandbox.commit_push("feature.txt", "v2\n")
    Path(docker_sandbox.env["STUB_DIR"], "flaky").write_text("1")
    r = docker_sandbox.deploy(sha)

    assert r.returncode != 0, "کرشِ بلافاصله پس از restart باید deploy را شکست دهد"
    head, prev, _ = docker_sandbox.rollback_files()
    assert head == docker_sandbox.head0, "کد باید به SHA قبلی برگردد"
    assert sum(1 for c in _docker_calls(docker_sandbox) if c.startswith("docker restart ")) == 2, \
        "restart اول + restart کد قبلی بعد از شکست"
