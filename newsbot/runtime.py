# -*- coding: utf-8 -*-
"""ARCH-05 (فاز ۱۲): state زندهٔ زمان اجرا — جدا از پیکربندی محیط.

config.py فقط env را می‌خواند؛ مقادیری که در post_init از تلگرام/لایهٔ UI ست می‌شوند
(BOT_USERNAME و پیام‌رسان‌های ثبت‌شده) این‌جا نگه داشته می‌شوند تا ماژول پیکربندی
به‌عنوان state زندهٔ قابل‌تغییر استفاده نشود.
"""
BOT_USERNAME = ""      # در post_init از تلگرام خوانده می‌شود
NOTIFY_SUPER = None    # async fn(text, kb=None) — در post_init ست می‌شود
NOTIFY_USER = None     # async fn(uid, text, kb=None)
