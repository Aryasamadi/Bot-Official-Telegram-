# -*- coding: utf-8 -*-
"""NewsBot v4 — پکیج ماژولار ربات مدیریت خودکار کانال تلگرام."""
__version__ = "4.0.0"
# DB-25: تک‌منبعِ نسخهٔ اسکیما (db.py از همین‌جا import می‌کند؛ قبلاً دو مقدار موازی drifted بود).
# v4: جدول‌های referrals/rate_limits + claimed_at/applied_at + trigger اتمیک اعتبار ارجاع + usage.ai
# v5 (فاز ۱۲/REF-3): trigger نسخهٔ «پاداش هر خرید» (delta افزایش credit_amount) — بدون تغییر جدول
SCHEMA_VERSION = 5
