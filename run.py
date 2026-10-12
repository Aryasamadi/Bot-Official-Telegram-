#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""نقطه‌ی ورود پروژه:
    python run.py            → اجرای ربات (polling)
    python run.py --check    → خودآزمایی بدون پولینگ (پیکربندی + ساخت/مهاجرت دیتابیس) — مناسب CI و پیش از دیپلوی
"""
import sys
from newsbot.app import main, preflight, init_core
from newsbot.logutils import log

if __name__ == "__main__":
    if "--check" in sys.argv:
        preflight(); init_core(); log.info("✅ خودآزمایی موفق: پیکربندی و دیتابیس سالم است.")
    else:
        main()
