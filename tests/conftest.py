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
# DEVOPS-08: نشانگرهای تخلیه هم باید داخلِ پوشهٔ موقتِ تست باشند؛ وگرنه یک
# نشانگرِ بازمانده در /tmp (مثلاً از یک استقرارِ واقعی یا از یک تستِ دیگر) کلِ
# چرخه‌های انتشار را در مجموعهٔ تست «آفلاین» می‌کرد.
os.environ["DRAIN_FLAG_FILE"] = os.path.join(_tmp, "drain.flag")
os.environ["DRAINED_FLAG_FILE"] = os.path.join(_tmp, "drained.flag")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture(scope="session", autouse=True)
def _core():
    from newsbot.app import init_core
    init_core()          # ساخت اسکیمای v7 + seed پلن‌ها + بازیابی ژورنال (همه آفلاین)
    yield
