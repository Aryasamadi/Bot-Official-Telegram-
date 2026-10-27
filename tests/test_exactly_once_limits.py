# -*- coding: utf-8 -*-
"""فاز ۵ — exactly-once انتشار و سقف‌ها (جدول ۶.۵).

کلیدی‌ترین رفتارها: هرگز پست تکراری (GAP-03/QA-01)، انتشار موازی = یک ارسال (GAP-07)،
شکست یک مقاله صف را نمی‌بندد (GAP-08)، RetryAfter رسانه را حفظ می‌کند (PUB-03)،
دیپ‌لینک فقط پس از موفقیت (PUB-04) و سقف روزانهٔ AI (BIZ-01).
"""
import asyncio, re
from datetime import timedelta

import pytest
from telegram.error import BadRequest, Forbidden, RetryAfter, TelegramError

from newsbot.core_articles import (article_insert, article_update, get_article, get_pub_journal,
                                   set_pub_journal, soft_window_candidate)
from newsbot.core_channels import add_channel, channel_by_chat
from newsbot.core_users import ensure_user, ai_claim, ai_cap_for, ai_used_today, usage_today
from newsbot.publish import publish_article, send_post
from newsbot import config


def _chan(chat_id):
    add_channel(1, chat_id, f"ch{chat_id}", "", 1)
    return channel_by_chat(chat_id)["id"]


def _ready(cid, h, title="t", post="<b>salam</b>", full=None):
    aid = article_insert(1, cid, h, f"https://x.test/{h}", title, 0, None, status="ready")
    article_update(aid, post_html=post, **({"full_html": full} if full else {}))
    return aid


class PubBot:
    """باتِ تستیِ انتشار: شمارش ارسال‌ها و خطای قابل‌تنظیم."""
    id = 999
    def __init__(self):
        self.sent = []        # (chat_id, text) پیام‌های موفق send_message
        self.attempted = []   # همهٔ متن‌های تلاش‌شده (حتی ناموفق)
        self.photos = []      # فراخوانی‌های send_photo
        self.fail_mode = None # "forbidden" | "retry_photo_once"
    async def get_chat_member(self, chat_id, user_id):
        return type("M", (), {"status": "administrator", "can_post_messages": True})()
    async def send_message(self, chat_id, text, **kw):
        self.attempted.append(text)
        if self.fail_mode == "forbidden":
            raise Forbidden("Forbidden: bot is not a member")
        self.sent.append((chat_id, text))
        return type("Msg", (), {"message_id": len(self.sent)})()
    async def send_photo(self, chat_id, photo, caption=None, parse_mode=None, **kw):
        self.photos.append((chat_id, photo, caption))
        if self.fail_mode == "retry_photo_once" and len(self.photos) == 1:
            raise RetryAfter(retry_after=1)
        return type("Msg", (), {"message_id": 100 + len(self.photos)})()


# ── PUB-01 / GAP-03 / QA-01: exactly-once ──

def test_unsettled_journal_without_media_never_reposts():
    """ژورنالِ unsettled بدون رسانه = تحویل نامعلوم: انتشار مجدد هرگز ارسال نمی‌کند."""
    cid = _chan(810)
    aid = _ready(cid, "h-p01-a")
    set_pub_journal(aid, {"phase": "unsettled", "admin_id": 1, "reserved_day": "2026-01-01",
                          "test_day": None, "test_user": None})
    bot = PubBot()
    ok, out = asyncio.run(publish_article(bot, aid))
    assert ok is False and out == "delivery_unknown"
    assert bot.sent == [] and bot.photos == []
    a = get_article(aid)
    assert a["status"] == "failed" and a["reason"] == "delivery_unknown"


def test_in_flight_journal_refused_and_quota_kept():
    """ژورنال in_flight (کرش پیش از بازیابی): رد انتشار + روزِ رزروشده حفظ می‌شود (PUB-05)."""
    cid = _chan(811)
    aid = _ready(cid, "h-p01-b")
    set_pub_journal(aid, {"phase": "in_flight", "admin_id": 1, "reserved_day": "2099-01-01",
                          "test_day": None, "test_user": None})
    bot = PubBot()
    ok, out = asyncio.run(publish_article(bot, aid))
    assert ok is False and out == "delivery_unknown"
    assert bot.sent == []
    j = get_pub_journal(aid)
    assert j is not None and j.get("phase") == "unsettled"
    assert j.get("reserved_day") == "2099-01-01", "سهمیه در ابهام تحویل برنمی‌گردد"


def test_unsettled_with_media_settles_without_resend():
    """unsettled با media_id: رسانه قبلاً تحویل شده — بدون ارسال دوباره settle می‌شود."""
    cid = _chan(812)
    aid = _ready(cid, "h-p01-c")
    set_pub_journal(aid, {"phase": "unsettled", "admin_id": 1, "reserved_day": None,
                          "test_day": None, "test_user": None, "media_id": 77, "text": "hello"})
    bot = PubBot()
    ok, out = asyncio.run(publish_article(bot, aid))
    assert ok is True
    assert bot.sent == [], "رسانه/کپشن قبلاً رفته؛ چیزی دوباره ارسال نمی‌شود"
    assert get_article(aid)["status"] == "published"


def test_soft_window_never_recycles_delivery_unknown():
    """PUB-01: بازچرخانی پنجرهٔ نرم مقالات delivery_unknown را رد می‌کند."""
    cid = _chan(813)
    aid = article_insert(1, cid, "h-sw-du", "https://x.test/sw1", "t", 0, None, status="failed", reason="delivery_unknown")
    assert soft_window_candidate(cid, -100123, "h-sw-du") is None
    article_update(aid, status="failed", reason="send_fails")
    assert soft_window_candidate(cid, -100123, "h-sw-du") == aid


# ── GAP-07: انتشار موازی ──

def test_parallel_publish_same_article_sends_once():
    """دو consumer هم‌زمان روی یک مقاله: دقیقاً یک ارسال."""
    cid = _chan(814)
    aid = _ready(cid, "h-par-1")
    bot = PubBot()
    async def main():
        return await asyncio.gather(publish_article(bot, aid), publish_article(bot, aid))
    r1, r2 = asyncio.run(main())
    assert sum(1 for ok, _ in (r1, r2) if ok) == 1
    assert len(bot.sent) == 1
    assert get_article(aid)["status"] == "published"


# ── GAP-08: قرنطینهٔ شکست ──

def test_failed_article_does_not_block_next():
    """شکست قطعیِ مقالهٔ اول drain را نمی‌شکند؛ مقالهٔ دوم در همان گذر منتشر می‌شود."""
    from newsbot.cycle import flush_ready
    cid = _chan(815)
    a1 = _ready(cid, "h-q-1", title="one", post="<b>one</b>")
    a2 = _ready(cid, "h-q-2", title="two", post="<b>two</b>")
    bot = PubBot()

    async def fail_once_then_ok(chat_id, text, **kw):
        bot.attempted.append(text)
        if "one" in text and not bot.sent:
            raise BadRequest("Message is too long")
        bot.sent.append((chat_id, text))
        return type("Msg", (), {"message_id": len(bot.sent)})()
    bot.send_message = fail_once_then_ok

    sent = asyncio.run(flush_ready(bot, 1, cid, max_n=2))
    assert sent >= 1
    assert get_article(a2)["status"] == "published", "مقالهٔ سالم نباید پشت مقالهٔ خراب بماند"
    assert int(get_article(a1)["send_fails"] or 0) >= 1
    assert get_article(a1)["status"] == "ready", "شکست اول فقط send_fails می‌شمارد، نه حذف"


# ── PUB-03: RetryAfter رسانه را حفظ می‌کند ──

def test_retryafter_on_photo_retries_media_not_text():
    bot = PubBot()
    bot.fail_mode = "retry_photo_once"
    media = {"kind": "photo", "url": "https://img.test/a.jpg"}
    msg = asyncio.run(send_post(bot, -1001, "<b>hello</b>", media))
    assert msg is not None
    assert len(bot.photos) == 2, "تلاش مجدد باید همان رسانه باشد"
    assert bot.sent == [], "هرگز به «متن تنها» تنزل نکرد"


# ── ISS-03: سازگاری RetryAfter با timedelta (PTB نسخه‌های جدیدتر) ──

class _RetryAfterValue(RetryAfter):
    """RetryAfter با مقدار retry_after دلخواه (عددی یا timedelta) برای تست مستقیم."""
    __slots__ = ("_value",)
    def __init__(self, value):
        TelegramError.__init__(self, "Flood control exceeded")
        self._value = value
    @property
    def retry_after(self): return self._value


class RetryBot:
    """باتِ تستی: N تلاش اول با RetryAfter رد می‌شود و بعدی موفق می‌شود."""
    id = 998
    def __init__(self, error, fail_times=1):
        self.error = error
        self.fail_times = fail_times
        self.attempts = 0
        self.sent = []
    async def get_chat_member(self, chat_id, user_id):
        return type("M", (), {"status": "administrator", "can_post_messages": True})()
    async def send_message(self, chat_id, text, **kw):
        self.attempts += 1
        if self.attempts <= self.fail_times: raise self.error
        self.sent.append((chat_id, text))
        return type("Msg", (), {"message_id": len(self.sent)})()


def _fake_sleep(monkeypatch):
    """sleep واقعی حذف و مدت‌های درخواستی ثبت می‌شود."""
    calls = []
    async def _sleep(seconds): calls.append(seconds)
    monkeypatch.setattr(asyncio, "sleep", _sleep)
    return calls


def test_retry_after_numeric_sleeps_and_retries(monkeypatch):
    """مقدار عددی: sleep برابر ثانیه+1 و یک تلاش مجدد همان مسیر."""
    sleeps = _fake_sleep(monkeypatch)
    bot = RetryBot(_RetryAfterValue(5))
    msg = asyncio.run(send_post(bot, -1001, "<b>hello</b>"))
    assert msg is not None
    assert sleeps == [6.0], "تأخیر باید از مقدار RetryAfter بیاید، نه از سقف ثابت"
    assert bot.attempts == 2 and len(bot.sent) == 1


def test_retry_after_timedelta_sleeps_and_retries(monkeypatch):
    """timedelta: total_seconds()+1 و همان یک تلاش مجدد."""
    sleeps = _fake_sleep(monkeypatch)
    bot = RetryBot(_RetryAfterValue(timedelta(seconds=7, microseconds=500000)))
    msg = asyncio.run(send_post(bot, -1001, "<b>hello</b>"))
    assert msg is not None
    assert sleeps == [8.5]
    assert bot.attempts == 2 and len(bot.sent) == 1


def test_retry_after_timedelta_with_real_ptb(monkeypatch):
    """حالت واقعی PTB (PTB_TIMEDELTA=1): retry_after از نوع timedelta برمی‌گردد."""
    monkeypatch.setenv("PTB_TIMEDELTA", "1")
    sleeps = _fake_sleep(monkeypatch)
    error = RetryAfter(retry_after=5)
    assert isinstance(error.retry_after, timedelta), "شبیه‌سازی محیط PTB نامعتبر است"
    bot = RetryBot(error)
    msg = asyncio.run(send_post(bot, -1001, "<b>hello</b>"))
    assert msg is not None
    assert sleeps == [6.0]
    assert bot.attempts == 2 and len(bot.sent) == 1


@pytest.mark.parametrize("value", [config.RETRY_AFTER_MAX - 1, timedelta(seconds=config.RETRY_AFTER_MAX - 1)])
def test_retry_after_just_below_cap_is_retried(monkeypatch, value):
    """مرز زیرِ سقف: wait == RETRY_AFTER_MAX مجاز است و sleep انجام می‌شود."""
    sleeps = _fake_sleep(monkeypatch)
    bot = RetryBot(_RetryAfterValue(value))
    msg = asyncio.run(send_post(bot, -1001, "<b>hello</b>"))
    assert msg is not None
    assert sleeps == [float(config.RETRY_AFTER_MAX)]
    assert bot.attempts == 2


@pytest.mark.parametrize("value", [config.RETRY_AFTER_MAX, timedelta(seconds=config.RETRY_AFTER_MAX), config.RETRY_AFTER_MAX + 1])
def test_retry_after_above_cap_reraises_without_sleep(monkeypatch, value):
    """مرز بالای سقف: همان استثنای اصلی re-raise می‌شود؛ نه sleep، نه تلاش مجدد."""
    sleeps = _fake_sleep(monkeypatch)
    error = _RetryAfterValue(value)
    bot = RetryBot(error)
    with pytest.raises(RetryAfter) as ei:
        asyncio.run(send_post(bot, -1001, "<b>hello</b>"))
    assert ei.value is error, "استثنای اصلی باید حفظ شود"
    assert sleeps == []
    assert bot.attempts == 1


@pytest.mark.parametrize("value, exc", [(None, TypeError), ("bad", ValueError)])
def test_retry_after_invalid_value_raises_without_sleep(monkeypatch, value, exc):
    """ورودی نامعتبر: بدون sleep و بدون تلاش مجدد، خطای تبدیل بالا می‌رود."""
    sleeps = _fake_sleep(monkeypatch)
    bot = RetryBot(_RetryAfterValue(value))
    with pytest.raises(exc):
        asyncio.run(send_post(bot, -1001, "<b>hello</b>"))
    assert sleeps == []
    assert bot.attempts == 1


def test_publish_article_retry_after_label_with_timedelta(monkeypatch):
    """مسیر انتشار: برچسب retry_after:N از ثانیه‌های timedelta ساخته می‌شود."""
    sleeps = _fake_sleep(monkeypatch)
    cid = _chan(818)
    aid = _ready(cid, "h-ra-td")
    bot = RetryBot(_RetryAfterValue(timedelta(seconds=200)), fail_times=99)
    ok, out = asyncio.run(publish_article(bot, aid))
    assert ok is False and out == "retry_after:200"
    assert sleeps == [], "مقدار بالای سقف نباید sleep کند"
    assert get_article(aid)["reason"] == "rate_limited"


def test_publish_article_retry_after_label_with_numeric(monkeypatch):
    """مسیر انتشار با مقدار عددی: همان برچسب و همان رفتار."""
    sleeps = _fake_sleep(monkeypatch)
    cid = _chan(819)
    aid = _ready(cid, "h-ra-num")
    bot = RetryBot(_RetryAfterValue(200), fail_times=99)
    ok, out = asyncio.run(publish_article(bot, aid))
    assert ok is False and out == "retry_after:200"
    assert sleeps == []
    assert get_article(aid)["reason"] == "rate_limited"


# ── PUB-04: دیپ‌لینک فقط پس از موفقیت ──

def test_deeplink_saved_only_after_success():
    cid = _chan(816)
    post = "<b>" + "x" * 100 + "</b>"
    full = post + " " + "y" * 250
    aid = _ready(cid, "h-dl-ok", post=post, full=full)
    bot = PubBot()
    ok, out = asyncio.run(publish_article(bot, aid))
    assert ok is True
    m = re.search(r"r_([A-Za-z0-9_\-]+)", bot.sent[0][1])
    assert m, "پست باید لینک «بیشتر» داشته باشد"
    from newsbot.db import q
    assert q("SELECT 1 FROM deeplinks WHERE key=?", (m.group(1),), one=True) is not None


def test_deeplink_not_saved_on_definitive_failure():
    cid = _chan(817)
    post = "<b>" + "x" * 100 + "</b>"
    full = post + " " + "y" * 250
    aid = _ready(cid, "h-dl-fail", post=post, full=full)
    bot = PubBot()
    bot.fail_mode = "forbidden"
    ok, out = asyncio.run(publish_article(bot, aid))
    assert ok is False and out.startswith("send_failed")
    m = re.search(r"r_([A-Za-z0-9_\-]+)", bot.attempted[0])
    assert m, "متن تلاش‌شده باید کلید دیپ‌لینک داشته باشد"
    from newsbot.db import q
    assert q("SELECT 1 FROM deeplinks WHERE key=?", (m.group(1),), one=True) is None, "شکست قطعی نباید ردیف یتیم بسازد"


# ── BIZ-01: سقف روزانهٔ AI ──

def test_ai_daily_cap_claims_until_limit():
    uid = 900001
    ensure_user(uid, "u1", "U1")
    old = config.AI_DAILY_FREE
    config.AI_DAILY_FREE = 2
    try:
        assert ai_cap_for(uid) == 2
        assert ai_claim(uid) is True
        assert ai_claim(uid) is True
        assert ai_claim(uid) is False, "سقف روزانه باید ادعای سوم را رد کند"
        assert ai_used_today(uid) == 2
        assert usage_today(uid)["ai"] == 2
    finally:
        config.AI_DAILY_FREE = old


def test_ai_cap_super_unlimited():
    ensure_user(1, "super", "S")
    old = config.AI_DAILY_FREE
    config.AI_DAILY_FREE = 1
    try:
        for _ in range(5):
            assert ai_claim(1) is True, "ابرادمین هرگز سقف AI ندارد"
    finally:
        config.AI_DAILY_FREE = old


def test_generate_blocked_by_ai_cap():
    """generate پشت سقف باید ai_limit بدهد، نه خطای مدل."""
    import newsbot.generate as gmod
    orig = gmod.ai_claim
    gmod.ai_claim = lambda uid: False
    try:
        j, model, err = asyncio.run(gmod.generate({"language": "fa"}, {"url": "https://x.test/z", "title": "z"}, owner_id=900001))
        assert j is None and err == "ai_limit"
    finally:
        gmod.ai_claim = orig


# ── PUB-6 (بسته-منسوخ): hard_html سقف را رعایت می‌کند ──

def test_hard_html_respects_hard_limit():
    """PUB-6 (بسته-منسوخ): hard_html با حاشیهٔ −۶۰/−۱۰۰ِ send_post هرگز از سقف تلگرام رد نمی‌شود.
    (خودِ hard_html ممکن است چند کاراکتر برای بستن تگ/« …» جلوتر برود؛ حاشیهٔ caller همین را جذب می‌کند.)"""
    from newsbot.text_format import hard_html
    t = "<b>" + "x" * 5000 + "</b>"
    assert len(hard_html(t, config.CAPTION_LIMIT - 60)) <= config.CAPTION_LIMIT
    assert len(hard_html(t, config.MSG_LIMIT - 100)) <= config.MSG_LIMIT
