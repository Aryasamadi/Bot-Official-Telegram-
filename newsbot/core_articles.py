# -*- coding: utf-8 -*-
"""مقالات، dedup، ژورنال انتشار (idempotent و crash-safe) و پاک‌سازی دوره‌ای.
ماشین حالت انتشار: prepared → in_flight → partial → ack → settle (حذف ژورنال).
هیچ مقاله‌ای پس از ارسال موفق تلگرام، دوباره منتشر نمی‌شود (ژورنال + جدول posted).
"""
import asyncio, json, time
from datetime import timedelta
from telegram.error import BadRequest, RetryAfter, NetworkError, Forbidden
from newsbot import config
from newsbot.db import q, db, _lock, _safe_fields, _trx, gget, gset
from newsbot.logutils import log, bump
from newsbot.timeutils import now_utc, now_iso, parse_dt, today_str
from newsbot.ratelimit import rate_mark, rate_cleanup
from newsbot.core_users import admin_limits, admin_offset
from newsbot.core_channels import ch_lock_unused if False else None  # placeholder removed
