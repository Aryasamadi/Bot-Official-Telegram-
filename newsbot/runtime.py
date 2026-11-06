# -*- coding: utf-8 -*-
"""ARCH-05 (فاز ۱۲): state زندهٔ زمان اجرا — جدا از پیکربندی محیط.

config.py فقط env را می‌خواند؛ مقادیری که در post_init از تلگرام/لایهٔ UI ست می‌شوند
(BOT_USERNAME و پیام‌رسان‌های ثبت‌شده) این‌جا نگه داشته می‌شوند تا ماژول پیکربندی
به‌عنوان state زندهٔ قابل‌تغییر استفاده نشود.

DEVOPS-08 (فاز C) — «تخلیهٔ ایمن» (Graceful Drain):
پیش از ری‌استارتِ کانتینر، ربات باید کارِ تازه را شروع نکند ولی انتشارهایی که از قبل
«در حالِ پرواز» هستند تا پایان ادامه یابند؛ وگرنه مقاله در وضعیت
``delivery_unknown`` می‌ماند و نیاز به دخالت دستی پیدا می‌کند.

پروتکل (سه تکه، هم بین پروسه و هم بین پروسه و اسکریپتِ استقرار مشترک است):
  1. ``DRAIN_FLAG_FILE``   — فایلِ نشانگرِ «تخلیه فعال است» (پیش‌فرض: /tmp/newsbot_draining).
  2. ``DRAINED_FLAG_FILE`` — فایلِ نشانگرِ «تخلیه کامل شد» (پیش‌فرض: /tmp/newsbot_drained).
  3. شمارندهٔ ``in_flight`` — تعدادِ انتشارهایی که رزرو ژورنال گرفته‌اند ولی هنوز
     به ``ack`` نرسیده‌اند (یعناً همان‌هایی که اگر وسطِ کار قطع شوند، delivery_unknown
     می‌سازند).

اسکریپتِ استقرار (``deploy/deploy.sh``) فایلِ (۱) را می‌سازد، منتظرِ (۲) می‌ماند و بعد
سرویس را با مهلتِ بلند متوقف می‌کند.
"""
import asyncio
import os
import threading
import time
from typing import Any, Callable, Optional

from newsbot import config

BOT_USERNAME: str = ""        # در post_init از تلگرام خوانده می‌شود
NOTIFY_SUPER: Optional[Callable[..., Any]] = None    # async fn(text, kb=None) — در post_init ست می‌شود
NOTIFY_USER: Optional[Callable[..., Any]] = None     # async fn(uid, text, kb=None)

# ── Graceful drain (DEVOPS-08) ───────────────────────────────────────────────────
_drain_lock = threading.Lock()
_draining = False
_drain_reason = ""
_in_flight = 0


def _touch(path: str) -> bool:
    """ساختن/به‌روزرسانیِ یک فایلِ نشانگر. خطا (مثلاً FS فقط‌خواندنی) نباید استقرار را بشکند."""
    try:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(f"{time.time():.3f}\n")
        return True
    except OSError:
        return False


def _unlink(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


def _flag_exists() -> bool:
    """آیا فایلِ نشانگرِ «تخلیه فعال است» روی دیسک هست؟"""
    try:
        return os.path.exists(config.DRAIN_FLAG_FILE)
    except OSError:
        return False


def drain_flag_file() -> str:
    """مسیرِ فایلِ نشانگرِ «تخلیه فعال است»."""
    return config.DRAIN_FLAG_FILE


def drained_flag_file() -> str:
    """مسیرِ فایلِ نشانگرِ «تخلیه کامل شد»."""
    return config.DRAINED_FLAG_FILE


def is_draining() -> bool:
    """True اگر تخلیه فعال باشد: پرچمِ درون‌فرایندی یا فایلِ نشانگرِ روی دیسک."""
    if _draining:
        return True
    return _flag_exists()


def drain_reason() -> str:
    """دلیلِ فعال‌شدنِ تخلیه (برای لاگ)."""
    return _drain_reason


def request_drain(reason: str = "") -> bool:
    """فعال‌کردنِ حالتِ تخلیه. برگشتی: True اگر همین حالا تازه فعال شد.

    از این پس هیچ انتشارِ تازه‌ای آغاز نمی‌شود؛ انتشارهای در حالِ پرواز ادامه می‌یابند.
    """
    global _draining, _drain_reason
    with _drain_lock:
        already = _draining
        _draining = True
        if reason:
            _drain_reason = reason
    _unlink(config.DRAINED_FLAG_FILE)
    _touch(config.DRAIN_FLAG_FILE)
    return not already


def cancel_drain() -> None:
    """برگرداندنِ حالتِ عادی (استفاده در تست‌ها و در راه‌اندازیِ تازه)."""
    global _draining, _drain_reason
    with _drain_lock:
        _draining = False
        _drain_reason = ""
    _unlink(config.DRAIN_FLAG_FILE)
    _unlink(config.DRAINED_FLAG_FILE)


def clear_stale_drain() -> None:
    """پاک‌کردنِ نشانگرِ تخلیه‌ی بازمانده از یک اجرای قبلی.

    یک پروسهٔ تازه هیچ انتشارِ در حالِ پروازی ندارد ⇒ نشانگرِ بازمانده فقط می‌تواند
    ربات را در حالتِ «همیشه در حالِ تخلیه» گیر بیندازد. هنگام راه‌اندازی پاک می‌شود.
    """
    global _draining, _drain_reason
    with _drain_lock:
        _draining = False
        _drain_reason = ""
    _unlink(config.DRAIN_FLAG_FILE)
    _unlink(config.DRAINED_FLAG_FILE)


def begin_publish() -> bool:
    """اجازهٔ آغازِ یک انتشارِ تازه.

    False ⇒ در حالتِ تخلیه هستیم و انتشارِ تازه نباید شروع شود.
    True ⇒ شمارندهٔ «در حالِ پرواز» یکی زیاد شد و باید با ``end_publish`` بسته شود.

    پرچمِ روی دیسک هم بررسی می‌شود: اسکریپتِ استقرار آن را می‌سازد و ممکن است
    پیش از آن‌که ناظرِ داخلیِ پروسه آن را ببیند، یک انتشارِ تازه شروع شود.
    """
    global _in_flight
    with _drain_lock:
        if _draining:
            return False
        if _flag_exists():
            return False
        _in_flight += 1
        return True


def end_publish() -> None:
    """پایانِ یک انتشار (هر مسیری: ack، شکست، لغو)."""
    global _in_flight
    with _drain_lock:
        if _in_flight > 0:
            _in_flight -= 1
        remaining = _in_flight
    if remaining == 0 and is_draining():
        mark_drained()


def in_flight_publishes() -> int:
    """تعدادِ انتشارهایی که رزرو گرفته‌اند ولی هنوز نهایی نشده‌اند."""
    with _drain_lock:
        return _in_flight


def mark_drained() -> None:
    """نشانگرِ «تخلیه کامل شد» را می‌سازد — اسکریپتِ استقرار منتظرِ همین فایل می‌ماند."""
    if is_draining():
        _touch(config.DRAINED_FLAG_FILE)


async def wait_for_in_flight_publishes(timeout: float = 30.0) -> bool:
    """صبر تا پایانِ انتشارهای در حالِ پرواز.

    True ⇒ شمارنده به صفر رسید (و نشانگرِ «تخلیه شد» نوشته شد).
    False ⇒ مهلت تمام شد و هنوز انتشاری در جریان است.
    """
    deadline = time.monotonic() + max(0.0, float(timeout))
    while True:
        if in_flight_publishes() <= 0:
            mark_drained()
            return True
        if time.monotonic() >= deadline:
            return False
        await asyncio.sleep(0.05)


async def drain(timeout: float = 30.0, reason: str = "") -> bool:
    """ترکیبِ «درخواستِ تخلیه» + «انتظار برای پایانِ انتشارهای جاری»."""
    request_drain(reason)
    return await wait_for_in_flight_publishes(timeout)


async def drain_watch_loop(interval: float = 0.5) -> None:
    """ناظرِ دائمیِ نشانگرِ تخلیه.

    وقتی اسکریپتِ استقرار فایلِ ``DRAIN_FLAG_FILE`` را می‌سازد، این ناظر آن را می‌بیند و
    (اگر هیچ انتشاری در حالِ پرواز نباشد) فایلِ ``DRAINED_FLAG_FILE`` را می‌نویسد تا
    استقرار بداند «تخلیه کامل شد» و با خیالِ راحت سرویس را متوقف کند.
    """
    try:
        while True:
            await asyncio.sleep(max(0.05, float(interval)))
            if not is_draining():
                continue
            if in_flight_publishes() <= 0:
                mark_drained()
                return
    except asyncio.CancelledError:
        raise
