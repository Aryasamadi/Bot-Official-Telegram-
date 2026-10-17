# -*- coding: utf-8 -*-
"""تست‌های واحد/یکپارچه‌ی هسته: زمان، فرمت، امنیت، پرداخت، ارجاع، صف، بودجهٔ AI."""
import asyncio
from datetime import timedelta

from newsbot import config
from newsbot.db import q
from newsbot.timeutils import parse_dt, now_utc, now_iso
from newsbot.text_format import sanitize_html, split_post_html, clean_ai_text, strip_source_url, fit_html
from newsbot.generate import ensure_quote
from newsbot.logutils import redact
from newsbot.net_http import is_private_host, public_url
from newsbot.core_discounts import disc_create, disc_use, disc_refund, disc_valid
from newsbot.core_plans import (create_plan, assign_plan, upgrade_quote, proration_remaining_days,
                                recover_stuck_payments)
from newsbot.core_payments import pay_create, pay_claim, pay_get
from newsbot.core_referrals import record_referral, credit_referral_on_payment, referral_stats, referral_cfg
from newsbot.core_users import channel_posts_today
from newsbot.core_channels import add_channel
from newsbot.ai_models import _budget, budget_reset, parse_json
from newsbot.adfilter import heuristic_ad_check
from newsbot.ui_i18n import reason_text


# ── زمان و فرمت ──
def test_parse_dt_formats():
    assert parse_dt("2026-10-08T12:00:00Z").year == 2026
    assert parse_dt("Sun, 27 Sep 2026 16:30:00 +0000") is not None
    assert parse_dt("") is None and parse_dt("not-a-date") is None

def test_sanitize_html_blocks_script():
    out = sanitize_html("<b>ok</b><script>alert(1)</script><a href='javascript:x'>j</a>")
    assert "<script" not in out and "javascript:" not in out and "<b>ok</b>" in out

def test_split_post_html_keeps_quote_in_head():
    text = "<b>T</b>\n\npara one " + "x" * 700 + "\n\n<blockquote>KEY</blockquote>\n\nend"
    head, rest = split_post_html(text, 400)
    assert "<blockquote>" in head and "KEY" in head

def test_clean_ai_text_removes_score_dict():
    out = clean_ai_text("متن خبر\nارزش محتوایی : ۹ ، غیرتبلیغاتی بودن : 8")
    assert "ارزش محتوایی" not in out and "متن خبر" in out

def test_strip_source_url_removes_only_source():
    t = 'خبر <a href="https://news.example.com/a">منبع</a> و <a href="https://other.org/x">دیگر</a>'
    out = strip_source_url(t, "https://news.example.com/a")
    assert "news.example.com" not in out and "other.org" in out

def test_ensure_quote_and_fit():
    assert "<blockquote>" in ensure_quote("<b>T</b>\n\nparagraph one here\n\nparagraph two here")
    txt, cut = fit_html("y" * 5000, 1000)
    assert cut is True and len(txt) <= 1000

# ── امنیت ──
def test_redact_masks_bot_token_and_keys():
    s = redact("err BOT_TOKEN=123456789:AAabcdef and sk-proj-abcdef123456 and Bearer xyzxyzxyzxyz")
    assert "123456789:AA" not in s and "sk-proj-" not in s and "xyzxyzxyzxyz" not in s

def test_private_hosts_rejected():
    for h in ("localhost", "127.0.0.1", "10.0.0.5", "192.168.1.1", "169.254.169.254", "::1", "0.0.0.0", "x.internal"):
        assert is_private_host(h) is True
    assert is_private_host("example.com") is False
    assert public_url("http://127.0.0.1/admin") is False

def test_reason_text_never_leaks_secrets():
    out = reason_text("ai: HTTP 500 sk-abcdef123456789", "fa")
    assert "sk-" not in out and "500" not in out

# ── تخفیف / پرداخت / ارجاع ──
def test_discount_atomic_use_and_refund():
    code = disc_create("TST10", 10, (now_utc() + timedelta(days=1)).isoformat(), max_uses=1)
    assert disc_use(code) is True and disc_use(code) is False
    assert disc_refund(code) is True and disc_valid(code)[1] == "ok"

def test_payment_claim_cas_and_stuck_recovery():
    rid = pay_create(777001, 1, note="t")
    assert pay_claim(rid) is True
    assert pay_claim(rid) is False                      # CAS: دومین تأیید برنده نمی‌شود
    q("UPDATE pay_requests SET claimed_at=? WHERE id=?", ((now_utc() - timedelta(minutes=config.PAY_STUCK_MIN + 1)).isoformat(), rid), commit=True)
    assert recover_stuck_payments() >= 1
    assert pay_get(rid)["status"] == "pending"          # پرداخت گیرکرده بازیابی شد

def test_referral_rules():
    # referrer (۱) باید واقعاً در جدول کاربران وجود داشته باشد؛ وگرنه record_referral
    # طبق قاعدهٔ «معرف باید وجود داشته باشد» درست رد می‌کند و تست بی‌جهت شکست می‌خورد.
    q("INSERT OR IGNORE INTO users(id,username,name,role,created_at,last_seen) VALUES(?,?,?,?,?,?)", (1, "ref", "Ref", "user", now_iso(), now_iso()), commit=True)
    assert record_referral(1, 1) is False                # self-referral ممنوع
    assert record_referral(88001, 88002) is False        # معرف وجود ندارد → رد (fail-closed)
    q("INSERT OR IGNORE INTO users(id,username,name,role,created_at,last_seen) VALUES(?,?,?,?,?,?)", (88001, "r", "R", "user", now_iso(), now_iso()), commit=True)
    assert record_referral(88001, 88002) is True
    assert record_referral(88003, 88002) is False        # انتساب یکتا
    pct = referral_cfg()[0]
    rid, amt = credit_referral_on_payment(88002, 100.0, ref_key="pay:1")
    assert rid == 88001 and amt == round(100.0 * pct / 100, 2)
    assert credit_referral_on_payment(88002, 100.0, ref_key="pay:1") is None   # همان پرداخت ⇒ idempotent
    rid2, amt2 = credit_referral_on_payment(88002, 50.0, ref_key="pay:2")      # REF-3: پاداش هر خرید
    assert rid2 == 88001 and amt2 == round(50.0 * pct / 100, 2)
    assert referral_stats(88001)[0] == 1
    assert round(referral_stats(88001)[1], 2) == round(amt + amt2, 2)

def test_upgrade_quote_proration():
    pid = create_plan(name="P1", days=30, price_num=30)
    uid = 99001
    q("INSERT OR IGNORE INTO users(id,username,name,role,created_at,last_seen) VALUES(?,?,?,?,?,?)", (uid, "u", "U", "admin", now_iso(), now_iso()), commit=True)
    assign_plan(uid, pid)
    assert proration_remaining_days(uid) > 29
    qt = upgrade_quote(uid, get_plan_safe(pid), get_plan_safe(pid))
    assert qt["kind"] == "upgrade" and qt["payable"] >= 0

def get_plan_safe(pid):
    from newsbot.core_plans import get_plan
    return get_plan(pid)

# ── صف / سهمیه / بودجه ──
def test_queue_backpressure_configured():
    assert config.MAX_QUEUE_SIZE >= 1

def test_channel_posts_today_offset_aware():
    cid = add_channel(1, -100999888777, "T", "", 1)
    ch = q("SELECT chat_id FROM channels WHERE id=?", (cid,), one=True)
    q("INSERT OR IGNORE INTO posted VALUES(?,?,?)", (ch["chat_id"], "h1", now_iso()), commit=True)
    q("INSERT OR IGNORE INTO posted VALUES(?,?,?)", (ch["chat_id"], "h2", (now_utc() - timedelta(hours=40)).isoformat()), commit=True)
    assert channel_posts_today(cid, 3.5) == 1

def test_ai_budget_isolated_per_task():
    # ContextVar هر تسک را جدا نگه می‌دارد؛ دو تسک هم‌زمان نباید بودجهٔ هم را ببینند.
    # (نسخهٔ قبلی asyncio.run(asyncio.gather(...)) بود که ValueError می‌داد: gather یک Future است نه coroutine.)
    async def one():
        budget_reset()               # آغاز تازه برای همین تسک (ContextVar)
        b = _budget(); b["calls"] += 5
        await asyncio.sleep(0)       # اجازهٔ interleave تا جداسازی واقعاً آزموده شود
        return _budget()["calls"]
    async def main():
        return await asyncio.gather(one(), one())
    assert asyncio.run(main()) == [5, 5]   # بودجهٔ دو تسک هم‌زمان با هم تداخل ندارد

def test_parse_json_multiline_strings():
    j = parse_json('{"post": "line1\nline2", "scores": {"a": 5}}')
    assert j and j["post"].startswith("line1")

def test_ad_filter_does_not_reject_clean_news():
    art = {"text": "نشست اقتصادی امروز برگزار شد و گزارش‌ها نشان می‌دهد تولید داخلی رشد داشته است. " * 8,
           "title": "گزارش نشست اقتصادی", "url": "https://example.com/news/1234"}
    is_ad, why, score = heuristic_ad_check(art)
    assert is_ad is False
