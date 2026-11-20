# -*- coding: utf-8 -*-
"""فاز ۵ — گاردریلِ پرسش و پاسخ (design: 2026-11-20-phase5-qa-quotas-design.md).

سهمیه از همان سطرهای ``qa_answers`` استخراج میشود: یک ``INSERT … SELECT … WHERE …
ON CONFLICT DO NOTHING`` تمامِ محدودیتها را در یک دستورِ اتمیک اعمال میکند. جدولِ
شمارندهٔ جدا وجود ندارد ⇒ drift و دورزدن با restart ممکن نیست.
"""
from datetime import datetime, timedelta

from newsbot import config
from newsbot.core_users import admin_offset, is_super
from newsbot.timeutils import iso_fixed, now_iso, offset_now, today_str

def _number(settings, key, default, lo, hi):
    value = (settings or {}).get(key)
    if value is None or isinstance(value, bool):
        return default
    try:
        out = int(value)
    except (TypeError, ValueError):
        return default
    if isinstance(value, float) and value != out:
        return default
    return out if lo <= out <= hi else default


def effective_limits(settings):
    """``(cooldown_s, user_max, post_max)`` — مقدارِ نامعتبر ⇒ پیشفرضِ env."""
    cooldown = _number(settings, "qa_cooldown_s", config.QA_COOLDOWN_S, 0, 3600)
    user_max = _number(settings, "qa_user_daily_max", config.QA_USER_DAILY_MAX, 1, 1000)
    post_max = _number(settings, "qa_post_daily_max", config.QA_POST_DAILY_MAX, 1, 5000)
    return cooldown, user_max, post_max


def day_start(local_day, offset):
    """نیمهشبِ محلیِ ``local_day`` به قالبِ UTCِ ``iso_fixed``."""
    midnight = datetime.strptime(local_day, "%Y-%m-%d").replace(tzinfo=config.UTC)
    return iso_fixed(midnight - timedelta(hours=float(offset)))


def windows(uid, settings, now):
    """``(since_user, since_post, cooldown_cutoff)`` بر اساسِ روزِ محلی."""
    cooldown_s = effective_limits(settings)[0]
    user_off = admin_offset(uid)
    stored_tz = (settings or {}).get("tz") or (settings or {}).get("utc_offset")
    channel_off = offset_now(stored_tz) if stored_tz is not None else config.DEFAULT_UTC_OFFSET
    since_user = day_start(today_str(user_off), user_off)
    since_post = day_start(today_str(channel_off), channel_off)
    cutoff = iso_fixed(now - timedelta(seconds=cooldown_s))
    return since_user, since_post, cutoff


_GUARDED_INSERT = """
INSERT INTO qa_answers(chat_id,msg_id,user_id,channel_id,post_id,status,answer,error,created_at,updated_at)
SELECT ?,?,?,?,?,'pending','','',?,?
WHERE (SELECT COUNT(*) FROM qa_answers WHERE user_id=? AND created_at>=?) < ?
  AND (SELECT COUNT(*) FROM qa_answers WHERE channel_id=? AND post_id=? AND created_at>=?) < ?
  AND (SELECT COALESCE(MAX(created_at),'') FROM qa_answers WHERE user_id=?) <= ?
ON CONFLICT(chat_id,msg_id) DO NOTHING
"""


def claim(conn, chat_id, msg_id, user_id, channel_id, post_id,
          since_user, since_post, cooldown_cutoff, user_max, post_max,
          bypass=False, at=None):
    """یک واحد رویدادِ پذیرفتهشده؛ خروجی ``''`` یا دلیلِ رد."""
    at = at or now_iso()
    if conn.execute("SELECT 1 FROM qa_answers WHERE chat_id=? AND msg_id=?",
                    (chat_id, msg_id)).fetchone():
        return "duplicate"
    if bypass:
        cur = conn.execute(
            "INSERT OR IGNORE INTO qa_answers(chat_id,msg_id,user_id,channel_id,post_id,status,answer,error,created_at,updated_at)"
            " VALUES(?,?,?,?,?,'pending','','',?,?)",
            (chat_id, msg_id, user_id, channel_id, post_id, at, at))
        return "" if int(getattr(cur, "rowcount", 0) or 0) == 1 else "duplicate"

    cur = conn.execute(_GUARDED_INSERT,
                       (chat_id, msg_id, user_id, channel_id, post_id, at, at,
                        user_id, since_user, user_max,
                        channel_id, post_id, since_post, post_max,
                        user_id, cooldown_cutoff))
    if int(getattr(cur, "rowcount", 0) or 0) == 1:
        return ""
    # تشخیصِ دلیل فقط برای لاگ/پیام است؛ enforcement همان دستورِ بالاست.
    used_user = int(conn.execute(
        "SELECT COUNT(*) AS n FROM qa_answers WHERE user_id=? AND created_at>=?",
        (user_id, since_user)).fetchone()["n"])
    used_post = int(conn.execute(
        "SELECT COUNT(*) AS n FROM qa_answers WHERE channel_id=? AND post_id=? AND created_at>=?",
        (channel_id, post_id, since_post)).fetchone()["n"])
    last = conn.execute(
        "SELECT COALESCE(MAX(created_at),'') AS t FROM qa_answers WHERE user_id=?",
        (user_id,)).fetchone()["t"]
    if last > cooldown_cutoff:
        return "cooldown"
    if used_user >= user_max:
        return "user_cap"
    if used_post >= post_max:
        return "post_cap"
    return "duplicate"
