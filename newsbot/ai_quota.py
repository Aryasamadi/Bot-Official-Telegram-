# -*- coding: utf-8 -*-
"""فاز ۴ — سهمیهٔ هوش مصنوعیِ سطحِ پلن (تصمیمِ مالک ۶).

قراردادها (design: docs/superpowers/specs/2026-11-16-phase4-reply-qa-design.md):
  • سقف در تعریفِ پلن است (``plans.ai_quota``)؛ ۰ = نامحدود.
  • سهمیه به **کاربر** تعلق دارد و **سراسری** است: تولیدِ مقاله، تستِ مدل و Q&A
    همه از یک دروازه (``core_users.ai_claim``) رد می‌شوند — هیچ سهمیهٔ per-channel
    یا per-feature وجود ندارد.
  • هر دورهٔ اشتراک یک ردیفِ گرانت دارد (``UNIQUE(user_id, expires_at)``) ⇒ تمدید،
    دورهٔ تازه با مصرفِ صفر می‌سازد و ردیفِ دورهٔ قبل دست‌نخورده می‌ماند (سابقه).
  • سقفِ گرانت هرگز در میانهٔ دوره **پایین** نمی‌آید؛ افزایشِ سقف توسطِ مدیر اعمال می‌شود
    تا کاربری که به‌خاطرِ خطای تنظیمات گیر کرده، آزاد شود.
"""
from newsbot.core_users import admin_limits
from newsbot.db import q, write
from newsbot.timeutils import iso_fixed, now_iso


def plan_grant(uid):
    """گرانتِ دورهٔ فعال یا ``None``.

    ``None`` یعنی «این کاربر سقفِ پلنی ندارد» (پلنِ فعال ندارد، یا سقفِ پلنش ۰/نامحدود
    است) ⇒ این دروازه چیزی را مسدود نمی‌کند؛ سقفِ روزانهٔ AI همچنان مستقل عمل می‌کند.
    ردیفِ گرانت **تنبل** ساخته می‌شود: نخستین درخواستِ AI در هر دوره آن را می‌سازد.
    """
    lim = admin_limits(uid)
    p = lim.get("plan")
    if not lim.get("active") or not p or not lim.get("expires"):
        return None
    try:
        quota = int(p["ai_quota"] or 0)
    except (IndexError, KeyError, TypeError, ValueError):
        quota = 0
    if quota <= 0:
        return None
    exp = iso_fixed(lim["expires"])
    row = q("SELECT * FROM ai_plan_grants WHERE user_id=? AND expires_at=?", (uid, exp), one=True)
    if row is None:
        q("INSERT OR IGNORE INTO ai_plan_grants(user_id,plan_id,quota,used,granted_at,expires_at)"
          " VALUES(?,?,?,0,?,?)", (uid, p["id"], quota, now_iso(), exp), commit=True)
        row = q("SELECT * FROM ai_plan_grants WHERE user_id=? AND expires_at=?", (uid, exp), one=True)
    elif quota > int(row["quota"] or 0):
        q("UPDATE ai_plan_grants SET quota=? WHERE id=?", (quota, row["id"]), commit=True)
        row = q("SELECT * FROM ai_plan_grants WHERE id=?", (row["id"],), one=True)
    return row


def plan_claim(uid):
    """یک واحد از سهمیهٔ پلن مصرف می‌کند. ``True`` = مجاز.

    بدونِ سقف ⇒ ``True`` (بی‌نهایت). با سقف ⇒ یک ``UPDATE`` شرطیِ اتمیک
    (``used < quota``)؛ ``rowcount`` صفر یعنی «تمام شده» و هیچ واحدی مصرف نمی‌شود.
    """
    g = plan_grant(uid)
    if not g:
        return True

    def _go(conn):
        cur = conn.execute("UPDATE ai_plan_grants SET used=used+1 WHERE id=? AND used<quota", (g["id"],))
        return int(getattr(cur, "rowcount", 0) or 0) == 1

    return bool(write(_go))


def units_text(quota, lang="fa"):
    """متنِ «تعداد واحد» برای نمایش؛ ۰ همان «نامحدود» است."""
    from newsbot.ui_i18n import tr
    try:
        n = int(quota or 0)
    except (TypeError, ValueError):
        n = 0
    return tr(lang, "unlimited_word") if n <= 0 else str(n)


def quota_lines(uid, lang="fa"):
    """سطرِ «کل/مصرف‌شده/باقی‌مانده» برای پنلِ کاربر. بدونِ سقف ⇒ «نامحدود»."""
    from newsbot.ui_i18n import tr
    g = plan_grant(uid)
    if not g:
        unlimited = tr(lang, "unlimited_word")
        return tr(lang, "plan_ai_line", total=unlimited, used="-", left=unlimited)
    total = int(g["quota"] or 0)
    used = int(g["used"] or 0)
    return tr(lang, "plan_ai_line", total=str(total), used=str(used), left=str(max(0, total - used)))
