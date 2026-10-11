# -*- coding: utf-8 -*-
"""اعلان‌های هسته (مدیر کلان و کاربر) — پیاده‌سازی تلگرامی در post_init سیم‌کشی می‌شود.
لایه‌ی هسته هرگز مستقیم به telegram وابسته نیست؛ فقط تابع‌های ثبت‌شده در config صدا زده می‌شوند.
"""
from newsbot import config
from newsbot.logutils import log, redact


async def notify_super(text, kb=None):
    """ارسال پیام به همه‌ی مدیران کلان؛ متن پیش از ارسال redact می‌شود (نشت راز ممنوع)."""
    if config.NOTIFY_SUPER:
        try: await config.NOTIFY_SUPER(redact(text), kb)
        except Exception as e: log.warning(f"notify super: {e}")


async def notify_user(uid, text, kb=None):
    """ارسال پیام به یک کاربر. در حالت زمان‌بند (SCHED_NOTIF_FAILURES) شکست بالا می‌رود
    تا _plan_notifications کلید هشدار را آزاد کند و تیک بعدی دوباره تلاش کند."""
    if config.NOTIFY_USER:
        try: await config.NOTIFY_USER(uid, text, kb)
        except Exception as e:
            log.warning(f"notify user {uid}: {e}")
            if config.SCHED_NOTIF_FAILURES.get():
                raise   # scheduled-warning failures must reach _plan_notifications' release branch
