# -*- coding: utf-8 -*-
"""محیط تست: دیتابیس موقت SQLite، توکن جعلی با قالب معتبر، بدون هیچ تماس شبکه‌ای."""
import atexit, os, shutil, sys, tempfile
import pytest

os.environ.setdefault("BOT_TOKEN", "123456789:AAtest_only_dummy_token_0000000000000")
os.environ.setdefault("SUPER_ADMIN_IDS", "1")
os.environ.setdefault("DB_BACKEND", "sqlite")
os.environ.setdefault("REFERRAL_ENABLED", "1")
_tmp = tempfile.mkdtemp(prefix="newsbot-tests-")
os.environ["DB_FILE"] = os.path.join(_tmp, "test.db")
# DEVOPS-08: نشانگرهای تخلیه هم باید داخلِ پوشهٔ موقتِ تست باشند؛ وگرنه یک
# نشانگرِ بازمانده در /tmp (مثلاً از یک استقرارِ واقعی یا از یک تستِ دیگر) کلِ
# چرخه‌های انتشار را در مجموعهٔ تست «آفلاین» می‌کرد.
os.environ["DRAIN_FLAG_FILE"] = os.path.join(_tmp, "drain.flag")
os.environ["DRAINED_FLAG_FILE"] = os.path.join(_tmp, "drained.flag")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _cleanup_test_dir():
    """TEST-013: بستنِ اتصالاتِ SQLite و پاک‌سازیِ پوشهٔ موقتِ جلسهٔ تست."""
    try:
        from newsbot import db
        conn = getattr(getattr(db, "_tls", None), "conn", None)
        if conn is not None:
            conn.close()
            db._tls.conn = None
    except Exception:
        pass
    shutil.rmtree(_tmp, ignore_errors=True)


atexit.register(_cleanup_test_dir)


@pytest.fixture(scope="session", autouse=True)
def _core():
    from newsbot.app import init_core
    init_core()          # ساخت اسکیمای v7 + seed پلن‌ها + بازیابی ژورنال (همه آفلاین)
    yield
    _cleanup_test_dir()


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    """TEST-013: دیتابیسِ کاملاً ایزوله برای تست‌هایی که نباید به ردیف‌های قبلی وابسته باشند."""
    from newsbot import config, db
    from newsbot.app import init_core
    old_conn = getattr(db._tls, "conn", None)
    old_ready = getattr(db, "_SCHEMA_READY", False)
    db_file = str(tmp_path / "isolated.db")
    monkeypatch.setattr(config, "DB_FILE", db_file)
    db._tls.conn = None
    db._SCHEMA_READY = False
    init_core()
    try:
        yield db_file
    finally:
        c = getattr(db._tls, "conn", None)
        if c is not None:
            try:
                c.close()
            except Exception:
                pass
        db._tls.conn = old_conn
        db._SCHEMA_READY = old_ready
