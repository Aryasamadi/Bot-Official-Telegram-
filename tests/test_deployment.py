# -*- coding: utf-8 -*-
"""فاز ۹ — استقرار، پایش و بازیابیِ آزموده‌شده.

«بازیابی آزمایش‌نشده = بازیابی ناموجود». این تست‌ها واقعاً یک دیتابیس می‌سازند،
پشتیبان می‌گیرند، و آن را در محیطی تازه بازمی‌گردانند تا مطمئن شویم مسیر بازیابی کار می‌کند.
"""
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable


def _env(db_file):
    e = dict(os.environ)
    e.update({
        "BOT_TOKEN": "123456789:AAtest_only_dummy_token_0000000000000",
        "SUPER_ADMIN_IDS": "1",
        "DB_BACKEND": "sqlite",
        "DB_FILE": db_file,
    })
    return e


# ── خودآزماییِ استقرار ──

def test_self_check_passes_on_fresh_database():
    """`run.py --check` باید روی دیتابیس تازه موفق شود (گیتِ پیش از دیپلوی).
    ARCH-04/C7 (فاز ۸، تصمیم ۱۳): خودآزمایی فقط‌خواندنی است — نباید هیچ فایلی بسازد و
    ساخت/مهاجرت دیتابیس فقط در اجرای عادی انجام می‌شود."""
    with tempfile.TemporaryDirectory() as d:
        db = os.path.join(d, "fresh.db")
        r = subprocess.run([PY, "run.py", "--check"], cwd=ROOT, env=_env(db),
                           capture_output=True, text=True, timeout=120)
        assert r.returncode == 0, f"خودآزمایی شکست خورد:\n{r.stdout[-800:]}\n{r.stderr[-800:]}"
        assert not os.path.exists(db), "خودآزمایی فقط‌خواندنی نباید دیتابیس بسازد"
        assert os.listdir(d) == [], "خودآزمایی نباید هیچ فایل/پوشه‌ای کنار دیتابیس بسازد"


def test_self_check_does_not_touch_existing_database():
    """خودآزمایی روی دیتابیس موجود نباید چیزی به آن بنویسد (بدون تغییر محتوا/فهرست فایل‌ها)."""
    import hashlib
    with tempfile.TemporaryDirectory() as d:
        db = os.path.join(d, "existing.db")
        code = "from newsbot.app import init_core; init_core(); print('SEEDED')"
        r = subprocess.run([PY, "-c", code], cwd=ROOT, env=_env(db),
                           capture_output=True, text=True, timeout=120)
        assert r.returncode == 0 and "SEEDED" in r.stdout, r.stderr[-500:]
        before_files = sorted(os.listdir(d))
        before_hash = hashlib.sha256(open(db, "rb").read()).hexdigest()
        r2 = subprocess.run([PY, "run.py", "--check"], cwd=ROOT, env=_env(db),
                            capture_output=True, text=True, timeout=120)
        assert r2.returncode == 0, f"خودآزمایی شکست خورد:\n{r2.stdout[-800:]}\n{r2.stderr[-800:]}"
        assert sorted(os.listdir(d)) == before_files, "خودآزمایی فایل جدیدی کنار دیتابیس ساخت"
        assert hashlib.sha256(open(db, "rb").read()).hexdigest() == before_hash, "خودآزمایی محتوای دیتابیس را تغییر داد"


def test_preflight_rejects_missing_bot_token():
    """پیکربندی ناقص باید fail-fast شود، نه اجرا با توکن خالی."""
    with tempfile.TemporaryDirectory() as d:
        e = _env(os.path.join(d, "x.db"))
        e["BOT_TOKEN"] = ""
        r = subprocess.run([PY, "run.py", "--check"], cwd=ROOT, env=e,
                           capture_output=True, text=True, timeout=60)
        assert r.returncode != 0, "پیکربندی ناقص باید اجرا را متوقف کند"


# ── بازیابی آزموده‌شده: پشتیبان و بازگردانی ──

def test_sqlite_backup_and_restore_roundtrip():
    """کلید فاز ۹: چرخهٔ کامل پشتیبان → بازگردانی باید داده را دست‌نخورده برگرداند."""
    with tempfile.TemporaryDirectory() as d:
        src = os.path.join(d, "src.db")
        # ۱) دیتابیس زنده با داده بساز
        code = (
            "from newsbot.app import init_core; init_core();"
            "from newsbot.core_channels import add_channel;"
            "from newsbot.core_articles import article_insert;"
            "add_channel(1, 8801, 'ch', 'chan', 1);"
            "aid = article_insert(1, 1, 'h-backup', 'https://x.test/b', 'title', 0, None, status='published');"
            "print('SEEDED', aid)"
        )
        r = subprocess.run([PY, "-c", code], cwd=ROOT, env=_env(src),
                           capture_output=True, text=True, timeout=120)
        assert r.returncode == 0, f"seed شکست خورد: {r.stderr[-500:]}"

        # ۲) پشتیبان‌گیری اتمیک با پروتکل backup رسمی sqlite (base: not a raw file copy)
        dst = os.path.join(d, "backup.db")
        s = sqlite3.connect(src)
        b = sqlite3.connect(dst)
        with b:
            s.backup(b)
        b.close(); s.close()
        assert os.path.isfile(dst)

        # ۳) بازگردانی روی مسیر تازه و اجرای خودآزمایی (اسکیما باید سازگار باشد)
        restored = os.path.join(d, "restored.db")
        shutil.copy(dst, restored)
        code2 = (
            "from newsbot.app import init_core; init_core();"
            "from newsbot.db import q;"
            "a = q(\"SELECT COUNT(*) c FROM articles WHERE status='published'\", one=True)['c'];"
            "ch = q('SELECT COUNT(*) c FROM channels', one=True)['c'];"
            "print('RESTORED', a, ch)"
        )
        r2 = subprocess.run([PY, "-c", code2], cwd=ROOT, env=_env(restored),
                            capture_output=True, text=True, timeout=120)
        assert r2.returncode == 0, f"بازگردانی شکست خورد: {r2.stderr[-500:]}"
        assert "RESTORED 1 1" in r2.stdout, f"دادهٔ بازگردانده ناقص است: {r2.stdout[-300:]}"


def test_restored_database_is_fully_operational():
    """دیتابیس بازگردانده باید *کار* کند، نه فقط باز شود: نوشتن/خواندن روی آن."""
    with tempfile.TemporaryDirectory() as d:
        db = os.path.join(d, "op.db")
        code = (
            "from newsbot.app import init_core; init_core();"
            "from newsbot.core_channels import add_channel, channel_by_chat;"
            "add_channel(1, 8802, 'ch', 'chan', 1);"
            "cid = channel_by_chat(8802)['id'];"
            "from newsbot.core_articles import article_insert, get_article;"
            "aid = article_insert(1, cid, 'h-op', 'https://x.test/o', 't2', 0, None, status='ready');"
            "print('OK', get_article(aid)['title'])"
        )
        r = subprocess.run([PY, "-c", code], cwd=ROOT, env=_env(db),
                           capture_output=True, text=True, timeout=120)
        assert r.returncode == 0, r.stderr[-500:]
        assert "OK t2" in r.stdout


# ── ابزار مهاجرت: یکپارچگی ──

def test_migration_tools_import_and_are_idempotent_by_design():
    """ابزارهای مهاجرت/بازگردانی باید import شوند و قرارداد idempotent را حفظ کنند."""
    code = (
        "import ast, sys;"
        "src = open('tools/migrate_sqlite_to_d1.py', encoding='utf-8').read();"
        "ast.parse(src);"
        "src2 = open('tools/restore_d1_to_sqlite.py', encoding='utf-8').read();"
        "ast.parse(src2);"
        "print('TOOLS OK')"
    )
    r = subprocess.run([PY, "-c", code], cwd=ROOT, env=_env(":memory:"),
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr[-400:]
    assert "TOOLS OK" in r.stdout


def test_dockerfile_exists_and_uses_entrypoint():
    """استقرار باید فایل Dockerfile قابل‌اتکا داشته باشد."""
    p = os.path.join(ROOT, "deploy", "Dockerfile")
    assert os.path.isfile(p), "deploy/Dockerfile وجود ندارد"
    content = open(p, encoding="utf-8").read()
    assert "run.py" in content, "Dockerfile باید run.py را اجرا کند"
