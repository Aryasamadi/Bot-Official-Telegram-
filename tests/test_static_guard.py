# -*- coding: utf-8 -*-
"""نگهبان ایستا: نبودِ «نام تعریف‌نشده» در کل کدبیس.

ریشهٔ اصلی خرابیِ خودکارسازی این بود که چند تابع/متغیر در مسیرهای داغ استفاده می‌شدند
ولی هرگز import نشده بودند (remaining/in_quiet/update_settings/list_channels/set_source_api/
log/disc_use). خطا فقط در زمان اجرا و آن هم پشتِ except دیده می‌شد.

این تست با pyflakes کل درخت را می‌پاید؛ اگر دوباره نامی بدون تعریف استفاده شود، CI می‌شکند.
تست «تست‌نشده = قبول‌نشده» بند ۶ پرامپت: این کلاس خطا اکنون پوشش دائمی دارد.
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TARGETS = ["newsbot", "tools", "run.py"]


def _run_pyflakes():
    try:
        from pyflakes.api import checkRecursive
        from pyflakes.reporter import Reporter
    except ImportError:  # pragma: no cover
        return None
    import io
    out, err = io.StringIO(), io.StringIO()
    n = checkRecursive([os.path.join(ROOT, t) for t in TARGETS], Reporter(out, err))
    return (out.getvalue() + err.getvalue()), n


def test_no_undefined_names_in_codebase():
    """هیچ ``undefined name`` در کل کدبیس نباید وجود داشته باشد (خطای زمان اجرا)."""
    res = _run_pyflakes()
    if res is None:
        import pytest
        pytest.skip("pyflakes نصب نیست")
    output, _ = res
    undefined = [ln for ln in output.splitlines() if "undefined name" in ln]
    assert not undefined, "نام‌های تعریف‌نشده پیدا شد (باید import شوند):\n" + "\n".join(undefined)


def test_all_modules_importable():
    """هر ماژول newsbot باید بدون خطا import شود (کشف خطاهای import سطح‌بالا)."""
    env = dict(os.environ)
    env.setdefault("BOT_TOKEN", "123456789:AAtest_only_dummy_token_0000000000000")
    env.setdefault("SUPER_ADMIN_IDS", "1")
    env.setdefault("DB_BACKEND", "sqlite")
    import tempfile
    env["DB_FILE"] = os.path.join(tempfile.mkdtemp(prefix="imp-"), "t.db")
    code = (
        "import importlib, pkgutil, sys;"
        "import newsbot;"
        "bad=[];\n"
        "for m in pkgutil.iter_modules(newsbot.__path__):\n"
        "    try: importlib.import_module('newsbot.'+m.name)\n"
        "    except Exception as e: bad.append(m.name+': '+type(e).__name__+' '+str(e))\n"
        "print('\\n'.join(bad))"
    )
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=ROOT, env=env)
    assert r.returncode == 0, f"اجرای import ناموفق: {r.stderr[-500:]}"
    assert not r.stdout.strip(), "ماژول‌های غیرقابل‌import:\n" + r.stdout.strip()


# ── H1: نگهبانِ .gitignore (جلوگیری از commit شدن رازها) ──

def _git_available():
    import shutil
    return shutil.which("git") is not None


def test_gitignore_blocks_secrets_and_artifacts():
    """فایل .gitignore باید وجود داشته باشد و .env و دیتابیس را مسکند.

    ریشهٔ H1: هیچ .gitignore وجود نداشت، در حالی که newsbot.db کلیدهای API را
    متن ساده در خود دارد (app.py آن را chmod 600 می‌کند). یک `git add -A` ساده
    می‌توانست رازها را به مخزن بفرستد.
    """
    import pytest
    gi = os.path.join(ROOT, ".gitignore")
    assert os.path.isfile(gi), ".gitignore وجود ندارد (بازگشت H1)"

    if not _git_available():
        pytest.skip("git در دسترس نیست")

    def ignored(path):
        r = subprocess.run(["git", "check-ignore", "-q", path], cwd=ROOT, capture_output=True, text=True)
        return r.returncode == 0

    for must in (".env", "newsbot.db", "newsbot.db-wal", "__pycache__/x.pyc", ".venv/x"):
        assert ignored(must), f"«{must}» باید در .gitignore باشد ولی نیست (خطر نشت راز)"

    # .env.example باید قابل‌ردیابی بماند (مستندسازی پیکربندی)
    assert not ignored(".env.example"), ".env.example نباید ignore شود"


def test_no_sensitive_files_are_git_tracked():
    """هیچ راز/دیتابیس/محیط‌مجازی نباید در ایندکس گیت باشد."""
    import pytest
    if not _git_available():
        pytest.skip("git در دسترس نیست")
    r = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True)
    if r.returncode != 0:
        pytest.skip("git در دسترس نیست")
    tracked = r.stdout.splitlines()
    import re as _re
    bad = [f for f in tracked if _re.search(r"(^|/)\.env$|\.db(-wal|-shm)?$|__pycache__|\.pyc$|\.venv/", f)]
    assert not bad, "فایل حساس در گیت ردیابی می‌شود: " + ", ".join(bad)
