# -*- coding: utf-8 -*-
"""تمام متغیرهای محیطی و ثابت‌های سراسری.
این ماژول هیچ وابستگی داخلی ندارد (اولین ماژولی که لود می‌شود).

فاز ۱۲ (CFG-01/02/03/09):
  • env عددی نامعتبر دیگر import را crash نمی‌کند — پیش‌فرض + ثبت در CONFIG_ERRORS
    (preflight این فهرست را به‌صورت خطای قابل‌اقدام و پیش از راه‌اندازی نشان می‌دهد).
  • SUPER_ADMIN_IDS توکن نامعتبر را بی‌صدا حذف نمی‌کند؛ در CONFIG_ERRORS ثبت می‌شود.
  • مسیر .env صریح و نسبت به ریشهٔ پروژه است (رفتار مستقل از CWD).
  • کلیدهای state زنده (BOT_USERNAME/NOTIFY_*) به newsbot/runtime.py منتقل شدند (ARCH-05).
"""
import os
from pathlib import Path
from contextvars import ContextVar
from datetime import timezone
from dotenv import load_dotenv

# envهای نامعتبر import را متوقف نمی‌کنند؛ این‌جا جمع می‌شوند تا preflight یک‌جا نشان دهد
CONFIG_ERRORS = []


def _env_raw(name):
    raw = os.getenv(name)
    return None if raw is None or not raw.strip() else raw.strip()


def _clamp(v, lo, hi):
    if lo is not None and v < lo: v = lo
    if hi is not None and v > hi: v = hi
    return v


def _env_int(name, default, lo=None, hi=None):
    raw = _env_raw(name)
    if raw is None:
        return default
    try:
        v = int(raw)
    except (TypeError, ValueError):
        CONFIG_ERRORS.append(f"{name}='{raw}' عدد صحیح نیست ⇒ پیش‌فرض {default} استفاده شد")
        return default
    fixed = _clamp(v, lo, hi)
    if fixed != v:
        CONFIG_ERRORS.append(f"{name}={v} بیرون از بازهٔ مجاز ⇒ به {fixed} محدود شد")
    return fixed


def _env_float(name, default, lo=None, hi=None):
    raw = _env_raw(name)
    if raw is None:
        return default
    try:
        v = float(raw)
    except (TypeError, ValueError):
        CONFIG_ERRORS.append(f"{name}='{raw}' عدد نیست ⇒ پیش‌فرض {default} استفاده شد")
        return default
    fixed = _clamp(v, lo, hi)
    if fixed != v:
        CONFIG_ERRORS.append(f"{name}={v} بیرون از بازهٔ مجاز ⇒ به {fixed} محدود شد")
    return fixed


# CFG-09: مسیر صریح نسبت به ریشهٔ پروژه (newsbot/..) — رفتار وابسته به CWD حذف شد
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

# ── هویت و دسترسی ──
BOT_TOKEN = os.getenv("BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN", "")
_su_bad, SUPER_ADMIN_IDS = [], set()
for _tok in os.getenv("SUPER_ADMIN_IDS", "").split(","):
    _tok = _tok.strip()
    if not _tok:
        continue
    if _tok.isdigit() and int(_tok) > 0:
        SUPER_ADMIN_IDS.add(int(_tok))
    else:
        _su_bad.append(_tok)
if _su_bad:
    CONFIG_ERRORS.append("SUPER_ADMIN_IDS: ورودی نامعتبر نادیده گرفته شد: " + ", ".join(_su_bad))
# ARCH-05 (فاز ۱۲): BOT_USERNAME و NOTIFY_SUPER/NOTIFY_USER در newsbot/runtime.py هستند
# (state زندهٔ زمان‌اجرا نباید داخل ماژول پیکربندی env بنشیند).

# ── دیتابیس ──
DB_FILE = os.getenv("DB_FILE", "newsbot.db")
DB_BACKEND = os.getenv("DB_BACKEND", "sqlite").strip().lower()      # sqlite | d1
CF_D1_DATABASE_ID = os.getenv("CF_D1_DATABASE_ID", "").strip()
CF_D1_TOKEN = os.getenv("CF_D1_TOKEN", "").strip() or os.getenv("CF_API_TOKEN", "").strip()
D1_TIMEOUT = _env_float("D1_TIMEOUT", 30.0, 1.0, 600.0)

# ── نگهداری داده ──
DATA_TTL_HOURS = _env_int("DATA_TTL_HOURS", 72)
DEDUP_TTL_HOURS = _env_int("DEDUP_TTL_HOURS", 72)
DEEPLINK_TTL_DAYS = _env_int("DEEPLINK_TTL_DAYS", 30)
DEEPLINK_KEEP_MIN = 5000
# DB-23 / تصمیم ۱۴ (۱۷ اکتبر ۲۰۲۶): آرشیو مقالات قدیمی‌تر از این آستانه — همراه بکاپ پیش از حذف
ARCHIVE_DAYS = max(30, _env_int("ARCHIVE_DAYS", 365, 30, 3650))
ARCHIVE_BACKUP_KEEP = 2      # تعداد فایل‌های بکاپ آرشیو که نگه داشته می‌شود

# ── تلگرام ──
API_BASE = os.getenv("TELEGRAM_API_BASE", "").strip()
MEDIA_UPLOAD_MB = 2000 if API_BASE else 50
MAX_MEDIA_MB = max(1, min(_env_int("MAX_MEDIA_MB", 50, 1), MEDIA_UPLOAD_MB))
MAX_MEDIA_BYTES = MAX_MEDIA_MB * 1024 * 1024
MAX_PAGE_BYTES = _env_int("MAX_PAGE_MB", 8, 1) * 1024 * 1024
CAPTION_LIMIT, MSG_LIMIT = 1024, 4096
RETRY_AFTER_MAX = _env_int("RETRY_AFTER_MAX", 120, 5, 600)
# ARCH-02 (فاز ۷): سقفِ کلِ یک درخواست fetch/دانلود رسانه — httpx فقط per-read timeout دارد
FETCH_TOTAL_SEC = max(5, _env_int("FETCH_TOTAL_SEC", 60, 5, 3600))
MEDIA_TOTAL_SEC = max(10, _env_int("MEDIA_TOTAL_SEC", 600, 10, 7200))
# PLB-10 (فاز ۷): سقف احترام به Retry-After در retryهای کشف خوراک
FEED_RETRY_AFTER_MAX = max(1, _env_int("FEED_RETRY_AFTER_MAX", 15, 1, 300))

# ── محتوا ──
MAX_ARTICLE_CHARS = _env_int("MAX_ARTICLE_CHARS", 120000)
MAX_ARTICLE_PAGES = _env_int("MAX_ARTICLE_PAGES", 5)
BOT_FULL_MAX = 20000
BOT_FULL_HARD_MAX = _env_int("BOT_FULL_HARD_MAX", 60000)
POST_LIMIT_DEFAULT = 900
POST_LIMIT_MAX = 900

# ── هوش مصنوعی ──
AI_INPUT_TEXT_CHARS = _env_int("AI_INPUT_TEXT_CHARS", 8000)
AI_MERGE_MAX_DEPTH = _env_int("AI_MERGE_MAX_DEPTH", 3)
AI_MAX_CALLS_PER_ARTICLE = _env_int("AI_MAX_CALLS_PER_ARTICLE", 12)
AI_ARTICLE_TIME_BUDGET = _env_int("AI_ARTICLE_TIME_BUDGET", 240)
AI_MAX_MODEL_ATTEMPTS = _env_int("AI_MAX_MODEL_ATTEMPTS", 6)
AI_CONTENT_RETRY_MODELS = _env_int("AI_CONTENT_RETRY_MODELS", 1)
AI_CACHE_TTL_DAYS = _env_int("AI_CACHE_TTL_DAYS", 14)
AI_DAILY_FREE = _env_int("AI_DAILY_FREE", 20)     # BIZ-01: سقف روزانهٔ AI کاربر بدون پلن/پلن رایگان
AI_DAILY_PAID = _env_int("AI_DAILY_PAID", 200)    # BIZ-01: سقف روزانهٔ AI کاربر پولی (ابرادمین نامحدود)
PROMPT_VERSION = "p5-1"
MODEL_BACKOFF_MAX_MIN = _env_int("MODEL_BACKOFF_MAX_MIN", 120)
MODEL_PROBE_MIN = 10
MODEL_PROBE_BATCH = _env_int("MODEL_PROBE_BATCH", 3)
# ── P0: پایداریِ مسیرِ تولید با AI ──────────────────────────────────────────────
# سقفِ زمانیِ کلِ یک درخواستِ پروایدر (بودجهٔ fetchِ منابع نباید آن را قطع کند:
# پیش‌تر FETCH_TOTAL_SEC=60 روی درخواستِ AI هم اعمال می‌شد ⇒ خطای 504ِ کاذب)
AI_PROVIDER_TIMEOUT_SEC = _env_int("AI_PROVIDER_TIMEOUT_SEC", 180, 10, 600)
# تلاشِ دوباره برای خطاهای گذرا (۴۲۹/۵xx/timeout) — خطاهای دائمی هرگز تکرار نمی‌شوند
AI_TRANSIENT_RETRIES = _env_int("AI_TRANSIENT_RETRIES", 2, 0, 5)
AI_RETRY_BASE_SEC = _env_float("AI_RETRY_BASE_SEC", 2.0, 0.1, 60.0)
# خاموشیِ کوتاهِ مدل بعد از خطای گذرا (پیش‌تر ۲۰ دقیقه ⇒ کلِ تولیدِ محتوا متوقف می‌شد)
MODEL_TRANSIENT_BACKOFF_MIN = _env_int("MODEL_TRANSIENT_BACKOFF_MIN", 2, 1, 60)
MODEL_TRANSIENT_BACKOFF_MAX_MIN = _env_int("MODEL_TRANSIENT_BACKOFF_MAX_MIN", 15, 1, 120)

# ── Cloudflare KV (آینه‌ی اختیاری «بیشتر») ──
CF_ACCOUNT_ID = os.getenv("CF_ACCOUNT_ID", "")
CF_KV_NAMESPACE_ID = os.getenv("CF_KV_NAMESPACE_ID") or os.getenv("CF_KV_ID", "")
CF_API_TOKEN = os.getenv("CF_API_TOKEN", "")
CF_ENABLED = bool(CF_ACCOUNT_ID and CF_KV_NAMESPACE_ID and CF_API_TOKEN)

# ── ارجاع (Referral) ──
REFERRAL_ENABLED = os.getenv("REFERRAL_ENABLED", "1").strip() == "1"
# CFG-03 (فاز ۱۲): اعتبارسنجی زنده — مقدار خارج از بازه همین‌جا کلمپ و در CONFIG_ERRORS ثبت می‌شود
REFERRAL_PERCENT = _env_float("REFERRAL_PERCENT", 20.0, 0.0, 50.0)
# REF-3/تصمیم 8 (فاز ۱۲): سقف «تعداد» منسوخ شد — پاداش با هر خرید تعلق می‌گیرد و سقف اختیاریِ
# مبلغی از پنل سوپرادمین (تنظیم ref_cap_amount؛ ۰ = نامحدود) اعمال می‌شود.
# PH-01 (Q-3a): این متغیر حذف شد — سقفِ «تعداد» هرگز اجرا نمی‌شد و فقط یک تصورِ نادرست می‌ساخت.
# سقفِ واقعی فقط مبلغی است و **به تفکیکِ واحدِ پول** مقایسه می‌شود.

# ── امنیت (SEC-09 / تصمیم ۱۰-الف، فاز ۱۲) ──
# کلید رمزنگاری کلیدهای AI در حالت سکون؛ هر رشتهٔ دلخواه (با SHA-256 به کلید Fernet تبدیل می‌شود).
# خالی ⇒ بدون رمزنگاری (سازگاری با نصب‌های موجود؛ استارت هشدار می‌دهد اگر کلید متن ساده وجود داشته باشد).
SECRETS_KEY = os.getenv("SECRETS_KEY", "").strip()
# فقط برای پرووایدرهای AI محلی (Ollama/LM Studio): اجازهٔ مقصد خصوصی برای base_url مدل
# فقط برای پروایدرهای AI محلی (Ollama/LM Studio): فهرستِ «میزبان:پورت»هایی که اجازهٔ
# دسترسی به میزبانِ خصوصی دارند — جداشده با کاما. خالی (پیش‌فرض) = هیچ میزبانِ خصوصی
# مجاز نیست. «all» یعنی همهٔ میزبان‌های خصوصی (پذیرشِ آگاهانهٔ ریسک).
# PH-02/Q-5 (SEC-007): پیش‌تر یک پرچمِ boolean بود که کلِ محافظتِ میزبانِ خصوصی را برای
# همهٔ مدیرها برمی‌داشت؛ حالا فهرستِ سفیدِ دقیق است.
ALLOW_PRIVATE_MODEL_HOSTS = os.getenv("ALLOW_PRIVATE_MODEL_HOSTS", "").strip()

# ── کانال اجباری (فقط برای ادمین‌ها) ──
MANDATORY_CHANNEL_ID = os.getenv("MANDATORY_CHANNEL_ID", "").strip()
MANDATORY_CHANNEL_USERNAME = os.getenv("MANDATORY_CHANNEL_USERNAME", "").strip()

# ── زمان ──
DEFAULT_UTC_OFFSET = _env_float("DEFAULT_UTC_OFFSET", 3.5, -12.0, 14.0)
# ARCH-10 (فاز ۷ · تصمیم ۱۱): منطقه‌ی پیش‌فرض با نام IANA؛ خالی ⇒ از DEFAULT_UTC_OFFSET استخراج می‌شود
DEFAULT_TZ = os.getenv("DEFAULT_TZ", "").strip()
UTC = timezone.utc
LANGS = ("fa", "en")

# ── منبع و مدل ──
SOURCE_COOLDOWN_MIN = 10
SOURCE_FAIL_LIMIT = 3
SOURCE_PAUSE_MIN = _env_int("SOURCE_PAUSE_MIN", 30)
MAX_DAILY_POSTS_CAP = _env_int("MAX_DAILY_POSTS_CAP", 500)
MIN_INTERVAL = _env_int("MIN_INTERVAL", 30)
MAX_LOOKBACK = 168
MAX_PPC = 5

# ── هم‌زمانی و زمان‌بندی ──
# PH-01/Q-2a: آستانهٔ «اختلافِ مؤثر» بین مبلغِ یخ‌زده (آنچه به مشتری نشان داده شد) و مبلغِ
# بازمحاسبه‌شده با کیف پولِ زنده. درصدی است تا برای هر دو واحدِ پول یکسان معنی داشته باشد.
# ۰ = سخت‌گیریِ کامل: هر اختلافی تأیید را متوقف می‌کند.
PAY_QUOTE_TOLERANCE_PCT = _env_float("PAY_QUOTE_TOLERANCE_PCT", 0.0, 0.0, 100.0)
AI_CONCURRENCY = _env_int("AI_CONCURRENCY", 3)
FETCH_CONCURRENCY = _env_int("FETCH_CONCURRENCY", 8)
CYCLE_CONCURRENCY = _env_int("CYCLE_CONCURRENCY", 2)
MAX_CYCLES_PER_TICK = _env_int("MAX_CYCLES_PER_TICK", 4)
# PH-07/PERF-003: normal DB work and long maintenance/backup work must not share a pool.
DB_POOL_SIZE = _env_int("DB_POOL_SIZE", 8, 1, 64)
MAINT_POOL_SIZE = _env_int("MAINT_POOL_SIZE", 1, 1, 8)
# PH-07/PERF-007: global Bot API upload limit and a per-tick publication budget.
PUBLISH_CONCURRENCY = _env_int("PUBLISH_CONCURRENCY", 2, 1, 32)
MAX_PUBLISHES_PER_TICK = _env_int("MAX_PUBLISHES_PER_TICK", 8, 0, 200)
# PH-07/NET-005: explicit PTB connection pool size (PTB 22 defaults to 256, but sizing is policy).
TELEGRAM_POOL_SIZE = _env_int("TELEGRAM_POOL_SIZE", 32, 4, 256)
RECOVER_EVERY_MIN = _env_int("RECOVER_EVERY_MIN", 5)
STALE_UPDATE_MAX_MIN = _env_int("STALE_UPDATE_MAX_MIN", 60)
TICK_SECONDS = _env_int("TICK_SECONDS", 120)
# PH-06/OBS-002: اگر اتوماسیون روشن است و کانالی وجود دارد ولی هیچ انتشار موفقی در این
# بازه ثبت نشده، «سبزِ بیکار» را با یک هشدار durable به مدیر کلان اعلام کن.
DEADMAN_STALE_HOURS = _env_int("DEADMAN_STALE_HOURS", 24, 1, 720)
TEST_COOLDOWN_SEC = _env_int("TEST_COOLDOWN_SEC", 120)
CB_RATE = _env_float("CB_RATE", 0.6)
ABUSE_RATE_SEC = _env_float("ABUSE_RATE_SEC", 1.0)
# SEC-03 (فاز ۳): محدودیت نرخ سراسری per-user — سقف به‌روزرسانی از هر نوع (دستور/کال‌بک/پیام) در پنجرهٔ ثانیه
GLOBAL_RATE_MAX = _env_int("GLOBAL_RATE_MAX", 40)
GLOBAL_RATE_WINDOW = _env_float("GLOBAL_RATE_WINDOW", 10.0)
MAX_QUEUE_SIZE = _env_int("MAX_QUEUE_SIZE", 500)     # backpressure صف ready هر کانال
PAY_STUCK_MIN = _env_int("PAY_STUCK_MIN", 10)        # بازیابی پرداخت گیرکرده در processing

# ── ماندگاری محدودیت نرخ حساس ──
RATE_PERSIST_PREFIXES = ("test:", "req:", "rcpt:", "tok:", "cmd:", "sup:", "srcadd:", "srcr:", "dl:", "disc:", "mtest:")

# UISEC-06 (فاز ۶): مهلت state فرم‌های ورودی — پس از این مدت state منقضی و ورودی بعدی رد می‌شود
AWAIT_TTL_SEC = _env_int("AWAIT_TTL_SEC", 1800)

# ── ابزارهای مشترک ──
_FA_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")

# ── ContextVarها (ایزوله per-task / per-cycle) ──
SCHED_NOTIF_FAILURES = ContextVar("sched_notif_failures", default=False)
AI_BUDGET = ContextVar("ai_budget", default=None)   # FIX v4: بودجه‌ی AI دیگر global نیست
