# -*- coding: utf-8 -*-
"""فاز ۴ — سیاست بقای داده: محتوای منتشرشده هرگز حذف نمی‌شود (تصمیم C4).

زمینه: بخش ۴ مأموریت صریحاً «حفظ دقیق محتوای تولیدشده توسط ایجنت» را الزام می‌کند.
پیاده‌سازی قبلی یک `DELETE FROM articles WHERE status='published' AND created_at<72h`
داشت که این الزام را نقض می‌کرد. تصمیم کاربر: حذف کامل آن خط.

این تست‌ها نگهبانِ آن تصمیم هستند: اگر روزی کسی دوباره حذف محتوای منتشرشده را اضافه کند،
اینجا شکسته می‌شود.
"""
import os
import time
from datetime import timedelta

from newsbot import config
from newsbot.db import q, gset
from newsbot.timeutils import now_utc
from newsbot.core_articles import article_insert, cleanup, get_article


def _mk_published(cid, age_hours, status="published"):
    """مقالهٔ ساختگی با created_at در گذشته (بدون تماس شبکه‌ای)."""
    created = (now_utc() - timedelta(hours=age_hours)).isoformat()
    aid = article_insert(1, cid, f"h{age_hours}{status}{time.time()}", "https://x.test/a", "t", 0, None, status=status)
    q("UPDATE articles SET created_at=? WHERE id=?", (created, aid), commit=True)
    return aid


def test_published_content_survives_cleanup_after_72h():
    """کلید C4: مقالهٔ منتشرشدهٔ ۷۲+ ساعت قبل باید بعد از cleanup باقی بماند."""
    aid = _mk_published(9001, age_hours=200)
    gset("last_cleanup", 0)          # گارد یک‌ساعتهٔ cleanup را دور بزن
    cleanup()
    assert get_article(aid) is not None, "محتوای منتشرشده نباید حذف شود (نقض C4)"


def test_published_content_survives_much_older():
    """حتی مقالهٔ منتشرشدهٔ خیلی قدیمی هم می‌ماند (بدون انقضا)."""
    aid = _mk_published(9002, age_hours=24 * 365)
    gset("last_cleanup", 0)
    cleanup()
    assert get_article(aid) is not None


def test_non_ready_unpublished_content_still_cleaned():
    """پاک‌سازی نباید به‌کل تعطیل شود: محتوای مردهٔ منتشرنشدهٔ کهنه باید حذف گردد."""
    aid = _mk_published(9003, age_hours=10_000, status="rejected")
    gset("last_cleanup", 0)
    cleanup()
    assert get_article(aid) is None, "محتوای ردشدهٔ کهنه باید پاک شود"


def test_config_default_ttl_is_72():
    """H2: پیش‌فرض TTL باید ۷۲ ساعت باشد (هم‌ترازی با معماری هدف)."""
    # مقدار پیش‌فرض پیکربندی‌شده باید ۷۲ باشد
    assert config.DATA_TTL_HOURS == int(os.getenv("DATA_TTL_HOURS", "72"))
    assert config.DEDUP_TTL_HOURS == int(os.getenv("DEDUP_TTL_HOURS", "72"))


def test_no_delete_of_published_rows_in_cleanup_source():
    """نگهبانِ منبعِ کد: هیچ DELETE … status='published' نباید در cleanup بماند.

    توجه: `status NOT IN ('ready','published')` مجاز است — آن یک allow-list برای
    *حفظ* محتوای منتشرشده است. الگوی ممنوع، انتساب مثبتِ status به 'published' است.
    """
    import inspect, re
    from newsbot import core_articles
    src = inspect.getsource(core_articles.cleanup)
    # الگوی خطرناک: status = 'published'  (با کوتیشن، به‌عنوان مقدار انتسابی)
    bad = re.compile(r"""status\s*=\s*['"]published['"]""", re.I)
    for line in src.splitlines():
        if "DELETE FROM articles" in line:
            assert not bad.search(line), f"حذف محتوای منتشرشده بازگشته (C4): {line.strip()}"
