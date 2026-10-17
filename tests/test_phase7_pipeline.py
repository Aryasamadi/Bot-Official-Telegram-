# -*- coding: utf-8 -*-
"""فاز ۷ — تست‌های خط لوله: QA-09 (شبکه) · PLB-01/02/03 (قرارداد و کش AI) · PLB-06/09 (چرخه) ·
PLB-08/10 (کشف و dedupe) · ARCH-02 (ددلاین کل) · BIZ-06 (شمارش پست کانال در گزارش).
همه آفلاین: fetch/http/ai_chat/discover/extract با monkeypatch جایگزین می‌شوند."""
import asyncio, json, time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import httpx
import pytest

from newsbot import config
from newsbot import cycle as cyc_mod
from newsbot import discover as disc
from newsbot import generate as gen_mod
from newsbot import net_http as nh
from newsbot import publish as pub_mod
from newsbot.ai_models import AIResponseError, _record_usage, _usage_dict, _validate_salvageable, tokens_today
from newsbot.core_articles import get_article, soft_window_candidate
from newsbot.core_channels import add_channel, channel_by_chat, get_settings, update_settings
from newsbot.core_sources import add_source
from newsbot.core_users import channel_posts_today, remaining
from newsbot.db import q
from newsbot.net_http import FetchError, retry_after_seconds
from newsbot.scheduler import report_text
from newsbot.timeutils import now_iso


def _chan(chat_id):
    add_channel(1, chat_id, f"p7{chat_id}", "", 1)
    return channel_by_chat(chat_id)["id"]


class PubBot:
    id = 999
    def __init__(self): self.sent = []
    async def get_chat_member(self, chat_id, user_id):
        return SimpleNamespace(status="administrator", can_post_messages=True)
    async def send_message(self, chat_id, text, **kw):
        self.sent.append((chat_id, text))
        return SimpleNamespace(message_id=len(self.sent))


# ── PLB-08: canonical‌سازی آدرس و dedupe چندURL-یک‌مقاله ──

def test_clean_url_canonicalizes_tracking_variants():
    a = disc.clean_url("https://www.Example.com/News/story?utm_source=x&fbclid=1&id=7#section")
    b = disc.clean_url("https://example.com/News/story?id=7&utm_medium=y")
    c = disc.clean_url("https://example.com:443/News/story/amp?id=7")
    assert a == b == c == "https://example.com/News/story?id=7", "سه نشرِ مختلفِ یک خبر باید یک canonical واحد بدهند"
    assert "utm" not in a and "fbclid" not in a


def test_feed_items_dedupe_same_story_across_urls():
    e1 = {"link": "https://www.site.test/news/1?utm_source=a", "title": "خبر یک", "summary": ""}
    e2 = {"link": "https://site.test/news/1/amp", "title": "خبر یک", "summary": ""}
    items = disc._feed_items(SimpleNamespace(entries=[e1, e2]), "https://site.test/", 10, "Asia/Tehran")
    assert len(items) == 1, "دو آدرسِ معادل نباید دو آیتم شوند"


# ── PLB-10: احترام به Retry-After در کشف خوراک ──

class _Resp:
    def __init__(self, code, ra=None):
        self.status_code = code
        self.headers = {"retry-after": ra} if ra else {}
        self.text = ""
        self.content = b""


def test_get_honors_retry_after_with_cap(monkeypatch):
    calls = {"n": 0}
    async def fake_fetch(url, headers=None, **kw):
        calls["n"] += 1
        return _Resp(429, "7") if calls["n"] == 1 else _Resp(200)
    slept = []
    async def fake_sleep(x): slept.append(x)
    monkeypatch.setattr(disc, "fetch", fake_fetch)
    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    r = asyncio.run(disc._get("https://feed.test/rss"))
    assert r.status_code == 200 and calls["n"] == 2
    assert slept == [min(7, config.FEED_RETRY_AFTER_MAX)], "برای 429 باید Retry-After سرور (با سقف) رعایت شود"


def test_get_403_retries_immediately(monkeypatch):
    calls = {"n": 0}
    async def fake_fetch(url, headers=None, **kw):
        calls["n"] += 1
        return _Resp(403) if calls["n"] == 1 else _Resp(200)
    slept = []
    async def fake_sleep(x): slept.append(x)
    monkeypatch.setattr(disc, "fetch", fake_fetch)
    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    assert asyncio.run(disc._get("https://feed.test/rss")).status_code == 200
    assert slept == [], "۴۰۳ فیلتر محتوایی است، نه نرخ — تلاش دوم فوری می‌ماند"


def test_retry_after_seconds_parses_seconds_and_http_date():
    assert retry_after_seconds("7") == 7.0
    for bad in ("", None, "0", "-5", "soon"):
        assert retry_after_seconds(bad) is None
    when = (datetime.now(timezone.utc) + timedelta(seconds=30)).strftime("%a, %d %b %Y %H:%M:%S GMT")
    val = retry_after_seconds(when)
    assert val is not None and 25 <= val <= 31, "قالب HTTP-date باید پشتیبانی شود"
    past = (datetime.now(timezone.utc) - timedelta(seconds=30)).strftime("%a, %d %b %Y %H:%M:%S GMT")
    assert retry_after_seconds(past) is None


# ── ARCH-02: ددلاین کلِ fetch و دانلود رسانه ──

def test_fetch_total_deadline_raises_504(monkeypatch):
    class CM:
        async def __aenter__(self): await asyncio.sleep(2); return None
        async def __aexit__(self, *a): return False
    class FC:
        def stream(self, *a, **k): return CM()
    monkeypatch.setattr(nh, "public_url_strict", lambda u: True)
    monkeypatch.setattr(nh, "http", lambda: FC())
    monkeypatch.setattr(config, "FETCH_TOTAL_SEC", 0.05)
    with pytest.raises(FetchError) as ei:
        asyncio.run(nh.fetch("https://slow.test/a"))
    assert ei.value.status == 504, "سرورِ کندپاسخ باید 504 بگیرد، نه قفل بی‌نهایت"


class _NeverResp:
    status_code = 200
    headers = {"content-type": "video/mp4", "content-length": "0"}
    async def aclose(self): pass
    async def aiter_bytes(self, n):
        await asyncio.sleep(30)
        yield b"x"


class _NeverStream:
    async def __aenter__(self): return _NeverResp()
    async def __aexit__(self, *a): return False


class _MediaClient:
    def stream(self, *a, **k): return _NeverStream()


def test_media_download_total_deadline(monkeypatch):
    async def no_sleep(_): pass
    monkeypatch.setattr(pub_mod, "public_url_strict", lambda u: True)
    monkeypatch.setattr(pub_mod, "http", lambda: _MediaClient())
    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    monkeypatch.setattr(config, "MEDIA_TOTAL_SEC", 0.05)
    t0 = time.monotonic()
    out = asyncio.run(pub_mod.download_media({"url": "https://v.test/a.mp4", "kind": "video"}))
    assert out is None, "بدنهٔ آهسته نباید بیشتر از سقف کل هزینه بگیرد"
    assert time.monotonic() - t0 < 5


# ── PLB-01: قرارداد نرم و نجات خروجی ──

def test_validate_salvageable_soft_contract():
    ok_text = '{"post": "متن سالم خبر", "category": "خبری"}'
    assert _validate_salvageable(ok_text) == ok_text, "نبود scores در قرارداد نرم مجاز است"
    prose = "یک متن prose بدون JSON"
    assert _validate_salvageable(prose) == prose
    for bad in ('{"post": ""}', '{"broken": ', '{"error": "x", "post": "t"}'):
        with pytest.raises(AIResponseError):
            _validate_salvageable(bad)


def _art(tag, text="متن کوتاه خبر."):
    return {"url": f"https://news.test/{tag}", "title": f"تیتر {tag}", "text": text, "media": None, "published": now_iso()}


def test_generate_salvages_prose_with_neutral_scores(monkeypatch):
    cid = _chan(-100920001); s = get_settings(cid)
    async def fake_chat(*a, **k): return ("این یک متن کامل خبری بدون هیچ JSON است که باید نجات پیدا کند.", "m1", None)
    monkeypatch.setattr(gen_mod, "ai_chat", fake_chat)
    art = _art("salvage-1")
    j, model, err = asyncio.run(gen_mod.generate(s, art))
    assert err is None and model == "m1" and j["post"]
    assert j["scores"] == {c["name"]: 5.0 for c in s["criteria"]}, "نبود امتیاز ⇒ امتیاز خنثی ۵/۱۰"
    assert j["score"] == 50.0
    assert q("SELECT 1 FROM ai_cache WHERE key=?", (gen_mod.ai_cache_key(s, art),), one=True) is None, "خروجی salvage کش نمی‌شود"


def test_generate_caches_valid_output(monkeypatch):
    cid = _chan(-100920002); s = get_settings(cid)
    valid = json.dumps({"category": "خبری", "title": "تیتر", "post": "<b>سالم</b> " + "متن خبری " * 40,
                        "scores": {c["name"]: 9 for c in s["criteria"]}, "full": None}, ensure_ascii=False)
    calls = {"n": 0}
    async def fake_chat(*a, **k):
        calls["n"] += 1
        return (valid, "m2", None)
    monkeypatch.setattr(gen_mod, "ai_chat", fake_chat)
    art = _art("cache-1")
    j1, m1, e1 = asyncio.run(gen_mod.generate(s, art))
    assert e1 is None and calls["n"] == 1 and j1["post"].startswith("<b>سالم</b>")
    j2, m2, e2 = asyncio.run(gen_mod.generate(s, art))
    assert e2 is None and calls["n"] == 1, "اجرای دوباره باید از کش بیاید"
    assert j2["post"] == j1["post"]


# ── PLB-02: کش خلاصهٔ تکه ──

def test_chunk_cache_avoids_second_summarization(monkeypatch):
    full = "".join(f"پاراگراف شماره {i} " + "م" * 60 + "\n" for i in range(300))
    calls = {"n": 0}
    async def fake_chat(*a, **k):
        calls["n"] += 1
        return (f"خلاصه {calls['n']}", "m3", None)
    monkeypatch.setattr(gen_mod, "ai_chat", fake_chat)
    s = {"language": "فارسی"}
    merged1, err1 = asyncio.run(gen_mod._summarize_chunks(s, full))
    n_first = calls["n"]
    assert err1 is None and merged1 and n_first >= 2, "متن بزرگ باید به چند تکه بشکند"
    merged2, err2 = asyncio.run(gen_mod._summarize_chunks(s, full))
    assert err2 is None and merged2 == merged1 and calls["n"] == n_first, "تکه‌های کش‌شده نباید دوباره به مدل بروند"


# ── PLB-03: متریک مصرف توکن ──

def test_usage_dict_and_tokens_today_delta():
    assert _usage_dict({"prompt_tokens": 12, "completion_tokens": 7}, "prompt_tokens", "completion_tokens") == {"pt": 12, "ct": 7}
    assert _usage_dict({"input_tokens": 5}, "input_tokens", "output_tokens") == {"pt": 5, "ct": 0}
    assert _usage_dict({}, "a", "b") is None and _usage_dict(None, "a", "b") is None
    before = tokens_today()
    _record_usage(9991, {"pt": 10, "ct": 5})
    _record_usage(9991, {"pt": 2, "ct": 3})
    after = tokens_today()
    assert after["calls"] - before["calls"] == 2
    assert after["prompt"] - before["prompt"] == 12
    assert after["completion"] - before["completion"] == 8
    assert "🧮" in report_text(None, None, "fa"), "گزارش سوپر باید خط توکن را نشان دهد"


# ── چرخه: PLB-06 / PLB-09 / QA-09 ──

def _run_cycle(monkeypatch, cid, discover_impl, extract_impl):
    monkeypatch.setattr(cyc_mod, "discover_source", discover_impl)
    monkeypatch.setattr(cyc_mod, "extract_article", extract_impl)
    monkeypatch.setattr(cyc_mod, "mandatory_configured", lambda lang="fa": False)
    return asyncio.run(cyc_mod.run_channel_cycle(PubBot(), 1, cid, test_mode=True))


def _item(url="https://src.test/news/e1"):
    return {"url": url, "title": "خبر تست", "published": now_iso(), "html": "", "media": None}


def test_extract_failure_is_retryable_failed_not_rejected(monkeypatch):
    cid = _chan(-100930001); chat = -100930001
    add_source(1, cid, "https://src.test/feed.xml")
    async def disc_ok(src, limit=25, use_cache=True, tz=None): return [_item()], True, "feed"
    async def extract_none(url, fallback_html="", tz=None): return None
    res = _run_cycle(monkeypatch, cid, disc_ok, extract_none)
    a = q("SELECT * FROM articles WHERE channel_id=? ORDER BY id DESC LIMIT 1", (cid,), one=True)
    assert a["status"] == "failed" and a["reason"] == "extract_failed", "شکست استخراج باید «ناموفقِ قابل‌تلاش» باشد، نه «ردشدهٔ» قطعی"
    assert res["errors"] >= 1
    assert soft_window_candidate(cid, chat, a["hash"]) == a["id"], "باید از پنجرهٔ نرم قابل بازچرخانی باشد"
    s = get_settings(cid)
    assert s["last_run"] and s["last_end"], "چرخه‌ای که خط لوله را آغاز کرد باید last_run بگیرد"


def test_cycle_without_sources_never_touches_last_run(monkeypatch):
    cid = _chan(-100930002)
    monkeypatch.setattr(cyc_mod, "mandatory_configured", lambda lang="fa": False)
    asyncio.run(cyc_mod.run_channel_cycle(PubBot(), 1, cid, test_mode=True))
    s = get_settings(cid)
    assert s["last_run"] is None, "خروج زودهنگام (بدون منبع) نباید last_run را جلو ببرد"
    assert s["last_end"], "اما پایان و نتیجهٔ چرخه ثبت می‌شود"


def test_connect_error_in_one_source_does_not_stop_cycle(monkeypatch):
    cid = _chan(-100930004)
    add_source(1, cid, "https://bad.test/feed.xml")
    add_source(1, cid, "https://good.test/feed.xml")
    async def disc(src, limit=25, use_cache=True, tz=None):
        if "bad" in src["url"]: raise httpx.ConnectError("SSL certificate verify failed")
        return [_item("https://good.test/news/1")], True, "feed"
    async def extract_none(url, fallback_html="", tz=None): return None
    res = _run_cycle(monkeypatch, cid, disc, extract_none)
    assert res["errors"] >= 1, "خطای شبکهٔ یک منبع باید شمرده شود"
    assert res["found"] >= 1, "منبع سالم دیگر باید در همان چرخه ادامه دهد"


def test_cycle_publishes_and_sets_last_run(monkeypatch):
    cid = _chan(-100930003)
    add_source(1, cid, "https://src.test/feed2.xml")
    async def disc_ok(src, limit=25, use_cache=True, tz=None): return [_item()], True, "feed"
    async def extract_ok(url, fallback_html="", tz=None):
        return {"title": "خبر سالم", "url": url, "text": "این یک متن خبری سالم و کامل برای تولید است. " * 5,
                "published": now_iso(), "media": None, "author": None, "site": None, "next": ""}
    async def gen_ok(s, art, on_queue=None, owner_id=None):
        gen = {"post": "<b>تیتر خبر</b>\n\n" + ("متن کامل این پست برای انتشار در کانال است. " * 6),
               "full": None, "title": "تیتر خبر", "category": s["categories"][0]["name"],
               "scores": {c["name"]: 9 for c in s["criteria"]}, "score": 90.0}
        return gen, "m9", None
    monkeypatch.setattr(cyc_mod, "generate", gen_ok)
    res = _run_cycle(monkeypatch, cid, disc_ok, extract_ok)
    assert res["published"] == 1 and res["accepted"] == 1
    a = q("SELECT * FROM articles WHERE channel_id=? ORDER BY id DESC LIMIT 1", (cid,), one=True)
    assert a["status"] == "published" and get_article(a["id"])["status"] == "published"
    assert get_settings(cid)["last_run"], "چرخهٔ موفق هم last_run می‌گیرد"


# ── BIZ-06: شمارش پست‌های کانال در گزارش و سقف روزانه ──

def test_channel_report_counts_use_channel_zone_and_cap():
    cid = _chan(-100940001); chat = -100940001
    update_settings(cid, daily_posts_cap=5, tz="Asia/Tehran")
    z = ZoneInfo("Asia/Tehran")
    loc = datetime.now(timezone.utc).astimezone(z)
    start = datetime(loc.year, loc.month, loc.day, tzinfo=z).astimezone(timezone.utc)
    q("INSERT INTO posted(channel_id,hash,posted_at) VALUES(?,?,?)", (chat, "biz-p1", datetime.now(timezone.utc).isoformat()), commit=True)
    q("INSERT INTO posted(channel_id,hash,posted_at) VALUES(?,?,?)", (chat, "biz-p2", (start - timedelta(minutes=1)).isoformat()), commit=True)
    assert channel_posts_today(cid, "Asia/Tehran") == 1
    assert channel_posts_today(cid, 3.5) == 1, "نام IANA و آفست باید یکسان بشمارند"
    txt = report_text(1, cid, "fa")
    assert "این کانال: 1/5" in txt, "گزارش کانال باید شمارش و سقف خودِ کانال را نشان دهد"
    assert remaining(1, "posts", cid) == 4, "ماندهٔ سهمیه باید از سقف/شمارش همین کانال دربیاید"
