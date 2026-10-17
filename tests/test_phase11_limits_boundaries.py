# -*- coding: utf-8 -*-
"""فاز ۱۱ — تست‌های حد و سرریز (QA-12/GAP-05).

مرزهای دقیق سقف‌ها و مقادیر حدی: سقف سهمیه دقیقاً «کمتر از» است (نه ≤)، شمارنده‌ها
هرگز منفی نمی‌شوند، اعداد بزرگ (روزهای پلن) بدون سرریز محاسبه می‌شوند و ورودی‌های
بیرون‌بازه در چند نقطه (تنظیمات کانال، تخفیف، تخفیف قیمت) به بازهٔ مجاز کلمپ می‌شوند.
"""
import json

from newsbot import config
from newsbot.core_articles import article_insert, reserve_publication, release_publication, get_pub_journal
from newsbot.core_channels import add_channel, channel_by_chat, get_settings
from newsbot.core_discounts import disc_create, disc_get, disc_use, disc_refund, discount_price
from newsbot.core_plans import (create_plan, assign_plan, get_plan, proration_remaining_days,
                                upgrade_quote)
from newsbot.core_users import (ensure_user, admin_offset, admin_limits, ai_claim, ai_cap_for,
                                ai_used_today, usage_today)
from newsbot.db import q
from newsbot.timeutils import today_str, now_utc, iso_fixed, parse_dt


def _user(uid): ensure_user(uid, f"u{uid}", f"u{uid}")

def _chan(uid, chat_id):
    add_channel(uid, chat_id, f"ch{chat_id}", "", 1)
    return channel_by_chat(chat_id)["id"]

_J0 = {"phase": "prepared", "reserved_day": None, "test_day": None, "test_user": None}


def test_reserve_publication_exact_cap_boundary():
    """سقف daily_posts=1 دقیقاً در مرز اعمال می‌شود: رزرو اول برنده، دوم CAS را می‌بازد و شمارنده اضافه نمی‌شود."""
    uid = 9101; _user(uid)
    pid = create_plan(name="cap1", days=30, daily_posts=1, daily_tests=1, max_sources=2, max_channels=1)
    assign_plan(uid, pid)
    cid = _chan(uid, 91010)
    a1 = article_insert(uid, cid, "h-b1", "https://x.test/b1", "t", 0, None, status="ready")
    j1 = reserve_publication(a1, uid, True, None, _J0)
    assert j1 is not None and j1["reserved_day"] is not None
    a2 = article_insert(uid, cid, "h-b2", "https://x.test/b2", "t", 0, None, status="ready")
    assert reserve_publication(a2, uid, True, None, _J0) is None
    row = q("SELECT posts FROM usage WHERE admin_id=? AND day=?", (uid, j1["reserved_day"]), one=True)
    assert row["posts"] == 1, "تلاش مازاد نباید شمارنده را جلو ببرد"
    assert usage_today(uid)["posts"] == 1


def test_release_publication_restores_slot_and_never_negative():
    """آزادسازی سهمیه: اسلات برمی‌گردد، رزرو مجدد ممکن می‌شود و شمارنده هرگز منفی نمی‌شود (MAX(0,...))."""
    uid = 9102; _user(uid)
    pid = create_plan(name="cap2", days=30, daily_posts=1, daily_tests=1, max_sources=2, max_channels=1)
    assign_plan(uid, pid)
    cid = _chan(uid, 91020)
    a1 = article_insert(uid, cid, "h-r1", "https://x.test/r1", "t", 0, None, status="ready")
    j1 = reserve_publication(a1, uid, True, None, _J0)
    jr = release_publication(a1, j1, "boom")
    assert jr["reserved_day"] is None
    a2 = article_insert(uid, cid, "h-r2", "https://x.test/r2", "t", 0, None, status="ready")
    j2 = reserve_publication(a2, uid, True, None, _J0)
    assert j2 is not None, "پس از آزادسازی، اسلات باید دوباره قابل رزرو باشد"
    release_publication(a2, get_pub_journal(a2), "again")
    row = q("SELECT posts FROM usage WHERE admin_id=? AND day=?", (uid, j1["reserved_day"]), one=True)
    assert row["posts"] == 0, "شمارندهٔ سهمیه هرگز نباید منفی شود"
    release_publication(a2, j2, "extra")   # آزادسازی مازاد هم شمارنده را منفی نمی‌کند
    row = q("SELECT posts FROM usage WHERE admin_id=? AND day=?", (uid, j1["reserved_day"]), one=True)
    assert row["posts"] == 0


def test_ai_claim_stops_exactly_at_cap():
    """سقف AI در مرز دقیق: از cap-1 یک ادعا تا خودِ cap بالا می‌رود و ادعای بعدی رد می‌شود."""
    uid = 9103; _user(uid)
    cap = ai_cap_for(uid)                      # بدون پلن فعال ⇒ سقف رایگان
    assert cap == config.AI_DAILY_FREE and cap >= 1
    day = today_str(admin_offset(uid))
    q("INSERT INTO usage(admin_id,day,posts,tests,ai) VALUES(?,?,0,0,?)", (uid, day, cap - 1), commit=True)
    assert ai_claim(uid) is True
    assert ai_used_today(uid) == cap, "ادعا باید دقیقاً روی سقف بایستد، نه بیشتر"
    assert ai_claim(uid) is False
    assert ai_used_today(uid) == cap


def test_assign_plan_large_days_no_overflow():
    """روزهای بزرگ پلن (۱۰^۶ ≈ ۲۷۰۰ سال) بدون سرریز datetime/محاسبات پشتیبانی می‌شود."""
    uid = 9104; _user(uid)
    pid = create_plan(name="big", days=1_000_000, daily_posts=5, daily_tests=2, max_sources=5, max_channels=1)
    exp, queued = assign_plan(uid, pid)
    assert queued is False and exp > now_utc()
    assert parse_dt(iso_fixed(exp)) == exp, "قالب کانونیکال باید بدون خطای سرریز round-trip شود"
    rem = proration_remaining_days(uid)
    assert 999_900 < rem <= 1_000_000
    assert admin_limits(uid)["active"] is True


def test_assign_plan_zero_and_negative_days():
    """روزهای صفر/منفی نباید کرش کنند؛ نتیجه: پلن غیرفعال (انقضا ≤ الان)."""
    uid = 9105; _user(uid)
    pid = create_plan(name="zero", days=0, daily_posts=1, daily_tests=1, max_sources=1, max_channels=1)
    assign_plan(uid, pid)
    assert admin_limits(uid)["active"] is False
    assign_plan(uid, pid, days=-5)
    assert admin_limits(uid)["active"] is False


def test_upgrade_quote_discount_extremes():
    """تخفیف ۰٪ مبلغ کامل و تخفیف ۱۰۰٪ دقیقاً صفر می‌دهد (payable هرگز منفی نمی‌شود)."""
    uid = 9106; _user(uid)
    cur = create_plan(name="cur", days=30, daily_posts=2, daily_tests=1, max_sources=2, max_channels=1, price_num=30)
    assign_plan(uid, cur)
    new = create_plan(name="new", days=30, daily_posts=50, daily_tests=10, max_sources=20, max_channels=3, price_num=60)
    p, c = get_plan(new), get_plan(cur)
    qt = upgrade_quote(uid, p, c, disc_percent=0)
    assert qt["payable"] == 30.0
    qt100 = upgrade_quote(uid, p, c, disc_percent=100)
    assert qt100["payable"] == 0.0


def test_discount_price_boundaries():
    """قیمت متنی: ۰٪/۱۰۰٪، ارقام فارسی و جداکننده‌ها؛ بدون عدد ⇒ متن «(−٪ تخفیف)»."""
    assert discount_price("1000", 0) == "1,000"
    assert discount_price("1000", 100) == "0"
    assert discount_price("1,234", 50) == "617"
    assert discount_price("۱۰٬۰۰۰", 10) == "9,000"
    assert discount_price("گران", 30) == "گران (-30%)"
    assert discount_price("", 50) == " (-50%)"


def test_discount_code_capacity_and_refund_boundaries():
    """ظرفیت کد تخفیف: دقیقاً max_uses مصرف می‌شود؛ برگشت هرگز used را منفی نمی‌کند؛ درصد خارج بازه کلمپ می‌شود."""
    disc_create("BND10", 10, None, max_uses=1)
    assert disc_use("BND10") is True
    assert disc_use("BND10") is False, "ظرفیت تمام‌شده نباید حتی یک مصرف دیگر بپذیرد"
    assert disc_get("BND10")["used"] == 1
    assert disc_refund("BND10") is True
    disc_refund("BND10")
    assert disc_get("BND10")["used"] == 0, "used هرگز منفی نمی‌شود"
    disc_create("UNL99", 50, None, max_uses=0)
    assert disc_use("UNL99") is True and disc_use("UNL99") is True   # 0 یعنی نامحدود
    assert disc_create("BIG", 150, None) is not None
    assert disc_get("BIG")["percent"] == 100, "درصد بزرگ‌تر از ۱۰۰ به ۱۰۰ کلمپ می‌شود"
    assert disc_create("TINY", -5, None) is not None
    assert disc_get("TINY")["percent"] == 1, "درصد کوچک‌تر از ۱ به ۱ کلمپ می‌شود"


def test_channel_settings_numeric_clamps():
    """تنظیمات کانال: مقادیر بزرگ/کوچک به بازهٔ مجاز کلمپ می‌شوند (بدون سرریز و بدون مقدار نامعتبر)."""
    uid = 9107; _user(uid)
    cid = _chan(uid, 91070)
    big = {"ui_lang": "fa", "post_limit": 10**9, "posts_per_cycle": 10**9, "interval_minutes": 0, "lookback_hours": 10**9}
    q("UPDATE channels SET settings=? WHERE id=?", (json.dumps(big), cid), commit=True)
    s = get_settings(cid)
    assert s["post_limit"] == config.POST_LIMIT_MAX
    assert s["posts_per_cycle"] == config.MAX_PPC
    assert s["interval_minutes"] == config.MIN_INTERVAL
    assert s["lookback_hours"] == config.MAX_LOOKBACK
    q("UPDATE channels SET settings=? WHERE id=?", (json.dumps({"ui_lang": "fa", "post_limit": 100}), cid), commit=True)
    assert get_settings(cid)["post_limit"] == 300, "کف سخت کپشن ۳۰۰ کاراکتر است"
