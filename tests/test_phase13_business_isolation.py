# -*- coding: utf-8 -*-
"""فاز ۱۳ — تست ایزولهٔ منطق تجاری (BIZ-10).

داده/سهمیه/کیف پول هر مستأجر (ادمین، کانال، معرف) باید فقط روی خودش اثر بگذارد؛
هیچ عملیات یک ادمین/کانال/معرف نباید سهمیه، موجودی یا تنظیمات دیگری را تغییر دهد.
"""
from newsbot.core_articles import article_insert, reserve_publication
from newsbot.core_channels import add_channel, channel_by_chat, get_settings, update_settings
from newsbot.core_plans import assign_plan, create_plan
from newsbot.core_referrals import (credit_referral_on_payment, record_referral,
                                    referral_cfg, set_referral_cfg)
from newsbot.core_users import ai_claim, ai_used_today, ensure_user, usage_today
from newsbot.db import q

_J0 = {"phase": "prepared", "reserved_day": None, "test_day": None, "test_user": None}


def _user(uid): ensure_user(uid, f"u{uid}", f"u{uid}")


def _chan(uid, chat_id):
    add_channel(uid, chat_id, f"ch{chat_id}", "", 1)
    return channel_by_chat(chat_id)["id"]


def _wallet(uid):
    return round(float(q("SELECT credit FROM users WHERE id=?", (uid,), one=True)["credit"] or 0), 2)


def test_daily_post_cap_isolated_between_admins():
    """پر شدن سقف روزانهٔ ادمین A نباید سهمیهٔ ادمین B را مصرف کند."""
    a, b = 9201, 9202
    for u in (a, b):
        _user(u)
        assign_plan(u, create_plan(name=f"iso13-{u}", days=30, daily_posts=1, daily_tests=1, max_sources=2, max_channels=1))
    ca, cb = _chan(a, 92010), _chan(b, 92020)
    a1 = article_insert(a, ca, "h-iso13-a1", "https://x.test/iso13-a1", "t", 0, None, status="ready")
    a2 = article_insert(a, ca, "h-iso13-a2", "https://x.test/iso13-a2", "t", 0, None, status="ready")
    b1 = article_insert(b, cb, "h-iso13-b1", "https://x.test/iso13-b1", "t", 0, None, status="ready")
    assert reserve_publication(a1, a, True, None, _J0) is not None
    assert reserve_publication(a2, a, True, None, _J0) is None, "سقف A پر شده است"
    assert reserve_publication(b1, b, True, None, _J0) is not None, "سقف B نباید از A اثر بگیرد"
    assert usage_today(a)["posts"] == 1 and usage_today(b)["posts"] == 1


def test_ai_cap_isolated_between_admins():
    """مصرف AI یک ادمین نباید شمارنده یا سقف ادمین دیگر را تغییر دهد."""
    a, b = 9203, 9204
    _user(a); _user(b)
    assert ai_claim(a) is True
    assert ai_used_today(a) == 1
    assert ai_used_today(b) == 0, "مصرف AI یک ادمین نباید سهمیهٔ دیگری را مصرف کند"
    assert ai_claim(b) is True, "B باید مستقل از A از سقف خودش استفاده کند"


def test_wallet_credit_isolated_between_referrers():
    """شارژ کیف پول معرف مربوطه فقط روی همان معرف اثر دارد؛ کیف پول معرف/پرداخت‌کنندهٔ دیگر دست‌نخورده می‌ماند."""
    ra, ba, rb, bb = 9205, 9206, 9207, 9208
    for u in (ra, ba, rb, bb):
        _user(u)
    q("UPDATE users SET credit=0 WHERE id IN (?,?,?,?)", (ra, ba, rb, bb), commit=True)
    q("DELETE FROM referrals WHERE referred_id IN (?,?)", (ba, bb), commit=True)
    assert record_referral(ra, ba) is True and record_referral(rb, bb) is True
    p0, c0 = referral_cfg()
    try:
        set_referral_cfg(percent=10, cap_amount=0)
        assert credit_referral_on_payment(ba, 100.0, ref_key="p13:iso:1") == (ra, 10.0)
        assert _wallet(ra) == 10.0
        assert _wallet(rb) == 0.0, "کیف پول معرف دیگر نباید تغییر کند"
        assert _wallet(ba) == 0.0 and _wallet(bb) == 0.0, "کیف پول پرداخت‌کننده‌ها نباید شارژ شود"
        assert credit_referral_on_payment(bb, 100.0, ref_key="p13:iso:2") == (rb, 10.0)
        assert _wallet(ra) == 10.0 and _wallet(rb) == 10.0
    finally:
        set_referral_cfg(percent=p0, cap_amount=c0)


def test_channel_settings_isolated_between_channels():
    """تنظیمات یک کانال نباید به کانال دیگر (حتی با همان ادمین) نشت کند."""
    a, b = 9209, 9210
    _user(a); _user(b)
    ca, cb = _chan(a, 92090), _chan(b, 92100)
    update_settings(ca, post_limit=555, topic="موضوع-الف")
    assert get_settings(ca)["post_limit"] == 555 and get_settings(ca)["topic"] == "موضوع-الف"
    sb = get_settings(cb)
    assert sb["post_limit"] != 555 and sb["topic"] != "موضوع-الف", "تنظیمات یک کانال نباید به کانال دیگر نشت کند"
