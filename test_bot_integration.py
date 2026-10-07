# -*- coding: utf-8 -*-
"""bot.py را با stub کردن کتابخانه‌های شبکه/تلگرام import می‌کند تا منطق واقعی دیتابیس ربات (q، assign_plan، reserve_publication…)
روی همین sandbox اجرا شود. شبکه و تلگرام واقعی در کار نیست."""
import importlib, json, os, shutil, sqlite3, sys, tempfile, types, unittest
from unittest.mock import MagicMock
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE); sys.path.insert(0, ROOT); sys.path.insert(0, HERE)
from fake_d1 import FakeD1
from test_d1sync import dump


def _install_stubs():
    for name in ("httpx", "feedparser", "trafilatura", "bs4", "dotenv", "telegram", "telegram.constants", "telegram.ext"):
        sys.modules.setdefault(name, MagicMock(name=name))
    sys.modules["dotenv"].load_dotenv = lambda *a, **k: None
    err = types.ModuleType("telegram.error")
    for n in ("BadRequest", "RetryAfter", "NetworkError", "Forbidden", "TimedOut", "TelegramError", "Conflict", "InvalidToken", "ChatMigrated"):
        setattr(err, n, type(n, (Exception,), {}))
    sys.modules["telegram.error"] = err


class TestBotWithD1(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(); cls.fake = FakeD1(os.path.join(cls.tmp, "d1.db"))
        os.environ.update(BOT_TOKEN="123:TEST", SUPER_ADMIN_IDS="111", DB_FILE=os.path.join(cls.tmp, "vps1.db"), CF_ACCOUNT_ID="acc",
                          CF_D1_DATABASE_ID="db", CF_D1_API_TOKEN="tok-SECRET", D1_API_BASE=cls.fake.base, D1_SYNC_INTERVAL="0.2",
                          D1_LEASE_TTL="6", D1_LEASE_WAIT="3", D1_BOOT_WAIT="3", D1_INSTANCE_ID="vps1")
        _install_stubs(); cls.bot = importlib.import_module("bot")

    @classmethod
    def tearDownClass(cls):
        cls.fake.close(); shutil.rmtree(cls.tmp, ignore_errors=True)

    def _reset_local(self, path, instance):
        b = self.bot; b._d1_stop()
        c = getattr(b._tls, "conn", None)
        if c is not None: c.close()
        b._tls.conn = None; b._SCHEMA_READY = False; b.DB_FILE = path; b._limits_cache.clear(); b._COLS.clear()
        os.environ["D1_INSTANCE_ID"] = instance

    def test_real_bot_data_survives_vps_replacement(self):
        b = self.bot
        b.init_core()                                                      # VPS#1: دیتابیس خالی + D1 خالی ⇒ fresh
        self.assertEqual(b.D1.mode, "fresh")
        b.ensure_user(111, "root", "Root"); b.ensure_user(222, "sara", "سارا")
        pid = [p for p in b.list_plans() if not p["is_free"]][0]["id"]
        b.assign_plan(222, pid)                                            # مسیر واقعی پلن/سهمیه
        cid = b.add_channel(222, -1001234567890, "کانال", "chan", 222)
        b.add_source(222, cid, "https://example.com/feed")
        a1 = b.article_insert(222, cid, "h1", "https://example.com/a", "خبر ۱", None, None, status="ready")
        a2 = b.article_insert(222, cid, "h2", "https://example.com/b", "خبر ۲", None, None, status="ready")
        j1 = b.reserve_publication(a1, 222, True, None, None)              # رزرو واقعی سهمیه + ژورنال (هنوز چیزی ارسال نشده)
        j2 = b.reserve_publication(a2, 222, True, None, None)
        self.assertIsNotNone(j1); self.assertIsNotNone(j2)
        b.set_pub_journal(a2, dict(j2, phase="in_flight"))                 # دقیقاً لحظهٔ ارسال به تلگرام (مثل PublicationTransport.send)
        self.assertTrue(b.d1_barrier("in_flight"))                         # پیش از ارسال، D1 این فاز را تأیید کرده
        local = sqlite3.connect(b.DB_FILE); want = dump(local); local.close()
        r = sqlite3.connect(os.path.join(self.tmp, "d1.db")); self.assertEqual(want, dump(r)); r.close()

        # --- VPS#1 ناگهان می‌میرد (پیام ممکن است به تلگرام رسیده باشد)؛ VPS#2 با دیتابیس خالی ---
        b.D1._hb_stop.set(); b.D1.rep.stop(2.0); b.D1.release(); b.D1 = None   # مرگ پروسه: همهٔ تردهایش هم می‌میرند
        self._reset_local(os.path.join(self.tmp, "vps2.db"), "vps2")
        b.db(); b._d1_boot()
        self.assertEqual(b.D1.mode, "restore")
        local2 = sqlite3.connect(b.DB_FILE); self.assertEqual(want, dump(local2)); local2.close()   # بازسازی کامل و بی‌کم‌وکاست
        b.seed_plans(); b.recover_publications()                           # همان ترتیب init_core
        self.assertEqual(len(b.list_plans()), len(want["plans"]))          # seed_plans روی دیتابیس بازسازی‌شده پلن تکراری نساخت
        # a1 (prepared): هیچ ارسالی نشده ⇒ رزرو آزاد می‌شود
        self.assertIsNone(b.get_pub_journal(a1)); self.assertEqual(b.get_article(a1)["status"], "ready")
        # a2 (in_flight): تحویل نامعلوم ⇒ هرگز خودکار دوباره ارسال نمی‌شود (جلوگیری از انتشار تکراری)، سهمیه نگه داشته می‌شود
        self.assertEqual(b.get_pub_journal(a2)["phase"], "unsettled")
        self.assertEqual((b.get_article(a2)["status"], b.get_article(a2)["reason"]), ("failed", "delivery_unknown"))
        self.assertEqual(b.usage_today(222)["posts"], 1)
        self.assertTrue(b.d1_barrier("after-recover"))                     # نتیجهٔ recovery هم به D1 رفت
        b._d1_stop()

    def test_check_mode_does_not_touch_d1(self):
        b = self.bot; self._reset_local(os.path.join(self.tmp, "chk.db"), "chk"); before = self.fake.requests
        b.init_core(d1=False); self.assertIsNone(b.D1); self.assertEqual(self.fake.requests, before)

    def test_missing_d1_config_refuses_to_start_when_required(self):
        b = self.bot; self._reset_local(os.path.join(self.tmp, "nocfg.db"), "nocfg")
        saved = {k: os.environ.pop(k) for k in ("CF_ACCOUNT_ID", "CF_D1_DATABASE_ID", "CF_D1_API_TOKEN")}
        try:
            with self.assertRaises(SystemExit): b.init_core()
        finally: os.environ.update(saved)


if __name__ == "__main__":
    unittest.main()
