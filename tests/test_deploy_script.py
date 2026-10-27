# -*- coding: utf-8 -*-
"""فاز ۲ — تمرین واقعی deploy/deploy.sh در قرنطینه (بدون دست زدن به تولید).

سناریوها:
  1) deploy موفق: sync به SHA + بکاپ آنلاین + ثبت نشانگرها + restart
  2) شکست دروازه (کد خراب): کد به SHA قبلی برمی‌گردد، سرویس هرگز restart نمی‌شود
  3) شکست سلامت پس از restart: کد برمی‌گردد و سرویس دوباره روی کد قبلی بالا می‌آید
  4) زیرفرمان rollback: بازگشت داوطلبانه به SHA قبلی + restart + سلامت
  5) درختِ کاری کثیف: deploy متوقف می‌شود و تغییرات محلیِ ثبت‌نشده نابود نمی‌شوند

هر سناریو یک origin موقت محلی می‌سازد (clone از همین مخزن) و deploy.sh واقعی را
با PATH استاب‌شدهٔ systemctl/journalctl/python3 و venv قلابی اجرا می‌کند.
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

    def commit_push(self, name, content, subdir=None, force=False):
        """یک commit جدید روی origin موقت می‌سازد و SHAاش را برمی‌گرداند."""
        d = self.app / (subdir or "")
        d.mkdir(parents=True, exist_ok=True)
        (d / name).write_text(content, encoding="utf-8")
        env = dict(self.env)
        env.update({"GIT_AUTHOR_NAME": "sandbox", "GIT_AUTHOR_EMAIL": "sb@t",
                    "GIT_COMMITTER_NAME": "sandbox", "GIT_COMMITTER_EMAIL": "sb@t"})
        if force:
            rel = (d / name).relative_to(self.app).as_posix()
            _git(self.app, "add", "-f", rel, env=env)
        else:
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
        '#!/bin/sh\necho "$@" >> "$(dirname "$0")/../../pip.log"\n'
        'if [ "${FAIL_PIP:-0}" = 1 ]; then exit 1; fi\nexit 0\n',
        encoding="utf-8",
    )

    # دیتابیسِ واقعی‌نما تا مسیر بکاپ آنلاین واقعاً اجرا شود و دروازه (run.py --check)
    # همان دیتابیسی را ببیند که سرویس از .env می‌خواند
    _make_db(app / "newsbot.db", marker="sandbox")

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


def test_dependency_install_failure_rolls_back_without_restart(sandbox):
    """شکست pip باید کد target را برگرداند و سرویس را دست نزند."""
    sha = sandbox.commit_push("feature.txt", "v2\n")

    r = sandbox.deploy(sha, extra={"FAIL_PIP": "1"})

    assert r.returncode != 0
    assert "dependency install failed" in r.stderr, r.stderr
    assert _git(sandbox.app, "rev-parse", "HEAD").strip() == sandbox.head0
    assert sandbox.restart_count() == 0
    assert not (sandbox.app / ".deployed_sha").exists()


def test_restart_failure_rolls_back_code_without_claiming_restart(sandbox):
    """شکست restart باید کد را برگرداند و شکست rollback/restart را صادقانه گزارش کند."""
    sha = sandbox.commit_push("feature.txt", "v2\n")
    Path(sandbox.env["STUB_DIR"], "fail-restart").write_text("1", encoding="utf-8")

    r = sandbox.deploy(sha)

    assert r.returncode != 0
    assert "service restart failed" in r.stderr, r.stderr
    assert "service restart on previous code" in r.stderr, r.stderr
    assert _git(sandbox.app, "rev-parse", "HEAD").strip() == sandbox.head0
    assert sandbox.restart_count() == 2
    assert not (sandbox.app / ".deployed_sha").exists()


def test_dirty_working_tree_stops_deploy_and_preserves_local_changes(sandbox):
    """سناریو ۵: «git reset --hard» نباید تغییرات محلیِ ثبت‌نشده را بی‌صدا نابود کند.

    این همان وضعیت VPS است: یک اصلاح دستی روی newsbot/db.py به‌علاوهٔ فایل پشتیبانِ
    untracked. deploy باید با پیام روشن متوقف شود، نه اینکه اصلاح را پاک کند.
    """
    sha = sandbox.commit_push("feature.txt", "v2\n")
    tracked = sandbox.app / "newsbot" / "db.py"
    tracked.write_text(tracked.read_text(encoding="utf-8") + "\n# local hotfix on the VPS\n",
                       encoding="utf-8")
    backup = sandbox.app / "newsbot" / "db.py.bak-d1-migration"
    backup.write_text("# original\n", encoding="utf-8")

    r = sandbox.deploy(sha)

    assert r.returncode != 0, "درختِ کثیف باید deploy را متوقف کند"
    assert "uncommitted changes" in r.stderr, r.stderr
    assert _git(sandbox.app, "rev-parse", "HEAD").strip() == sandbox.head0, "HEAD نباید تغییر کند"
    assert not (sandbox.app / ".deployed_sha.prev").exists(), "نشانگر rollback نباید پیش از گیت تغییر کند"
    assert sandbox.restart_count() == 0, "سرویس نباید restart شود"
    # تغییر محلی و فایل پشتیبان باید سالم مانده باشند
    assert "local hotfix on the VPS" in tracked.read_text(encoding="utf-8")
    assert backup.exists(), "فایل untracked (پشتیبان) نباید حذف شود"


def test_untracked_file_stops_deploy_and_is_preserved(sandbox):
    """فایل جدیدِ untracked نیز وضعیت محلی است و نباید ناخواسته حذف/بازنویسی شود."""
    sha = sandbox.commit_push("feature.txt", "v2\n")
    local = sandbox.app / "newsbot" / "local_operator_note.py"
    local.write_text("keep me\n", encoding="utf-8")

    r = sandbox.deploy(sha)

    assert r.returncode != 0
    assert "untracked" in r.stderr, r.stderr
    assert local.read_text(encoding="utf-8") == "keep me\n"
    assert _git(sandbox.app, "rev-parse", "HEAD").strip() == sandbox.head0
    assert sandbox.restart_count() == 0


def test_staged_change_stops_deploy_and_is_preserved(sandbox):
    """تغییر staged نیز باید قبل از reset متوقف شود، حتی اگر working tree پاک باشد."""
    sha = sandbox.commit_push("feature.txt", "v2\n")
    tracked = sandbox.app / "newsbot" / "db.py"
    tracked.write_text(tracked.read_text(encoding="utf-8") + "\n# staged hotfix\n",
                       encoding="utf-8")
    _git(sandbox.app, "add", "newsbot/db.py")

    r = sandbox.deploy(sha)

    assert r.returncode != 0
    assert "uncommitted changes" in r.stderr, r.stderr
    assert "# staged hotfix" in tracked.read_text(encoding="utf-8")
    staged = _run(["git", "-C", str(sandbox.app), "diff", "--cached", "--quiet"],
                  cwd=sandbox.app, check=False)
    assert staged.returncode != 0
    assert _git(sandbox.app, "rev-parse", "HEAD").strip() == sandbox.head0
    assert sandbox.restart_count() == 0


def test_untracked_path_conflicting_with_target_is_not_overwritten(sandbox):
    """فایل ignoredِ هم‌مسیر commit مقصد نیز باید حفظ شود، نه اینکه بازنویسی شود."""
    collision = sandbox.app / "newsbot" / "collision.log"
    collision.write_text("local operator data\n", encoding="utf-8")
    sha = sandbox.commit_push("collision.log", "target content\n", subdir="newsbot", force=True)
    collision.write_text("local operator data\n", encoding="utf-8")

    r = sandbox.deploy(sha)

    assert r.returncode != 0
    assert "conflicts with local untracked files" in r.stderr, r.stderr
    assert collision.read_text(encoding="utf-8") == "local operator data\n"
    assert _git(sandbox.app, "rev-parse", "HEAD").strip() == sandbox.head0
    assert sandbox.restart_count() == 0


def test_fetch_failure_preserves_head_and_does_not_restart(sandbox):
    """شکست fetch باید قبل از هر تغییر کد/سرویس متوقف شود."""
    sha = sandbox.commit_push("feature.txt", "v2\n")
    _git(sandbox.app, "remote", "set-url", "origin", str(sandbox.tmp / "missing-origin.git"))

    r = sandbox.deploy(sha)

    assert r.returncode != 0
    assert "git fetch origin main failed" in r.stderr, r.stderr
    assert _git(sandbox.app, "rev-parse", "HEAD").strip() == sandbox.head0
    assert sandbox.restart_count() == 0


def test_reset_failure_preserves_head_and_does_not_restart(sandbox):
    """شکست reset باید خطای روشن بدهد و سرویس را لمس نکند."""
    sha = sandbox.commit_push("feature.txt", "v2\n")
    (sandbox.app / ".git" / "index.lock").write_text("locked\n", encoding="utf-8")

    r = sandbox.deploy(sha)

    assert r.returncode != 0
    assert "git reset to" in r.stderr, r.stderr
    assert _git(sandbox.app, "rev-parse", "HEAD").strip() == sandbox.head0
    assert sandbox.restart_count() == 0


def test_rollback_subcommand_stops_on_dirty_working_tree(sandbox):
    """rollback دستی هم نباید تغییرات محلیِ uncommitted را نابود کند."""
    sha = sandbox.commit_push("feature.txt", "v2\n")
    sandbox.deploy(sha, check=True)
    sandbox.stub_log.write_text("")
    tracked = sandbox.app / "newsbot" / "db.py"
    tracked.write_text(tracked.read_text(encoding="utf-8") + "\n# local rollback guard\n",
                       encoding="utf-8")

    r = sandbox.rollback()

    assert r.returncode != 0
    assert "refusing rollback" in r.stderr, r.stderr
    assert "# local rollback guard" in tracked.read_text(encoding="utf-8")
    assert _git(sandbox.app, "rev-parse", "HEAD").strip() == sha
    assert sandbox.restart_count() == 0


def test_rollback_subcommand_does_not_overwrite_ignored_file(sandbox):
    """rollback نیز فایل ignoredِ هم‌مسیر SHA مقصد را نباید بازنویسی کند."""
    collision = sandbox.app / "collision.log"
    collision.write_text("tracked in previous release\n", encoding="utf-8")
    env = dict(sandbox.env)
    env.update({"GIT_AUTHOR_NAME": "sandbox", "GIT_AUTHOR_EMAIL": "sb@t",
                "GIT_COMMITTER_NAME": "sandbox", "GIT_COMMITTER_EMAIL": "sb@t"})
    _git(sandbox.app, "add", "-f", "collision.log", env=env)
    _git(sandbox.app, "commit", "-q", "-m", "sandbox: tracked collision", env=env)
    previous = _git(sandbox.app, "rev-parse", "HEAD").strip()
    _git(sandbox.app, "reset", "-q", "--hard", sandbox.head0, env=env)

    sha = sandbox.commit_push("feature.txt", "v2\n")
    sandbox.deploy(sha, check=True)
    sandbox.stub_log.write_text("")
    (sandbox.app / ".deployed_sha.prev").write_text(f"{previous}\n", encoding="utf-8")
    collision.write_text("local operator data\n", encoding="utf-8")

    r = sandbox.rollback()

    assert r.returncode != 0
    assert "conflicts with local untracked files" in r.stderr, r.stderr
    assert collision.read_text(encoding="utf-8") == "local operator data\n"
    assert _git(sandbox.app, "rev-parse", "HEAD").strip() == sha
    assert sandbox.restart_count() == 0


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


# ── ISS-04 / OPS-001 — یکسانیِ هویت سرویس میان اسکریپت، مستندات و لاگر ──────────
LEGACY_IDENTITY = "bot" + "-ai-" + "automatic"
IDENTITY_DOCS = ("README.md", "DEPLOY-VPS-UPDATE.md", "docs/DEPLOY_RUNBOOK.md")
SYSTEMD_DROPINS = ("deploy/systemd/10-restart-always.conf", "deploy/systemd/20-watchdog.conf")


def _deploy_defaults():
    text = DEPLOY_SH.read_text(encoding="utf-8")
    app_dir = re.search(r'^APP_DIR="\$\{APP_DIR:-([^}]+)\}"', text, re.M)
    service = re.search(r'^SERVICE="\$\{SERVICE:-([^}]+)\}"', text, re.M)
    assert app_dir, "deploy.sh باید APP_DIR را با مقدار پیش‌فرض تعریف کند"
    assert service, "deploy.sh باید SERVICE را با مقدار پیش‌فرض تعریف کند"
    return app_dir.group(1), service.group(1)


def _unit_name(service):
    return service[: -len(".service")] if service.endswith(".service") else service


def test_deploy_defaults_match_documented_service_identity():
    """پیش‌فرض‌های deploy.sh باید همان مسیر و نام سرویسی باشد که مستندات می‌گویند.

    سناریوی sandbox متغیرهای APP_DIR/SERVICE را override می‌کند، پس بدون این تست
    یک ناسازگاری میان کد و مستندات بی‌سروصدا از چشم ۱۴ سناریوی deploy پنهان می‌ماند.
    """
    app_dir, service = _deploy_defaults()
    unit = _unit_name(service)
    assert app_dir == "/opt/bot-official-telegram"
    assert unit == "newsbot"
    for doc in IDENTITY_DOCS:
        text = (ROOT / doc).read_text(encoding="utf-8")
        assert app_dir in text, f"{doc} باید مسیر نصب {app_dir} را ذکر کند"
        assert re.search(r"(?:systemctl|journalctl)\b[^\n]*\bnewsbot(?:\.service)?\b", text), \
            f"{doc} باید سرویس {unit} را در فرمان‌های systemd صدا بزند"


def test_legacy_service_identity_is_absent_from_deployment_surface():
    """هویت سرویس پیشین نباید در اسکریپت استقرار، drop-inها یا مستندات باقی بماند."""
    targets = list(IDENTITY_DOCS) + list(SYSTEMD_DROPINS) + ["deploy/deploy.sh"]
    targets += [p.relative_to(ROOT).as_posix() for p in sorted((ROOT / "docs").glob("*.md"))]
    for rel in sorted(set(targets)):
        path = ROOT / rel
        if not path.exists():
            continue
        assert LEGACY_IDENTITY not in path.read_text(encoding="utf-8"), rel


def test_systemd_logger_tag_matches_service_identity():
    """تگ لاگر ExecStopPost باید همان نام سرویسی باشد که اسکریپت restart می‌کند."""
    _, service = _deploy_defaults()
    tag = _unit_name(service)
    conf = (ROOT / "deploy/systemd/10-restart-always.conf").read_text(encoding="utf-8")
    assert f"logger -t {tag} " in conf, "تگ لاگر drop-in با نام سرویس هم‌راستا نیست"
    runbook = (ROOT / "docs/DEPLOY_RUNBOOK.md").read_text(encoding="utf-8")
    assert f"logger -t {tag} " in runbook, "تگ لاگر در runbook با نام سرویس هم‌راستا نیست"


# ── مسیر دیتابیس و بکاپ ────────────────────────────────────────────────────────
def _write_env(sandbox, **values):
    lines = [
        "BOT_TOKEN=123456789:AAsandbox_dummy_token_0000000000000",
        "SUPER_ADMIN_IDS=1",
    ]
    lines += [f"{key}={value}" for key, value in values.items()]
    (sandbox.app / ".env").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _make_db(path, marker="deploy-backup-probe"):
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY)")
        conn.execute("CREATE TABLE deploy_marker (v TEXT)")
        conn.execute("INSERT INTO deploy_marker VALUES (?)", (marker,))
        conn.commit()
    finally:
        conn.close()


def _marker(path):
    conn = sqlite3.connect(str(path))
    try:
        row = conn.execute("SELECT v FROM deploy_marker").fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def _integrity(path):
    conn = sqlite3.connect(str(path))
    try:
        return conn.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        conn.close()


def test_backup_uses_db_file_from_env_absolute_path(sandbox):
    """مسیر دیتابیس باید از .env خوانده شود — همان‌طور که خود برنامه می‌خواند.

    هاستِ واقعی DB_FILE=/opt/bot-official-telegram/data/newsbot.db دارد؛ اگر deploy.sh
    مسیر دیگری را حدس بزند، بکاپ بی‌صدا رد می‌شود و داده در معرض خطر می‌ماند.
    """
    sha = sandbox.commit_push("feature.txt", "v2\n")
    db = sandbox.app / "data" / "newsbot.db"
    _make_db(db)
    _write_env(sandbox, DB_BACKEND="sqlite", DB_FILE=str(db).replace("\\", "/"))
    (sandbox.app / "newsbot.db").unlink(missing_ok=True)

    sandbox.deploy(sha, extra={"DB_FILE": str(db)}, check=True)

    backups = list(sandbox.backups.glob("newsbot-*.db"))
    assert len(backups) == 1, backups
    assert _marker(backups[0]) == "deploy-backup-probe", "بکاپ باید از فایلِ تنظیم‌شده گرفته شود"
    assert not (sandbox.app / "newsbot.db").exists(), "نباید مسیرِ حدسی ساخته یا استفاده شود"


def test_backup_resolves_relative_db_file_against_app_dir(sandbox):
    """DB_FILE نسبی ⇒ نسبت به WorkingDirectory سرویس (ریشهٔ APP_DIR) تفسیر می‌شود."""
    sha = sandbox.commit_push("feature.txt", "v2\n")
    db = sandbox.app / "data" / "newsbot.db"
    _make_db(db)
    _write_env(sandbox, DB_BACKEND="sqlite", DB_FILE="data/newsbot.db")
    (sandbox.app / "newsbot.db").unlink(missing_ok=True)

    sandbox.deploy(sha, extra={"DB_FILE": "data/newsbot.db"}, check=True)

    backups = list(sandbox.backups.glob("newsbot-*.db"))
    assert len(backups) == 1, backups
    assert _marker(backups[0]) == "deploy-backup-probe"


def test_missing_database_aborts_deploy_without_backup(sandbox):
    """نبودِ دیتابیس ⇒ توقف صریح؛ بکاپ هرگز بی‌صدا رد نمی‌شود و کد دست‌نخورده می‌ماند."""
    sha = sandbox.commit_push("feature.txt", "v2\n")
    _write_env(sandbox, DB_BACKEND="sqlite", DB_FILE="data/newsbot.db")
    (sandbox.app / "newsbot.db").unlink(missing_ok=True)

    r = sandbox.deploy(sha, extra={"DB_FILE": "data/newsbot.db"})

    assert r.returncode != 0, "نبودِ دیتابیس باید deploy را متوقف کند"
    assert "database file not found" in r.stderr, r.stderr
    assert "refusing to deploy without a database backup" in r.stderr, r.stderr
    assert list(sandbox.backups.glob("newsbot-*.db")) == [], "نباید فایل بکاپ ساخته شود"
    assert _git(sandbox.app, "rev-parse", "HEAD").strip() == sandbox.head0
    assert not (sandbox.app / ".deployed_sha.prev").exists(), "نباید هیچ اثرِ جانبی بماند"
    assert sandbox.restart_count() == 0


def test_db_file_mismatch_reports_the_other_database_files(sandbox):
    """اگر دیتابیسِ واقعی جای دیگری باشد، مسیرِ ناسازگار باید صریح گزارش شود."""
    sha = sandbox.commit_push("feature.txt", "v2\n")
    _make_db(sandbox.app / "data" / "newsbot.db")
    _write_env(sandbox, DB_BACKEND="sqlite", DB_FILE="data/live.db")
    (sandbox.app / "newsbot.db").unlink(missing_ok=True)

    r = sandbox.deploy(sha, extra={"DB_FILE": "data/live.db"})

    assert r.returncode != 0
    assert "database file not found" in r.stderr, r.stderr
    assert "other database files exist" in r.stderr, r.stderr
    assert "data/newsbot.db" in r.stderr.replace("\\", "/"), r.stderr
    assert list(sandbox.backups.glob("newsbot-*.db")) == []
    assert _git(sandbox.app, "rev-parse", "HEAD").strip() == sandbox.head0
    assert sandbox.restart_count() == 0


def test_d1_backend_skips_backup_explicitly(sandbox):
    """با بک‌اند D1 فایل محلی وجود ندارد؛ رد شدنِ بکاپ باید صریح و قابل‌تشخیص باشد."""
    sha = sandbox.commit_push("feature.txt", "v2\n")
    _write_env(sandbox, DB_BACKEND="d1", DB_FILE="data/newsbot.db",
               CF_ACCOUNT_ID="acct", CF_D1_DATABASE_ID="dbid", CF_D1_TOKEN="token")
    (sandbox.app / "newsbot.db").unlink(missing_ok=True)

    r = sandbox.deploy(sha, extra={"DB_BACKEND": "d1"}, check=True)

    assert "backend=d1" in r.stdout, r.stdout
    assert list(sandbox.backups.glob("newsbot-*.db")) == []


def test_backup_is_a_verified_copy_and_leaves_the_source_untouched(sandbox):
    """بکاپ باید سالم و کامل باشد و دیتابیسِ مبدأ نباید تغییری کند."""
    sha = sandbox.commit_push("feature.txt", "v2\n")
    db = sandbox.app / "data" / "newsbot.db"
    _make_db(db)
    _write_env(sandbox, DB_BACKEND="sqlite", DB_FILE="data/newsbot.db")
    (sandbox.app / "newsbot.db").unlink(missing_ok=True)
    before = db.read_bytes()

    sandbox.deploy(sha, extra={"DB_FILE": "data/newsbot.db"}, check=True)

    backups = list(sandbox.backups.glob("newsbot-*.db"))
    assert len(backups) == 1, backups
    assert _integrity(backups[0]) == "ok", "بکاپ باید از نظر SQLite سالم باشد"
    assert _marker(backups[0]) == "deploy-backup-probe"
    assert db.read_bytes() == before, "دیتابیسِ مبدأ نباید تغییر کند"
    assert not (db.parent / (db.name + "-wal")).exists(), "نباید فایل جانبی ساخته شود"
    assert not (db.parent / (db.name + "-shm")).exists(), "نباید فایل جانبی ساخته شود"


def test_gate_validates_the_database_from_env_not_the_process_environment(sandbox):
    """دروازه باید همان دیتابیسی را بسنجد که سرویس می‌خواند (DB_FILE در .env).

    اگر DB_FILE تصادفی در env فرایند deploy باقی بماند، load_dotenv آن را override
    نمی‌کند و دروازه دیتابیسِ دیگری را بررسی می‌کند — یعنی گیتِ پیش از restart
    سرویسِ واقعی را تأیید نمی‌کند.
    """
    sha = sandbox.commit_push("feature.txt", "v2\n")
    db = sandbox.app / "data" / "newsbot.db"
    _make_db(db)
    _write_env(sandbox, DB_BACKEND="sqlite", DB_FILE="data/newsbot.db")
    stale = sandbox.app / "stale.db"
    sqlite3.connect(str(stale)).close()          # دیتابیسِ بدون جدول users
    (sandbox.app / "newsbot.db").unlink(missing_ok=True)

    r = sandbox.deploy(sha, extra={"DB_FILE": str(stale)}, check=True)

    assert "deploy complete" in r.stdout, r.stdout
    backups = list(sandbox.backups.glob("newsbot-*.db"))
    assert len(backups) == 1, backups
    assert _marker(backups[0]) == "deploy-backup-probe"


# ── مسیر Python و محیط مجازی ───────────────────────────────────────────────────
def test_deploy_works_with_venv_layout_without_dot(sandbox):
    """هاستِ واقعی venv/ دارد (بدون نقطه) — استقرار باید با همان layout کار کند."""
    sha = sandbox.commit_push("feature.txt", "v2\n")
    shutil.move(str(sandbox.app / ".venv"), str(sandbox.app / "venv"))

    r = sandbox.deploy(sha, check=True)

    assert "deploy complete" in r.stdout, r.stdout
    assert sandbox.restart_count() == 1


def test_ambiguous_venv_layout_is_rejected(sandbox):
    """دو محیط مجازی ⇒ خطای صریح؛ انتخاب با VENV_DIR انجام می‌شود (نه حدس)."""
    sha = sandbox.commit_push("feature.txt", "v2\n")
    shutil.copytree(str(sandbox.app / ".venv"), str(sandbox.app / "venv"))

    r = sandbox.deploy(sha)

    assert r.returncode != 0
    assert "ambiguous virtualenv" in r.stderr, r.stderr
    assert "VENV_DIR" in r.stderr, r.stderr
    assert sandbox.restart_count() == 0

    r2 = sandbox.deploy(sha, extra={"VENV_DIR": "venv"}, check=True)
    assert "deploy complete" in r2.stdout, r2.stdout


def test_missing_venv_is_rejected_with_a_clear_error(sandbox):
    """نبودِ محیط مجازی ⇒ خطای روشن به‌جای شکست مبهم در مرحلهٔ بعد."""
    sha = sandbox.commit_push("feature.txt", "v2\n")
    shutil.rmtree(str(sandbox.app / ".venv"))

    r = sandbox.deploy(sha)

    assert r.returncode != 0
    assert "no virtualenv found" in r.stderr, r.stderr
    assert "VENV_DIR" in r.stderr, r.stderr
    assert _git(sandbox.app, "rev-parse", "HEAD").strip() == sandbox.head0
    assert sandbox.restart_count() == 0


def test_venv_dir_override_selects_the_requested_layout(sandbox):
    """VENV_DIR باید واقعاً همان محیط را انتخاب کند — اثرش قابل‌اثبات است."""
    sha = sandbox.commit_push("feature.txt", "v2\n")
    shutil.copytree(str(sandbox.app / ".venv"), str(sandbox.app / "venv"))
    failing_pip = sandbox.app / "venv" / "bin" / "pip"
    failing_pip.write_text(
        '#!/bin/sh\necho "pip $*" >> "$(dirname "$0")/../../pip.log"\nexit 1\n',
        encoding="utf-8",
    )
    os.chmod(str(failing_pip), 0o755)

    r = sandbox.deploy(sha, extra={"VENV_DIR": "venv"})

    assert r.returncode != 0
    assert "dependency install failed" in r.stderr, r.stderr
    assert (sandbox.app / "pip.log").exists(), "pip باید از محیطِ انتخاب‌شده اجرا شود"

    r2 = sandbox.deploy(sha, extra={"VENV_DIR": ".venv"}, check=True)
    assert "deploy complete" in r2.stdout, r2.stdout
