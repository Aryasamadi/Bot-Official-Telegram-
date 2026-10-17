#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""نقطه‌ی ورود پروژه:
    python run.py            → اجرای ربات (polling)
    python run.py --check    → خودآزمایی بدون پولینگ (پیکربندی + بازرسی فقط‌خواندنی دیتابیس) — مناسب CI و پیش از دیپلوی
                              ARCH-04/C7 (تصمیم ۱۳): در این حالت هیچ فایل/پوشه‌ای ساخته و هیچ نوشتنی روی دیتابیس انجام نمی‌شود.
"""
import sys
from newsbot.app import main, preflight
from newsbot.logutils import log

if __name__ == "__main__":
    if "--check" in sys.argv:
        preflight(check=True); log.info("✅ خودآزمایی موفق: پیکربندی سالم است و دیتابیس (در صورت وجود) فقط‌خواندنی بررسی شد.")
    else:
        main()
