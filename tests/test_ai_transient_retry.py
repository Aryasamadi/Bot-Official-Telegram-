# -*- coding: utf-8 -*-
"""P0 — پایداریِ مسیرِ تولیدِ محتوا با AI.

ریشهٔ خرابیِ تولید در تولیدِ واقعی:
``newsbot.net_http.FetchError`` (یک ``RuntimeError``) از ``fetch_post`` بیرون می‌پرید و
لایهٔ AI آن را نمی‌گرفت ⇒ کلِ چرخه می‌مرد و کاربر فقط «خطای موقت سرویس هوش مصنوعی» با
مرحلهٔ «شروع» می‌دید. علاوه بر آن، خطای گذرا (۴۲۹/۵xx/timeout) بدونِ تلاشِ دوباره
مدل را برای ده‌ها دقیقه از کار می‌انداخت و هیچ مدلِ جایگزینی هم امتحان نمی‌شد.

پوشش: (۱) خطای لایهٔ انتقال ⇒ هیچ استثنایی از AI بیرون نمی‌رود؛ (۲) تلاشِ محدود برای
خطای گذرا و عدمِ تکرار برای خطای دائمی؛ (۳) خاموشیِ کوتاه برای خطای گذرا؛
(۴) پاسخِ ناقص هرگز منتشر نمی‌شود؛ (۵) سقفِ زمانیِ درخواستِ AI از بودجهٔ fetchِ منبع
جداست؛ (۶) یک خطای غیرمنتظره در AI کلِ چرخه را نمی‌کشد.
"""
import asyncio
import json
from datetime import timedelta

import pytest

from newsbot import config
from newsbot import ai_models as amod
from newsbot.db import q
from newsbot.net_http import FetchError
from newsbot.timeutils import now_iso, now_utc, parse_dt


class FakeResp:
    """پاسخِ کمینهٔ provider (بدون هیچ تماس شبکه‌ای)."""

    def __init__(self, status=200, payload=None, text="", headers=None):
        self.status_code = status
        self._payload = payload
        self.text = text if text else (json.dumps(payload) if payload is not None else "")
        self.headers = headers or {}

    def json(self):
        if self._payload is None:
            raise ValueError("no json body")
        return self._payload


def _ok_payload(content="READY"):
    return {"choices": [{"message": {"content": content}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 2}}


def _err_payload(status, message="boom"):
    return {"error": {"message": message, "type": "invalid_request_error"}}


@pytest.fixture(autouse=True)
def _no_notify(monkeypatch):
    """اعلانِ تلگرامیِ مدیر در تست نباید شبکه را لمس کند."""
    async def _noop(*a, **k): return True
    monkeypatch.setattr(amod, "notify_super", _noop)
    monkeypatch.setattr(config, "AI_RETRY_BASE_SEC", 0.05)   # تأخیرِ retry در تست کوتاه است


@pytest.fixture
def model():
    """یک مدلِ عمومیِ فعال (کلیدش آزمایشی است و هرگز واقعی نیست)."""
    q("DELETE FROM ai_models WHERE name=?", ("p0-model",), commit=True)
    amod.add_model("https://provider.test/v1", "sk-test-not-a-real-key", "test-model", name="p0-model", priority=1)
    mid = q("SELECT id FROM ai_models WHERE name=?", ("p0-model",), one=True)["id"]
    yield mid
    q("DELETE FROM ai_models WHERE id=?", (mid,), commit=True)


def _calls(monkeypatch, seq):
    """‎_post‎ را با یک دنبالهٔ پاسخ/خطا جایگزین می‌کند و شمارنده می‌دهد."""
    box = {"n": 0}

    async def _fake(url, **kw):
        i = box["n"]; box["n"] += 1
        item = seq[i] if i < len(seq) else seq[-1]
        if isinstance(item, Exception): raise item
        return item
    monkeypatch.setattr(amod, "_post", _fake)
    return box


# ══════════════════════════════════════════════════════════════════════════════════════
# ۱) خطای لایهٔ انتقال نباید از AI بیرون بپرد (ریشهٔ خرابیِ تولید)
# ══════════════════════════════════════════════════════════════════════════════════════
def test_provider_timeout_does_not_escape_the_ai_layer(monkeypatch, model):
    """‎FetchError(504)‎ پیش‌تر کلِ چرخه را می‌کشت؛ حالا فقط همین مقاله شکست می‌خورد."""
    box = _calls(monkeypatch, [FetchError(504, "https://provider.test/v1/chat/completions")])
    out, label, err = asyncio.run(amod.ai_chat("sys", "user"))
    assert out is None and label is None, "خروجی نباید ساخته شود"
    assert err == "ai_timeout", f"کدِ خطا باید دقیق باشد، نه «خطای موقت»: {err}"
    assert box["n"] >= 2, "خطای گذرا باید حداقل یک‌بار دوباره امتحان شود"


def test_blocked_provider_host_is_not_retried(monkeypatch, model):
    """۴۰۳ِ دروازهٔ خروج دائمی است ⇒ تکرارِ بی‌فایده ممنوع."""
    box = _calls(monkeypatch, [FetchError(403, "https://provider.test/v1/chat/completions")])
    out, label, err = asyncio.run(amod.ai_chat("sys", "user"))
    assert out is None and err == "ai_auth"
    assert box["n"] == 1, "خطای دائمی نباید دوباره امتحان شود"


def test_unexpected_transport_error_does_not_escape(monkeypatch, model):
    """هر خطای انتقالیِ دیگر هم نباید استثنا را از AI بیرون ببرد."""
    _calls(monkeypatch, [RuntimeError("connection reset by peer")])
    try:
        out, label, err = asyncio.run(amod.ai_chat("sys", "user"))
    except Exception as e:
        pytest.fail(f"خطای انتقالی از AI بیرون پرید: {type(e).__name__}: {e}")
    assert out is None and err


# ══════════════════════════════════════════════════════════════════════════════════════
# ۲) تلاشِ محدود برای خطای گذرا / عدمِ تکرار برای خطای دائمی
# ══════════════════════════════════════════════════════════════════════════════════════
def test_transient_429_is_retried_and_then_succeeds(monkeypatch, model):
    """۴۲۹ (محدودیتِ نرخ) گذراست ⇒ باید دوباره امتحان شود و موفقیت ادامه دهد."""
    box = _calls(monkeypatch, [FakeResp(429, _err_payload(429, "Too Many Requests")),
                               FakeResp(200, _ok_payload("READY"))])
    out, label, err = asyncio.run(amod.ai_chat("You are a health-check.", "Say: READY"))
    assert err is None and out == "READY", f"پس از ۴۲۹ باید موفق می‌شد: {err}"
    assert box["n"] == 2, "دقیقاً یک تلاشِ دوباره"


def test_permanent_401_is_not_retried(monkeypatch, model):
    """۴۰۱ (کلیدِ غلط) دائمی است ⇒ هرگز تکرار نمی‌شود."""
    box = _calls(monkeypatch, [FakeResp(401, _err_payload(401, "invalid api key"))])
    out, label, err = asyncio.run(amod.ai_chat("sys", "user"))
    assert out is None and err == "ai_auth"
    assert box["n"] == 1, "خطای دائمی نباید دوباره امتحان شود"


def test_edge_404_plain_text_is_transient_but_json_404_is_permanent(monkeypatch, model):
    """صفحهٔ خطای لبه/پروکسی (متن ساده) گذراست؛ خطای ساختاریافتهٔ provider دائمی."""
    box = _calls(monkeypatch, [FakeResp(404, None, text="404 page not found\n"),
                               FakeResp(404, None, text="404 page not found\n"),
                               FakeResp(404, None, text="404 page not found\n")])
    asyncio.run(amod.ai_chat("sys", "user"))
    assert box["n"] > 1, "۴۰۴ِ متنی باید دوباره امتحان شود"

    box2 = _calls(monkeypatch, [FakeResp(404, _err_payload(404, "model not found"))])
    asyncio.run(amod.ai_chat("sys", "user"))
    assert box2["n"] == 1, "۴۰۴ِ JSON (مدل ناموجود) دائمی است"


def test_transient_retry_is_bounded(monkeypatch, model):
    """تعدادِ تلاشِ دوباره سقف دارد (بی‌نهایت تکرار ممنوع)."""
    monkeypatch.setattr(config, "AI_TRANSIENT_RETRIES", 2)
    box = _calls(monkeypatch, [FakeResp(503, _err_payload(503, "upstream unavailable"))])
    asyncio.run(amod.ai_chat("sys", "user"))
    assert box["n"] == 3, f"باید حداکثر ۳ تلاش باشد (۱ + ۲ تلاشِ دوباره)، شد: {box['n']}"


# ══════════════════════════════════════════════════════════════════════════════════════
# ۳) خاموشیِ مدل: خطای گذرا ⇒ خاموشیِ کوتاه (پیش‌تر ۲۰ دقیقه ⇒ توقفِ کلِ تولید)
# ══════════════════════════════════════════════════════════════════════════════════════
def _minutes_between(a, b):
    return round((parse_dt(a) - parse_dt(b)).total_seconds() / 60)


def test_transient_failure_backoff_is_short(monkeypatch, model):
    """پس از دو خطای گذرا، مدل باید ظرفِ چند دقیقه برگردد (نه ۲۰ دقیقه)."""
    monkeypatch.setattr(config, "AI_TRANSIENT_RETRIES", 0)
    q("UPDATE ai_models SET fail_count=1 WHERE id=?", (model,), commit=True)
    _calls(monkeypatch, [FetchError(504, "https://provider.test/v1/chat/completions")])
    asyncio.run(amod.ai_chat("sys", "user"))
    row = q("SELECT * FROM ai_models WHERE id=?", (model,), one=True)
    assert row["fail_count"] == 2
    mins = _minutes_between(row["next_try_after"], now_iso())
    assert 0 < mins <= config.MODEL_TRANSIENT_BACKOFF_MAX_MIN, \
        f"خاموشیِ موقت باید کوتاه باشد، شد: {mins} دقیقه"


def test_permanent_failure_backoff_stays_long(monkeypatch, model):
    """خطای دائمی (کلیدِ غلط) خاموشیِ طولانی دارد و تکرارِ بی‌نهایت نمی‌شود."""
    monkeypatch.setattr(config, "AI_TRANSIENT_RETRIES", 0)
    q("UPDATE ai_models SET fail_count=1 WHERE id=?", (model,), commit=True)
    _calls(monkeypatch, [FakeResp(401, _err_payload(401, "invalid api key"))])
    asyncio.run(amod.ai_chat("sys", "user"))
    row = q("SELECT * FROM ai_models WHERE id=?", (model,), one=True)
    assert row["fail_count"] == 2 and row["status"] == "down"
    mins = _minutes_between(row["next_try_after"], now_iso())
    assert mins >= 10, f"خطای دائمی باید خاموشیِ طولانی داشته باشد، شد: {mins} دقیقه"


# ══════════════════════════════════════════════════════════════════════════════════════
# ۴) پاسخِ ناقص هرگز منتشر نمی‌شود و پروایدرِ سالم down نمی‌شود
# ══════════════════════════════════════════════════════════════════════════════════════
def test_truncated_response_is_rejected_and_provider_is_not_marked_down(monkeypatch, model):
    """پاسخِ بریده‌شده (finish_reason=length) ⇒ محتوای ناقص منتشر نمی‌شود."""
    payload = {"choices": [{"message": {"content": json.dumps({"post": "<b>نیمه‌کاره</b>", "scores": {}})},
                            "finish_reason": "length"}]}
    _calls(monkeypatch, [FakeResp(200, payload)])
    out, label, err = asyncio.run(amod.ai_chat("sys", "user", validate=amod._validate_salvageable))
    assert out is None, "خروجیِ ناقص نباید به مسیرِ انتشار برسد"
    assert err == "ai_invalid", f"کدِ درست «خروجی نامعتبر» است، شد: {err}"
    row = q("SELECT * FROM ai_models WHERE id=?", (model,), one=True)
    assert row["fail_count"] == 0, "پروایدرِ سالم نباید به‌خاطرِ خروجیِ بد down شود"
    assert row["status"] == "ok"


def test_model_that_spends_its_whole_budget_on_reasoning_is_reported_precisely(monkeypatch, model):
    """مدلِ استدلالی که کلِ بودجهٔ توکنش را صرفِ تفکر کرده ⇒ کدِ «بودجهٔ توکن» (نه خطای مبهم)."""
    payload = {"choices": [{"message": {"content": "", "reasoning_content": "…" * 500}, "finish_reason": "length"}]}
    _calls(monkeypatch, [FakeResp(200, payload)])
    out, label, err = asyncio.run(amod.ai_chat("sys", "user", validate=amod._validate_salvageable))
    assert out is None and err == "ai_tokens", f"کدِ دقیق لازم است، شد: {err}"
    assert "token" in amod.ai_err_text(err, "en").lower()
    row = q("SELECT * FROM ai_models WHERE id=?", (model,), one=True)
    assert row["fail_count"] == 0, "این خطا از خودِ مدل است، نه از سلامتِ پروایدر"


def test_all_models_in_backoff_is_reported_as_temporary_not_as_missing_model(monkeypatch, model):
    """مدلِ موجود ولی در backoff ⇒ «موقت» (کاربر بداند برمی‌گردد)، نه «مدلی نیست»."""
    q("UPDATE ai_models SET status='down', next_try_after=? WHERE id=?",
      ((now_utc() + timedelta(minutes=30)).isoformat(), model), commit=True)
    box = _calls(monkeypatch, [FakeResp(200, _ok_payload("READY"))])
    out, label, err = asyncio.run(amod.ai_chat("sys", "user"))
    assert out is None and err == "ai_busy", f"خطای موقت باید گزارش شود، شد: {err}"
    assert box["n"] == 0, "مدلِ در backoff نباید صدا زده شود"


# ══════════════════════════════════════════════════════════════════════════════════════
# ۵) سقفِ زمانیِ درخواستِ AI از بودجهٔ fetchِ منبع جداست
# ══════════════════════════════════════════════════════════════════════════════════════
def test_ai_request_uses_the_provider_timeout_not_the_fetch_budget(monkeypatch, model):
    """پیش‌تر ‎FETCH_TOTAL_SEC=60‎ روی درخواستِ AI هم اعمال می‌شد ⇒ ۵۰۴ِ کاذب."""
    import newsbot.net_http as nh
    seen = {}

    async def _fake_fetch(url, **kw):
        seen.update(kw); return FakeResp(200, _ok_payload("READY"))
    monkeypatch.setattr(nh, "fetch_post", _fake_fetch)
    asyncio.run(amod.ai_chat("sys", "user"))
    assert "total_timeout" in seen, "درخواستِ AI باید سقفِ زمانیِ خودش را داشته باشد"
    assert seen["total_timeout"] > config.FETCH_TOTAL_SEC, \
        f"سقفِ AI نباید بودجهٔ fetchِ منبع باشد: {seen['total_timeout']} <= {config.FETCH_TOTAL_SEC}"
    assert seen["total_timeout"] <= config.AI_PROVIDER_TIMEOUT_SEC
    # سقفِ خواندن هم نباید زودتر از سقفِ کل بپرد (پیش‌تر ۱۲۰ ثانیهٔ ثابت بود)
    assert seen["timeout"].read >= seen["total_timeout"] - 1, \
        f"سقفِ خواندن ({seen['timeout'].read}) باید با سقفِ کل ({seen['total_timeout']}) هماهنگ باشد"


# ══════════════════════════════════════════════════════════════════════════════════════
# ۶) یک خطای غیرمنتظره در AI نباید کلِ چرخه را بکشد
# ══════════════════════════════════════════════════════════════════════════════════════
def test_cycle_survives_an_unexpected_ai_exception(monkeypatch):
    """اولین نامزد با استثنا می‌میرد ⇒ نامزدِ بعدی در همان چرخه پردازش می‌شود."""
    from types import SimpleNamespace
    from newsbot import cycle as cyc_mod
    from newsbot.core_channels import add_channel, channel_by_chat
    from newsbot.core_sources import add_source
    from newsbot.timeutils import now_iso

    chat_id = -100950011
    add_channel(1, chat_id, "p0chan", "", 1)
    cid = channel_by_chat(chat_id)["id"]
    add_source(1, cid, "https://src.test/p0.xml")

    class Bot:
        id = 999
        async def get_chat_member(self, chat_id, user_id):
            return SimpleNamespace(status="administrator", can_post_messages=True)
        async def send_message(self, chat_id, text, **kw):
            return SimpleNamespace(message_id=1)

    async def disc(src, limit=25, use_cache=True, tz=None):
        return [{"url": "https://src.test/a", "title": "خبر یک", "published": now_iso(), "html": "", "media": None},
                {"url": "https://src.test/b", "title": "خبر دو", "published": now_iso(), "html": "", "media": None}], True, "feed"

    async def extract(url, fallback_html="", tz=None):
        return {"title": "خبر", "url": url, "text": "متن خبری قابل استخراج برای تولید محتوا. " * 6,
                "published": now_iso(), "media": None, "author": None, "site": None, "next": ""}

    seen = {"n": 0}

    async def gen(s, art, on_queue=None, owner_id=None):
        seen["n"] += 1
        if seen["n"] == 1: raise RuntimeError("boom in ai path")
        gen = {"post": "<b>تیتر</b>\n\n" + ("متن کامل این پست برای انتشار. " * 8),
               "full": None, "title": "تیتر", "category": s["categories"][0]["name"],
               "scores": {c["name"]: 9 for c in s["criteria"]}, "score": 90.0}
        return gen, "m9", None

    monkeypatch.setattr(cyc_mod, "discover_source", disc)
    monkeypatch.setattr(cyc_mod, "extract_article", extract)
    monkeypatch.setattr(cyc_mod, "generate", gen)
    res = asyncio.run(cyc_mod.run_channel_cycle(Bot(), 1, cid, test_mode=True))
    assert res["published"] == 1, "خطای یک نامزد نباید کلِ چرخه را از کار بیندازد"
    assert res["errors"] >= 1


# ══════════════════════════════════════════════════════════════════════════════════════
# ۷) رازها در لاگ نریزند
# ══════════════════════════════════════════════════════════════════════════════════════
def test_api_key_never_appears_in_the_logs(monkeypatch, model, caplog):
    """کلیدِ API نباید در لاگِ خطای پروایدر دیده شود."""
    secret = "sk-test-not-a-real-key"
    _calls(monkeypatch, [FakeResp(401, _err_payload(401, "invalid api key"))])
    with caplog.at_level("DEBUG"):
        asyncio.run(amod.ai_chat("sys", "user"))
    assert secret not in caplog.text
