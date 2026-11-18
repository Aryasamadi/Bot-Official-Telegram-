# -*- coding: utf-8 -*-
"""SEC-09 (فاز ۱۲ · تصمیم ۱۰-الف): رمزنگاری حداقلیِ کلیدهای AI در حالت سکون.

قالب مقدار رمزشده: «enc:v1:<token>» — از متن سادهٔ نصب‌های قدیمی قابل تشخیص است و
رمزنگاری دوباره (idempotent) انجام نمی‌شود. کلید از env «SECRETS_KEY» می‌آید
(هر رشتهٔ دلخواه؛ با SHA-256 به کلید Fernet تبدیل می‌شود).

بدون SECRETS_KEY: نوشتن/خواندن بدون رمز ادامه می‌یابد و فقط هشدار لاگ می‌شود
(سازگاری کامل با نصب‌های موجود؛ مهاجرت با تنظیم کلید به‌صورت خودکار در استارت اجرا می‌شود).
"""
import base64
import hashlib
import unicodedata
from newsbot import config
from newsbot.logutils import log

PREFIX = "enc:v1:"


def clean_api_key(value):
    """کلیدِ API را برای ذخیره آماده می‌کند: نویسه‌های نامرئی حذف، فاصلهٔ کناری بریده.

    چرا سخت‌گیر است: کلیدی که با خطِ‌جدید/فاصله (از یک دستورِ چندکلمه‌ای) یا با
    نویسهٔ نامرئی (ZWSP/LRM/BOM — رایج در کپیِ متنِ راست‌به‌چپ) ذخیره شود، شکلِ
    ظاهریِ درست دارد ولی پروایدر آن را رد می‌کند و کاربر فقط «403 Authorization
    failed» می‌بیند و به‌اشتباه فکر می‌کند مدل خراب است. هیچ کلیدِ واقعی‌ای فاصله
    ندارد، پس هر فاصلهٔ باقی‌مانده یعنی ورودیِ خراب ⇒ خطای صریح، نه ذخیرهٔ بی‌صدا.
    """
    raw = str(value or "")
    removed = sum(1 for ch in raw if unicodedata.category(ch) == "Cf")
    key = "".join(ch for ch in raw if unicodedata.category(ch) != "Cf").strip()
    if removed:
        # فقط تعداد؛ هرگز خودِ کلید.
        log.warning("کلید API: %d نویسهٔ نامرئی حذف شد (کپیِ آلوده)", removed)
    if any(ch.isspace() or unicodedata.category(ch) in ("Cc", "Cs") for ch in key):
        raise ValueError("کلید API نباید فاصله یا نویسهٔ کنترلی داشته باشد؛ دوباره و یک‌خطی بفرستید.")
    return key


def _fernet():
    key = (config.SECRETS_KEY or "").strip()
    if not key:
        return None
    try:
        from cryptography.fernet import Fernet
        return Fernet(base64.urlsafe_b64encode(hashlib.sha256(key.encode("utf-8")).digest()))
    except ImportError:
        log.warning("رمزنگاری رازها: بستهٔ cryptography نصب نیست (pip install -r requirements.txt)")
        return None


def is_encrypted(value):
    return isinstance(value, str) and value.startswith(PREFIX)


def encrypt(value):
    """رمزنگاری مقدار (idempotent)؛ بدون کلید یا با خطا ⇒ متن اصلی برمی‌گردد."""
    value = value or ""
    if not value or is_encrypted(value):
        return value
    f = _fernet()
    if f is None:
        return value
    try:
        return PREFIX + f.encrypt(value.encode("utf-8")).decode("ascii")
    except Exception:
        log.warning("رمزنگاری کلید AI شکست خورد؛ متن ساده ذخیره می‌شود", exc_info=True)
        return value


def decrypt(value):
    """رمزگشایی مقدار enc:v1؛ مقدار متن ساده همین‌طور برمی‌گردد.
    بدون کلید/با کلید اشتباه ⇒ "" (fail-closed: کلید نامعتبر هرگز به provider نمی‌رود)."""
    value = value or ""
    if not is_encrypted(value):
        return value
    f = _fernet()
    if f is None:
        log.warning("SECRETS_KEY تنظیم نشده — رمزگشایی کلید AI ممکن نیست")
        return ""
    try:
        return f.decrypt(value[len(PREFIX):].encode("ascii")).decode("utf-8")
    except Exception:
        log.warning("رمزگشایی کلید AI شکست خورد (SECRETS_KEY اشتباه؟)", exc_info=True)
        return ""


def migrate_ai_keys():
    """مهاجرت یک‌بارهٔ استارت: کلیدهای متن سادهٔ جدول ai_models رمزنگاری می‌شوند (idempotent).
    خروجی: تعداد ردیف‌های رمزنگاری‌شده. بدون SECRETS_KEY فقط هشدار (بدون تغییر داده)."""
    from newsbot.db import q
    if not (config.SECRETS_KEY or "").strip():
        n = q("SELECT COUNT(*) c FROM ai_models WHERE api_key IS NOT NULL AND api_key != ''", one=True)
        if n and n["c"]:
            log.warning("SEC-09: %d کلید AI متن ساده در دیتابیس است؛ برای رمزنگاری SECRETS_KEY را تنظیم کنید", n["c"])
        return 0
    rows = q("SELECT id, api_key FROM ai_models WHERE api_key IS NOT NULL AND api_key != ''")
    done = 0
    for r in rows:
        if is_encrypted(r["api_key"]):
            continue
        enc = encrypt(r["api_key"])
        if enc != r["api_key"]:
            q("UPDATE ai_models SET api_key=? WHERE id=?", (enc, r["id"]), commit=True)
            done += 1
    if done:
        log.info("SEC-09: %d کلید AI رمزنگاری شد (enc:v1)", done)
    return done
