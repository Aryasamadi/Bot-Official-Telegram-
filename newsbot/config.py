# -*- coding: utf-8 -*-
"""تمام متغیرهای محیطی و ثابت‌های سراسری.
این ماژول هیچ وابستگی داخلی ندارد (اولین ماژولی که لود می‌شود)."""
import os
from contextvars import ContextVar
from datetime import timezone
from dotenv import load_dotenv
load_dotenv()

# ── هویت و دسترسی ──
BOT_TOKEN = os.getenv("BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN", "")
SUPER_ADMIN_IDS = {int(x) for x in os.getenv("SUPER_ADMIN_IDS", "").split(",") if x.strip().isdigit() and int(x) > 0}
BOT_USERNAME = ""                      # در post_init از تلگرام خوانده می‌شود
NOTIFY_SUPER = None                    # async fn(text, kb=None) — در post_init ست می‌شود
NOTIFY_USER = None                     # async fn(uid, text, kb=None)

# ── دیتابیس ──
DB_FILE = os.getenv("DB_FILE", "newsbot.db")
DB_BACKEND = os.getenv("DB_BACKEND", "sqlite").strip().lower()      # sqlite | d1
CF_D1_DATABASE_ID = os.getenv("CF_D1_DATABASE_ID", "").strip()
CF_D1_TOKEN = os.getenv("CF_D1_TOKEN", "").strip() or os.getenv("CF_API_TOKEN", "").strip()
D1_TIMEOUT = float(os.getenv("D1_TIMEOUT", "30"))

# ── نگهداری داده ──
DATA_TTL_HOURS = int(os.getenv("DATA_TTL_HOURS", "72"))
DEDUP_TTL_HOURS = int(os.getenv("DEDUP_TTL_HOURS", "72"))
DEEPLINK_TTL_DAYS = int(os.getenv("DEEPLINK_TTL_DAYS", "30"))
DEEPLINK_KEEP_MIN = 5000

# ── تلگرام ──
API_BASE = os.getenv("TELEGRAM_API_BASE", "").strip()
MEDIA_UPLOAD_MB = 2000 if API_BASE else 50
MAX_MEDIA_MB = max(1, min(int(os.getenv("MAX_MEDIA_MB", "50")), MEDIA_UPLOAD_MB))
MAX_MEDIA_BYTES = MAX_MEDIA_MB * 1024 * 1024
MAX_PAGE_BYTES = int(os.getenv("MAX_PAGE_MB", "8")) * 1024 * 1024
CAPTION_LIMIT, MSG_LIMIT = 1024, 4096
RETRY_AFTER_MAX = int(os.getenv("RETRY_AFTER_MAX", "120"))

# ── محتوا ──
MAX_ARTICLE_CHARS = int(os.getenv("MAX_ARTICLE_CHARS", "120000"))
MAX_ARTICLE_PAGES = int(os.getenv("MAX_ARTICLE_PAGES", "5"))
BOT_FULL_MAX = 20000
BOT_FULL_HARD_MAX = int(os.getenv("BOT_FULL_HARD_MAX", "60000"))
POST_LIMIT_DEFAULT = 900
POST_LIMIT_MAX = 900

# ── هوش مصنوعی ──
AI_INPUT_TEXT_CHARS = int(os.getenv("AI_INPUT_TEXT_CHARS", "8000"))
AI_MERGE_MAX_DEPTH = int(os.getenv("AI_MERGE_MAX_DEPTH", "3"))
AI_MAX_CALLS_PER_ARTICLE = int(os.getenv("AI_MAX_CALLS_PER_ARTICLE", "12"))
AI_ARTICLE_TIME_BUDGET = int(os.getenv("AI_ARTICLE_TIME_BUDGET", "240"))
AI_MAX_MODEL_ATTEMPTS = int(os.getenv("AI_MAX_MODEL_ATTEMPTS", "6"))
AI_CONTENT_RETRY_MODELS = int(os.getenv("AI_CONTENT_RETRY_MODELS", "1"))
AI_CACHE_TTL_DAYS = int(os.getenv("AI_CACHE_TTL_DAYS", "14"))
AI_DAILY_FREE = int(os.getenv("AI_DAILY_FREE", "20"))     # BIZ-01: سقف روزانهٔ AI کاربر بدون پلن/پلن رایگان
AI_DAILY_PAID = int(os.getenv("AI_DAILY_PAID", "200"))    # BIZ-01: سقف روزانهٔ AI کاربر پولی (ابرادمین نامحدود)
PROMPT_VERSION = "p5-1"
MODEL_BACKOFF_MAX_MIN = int(os.getenv("MODEL_BACKOFF_MAX_MIN", "120"))
MODEL_PROBE_MIN = 10
MODEL_PROBE_BATCH = int(os.getenv("MODEL_PROBE_BATCH", "3"))

# ── Cloudflare KV (آینه‌ی اختیاری «بیشتر») ──
CF_ACCOUNT_ID = os.getenv("CF_ACCOUNT_ID", "")
CF_KV_NAMESPACE_ID = os.getenv("CF_KV_NAMESPACE_ID") or os.getenv("CF_KV_ID", "")
CF_API_TOKEN = os.getenv("CF_API_TOKEN", "")
CF_ENABLED = bool(CF_ACCOUNT_ID and CF_KV_NAMESPACE_ID and CF_API_TOKEN)

# ── ارجاع (Referral) ──
REFERRAL_ENABLED = os.getenv("REFERRAL_ENABLED", "1").strip() == "1"
REFERRAL_PERCENT = max(0.0, min(50.0, float(os.getenv("REFERRAL_PERCENT", "10"))))
# سقف تعداد اعتبارهای پرداخت‌محور برای هر معرف (core_referrals). قبلاً این متغیر در config نبود
# و .env.example آن را مستند می‌کرد ⇒ هر مسیر اعتبار ارجاع با AttributeError می‌ترکید.
REFERRAL_MAX_PER_USER = max(0, int(os.getenv("REFERRAL_MAX_PER_USER", "50")))

# ── کانال اجباری (فقط برای ادمین‌ها) ──
MANDATORY_CHANNEL_ID = os.getenv("MANDATORY_CHANNEL_ID", "").strip()
MANDATORY_CHANNEL_USERNAME = os.getenv("MANDATORY_CHANNEL_USERNAME", "").strip()

# ── زمان ──
DEFAULT_UTC_OFFSET = float(os.getenv("DEFAULT_UTC_OFFSET", "3.5"))
UTC = timezone.utc
LANGS = ("fa", "en")

# ── منبع و مدل ──
SOURCE_COOLDOWN_MIN = 10
SOURCE_FAIL_LIMIT = 3
SOURCE_PAUSE_MIN = int(os.getenv("SOURCE_PAUSE_MIN", "30"))
MAX_DAILY_POSTS_CAP = int(os.getenv("MAX_DAILY_POSTS_CAP", "500"))
MIN_INTERVAL = int(os.getenv("MIN_INTERVAL", "30"))
MAX_LOOKBACK = 168
MAX_PPC = 5

# ── هم‌زمانی و زمان‌بندی ──
AI_CONCURRENCY = int(os.getenv("AI_CONCURRENCY", "3"))
FETCH_CONCURRENCY = int(os.getenv("FETCH_CONCURRENCY", "8"))
CYCLE_CONCURRENCY = int(os.getenv("CYCLE_CONCURRENCY", "2"))
MAX_CYCLES_PER_TICK = int(os.getenv("MAX_CYCLES_PER_TICK", "4"))
RECOVER_EVERY_MIN = int(os.getenv("RECOVER_EVERY_MIN", "5"))
STALE_UPDATE_MAX_MIN = int(os.getenv("STALE_UPDATE_MAX_MIN", "60"))
TICK_SECONDS = int(os.getenv("TICK_SECONDS", "120"))
TEST_COOLDOWN_SEC = int(os.getenv("TEST_COOLDOWN_SEC", "120"))
CB_RATE = float(os.getenv("CB_RATE", "0.6"))
ABUSE_RATE_SEC = float(os.getenv("ABUSE_RATE_SEC", "1.0"))
# SEC-03 (فاز ۳): محدودیت نرخ سراسری per-user — سقف به‌روزرسانی از هر نوع (دستور/کال‌بک/پیام) در پنجرهٔ ثانیه
GLOBAL_RATE_MAX = int(os.getenv("GLOBAL_RATE_MAX", "40"))
GLOBAL_RATE_WINDOW = float(os.getenv("GLOBAL_RATE_WINDOW", "10"))
MAX_QUEUE_SIZE = int(os.getenv("MAX_QUEUE_SIZE", "500"))     # backpressure صف ready هر کانال
PAY_STUCK_MIN = int(os.getenv("PAY_STUCK_MIN", "10"))        # بازیابی پرداخت گیرکرده در processing

# ── ماندگاری محدودیت نرخ حساس ──
RATE_PERSIST_PREFIXES = ("test:", "req:", "rcpt:", "tok:", "cmd:", "sup:", "srcadd:", "srcr:", "dl:", "disc:", "mtest:")

# UISEC-06 (فاز ۶): مهلت state فرم‌های ورودی — پس از این مدت state منقضی و ورودی بعدی رد می‌شود
AWAIT_TTL_SEC = int(os.getenv("AWAIT_TTL_SEC", "1800"))

# ── ابزارهای مشترک ──
_FA_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")

# ── ContextVarها (ایزوله per-task / per-cycle) ──
SCHED_NOTIF_FAILURES = ContextVar("sched_notif_failures", default=False)
AI_BUDGET = ContextVar("ai_budget", default=None)   # FIX v4: بودجه‌ی AI دیگر global نیست
