# -*- coding: utf-8 -*-
"""اعلان‌های هسته (مدیر کلان و کاربر) — پیاده‌سازی تلگرامی در post_init سیم‌کشی می‌شود.
لایه‌ی هسته هرگز مستقیم به telegram وابسته نیست؛ فقط تابع‌های ثبت‌شده در newsbot/runtime
صدا زده می‌شوند (ARCH-05 — فاز ۱۲: این state از ماژول پیکربندی خارج شد).

ARCH-09 (فاز ۱۳): اعلان‌های مدیران کلان صفِ محدود + ری‌ترای نمایی دارند. صف فقط وقتی فعال
است که worker در post_init اپلیکیشن استارت شده باشد؛ در نبودش رفتار قبلی (تلاش مستقیم
تک‌شات) حفظ می‌شود تا فراخوانی‌های خارج از اپلیکیشن از کار نیفتند.
"""
import asyncio
from newsbot import config
from newsbot import runtime
from newsbot.logutils import log, redact

SUPER_QUEUE_MAX = 100                  # سقف صف؛ سرریز ⇒ دراپ با هشدار (مسیر بحرانی هرگز بلاک نمی‌شود)
SUPER_RETRY_DELAYS = (1.0, 3.0, 8.0)   # پس از تلاش اول؛ جمعاً ۴ تلاش با فاصله‌های نمایی
_super_q = None
_super_worker = None


def _queue():
    if _super_q is None:
        globals()["_super_q"] = asyncio.Queue(maxsize=SUPER_QUEUE_MAX)
    return _super_q


def super_queue_size():
    """اندازهٔ فعلی صف (برای پایش/تست؛ بدون ساخت صف)."""
    return _super_q.qsize() if _super_q is not None else 0


def ensure_super_worker():
    """worker صف را (دوباره) راه می‌اندازد — در post_init اپلیکیشن صدا زده می‌شود."""
    global _super_worker
    if _super_worker is None or _super_worker.done():
        _super_worker = asyncio.get_running_loop().create_task(_super_loop(), name="notify-super")
    return _super_worker


async def _send_super_with_retry(text, kb):
    """ارسال با ری‌ترای نمایی؛ خروجی True اگر (حتی با تلاش دوباره) رسید."""
    attempts = len(SUPER_RETRY_DELAYS) + 1
    last = None
    for i in range(attempts):
        if i:
            await asyncio.sleep(SUPER_RETRY_DELAYS[i - 1])
        try:
            if runtime.NOTIFY_SUPER:
                await runtime.NOTIFY_SUPER(text, kb)
            return True
        except Exception as e:
            last = e
    log.error("notify super: پس از %d تلاش ناموفق ماند: %s", attempts, last)
    return False


def _persist(text, kb, err):
    """PH-03/OBS-011: اعلانِ نرسیده durable می‌شود (dead-letter) تا بعداً برود، نه اینکه گم شود."""
    try:
        from newsbot.core_alerts import alert_enqueue
        nid = alert_enqueue(text, kb)
        log.error("notify super: اعلان ذخیره شد برای تلاشِ بعدی (id=%s): %s", nid, err)
        return nid
    except Exception as e:
        log.error("notify super: ذخیرهٔ اعلان هم ناموفق بود: %s", e)
        return None


async def _super_loop():
    q = _queue()
    while True:
        text, kb = await q.get()
        try:
            ok = await _send_super_with_retry(text, kb)
            if not ok: _persist(text, kb, "delivery failed")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.warning(f"notify super worker: {e}")
            _persist(text, kb, e)
        finally:
            q.task_done()


async def notify_super(text, kb=None):
    """ارسال پیام به همه‌ی مدیران کلان؛ متن پیش از ارسال redact می‌شود (نشت راز ممنوع).
    با worker فعال ⇒ صف + ری‌ترای (ARCH-09)؛ بدون worker ⇒ تلاش مستقیم تک‌شات (سازگاری).

    PH-03/OBS-011: اگر ارسال ناموفق بود یا صف پر بود ⇒ پیام **حذف نمی‌شود**؛ در جدولِ
    ``notifications`` ذخیره می‌شود تا زمان‌بند دوباره بفرستد (و اگر تلاش‌ها تمام شد،
    به‌صورت dead-letter در بخشِ سلامت دیده می‌شود)."""
    if not runtime.NOTIFY_SUPER:
        return
    body = redact(text)
    if _super_worker is not None and not _super_worker.done():
        try:
            _queue().put_nowait((body, kb))
        except asyncio.QueueFull:
            log.warning("notify super: صف پر است؛ پیام به dead-letter منتقل شد")
            _persist(body, kb, "queue full")
        return
    try:
        await runtime.NOTIFY_SUPER(body, kb)
    except Exception as e:
        log.warning(f"notify super: {e}")
        _persist(body, kb, e)


async def drain_super_queue(timeout=10.0):
    """انتظار تا تخلیهٔ کامل صف (تست‌ها/خاموشی)."""
    await asyncio.wait_for(_queue().join(), timeout)


async def stop_super_worker():
    """توقف تمیز worker (خاموشی اپلیکیشن/تست)."""
    global _super_worker
    t, _super_worker = _super_worker, None
    if t is not None and not t.done():
        t.cancel()
        try:
            await t
        except BaseException:
            pass


async def notify_user(uid, text, kb=None):
    """ارسال پیام به یک کاربر. در حالت زمان‌بند (SCHED_NOTIF_FAILURES) شکست بالا می‌رود
    تا _plan_notifications کلید هشدار را آزاد کند و تیک بعدی دوباره تلاش کند."""
    if runtime.NOTIFY_USER:
        try: await runtime.NOTIFY_USER(uid, text, kb)
        except Exception as e:
            log.warning(f"notify user {uid}: {e}")
            if config.SCHED_NOTIF_FAILURES.get():
                raise   # scheduled-warning failures must reach _plan_notifications' release branch
