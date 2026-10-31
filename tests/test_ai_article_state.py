# -*- coding: utf-8 -*-
"""ماشینِ حالتِ مقاله در برابرِ خطای AI — هشت سناریوی اجباری.

پیش از این، هر خطای AI (timeout/deadline/429/5xx/پاسخِ ناقص) دقیقاً مثلِ «ردِ
قطعیِ محتوا» ذخیره می‌شد و مقاله‌های قدیمی هم دوباره وارد صفِ AI می‌شدند. این
تست‌ها منطقِ برنامه را (نه رفتارِ قبلیِ آن را) تثبیت می‌کنند:

  ۱) timeout و deadline ⇒ «ردِ قطعی» نیستند.
  ۲) ۴۲۹ و ۵xx ⇒ همچنان موقت‌اند.
  ۳) مقالهٔ قدیمی هنگام retry از صف خارج می‌شود.
  ۴) مقالهٔ موقتاً ناموفق پس از رفعِ مشکل دوباره پردازش می‌شود.
  ۵) retry محدود و دارای backoff است.
  ۶) مقالهٔ منتشرشده دوباره منتشر نمی‌شود.
  ۷) مهاجرتِ دیتابیس اطلاعاتِ قبلی را حفظ می‌کند.
  ۸) اجرای دوبارهٔ چرخه، انتشارِ تکراری نمی‌سازد.

همه آفلاین‌اند: شبکه/AI با monkeypatch جایگزین می‌شود و فقط منطقِ واقعیِ
برنامه (دیتابیس، ماشینِ حالت، چرخه) اجرا می‌شود."""
import asyncio
import os
import sqlite3
import tempfile
from datetime import timedelta
from types import SimpleNamespace

from newsbot import config
from newsbot import cycle as cyc_mod
from newsbot import publish as pub_mod
from newsbot.ai_models import err_code, is_ai_transient
from newsbot.core_articles import (AI_STATE_EXHAUSTED, AI_STATE_REJECTED, AI_STATE_RETRYABLE,
                                   article_insert, get_article, mark_ai_failure, mark_ai_rejected,
                                   posted_before, soft_window_candidate)
from newsbot.core_channels import add_channel, channel_by_chat
from newsbot.core_sources import add_source
from newsbot.db import q
from newsbot.timeutils import now_iso, now_utc, url_hash


# ── ابزارهای کمکی ──────────────────────────────────────────────────────────────

def _chan(chat_id):
    add_channel(1, chat_id, f"t{chat_id}", "", 1)
    return channel_by_chat(chat_id)["id"]


def _item(url, published=None, title="خبر تست"):
    return {"url": url, "title": title, "published": published or now_iso(), "html": "", "media": None}


def _art(url, published=None, title="خبر تست", text=None):
    return {"url": url, "title": title,
            "text": text or ("این یک متن خبری سالم و کامل برای تولید محتوا است. " * 5),
            "published": published or now_iso(), "media": None, "author": None, "site": None, "next": ""}


class PubBot:
    """رباتِ آزمایشی: پیام‌های ارسال‌شده را نگه می‌دارد."""
    id = 999
    def __init__(self):
        self.sent = []
    async def get_chat_member(self, chat_id, user_id):
        return SimpleNamespace(status="administrator", can_post_messages=True)
    async def send_message(self, chat_id, text, **kw):
        self.sent.append((chat_id, text))
        return SimpleNamespace(message_id=len(self.sent))
    async def send_photo(self, chat_id, photo, caption="", **kw):
        self.sent.append((chat_id, caption))
        return SimpleNamespace(message_id=len(self.sent))


def _run_cycle(monkeypatch, cid, discover_impl, extract_impl, gen_impl=None, bot=None):
    monkeypatch.setattr(cyc_mod, "discover_source", discover_impl)
    monkeypatch.setattr(cyc_mod, "extract_article", extract_impl)
    monkeypatch.setattr(cyc_mod, "mandatory_configured", lambda lang="fa": False)
    if gen_impl is not None:
        monkeypatch.setattr(cyc_mod, "generate", gen_impl)
    return asyncio.run(cyc_mod.run_channel_cycle(bot or PubBot(), 1, cid, test_mode=True))


def _good_gen(s, art, on_queue=None, owner_id=None):
    return ({"post": "<b>تیتر خبر</b>\n\n" + ("متن کامل این پست برای انتشار در کانال است. " * 6),
             "full": None, "title": "تیتر خبر", "category": s["categories"][0]["name"],
             "scores": {c["name"]: 9 for c in s["criteria"]}, "score": 90.0}, "m-test", None)


# ── ۱) timeout و deadline ⇒ «ردِ قطعیِ محتوا» نیستند ───────────────────────────

def test_timeout_and_deadline_do_not_reject_the_article_permanently():
    cid = _chan(-100950001)
    aid = article_insert(1, cid, "h-timeout", "https://src.test/news/t1", "خبر تایم‌اوت", 0, now_iso())
    assert aid, "مقاله باید درج شود"

    mark_ai_failure(aid, "ai_timeout", provider="vyceai.com")
    a = get_article(aid)
    assert a["status"] == "failed", "شکستِ موقت نباید وضعیتِ «ردشده» بگیرد"
    assert a["status"] != "rejected"
    assert a["ai_state"] == AI_STATE_RETRYABLE, "timeout باید «موقت» ثبت شود"
    assert a["reason"] == "ai: ai_timeout"
    assert a["ai_attempts"] == 1 and a["ai_last_error"] == "ai_timeout"
    assert a["ai_next_retry"], "زمانِ تلاشِ بعدی باید ثبت شود"

    # deadline: همان مقالهٔ در حالِ کار
    aid2 = article_insert(1, cid, "h-deadline", "https://src.test/news/t2", "خبر ددلاین", 0, now_iso())
    cyc_mod._current_article.set(aid2)
    res = asyncio.run(cyc_mod._deadline_result(1, cid, 80))
    cyc_mod._current_article.set(None)
    b = get_article(aid2)
    assert res["stop_reason"] == "deadline"
    assert b["status"] == "failed" and b["status"] != "rejected", "deadline هم نباید مقاله را ردِ قطعی کند"
    assert b["ai_state"] == AI_STATE_RETRYABLE, "deadline یک خطای موقت است"
    assert b["reason"] == "ai: deadline" and b["ai_attempts"] == 1


# ── ۲) ۴۲۹ و ۵xx همچنان موقت‌اند ─────────────────────────────────────────────

def test_rate_limit_and_server_errors_stay_transient():
    assert err_code("HTTP 429 Too Many Requests") == "ai_busy"
    assert err_code("503 Service Unavailable") == "ai_busy"
    assert err_code("500 Internal Server Error") == "ai_busy"
    assert err_code("HTTP 504 Gateway Timeout") == "ai_timeout"
    for code in ("ai_busy", "ai_timeout", "ai_none", "ai_all_failed", "ai_empty", "ai_invalid"):
        assert is_ai_transient(code), f"{code} باید خطای موقت باشد"
    # ۵xxها (از جمله ۵۰۲ که به‌خاطرِ واژهٔ «gateway» به ai_timeout می‌افتد) در هر دو حالت موقت‌اند
    for raw in ("502 Bad Gateway", "500", "503", "504"):
        assert is_ai_transient(err_code(raw)), f"{raw} باید خطای موقت باشد"

    cid = _chan(-100950002)
    aid = article_insert(1, cid, "h-429", "https://src.test/news/r1", "خبر ۴۲۹", 0, now_iso())
    mark_ai_failure(aid, err_code("HTTP 429 Too Many Requests"), provider="vyceai.com")
    a = get_article(aid)
    assert a["status"] == "failed" and a["ai_state"] == AI_STATE_RETRYABLE
    assert a["ai_state"] != AI_STATE_REJECTED, "۴۲۹ خطای موقت است، نه ردِ محتوا"
    assert soft_window_candidate(cid, -100950002, "h-429") is not None or a["ai_next_retry"], \
        "مقالهٔ ۴۲۹ باید مسیرِ retry داشته باشد (نه حذف)"

    # ۵xx هم در چرخه «موقت» ثبت می‌شود، نه «ردِ محتوا»
    aid2 = article_insert(1, cid, "h-503", "https://src.test/news/r2", "خبر ۵۰۳", 0, now_iso())
    mark_ai_failure(aid2, err_code("503 Service Unavailable"), provider="vyceai.com")
    b = get_article(aid2)
    assert b["status"] == "failed" and b["ai_state"] == AI_STATE_RETRYABLE
    assert b["ai_state"] != AI_STATE_REJECTED


# ── ۳) مقالهٔ قدیمی هنگام retry از صف خارج می‌شود ─────────────────────────────

def test_an_old_article_leaves_the_queue_on_retry(monkeypatch):
    cid = _chan(-100950003); chat = -100950003
    add_source(1, cid, "https://src.test/feed-old.xml")
    old = (now_utc() - timedelta(days=10)).isoformat()
    aid = article_insert(1, cid, url_hash("https://src.test/news/old1"), "https://src.test/news/old1", "خبر قدیمی", 0, old,
                         status="failed", reason="ai: ai_timeout")
    assert aid

    async def disc_ok(src, limit=25, use_cache=True, tz=None):
        return [_item("https://src.test/news/old1", published=old, title="خبر قدیمی")], True, "feed"
    async def extract_old(url, fallback_html="", tz=None):
        return _art(url, published=old, title="خبر قدیمی")
    called = {"n": 0}
    async def gen_never(s, art, on_queue=None, owner_id=None):
        called["n"] += 1
        return _good_gen(s, art)

    res = _run_cycle(monkeypatch, cid, disc_ok, extract_old, gen_never)

    a = get_article(aid)
    assert called["n"] == 0, "مقالهٔ قدیمی نباید به AI برسد"
    assert res["published"] == 0 and res["accepted"] == 0
    assert a["status"] == "skipped", "مقالهٔ قدیمی باید از صف خارج شود"
    assert a["reason"] == "stale"
    assert a["ai_state"] == "stale" and not a["ai_next_retry"], "مقالهٔ قدیمی نباید دوباره در صف بماند"
    assert soft_window_candidate(cid, chat, url_hash("https://src.test/news/old1")) is None, "مقالهٔ قدیمی دیگر قابلِ بازچرخانی نیست"


# ── ۴) مقالهٔ موقتاً ناموفق پس از رفعِ مشکل دوباره پردازش می‌شود ────────────────

def test_a_temporarily_failed_article_is_processed_again_after_the_problem_is_fixed(monkeypatch):
    cid = _chan(-100950004)
    add_source(1, cid, "https://src.test/feed-retry.xml")
    url = "https://src.test/news/retry1"
    aid = article_insert(1, cid, url_hash(url), url, "خبر موقتاً ناموفق", 0, now_iso(),
                         status="failed", reason="ai: ai_timeout")
    assert aid

    async def disc_ok(src, limit=25, use_cache=True, tz=None):
        return [_item(url, title="خبر موقتاً ناموفق")], True, "feed"
    async def extract_ok(url, fallback_html="", tz=None):
        return _art(url, title="خبر موقتاً ناموفق")
    async def gen_ok(s, art, on_queue=None, owner_id=None):
        return _good_gen(s, art)

    res = _run_cycle(monkeypatch, cid, disc_ok, extract_ok, gen_ok)

    a = get_article(aid)
    assert res["accepted"] == 1 and res["published"] == 1, "مقالهٔ موقتاً ناموفق باید دوباره پردازش و منتشر شود"
    assert a["status"] == "published"
    assert a["ai_state"] == "" and not a["ai_next_retry"], "پس از موفقیت، حالتِ موقت پاک می‌شود"
    assert a["reason"] == ""


# ── ۵) retry محدود و دارای backoff است ────────────────────────────────────────

def test_retry_is_capped_and_backed_off():
    cid = _chan(-100950005); chat = -100950005
    aid = article_insert(1, cid, "h-cap", "https://src.test/news/cap1", "خبر کران‌دار", 0, now_iso())

    mark_ai_failure(aid, "ai_timeout")
    first = get_article(aid)
    assert first["ai_attempts"] == 1
    assert first["ai_state"] == AI_STATE_RETRYABLE
    nxt1 = first["ai_next_retry"]
    assert nxt1, "زمانِ تلاشِ بعدی باید ثبت شود"
    # هنوز وقتش نرسیده ⇒ به صف برنمی‌گردد
    assert soft_window_candidate(cid, chat, "h-cap") is None, "backoff باید رعایت شود"

    # backoffِ نمایی ⇒ تلاشِ دوم دیرتر از تلاشِ اول
    mark_ai_failure(aid, "ai_timeout")
    second = get_article(aid)
    assert second["ai_attempts"] == 2
    assert second["ai_next_retry"] >= nxt1, "backoff باید نمایی باشد"

    # سقفِ تلاش ⇒ مقاله پارک می‌شود (نه حذف، نه «ردِ محتوا»)
    for _ in range(max(0, config.AI_MAX_ARTICLE_ATTEMPTS - 2)):
        mark_ai_failure(aid, "ai_timeout")
    final = get_article(aid)
    assert final["ai_attempts"] == config.AI_MAX_ARTICLE_ATTEMPTS
    assert final["ai_state"] == AI_STATE_EXHAUSTED, "پس از سقفِ تلاش، مقاله پارک می‌شود"
    assert final["status"] == "failed" and final["status"] != "rejected", "پارک ≠ ردِ قطعیِ محتوا"
    assert final["ai_next_retry"], "زمانِ تلاشِ بعدی ثبت می‌شود ولی مقاله از صفِ بازچرخانی بیرون است"
    assert soft_window_candidate(cid, chat, "h-cap") is None, "مقالهٔ exhausted دیگر وارد صف نمی‌شود"

    # در مقابل: «ردِ قطعیِ محتوا» (امتیازِ پایین) هرگز به صف برنمی‌گردد
    url_rej = "https://src.test/news/rej1"
    aid_rej = article_insert(1, cid, url_hash(url_rej), url_rej, "خبرِ کم‌امتیاز", 0, now_iso())
    mark_ai_rejected(aid_rej, "score 42.0 < 60", score=42.0)
    r = get_article(aid_rej)
    assert r["status"] == "rejected" and r["ai_state"] == AI_STATE_REJECTED
    assert soft_window_candidate(cid, chat, url_hash(url_rej)) is None, "ردِ قطعیِ محتوا هرگز retry نمی‌شود"
    # یک خطای موقتِ بعدی نباید تصمیمِ محتواییِ گرفته‌شده را خراب کند
    mark_ai_failure(aid_rej, "ai_timeout")
    assert get_article(aid_rej)["ai_state"] == AI_STATE_REJECTED, "ردِ محتوا با خطای موقت جابه‌جا نمی‌شود"
    assert get_article(aid_rej)["status"] == "rejected"


# ── ۶) مقالهٔ منتشرشده دوباره منتشر نمی‌شود ────────────────────────────────────

def test_a_published_article_is_not_published_again(monkeypatch):
    cid = _chan(-100950006); chat = -100950006
    add_source(1, cid, "https://src.test/feed-pub.xml")
    url = "https://src.test/news/pub1"
    bot = PubBot()

    async def disc_ok(src, limit=25, use_cache=True, tz=None):
        return [_item(url, title="خبر منتشرشده")], True, "feed"
    async def extract_ok(url, fallback_html="", tz=None):
        return _art(url, title="خبر منتشرشده")
    async def gen_ok(s, art, on_queue=None, owner_id=None):
        return _good_gen(s, art)

    res = _run_cycle(monkeypatch, cid, disc_ok, extract_ok, gen_ok, bot=bot)
    assert res["published"] == 1
    a = q("SELECT * FROM articles WHERE hash=?", (url_hash(url),), one=True)
    assert a and a["status"] == "published" and a["links"], "انتشار باید لینک ثبت کند"
    assert len(bot.sent) == 1

    # انتشارِ دوبارهٔ همان مقاله (فراخوانیِ مستقیم) ⇒ هیچ پیامی ارسال نمی‌شود
    ok, out = asyncio.run(pub_mod.publish_article(bot, a["id"]))
    assert ok is False and out == "not_ready", "مقالهٔ منتشرشده دوباره منتشر نمی‌شود"
    assert len(bot.sent) == 1, "ربات فقط یک پیام گرفته است"
    assert posted_before(chat, url_hash(url)), "رکوردِ «منتشرشده» باید ثبت شده باشد"
    assert soft_window_candidate(cid, chat, url_hash(url)) is None, "مقالهٔ منتشرشده قابلِ بازچرخانی نیست"
    assert get_article(a["id"])["status"] == "published"


# ── ۷) مهاجرتِ دیتابیس اطلاعاتِ قبلی را حفظ می‌کند ────────────────────────────

def test_the_migration_keeps_the_previous_data():
    """روی یک پایگاه‌دادهٔ *کپی* با اسکیمای قدیمی: مهاجرت اجرا می‌شود، داده سالم می‌ماند.

    روش: اسکیمای کامل ساخته می‌شود، سپس ستون‌های ``ai_*`` (که در نسخهٔ قبلی نبودند)
    حذف می‌شوند تا پایگاه‌داده دقیقاً «قدیمی» باشد؛ یک مقالهٔ منتشرشده درج می‌شود و
    بعد مهاجرت (دو بار، تا idempotent بودن هم سنجیده شود) اجرا می‌شود."""
    from newsbot import db as db_mod

    tmp = tempfile.mkdtemp(prefix="newsbot-migrate-")
    path = os.path.join(tmp, "old.db")
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    db_mod._exec_schema(conn, db_mod.SCHEMA)          # اسکیمای کامل (شامل جدول‌های دیگر)
    for col in ("ai_state", "ai_attempts", "ai_last_error", "ai_last_attempt", "ai_next_retry", "ai_provider"):
        conn.execute(f"ALTER TABLE articles DROP COLUMN {col}")   # بازگشت به نسخهٔ قدیمی
    conn.execute("INSERT INTO articles (admin_id,channel_id,hash,url,title,source_id,published_at,"
                 "created_at,status,reason,score,post_html) VALUES (1,7,'h-old-db','https://s.test/n/1',"
                 "'عنوانِ قبلی',3,'2026-01-01T00:00:00+00:00','2026-01-01T00:00:00+00:00','published','',77.5,'<b>متن</b>')")
    conn.commit()

    cols_before = {r[1] for r in conn.execute("PRAGMA table_info(articles)").fetchall()}
    assert "ai_state" not in cols_before, "پیش از مهاجرت نباید این ستون‌ها وجود داشته باشند"

    db_mod._apply_migrations(conn)      # اجرای مهاجرت روی کپی
    db_mod._apply_migrations(conn)      # اجرای دوباره ⇒ idempotent

    cols = {r[1] for r in conn.execute("PRAGMA table_info(articles)").fetchall()}
    for c in ("ai_state", "ai_attempts", "ai_last_error", "ai_last_attempt", "ai_next_retry", "ai_provider"):
        assert c in cols, f"ستون {c} باید اضافه شده باشد"

    data = dict(conn.execute("SELECT * FROM articles WHERE id=1").fetchone())
    assert data["hash"] == "h-old-db" and data["title"] == "عنوانِ قبلی", "دادهٔ قبلی نباید تغییر کند"
    assert data["status"] == "published" and data["score"] == 77.5 and data["post_html"] == "<b>متن</b>"
    assert data["ai_state"] == "" and data["ai_attempts"] == 0, "ستون‌های جدید با مقدارِ پیش‌فرضِ خالی"
    assert conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0] == 1, "هیچ ردیفی حذف نشود"
    conn.close()


# ── ۸) اجرای دوبارهٔ چرخه، انتشارِ تکراری نمی‌سازد ────────────────────────────

def test_rerunning_the_cycle_does_not_publish_twice(monkeypatch):
    cid = _chan(-100950008)
    add_source(1, cid, "https://src.test/feed-twice.xml")
    url = "https://src.test/news/twice1"
    bot = PubBot()

    async def disc_ok(src, limit=25, use_cache=True, tz=None):
        return [_item(url, title="خبر دوبار")], True, "feed"
    async def extract_ok(url, fallback_html="", tz=None):
        return _art(url, title="خبر دوبار")
    async def gen_ok(s, art, on_queue=None, owner_id=None):
        return _good_gen(s, art)

    first = _run_cycle(monkeypatch, cid, disc_ok, extract_ok, gen_ok, bot=bot)
    second = _run_cycle(monkeypatch, cid, disc_ok, extract_ok, gen_ok, bot=bot)
    third = _run_cycle(monkeypatch, cid, disc_ok, extract_ok, gen_ok, bot=bot)

    assert first["published"] == 1, "چرخهٔ اول باید منتشر کند"
    assert second["published"] == 0 and third["published"] == 0, "چرخه‌های بعدی نباید دوباره منتشر کنند"
    assert len(bot.sent) == 1, "فقط یک پیام واقعاً ارسال شده است"
    rows = q("SELECT id,status,links FROM articles WHERE hash=?", (url_hash(url),))
    assert len(rows) == 1, "یک مقاله، نه دو تا"
    assert rows[0]["status"] == "published"
    posted = q("SELECT COUNT(*) c FROM posted WHERE hash=?", (url_hash(url),), one=True)
    assert posted["c"] == 1, "رکوردِ «منتشرشده» نباید تکرار شود"
