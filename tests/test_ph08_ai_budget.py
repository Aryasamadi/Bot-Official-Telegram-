# -*- coding: utf-8 -*-
"""PH-08 — کنترلِ هزینه و درستیِ کشِ AI.

پوشش: PIPE-001 (بودجهٔ مقاله در میانه ریست نشود) · PIPE-003 (TTL در زمانِ خواندن) ·
PIPE-004 (محتوا در کلیدِ کش) · PIPE-006 (سقفِ روزانه به ازای هر فراخوانی) ·
PIPE-007 (مسیرِ تستِ مدل هم سهم می‌گیرد).
"""
import asyncio
import json
import time

import pytest

from newsbot import config
from newsbot.db import q
from newsbot.timeutils import iso_fixed, now_utc
from datetime import timedelta


@pytest.fixture(autouse=True)
def _clean_budget():
    from newsbot.ai_models import budget_reset
    budget_reset()
    yield
    budget_reset()


# ══════════════════════════════════════════════════════════════════════════════════════
# PIPE-001 — بودجهٔ یک مقاله در میانهٔ کار ریست نمی‌شود
# ══════════════════════════════════════════════════════════════════════════════════════
def _no_provider(monkeypatch):
    """هیچ فراخوانیِ واقعیِ شبکه نباید انجام شود."""
    import newsbot.ai_models as amod
    async def _stub(*a, **k):
        return None, None, "ai_none"
    monkeypatch.setattr(amod, "_try_models", _stub)


def test_article_budget_is_not_reset_in_the_middle_of_an_article(monkeypatch):
    """با بودجهٔ «سخت»، گذشتِ زمان نباید شمارندهٔ فراخوانیِ همان مقاله را صفر کند."""
    from newsbot.ai_models import ai_chat, budget_reset
    _no_provider(monkeypatch)
    budget_reset(hard=True)
    b = config.AI_BUDGET.get()
    b["t0"] = time.monotonic() - config.AI_ARTICLE_TIME_BUDGET * 3   # پنجره گذشته
    b["calls"] = config.AI_MAX_CALLS_PER_ARTICLE                       # سقف پر
    out, usage, err = asyncio.run(ai_chat("sys", "user"))
    assert err == "ai_budget", "بودجهٔ مقاله نباید در میانه ریست شود (وگرنه سقف بی‌اثر می‌شود)"


def test_non_article_calls_may_start_a_fresh_window(monkeypatch):
    """فراخوانیِ خارج از مسیرِ مقاله (مثلاً تستِ مدل) می‌تواند پنجرهٔ تازه بگیرد."""
    from newsbot.ai_models import ai_chat, budget_reset
    _no_provider(monkeypatch)
    budget_reset()                       # نرم (غیرمقاله)
    b = config.AI_BUDGET.get()
    b["t0"] = time.monotonic() - config.AI_ARTICLE_TIME_BUDGET * 3
    b["calls"] = config.AI_MAX_CALLS_PER_ARTICLE
    out, usage, err = asyncio.run(ai_chat("sys", "user"))
    assert err != "ai_budget", "برای فراخوانیِ غیرمقاله، پنجرهٔ تازه مجاز است"


# ══════════════════════════════════════════════════════════════════════════════════════
# PIPE-006 / PIPE-007 — سهمِ روزانه به ازای هر فراخوانیِ واقعیِ مدل
# ══════════════════════════════════════════════════════════════════════════════════════
def test_daily_cap_is_claimed_per_provider_call(monkeypatch):
    """وقتی سهمِ روزانه تمام است، فراخوانیِ مدل باید ai_limit بدهد (نه اینکه بی‌صدا رد شود)."""
    import newsbot.ai_models as amod
    monkeypatch.setattr(amod, "ai_claim", lambda uid: False)
    out, usage, err = asyncio.run(amod.ai_chat("sys", "user", owner_id=900001))
    assert out is None and err == "ai_limit"


def test_calls_without_an_owner_do_not_consume_the_quota(monkeypatch):
    """مسیرهای سیستمی (مثل probe) سهمِ کاربر را مصرف نمی‌کنند."""
    import newsbot.ai_models as amod
    called = []
    monkeypatch.setattr(amod, "ai_claim", lambda uid: called.append(uid) or True)
    monkeypatch.setattr(amod, "_try_models", lambda *a, **k: asyncio.sleep(0, result=(None, None, "ai_none")))
    out, usage, err = asyncio.run(amod.ai_chat("sys", "user"))
    assert not called, "فراخوانیِ بدونِ owner نباید از سهمِ روزانهٔ کسی کم کند"


def test_personal_model_test_path_consumes_the_quota(monkeypatch):
    """PIPE-007 — تستِ مدلِ شخصی هم هزینهٔ AI دارد و باید مشمولِ سقف باشد."""
    import newsbot.ai_models as amod
    monkeypatch.setattr(amod, "ai_claim", lambda uid: False)
    # همان فراخوانی‌ای که مسیرِ تستِ شخصی می‌زند (newsbot/ui_widgets.py :: run_model_test)
    out, usage, err = asyncio.run(amod.ai_chat("You are a health-check.", "Say: READY",
                                              owner_id=900002, selected_id=1, validate=None))
    assert err == "ai_limit", "تستِ مدلِ شخصی نباید از سقفِ روزانه فرار کند"


# ══════════════════════════════════════════════════════════════════════════════════════
# PIPE-003 — TTL در زمانِ خواندن
# ══════════════════════════════════════════════════════════════════════════════════════
def test_expired_cache_row_is_a_miss(monkeypatch):
    """رکوردِ کشِ منقضی نباید سرو شود (پاک‌سازیِ ساعتی ممکن است اجرا نشده باشد)."""
    import newsbot.generate as gmod
    from newsbot.generate import ai_cache_key
    settings = {"language": "fa", "topic": "t", "prompt": "p", "categories": [], "criteria": [], "max_words": 120, "post_limit": 900, "hashtags": True}
    art = {"url": "https://x.test/ttl", "title": "ttl", "text": "hello"}
    key = ai_cache_key(settings, art)
    old = iso_fixed(now_utc() - timedelta(days=config.AI_CACHE_TTL_DAYS + 5))
    q("INSERT OR REPLACE INTO ai_cache(key,value,created_at) VALUES(?,?,?)",
      (key, json.dumps({"j": {"post": "<b>STALE</b>"}, "model": "m"}), old), commit=True)

    called = []
    async def fake_chat(*a, **k):
        called.append(1)
        return None, None, "ai_none"
    monkeypatch.setattr(gmod, "ai_chat", fake_chat)
    j, model, err = asyncio.run(gmod.generate(settings, art))
    assert not (isinstance(j, dict) and j.get("post") == "<b>STALE</b>"), "کشِ منقضی نباید سرو شود"


# ══════════════════════════════════════════════════════════════════════════════════════
# PIPE-004 — محتوا در کلیدِ کش
# ══════════════════════════════════════════════════════════════════════════════════════
def test_cache_key_depends_on_the_article_text():
    """اگر متنِ مقاله عوض شود، کلیدِ کش هم عوض می‌شود (خروجیِ کهنه سرو نمی‌شود)."""
    from newsbot.generate import ai_cache_key
    s = {"language": "fa", "topic": "t", "prompt": "p", "categories": [], "criteria": [], "max_words": 120, "post_limit": 900, "hashtags": True}
    a = {"url": "https://x.test/same", "title": "same", "text": "نسخهٔ اول"}
    b = {"url": "https://x.test/same", "title": "same", "text": "نسخهٔ دومِ اصلاح‌شده"}
    assert ai_cache_key(s, a) != ai_cache_key(s, b), "محتوا باید در کلیدِ کش اثر داشته باشد"
    assert ai_cache_key(s, a) == ai_cache_key(s, dict(a)), "کلید باید پایدار باشد"
