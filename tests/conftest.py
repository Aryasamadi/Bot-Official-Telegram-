# -*- coding: utf-8 -*-
"""محیط تست: دیتابیس موقت SQLite، توکن جعلی با قالب معتبر، بدون هیچ تماس شبکه‌ای."""
import os, sys, tempfile
import pytest

os.environ.setdefault("BOT_TOKEN", "123456789:AAtest_only_dummy_token_0000000000000")
os.environ.setdefault("SUPER_ADMIN_IDS", "1")
os.environ.setdefault("DB_BACKEND", "sqlite")
os.environ.setdefault("REFERRAL_ENABLED", "1")
_tmp = tempfile.mkdtemp(prefix="newsbot-tests-")
os.environ["DB_FILE"] = os.path.join(_tmp, "test.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture(scope="session", autouse=True)
def _core():
    from newsbot.app import init_core
    init_core()          # ساخت اسکیمای v2 + seed پلن‌ها + بازیابی ژورنال (همه آفلاین)
    yield
