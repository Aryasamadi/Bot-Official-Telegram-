# -*- coding: utf-8 -*-
"""فاز ۸ — معماری، همزمانی و شبکهٔ SQLite (جدول ۶.۸).

یافته‌های پوشش‌داده‌شده:
  • ARCH-01: arun() روی استخر تردهای اختصاصی DB؛ حلقهٔ رویداد بلاک نمی‌شود؛ ContextVarها منتقل می‌شوند.
  • GAP-09: نوشتن‌های هم‌زمان SQLite از مسیر async بدون خطای locked/busy.
  • DB-10/BIZ-07: درج هم‌زمان کاربر جدید (race) بدون IntegrityError.
  • OPS-21: throttle ماندگاری rate-limit در دیتابیس.
  • GAP-12: اجرای دقیق سقف پنجره‌ای (rate_window).
  • GAP-15/QA-11: بازیابی crash ژورنال انتشار (ack → settle، in_flight → delivery_unknown، prepared → بازگشت سهمیه).
  • ARCH-06: خاموشی تسک‌های نام‌دار را جمع می‌کند (با دور دوم لغو برای تسک‌های سرسخت) و تسک نامرتبط را دست نمی‌زند.
"""
import asyncio, concurrent.futures, contextvars, threading, time

from newsbot.db import q, arun, close_db_pool
from newsbot.core_articles import (article_insert, article_update, get_article, get_pub_journal,
                                   set_pub_journal, posted_before, recover_publications)
from newsbot.core_channels import add_channel, channel_by_chat
from newsbot.ratelimit import rate_ok, rate_clear, rate_window, _PERSIST_MIN_GAP
import newsbot.app as APP


def _chan(chat_id):
    add_channel(1, chat_id, f"ch{chat_id}", "", 1)
    return channel_by_chat(chat_id)["id"]


def _ready(cid, h, title="t", post="<b>salam</b>"):
    aid = article_insert(1, cid, h, f"https://x.test/{h}", title, 0, None, status="ready")
    article_update(aid, post_html=post)
    return aid


# ── ARCH-01: arun و استخر تردهای دیتابیس ──

def test_arun_runs_on_dedicated_db_threads():
    """arun باید تابع همزمان را روی تردهای استخر اختصاصی «newsbot-db» اجرا کند."""
    names = []

    async def go():
        for _ in range(3):
            names.append(await arun(lambda: threading.current_thread().name))

    asyncio.run(go())
    assert names and all(nm.startswith("newsbot-db") for nm in names), f"تردهای نادرست: {names}"


def test_arun_does_not_block_event_loop():
    """کار همزمانِ کند DB (مثل sleep) نباید حلقهٔ رویداد را بخواباند؛ تیکر باید ادامه دهد."""
    async def go():
        ticks = 0
        stop = asyncio.Event()

        async def ticker():
            nonlocal ticks
            while not stop.is_set():
                await asyncio.sleep(0.01)
                ticks += 1

        t = asyncio.create_task(ticker())
        t0 = time.monotonic()
        await arun(time.sleep, 0.35)
        elapsed = time.monotonic() - t0
        stop.set(); await t
        return ticks, elapsed

    ticks, elapsed = asyncio.run(go())
    assert elapsed >= 0.3, "خود arun باید واقعاً صبر کند"
    assert ticks >= 10, f"حلقهٔ رویداد در حین کار DB بلاک شده (ticks={ticks})"


def test_arun_propagates_contextvars():
    """ContextVarها (زمینهٔ لاگ/عملیات) باید مثل asyncio.to_thread منتقل شوند."""
    var = contextvars.ContextVar("ph8_test_var", default="outside")

    async def go():
        var.set("inside")
        return await arun(var.get)

    assert asyncio.run(go()) == "inside"


def test_close_db_pool_reinitializes_lazily():
    """بستن استخر در خاموشی نباید arun بعدی را بشکند؛ استخر به‌صورت lazy بازسازی می‌شود."""
    async def go():
        v1 = await arun(lambda: 1)
        close_db_pool()
        v2 = await arun(lambda: 2)
        return v1, v2

    assert asyncio.run(go()) == (1, 2)


# ── GAP-09: نوشتن موازی روی SQLite از مسیر async ──

def test_parallel_db_writes_via_arun_gap09():
    """۲۰ نوشتن هم‌زمان از تردهای مختلف نباید خطای locked/busy بدهند (WAL + قفل نوشتن + retry)."""
    base, day, n = 931000, "1999-12-31", 20

    async def go():
        await asyncio.gather(*[
            arun(q, "INSERT OR REPLACE INTO usage(admin_id,day,posts) VALUES(?,?,?)", (base + i, day, i), commit=True)
            for i in range(n)
        ])

    asyncio.run(go())
    c = q("SELECT COUNT(*) c FROM usage WHERE day=? AND admin_id>=? AND admin_id<?", (day, base, base + n), one=True)["c"]
    assert c == n, f"فقط {c} از {n} نوشتن موازی سالم ثبت شد"


# ── DB-10 / BIZ-07: مسابقهٔ درج کاربر جدید (خواهر DB-10) ──

def test_ensure_user_concurrent_insert_race_db10():
    """۴ نخ هم‌زمانِ ساخت یک کاربر جدید نباید IntegrityError بدهند و باید یک ردیف بماند."""
    import newsbot.core_users as CU
    uid = 920001
    q("DELETE FROM users WHERE id=?", (uid,), commit=True)
    barrier = threading.Barrier(4, timeout=10)
    original = CU.get_user
    lock = threading.Lock()

    def racing_get_user(gu_uid):
        row = original(gu_uid)
        if gu_uid == uid and row is None:
            # همه‌ی رقیب‌ها را دقیقاً پس از «کاربر نیست» نگه می‌داریم ⇒ مسابقهٔ قطعی
            with lock:
                pass
            barrier.wait()
        return row

    CU.get_user = racing_get_user
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
            futs = [ex.submit(CU.ensure_user, uid, f"u{i}", f"n{i}") for i in range(4)]
            results = [f.result(timeout=20) for f in futs]
    finally:
        CU.get_user = original
    assert all(r and r["id"] == uid for r in results)
    c = q("SELECT COUNT(*) c FROM users WHERE id=?", (uid,), one=True)["c"]
    assert c == 1, "کاربر تکراری یا ردیف گم‌شده"


# ── OPS-21: throttle ماندگاری rate-limit ──

def test_rate_persist_is_throttled():
    """نوشتن ماندگار rate-limit حداکثر هر ۱۰ ثانیه per-key؛ spam کاربر ⇒ یک INSERT نه N تا."""
    import newsbot.db as DB
    key = "cmd:934001:ph8-throttle"
    rate_clear(key)
    calls = []
    real_q = DB.q

    def counting_q(sql, params=(), **kw):
        calls.append(sql)
        return real_q(sql, params, **kw)

    DB.q = counting_q
    try:
        for _ in range(5):
            assert rate_ok(key, 0)
        writes = [c for c in calls if "INSERT OR REPLACE INTO rate_limits" in c]
        assert len(writes) == 1, f"throttle شکست: {len(writes)} نوشتن در ۵ فراخوانی"
        # پس از عبور از بازهٔ ۱۰ ثانیه، نوشتن بعدی مجاز است
        import newsbot.ratelimit as RL
        RL._last_persist[key] -= _PERSIST_MIN_GAP + 1
        assert rate_ok(key, 0)
        writes = [c for c in calls if "INSERT OR REPLACE INTO rate_limits" in c]
        assert len(writes) == 2
    finally:
        DB.q = real_q
        rate_clear(key)


# ── GAP-12: سقف دقیق پنجره‌ای ──

def test_rate_window_enforces_exact_cap_in_burst():
    """۵۰۰ فراخوانی در یک لحظه ⇒ دقیقاً limit تای اول True (سقف سراسری per-user)."""
    key = "w:934002:ph8-burst"
    now = time.time()
    oks = sum(1 for _ in range(500) if rate_window(key, 10, 60, now=now))
    assert oks == 10, f"سقف پنجره‌ای نقض شد: {oks}"


# ── GAP-15 / QA-11: بازیابی crash ژورنال انتشار ──

def test_recover_ack_journal_settles_as_published():
    """ژورنال ack (ارسال موفق، settle نشده): بازیابی باید آن را منتشرشده کند و ژورنال را بپاکد."""
    cid = _chan(9101)
    aid = _ready(cid, "h-ph8-ack")
    set_pub_journal(aid, {"phase": "ack", "message_id": 42, "admin_id": 1,
                          "reserved_day": None, "test_day": None, "test_user": None})
    recover_publications()
    a = get_article(aid)
    assert a["status"] == "published"
    assert get_pub_journal(aid) is None
    assert posted_before(9101, "h-ph8-ack")


def test_recover_in_flight_journal_marks_delivery_unknown():
    """ژورنال in_flight (کرش وسط ارسال): نه repost، نه ماندن در صف؛ روزِ رزروشده برای ابهام تحویل حفظ می‌شود."""
    cid = _chan(9102)
    aid = _ready(cid, "h-ph8-inflight")
    set_pub_journal(aid, {"phase": "in_flight", "message_id": None, "admin_id": 1,
                          "reserved_day": "1999-01-01", "test_day": None, "test_user": None})
    recover_publications()
    a = get_article(aid)
    assert a["status"] == "failed" and a["reason"] == "delivery_unknown"
    j = get_pub_journal(aid)
    assert j and j["phase"] == "unsettled" and j["reserved_day"] == "1999-01-01"


def test_recover_prepared_journal_returns_reserved_quota():
    """ژورنال prepared (کرش پیش از ارسال): سهمیهٔ رزروشده برمی‌گردد و ژورنال پاک می‌شود."""
    cid = _chan(9103)
    aid = _ready(cid, "h-ph8-prepared")
    q("INSERT OR REPLACE INTO usage(admin_id,day,posts,tests) VALUES(1,'1999-01-02',1,0)", commit=True)
    set_pub_journal(aid, {"phase": "prepared", "admin_id": 1,
                          "reserved_day": "1999-01-02", "test_day": None, "test_user": None})
    recover_publications()
    row = q("SELECT posts FROM usage WHERE admin_id=1 AND day='1999-01-02'", one=True)
    assert row and row["posts"] == 0, "سهمیهٔ رزروشده برنگشت"
    assert get_pub_journal(aid) is None


# ── ARCH-06: خاموشی تمیز تسک‌های نام‌دار ──

def test_shutdown_cancels_named_tasks_and_keeps_others(monkeypatch):
    """post_shutdown تسک‌های publication/user/thinking را لغو می‌کند و تسک نامرتبط را دست نمی‌زند."""
    monkeypatch.setattr(APP, "_SHUTDOWN_CANCEL_TIMEOUT", 0.3)
    monkeypatch.setattr(APP, "_SHUTDOWN_GRACE", 0.2)
    cancelled = []

    async def scenario():
        async def long_task():
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                cancelled.append(1); raise

        t1 = asyncio.create_task(long_task(), name="publication:ph8-1")
        t2 = asyncio.create_task(long_task(), name="thinking:1:cycle")
        t3 = asyncio.create_task(long_task(), name="other:keep")
        await asyncio.sleep(0.05)   # تسک‌ها شروع شوند
        await APP.post_shutdown(None)
        named_cancels = len(cancelled)
        unrelated_intact = (not t3.cancelled()) and (not t3.done())
        t3.cancel()                 # نظافت داخلی حلقه تا حلقهٔ رویداد تسک مانده را در بستن، لغو نکند
        try:
            await t3
        except asyncio.CancelledError:
            pass
        return t1, t2, named_cancels, unrelated_intact

    t1, t2, named_cancels, unrelated_intact = asyncio.run(scenario())
    assert t1.cancelled() and t2.cancelled(), "تسک‌های نام‌دار لغو نشدند"
    assert named_cancels == 2
    assert unrelated_intact, "تسک نامرتبط نباید لغو شود"


def test_shutdown_second_cancel_round_for_stubborn_tasks(monkeypatch):
    """تسکی که لغو اول را قورت می‌دهد، با دور دوم لغو جمع می‌شود و خاموشی معطل نمی‌ماند."""
    monkeypatch.setattr(APP, "_SHUTDOWN_CANCEL_TIMEOUT", 0.3)
    monkeypatch.setattr(APP, "_SHUTDOWN_GRACE", 0.2)
    state = {"cancels": 0}

    async def scenario():
        async def stubborn():
            while True:
                try:
                    await asyncio.sleep(30)
                except asyncio.CancelledError:
                    state["cancels"] += 1
                    if state["cancels"] >= 2:
                        raise   # فقط دور دوم لغو را می‌پذیرد

        t = asyncio.create_task(stubborn(), name="user:934003:stubborn")
        await asyncio.sleep(0.05)
        t0 = time.monotonic()
        await APP.post_shutdown(None)
        return t, time.monotonic() - t0

    t, dur = asyncio.run(scenario())
    assert t.cancelled(), "دور دوم لغو تسک سرسخت را جمع نکرد"
    assert state["cancels"] >= 2
    assert dur < 3, f"خاموشی با تسک سرسخت معطل ماند ({dur:.1f}s)"
