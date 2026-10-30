# -*- coding: utf-8 -*-
"""PH-04 — ایمنیِ مسیرِ استقرار (بدونِ تماس با سرور).

پوشش: OPS-04 (اسکریپت از TARGET_SHA) · OPS-05 (مسیرِ دستی نیازمند CI سبز) ·
OPS-06 (rollback وابستگی‌ها را هم برمی‌گرداند) · OPS-08 (EnvironmentFile اختیاری هشدار می‌دهد) ·
OPS-09 (بکاپ تأیید می‌شود) · OPS-10 (پوشهٔ آرشیو هم بکاپ می‌شود) · OPS-13 (‎--dry-run).

این تست‌ها هیچ سرویسی را صدا نمی‌زنند و هیچ فایلِ خارج از پوشهٔ موقتِ خودشان را نمی‌نویسند.
"""
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEPLOY_SH = ROOT / "deploy" / "deploy.sh"
DEPLOY_YML = ROOT / ".github" / "workflows" / "deploy-vps.yml"
MIGRATE = ROOT / "tools" / "migrate_sqlite_to_d1.py"
RESTORE = ROOT / "tools" / "restore_d1_to_sqlite.py"


def _src(path):
    return path.read_text(encoding="utf-8")


# ── قراردادِ اسکریپتِ استقرار ──────────────────────────────────────────────────────
def test_health_budget_matches_the_unit_startup_timeout():
    """OPS-02 — بودجهٔ سلامت نباید از TimeoutStartSec (۱۸۰ ثانیه) کمتر باشد."""
    src = _src(DEPLOY_SH)
    m = re.search(r'HEALTH_TRIES="\$\{HEALTH_TRIES:-(\d+)\}"', src)
    assert m, "HEALTH_TRIES باید یک پیش‌فرضِ صریح داشته باشد"
    assert int(m.group(1)) >= 180, "بودجهٔ سلامت باید با TimeoutStartSec (۱۸۰) هم‌خوان باشد"


def test_health_waits_for_ready_on_type_notify():
    """OPS-02 — با Type=notify، تا READY=1 (SubState=running) باید منتظر ماند."""
    src = _src(DEPLOY_SH)
    assert 'Type' in src and 'SubState' in src
    assert 'notify' in src and 'running' in src


def test_journal_window_is_anchored_to_the_restart():
    """OPS-03 — پنجرهٔ ژورنال باید به لحظهٔ همین restart لنگر باشد، نه «۱۲۰ ثانیهٔ گذشته»."""
    src = _src(DEPLOY_SH)
    assert "RESTART_EPOCH" in src
    assert 'journalctl -u "$SERVICE" --since "@$since"' in src
    # کلمهٔ «traceback» نباید معیارِ خرابی باشد (برنامه برای استثناهایِ مدیریت‌شده هم آن را می‌نویسد)
    journal_block = src.split("journal_failed_since()", 1)[-1].split("\n}", 1)[0]
    assert "traceback" not in journal_block, "«traceback» نباید نشانهٔ خرابی باشد"
    assert "main process exited" in journal_block, "نشانهٔ مرگبارِ واقعی باید بررسی شود"


def test_failed_restart_triggers_the_rollback_path():
    """OPS-26 — شکستِ restart باید از مسیرِ rollback بگذرد، نه exitِ خام."""
    src = _src(DEPLOY_SH)
    assert "start_service || rollback_and_die" in src
    assert 'systemctl restart "$SERVICE" || die' not in src, "restart نباید مستقیم die کند"


def test_rollback_restores_the_dependencies_too():
    """OPS-06 — rollback باید وابستگی‌ها را هم به نسخهٔ قبلی برگرداند."""
    src = _src(DEPLOY_SH)
    block = src.split("rollback_and_die()", 1)[-1].split("\n}", 1)[0]
    assert "install_deps" in block, "rollback باید دوباره وابستگی‌هایِ نسخهٔ قبلی را نصب کند"


def test_backup_is_verified():
    """OPS-09 — بکاپ باید باز و با quick_check تأیید شود."""
    src = _src(DEPLOY_SH)
    assert "quick_check" in src
    assert "backup verification failed" in src


def test_archive_folder_is_backed_up():
    """OPS-10 — پوشهٔ آرشیوِ مقالاتِ قدیمی هم باید در بکاپ باشد."""
    src = _src(DEPLOY_SH)
    assert '"$(dirname "$db")/backups"' in src, "پوشهٔ آرشیوِ کنارِ دیتابیس باید کپی شود"
    assert '"$BACKUP_DIR/archive"' in src, "آرشیو باید در مجموعهٔ بکاپ برود"


def test_optional_environment_file_is_reported():
    """OPS-08 — EnvironmentFile اختیاری باید هشدار بدهد (حلقهٔ ری‌استارتِ بی‌پایان)."""
    src = _src(DEPLOY_SH)
    assert "EnvironmentFile=-" in src
    assert "OPTIONAL EnvironmentFile" in src


# ── قراردادِ workflow ───────────────────────────────────────────────────────────────
def test_deploy_script_comes_from_the_target_sha():
    """OPS-04 — checkout باید روی همان SHA باشد."""
    src = _src(DEPLOY_YML)
    assert "ref: ${{ env.TARGET_SHA }}" in src, "checkout باید به TARGET_SHA سنجاق باشد"


def test_manual_dispatch_requires_a_sha_and_a_green_ci_run():
    """OPS-05 — مسیرِ دستی باید SHA بگیرد و CIِ سبزِ همان SHA را بخواهد."""
    src = _src(DEPLOY_YML)
    assert "workflow_dispatch:" in src
    assert "inputs:" in src and "sha:" in src
    assert "Verify the target SHA has a successful CI run" in src
    assert "inputs.sha" in src
    assert "conclusion" in src and "!= \"success\"" in src


def test_deploy_fails_fast_when_the_vps_port_is_unreachable():
    """۲۰ اکتبر ۲۰۲۶: دیوارِ آتشِ سرویس‌دهنده همهٔ ترافیکِ ورودی را دور می‌ریخت و
    خطا فقط به شکلِ timeoutِ مبهم در مرحلهٔ scp دیده می‌شد. یک پیش‌شرطِ صریح لازم است."""
    src = _src(DEPLOY_YML)
    assert "the VPS must accept TCP on the SSH port" in src
    assert "/dev/tcp/$VPS_HOST/$VPS_PORT" in src, "باید پیش از scp پورتِ SSH سنجیده شود"
    assert "::error::" in src, "خطای نامفهوم ممنوع؛ پیام باید صریح باشد"
    # آدرسِ VPS نباید چاپ شود (secret است)
    assert 'echo "$VPS_HOST"' not in src and "echo $VPS_HOST" not in src


def test_ssh_port_is_configurable_without_editing_the_workflow():
    """پورتِ SSH باید از secret بیاید تا تغییرِ پورت نیازی به ویرایشِ کد نداشته باشد."""
    src = _src(DEPLOY_YML)
    assert "secrets.VPS_SSH_PORT" in src
    assert src.count("port: ${{ env.VPS_PORT }}") == 2, "هر دو اکشن (scp و ssh) باید پورت را از env بگیرند"
    assert "port: 22" not in src, "پورتِ ثابتِ ۲۲ دیگر نباید hardcode شود"


def test_pending_manual_approval_does_not_lock_the_deploy_queue():
    """قفلِ هم‌زمانی باید در سطحِ job باشد.

    پیش‌تر قفل در سطحِ workflow بود ⇒ یک اجرای معلقِ «در انتظارِ تأییدِ دستی»
    همهٔ استقرارهای بعدی را قفل می‌کرد (و یک اجرای معلقِ ۲۹ اکتبر، استقرارهای
    ۳۰ اکتبر را هم متوقف نگه داشت).
    """
    src = _src(DEPLOY_YML)
    head, _, jobs = src.partition("\njobs:")
    assert "concurrency:" not in head, "قفل نباید در سطحِ workflow باشد"
    assert "concurrency:" in jobs and "production-vps-deploy" in jobs
    # قفل باید درونِ تعریفِ job باشد (نه در سطحِ بالاتر)
    job_block = jobs.split("\n  deploy:", 1)[1]
    assert "concurrency:" in job_block


# ── ابزارهای مهاجرت: پیش‌نمایش بدونِ نوشتن ─────────────────────────────────────────
def _run(args, env=None):
    e = dict(os.environ)
    e.update(env or {})
    return subprocess.run([sys.executable, *args], capture_output=True, text=True, env=e, cwd=str(ROOT))


def test_restore_dry_run_writes_no_file(tmp_path):
    """OPS-13 — ‎--dry-run نباید فایلی بسازد."""
    out = tmp_path / "should-not-exist.db"
    r = _run([str(RESTORE), str(out), "--dry-run"],
             env={"CF_ACCOUNT_ID": "acct", "CF_D1_DATABASE_ID": "dbid", "CF_D1_TOKEN": "tok"})
    assert not out.exists(), "حالتِ پیش‌نمایش نباید فایلی بنویسد"
    assert "--dry-run" in (r.stdout + r.stderr)


def test_migrate_dry_run_writes_no_target(tmp_path):
    """OPS-13 — ‎--dry-run نباید به مقصدِ D1 چیزی بنویسد (و شبکه هم صدا نمی‌زند)."""
    src_db = tmp_path / "src.db"
    import sqlite3
    con = sqlite3.connect(str(src_db))
    con.execute("CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, name TEXT)")
    con.execute("INSERT INTO users(id,name) VALUES(1,'x')")
    con.commit(); con.close()
    r = _run([str(MIGRATE), "--dry-run"],
             env={"CF_ACCOUNT_ID": "acct", "CF_D1_DATABASE_ID": "dbid", "CF_D1_TOKEN": "tok",
                   "DB_FILE": str(src_db)})
    out = r.stdout + r.stderr
    assert "پیش‌نمایش" in out or "dry-run" in out, "باید حالتِ پیش‌نمایش را اعلام کند"
    assert "HTTP" not in out, "حالتِ پیش‌نمایش نباید درخواستِ شبکه بفرستد"
