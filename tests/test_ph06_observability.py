# -*- coding: utf-8 -*-
"""PH-06 — observability/alerting truth: delivery, outbox, health, dead-man, logs.

این تست‌ها روی *مسیر واقعی* قفل می‌گذارند: wrapper تلگرام تعداد تحویل را برمی‌گرداند،
زمان‌بند فقط با تأیید واقعی «تحویل شد» می‌نویسد، اعلان پیش از ارسال در outbox است،
و health هرگز خطای probe را به «سالم/صفر» تبدیل نمی‌کند.
"""
import asyncio
import sqlite3
from datetime import timedelta

from newsbot import config, runtime
from newsbot.db import q, gget, gset
from newsbot.timeutils import now_iso, now_utc


class _Bot:
    def __init__(self, fail=()):
        self.fail = set(fail)
        self.sent = []

    async def send_message(self, chat_id, text, **kw):
        if chat_id in self.fail:
            raise RuntimeError("telegram down")
        self.sent.append((chat_id, text, kw))
        return object()


def _clean_notifications():
    q("DELETE FROM notifications", commit=True)


def _mk_user(uid, role="user", lang="fa"):
    q("INSERT OR REPLACE INTO users(id,username,name,role,banned,lang,created_at,last_seen) VALUES(?,?,?,?,?,?,?,?)",
      (uid, f"u{uid}", f"User {uid}", role, 0, lang, now_iso(), now_iso()), commit=True)


def _mk_plan(uid):
    from newsbot.core_plans import create_plan
    return create_plan(name=f"ph06-{uid}", name_en=f"ph06-{uid}", days=30, daily_posts=5,
                       max_sources=10, max_channels=5, daily_tests=5, price="100000 تومان",
                       price_num=100.0, price_num_fa=1000.0)


# ── OBS-011: truth from the real wrapper, not just “no exception” ───────────────────
def test_obs011_wrapper_returns_zero_when_every_recipient_fails(monkeypatch):
    from newsbot import ui_widgets
    monkeypatch.setattr(ui_widgets, "BOT", _Bot(fail={1, 2}))
    monkeypatch.setattr(config, "SUPER_ADMIN_IDS", {1, 2})
    assert asyncio.run(ui_widgets.notify_supers("هشدار")) == 0


def test_obs011_wrapper_returns_the_number_of_delivered_recipients(monkeypatch):
    from newsbot import ui_widgets
    bot = _Bot(fail={2})
    monkeypatch.setattr(ui_widgets, "BOT", bot)
    monkeypatch.setattr(config, "SUPER_ADMIN_IDS", {1, 2})
    assert asyncio.run(ui_widgets.notify_supers("هشدار")) == 1
    assert len(bot.sent) == 1


def test_obs012_notify_user_reports_wrapper_false(monkeypatch):
    from newsbot.notify import notify_user
    async def fail(uid_, text, kb=None):
        return False
    monkeypatch.setattr(runtime, "NOTIFY_USER", fail)
    assert asyncio.run(notify_user(906000, "x")) is False


def test_obs011_scheduler_does_not_mark_delivered_when_wrapper_delivers_zero(monkeypatch):
    from newsbot import ui_widgets
    from newsbot.core_alerts import alert_enqueue
    from newsbot.scheduler import _retry_alerts
    _clean_notifications()
    nid = alert_enqueue("هشدار", next_attempt_at=0)
    bot = _Bot(fail={1})
    monkeypatch.setattr(ui_widgets, "BOT", bot)
    monkeypatch.setattr(config, "SUPER_ADMIN_IDS", {1})
    monkeypatch.setattr(runtime, "NOTIFY_SUPER", ui_widgets.notify_supers)
    asyncio.run(_retry_alerts())
    row = q("SELECT * FROM notifications WHERE id=?", (nid,), one=True)
    assert row["delivered_at"] is None, "شکست همهٔ گیرندگان نباید «تحویل شد» ثبت شود"
    assert int(row["attempts"]) == 1
    bot.fail.clear()
    q("UPDATE notifications SET next_attempt_at=0 WHERE id=?", (nid,), commit=True)
    asyncio.run(_retry_alerts())
    assert q("SELECT delivered_at FROM notifications WHERE id=?", (nid,), one=True)["delivered_at"]


def test_obs011_pending_alert_survives_worker_shutdown(monkeypatch):
    from newsbot import notify
    _clean_notifications()
    started = None

    async def block(text, kb=None):
        nonlocal started
        started = asyncio.Event()
        started.set()
        await asyncio.Event().wait()       # تا cancel شدن
        return 1

    monkeypatch.setattr(runtime, "NOTIFY_SUPER", block)
    monkeypatch.setattr(notify, "_super_worker", None)
    monkeypatch.setattr(notify, "_super_q", None)

    async def scenario():
        notify.ensure_super_worker()
        await notify.notify_super("قبل از خاموشی")
        for _ in range(100):
            if started is not None:
                break
            await asyncio.sleep(0.01)
        assert started is not None
        await asyncio.wait_for(started.wait(), 2)
        await notify.stop_super_worker()

    asyncio.run(scenario())
    row = q("SELECT * FROM notifications", one=True)
    assert row is not None and row["delivered_at"] is None, "پیام در صف حافظه با خاموشی گم شد"
    assert not row["dead"]


# ── OBS-012: the payment marker follows the actual notification result ───────────────
def test_obs012_payment_is_marked_notified_only_after_success(monkeypatch):
    from newsbot import core_payments, ui_widgets
    from newsbot.scheduler import _renotify_payments
    uid = 906001
    _mk_user(uid)
    pid = _mk_plan(uid)
    rid = q("INSERT INTO pay_requests(user_id,plan_id,status,created_at,decided_at,final_price) "
            "VALUES(?,?, 'approved',?,?,?)", (uid, pid, now_iso(), now_iso(), "100"), commit=True)
    pay_row = q("SELECT * FROM pay_requests WHERE id=?", (rid,), one=True)
    monkeypatch.setattr(core_payments, "pay_unnotified", lambda limit=20: [pay_row])
    try:
        async def fail(uid_, text, kb=None):
            return False
        monkeypatch.setattr(ui_widgets, "notify_user_fn", fail)
        asyncio.run(_renotify_payments())
        row = q("SELECT notified_at FROM pay_requests WHERE id=?", (rid,), one=True)
        assert row["notified_at"] is None, "پیام نرفته ولی پرداخت «اعلام‌شده» علامت خورد"

        async def ok(uid_, text, kb=None):
            return True
        monkeypatch.setattr(ui_widgets, "notify_user_fn", ok)
        asyncio.run(_renotify_payments())
        row = q("SELECT notified_at FROM pay_requests WHERE id=?", (rid,), one=True)
        assert row["notified_at"], "پیام موفق باید marker را ثبت کند"
    finally:
        q("DELETE FROM pay_requests WHERE id=?", (rid,), commit=True)
        q("DELETE FROM plans WHERE id=?", (pid,), commit=True)
        q("DELETE FROM users WHERE id=?", (uid,), commit=True)


def test_obs005_referral_reward_is_atomic_and_idempotent():
    import pytest
    from newsbot.core_referrals import record_referral
    from newsbot.core_payments import pay_create, pay_approve_atomic, PayAlreadyDecided
    ref, payer = 906010, 906011
    _mk_user(ref)
    _mk_user(payer)
    pid = _mk_plan(payer)
    assert record_referral(ref, payer) is True
    rid = pay_create(payer, pid, receipt_chat=1, receipt_msg=1, final_price="100")
    try:
        pay_approve_atomic(rid)
        first = q("SELECT credit_amount FROM referrals WHERE referred_id=?", (payer,), one=True)["credit_amount"]
        assert float(first or 0) > 0, "پاداش ارجاع در تراکنش تأیید پرداخت اعطا نشد"
        with pytest.raises(PayAlreadyDecided):
            pay_approve_atomic(rid)
        second = q("SELECT credit_amount FROM referrals WHERE referred_id=?", (payer,), one=True)["credit_amount"]
        assert float(second or 0) == float(first or 0), "تأیید دوم پاداش را دوبار اعمال کرد"
    finally:
        q("DELETE FROM pay_requests WHERE id=?", (rid,), commit=True)
        q("DELETE FROM referrals WHERE referred_id=?", (payer,), commit=True)
        q("DELETE FROM plans WHERE id=?", (pid,), commit=True)
        q("DELETE FROM users WHERE id IN (?,?)", (ref, payer), commit=True)


# ── OBS-002/OBS-003/OBS-009: health truth ───────────────────────────────────────────
def test_obs002_health_is_unknown_not_green_when_db_probe_fails(monkeypatch):
    from newsbot import ui_views_super as V
    def boom(*a, **k):
        raise RuntimeError("db locked")
    monkeypatch.setattr(V, "gget", boom)
    snap = V.health_snapshot()
    assert snap["status"] == "unknown"
    assert snap["errors"]


def test_obs003_d1_size_is_unknown_not_zero(monkeypatch):
    from newsbot import ui_views_super as V
    monkeypatch.setattr(config, "DB_BACKEND", "d1")
    assert V.health_snapshot()["db_size_mb"] is None


def test_obs009_stale_last_publish_degrades_health():
    from newsbot import ui_views_super as V
    cid = q("INSERT INTO channels(admin_id,chat_id,title,created_at) VALUES(1,906002,'t',?)",
            (now_iso(),), commit=True)
    old = (now_utc() - timedelta(hours=config.DEADMAN_STALE_HOURS + 3)).isoformat()
    gset("last_successful_publish", old)
    gset("automation_enabled", True)
    gset("heartbeat", now_iso())
    try:
        snap = V.health_snapshot()
        assert snap["status"] == "degraded"
        assert any("last publish" in r for r in snap["reasons"])
    finally:
        q("DELETE FROM channels WHERE id=?", (cid,), commit=True)
        q("DELETE FROM settings WHERE key IN ('last_successful_publish','automation_enabled','heartbeat')", commit=True)


def test_obs002_deadman_alert_is_durable_and_suppressed_after_first(monkeypatch):
    from newsbot import notify
    from newsbot.scheduler import _deadman_check
    _clean_notifications()
    cid = q("INSERT INTO channels(admin_id,chat_id,title,created_at) VALUES(1,906003,'t',?)",
            (now_iso(),), commit=True)
    gset("automation_enabled", True)
    gset("last_successful_publish", (now_utc() - timedelta(hours=config.DEADMAN_STALE_HOURS + 1)).isoformat())
    q("DELETE FROM settings WHERE key='last_deadman_alert'", commit=True)
    sent = []
    async def cap(text, kb=None):
        sent.append(text)
        return 1
    monkeypatch.setattr(runtime, "NOTIFY_SUPER", cap)
    monkeypatch.setattr(notify, "_super_worker", None)
    monkeypatch.setattr(notify, "_super_q", None)
    try:
        assert asyncio.run(_deadman_check()) is True
        assert sent and "انتشار" in sent[0]
        assert asyncio.run(_deadman_check()) is False, "هشدار تکراری در یک پنجره نباید تکرار شود"
        assert len(sent) == 1
        assert q("SELECT delivered_at FROM notifications", one=True)["delivered_at"]
    finally:
        q("DELETE FROM channels WHERE id=?", (cid,), commit=True)
        q("DELETE FROM settings WHERE key IN ('last_successful_publish','automation_enabled','last_deadman_alert')", commit=True)


def test_obs002_settle_records_last_successful_publish():
    from newsbot.core_articles import settle_publication
    q("DELETE FROM settings WHERE key='last_successful_publish'", commit=True)
    cid = q("INSERT INTO channels(admin_id,chat_id,title,created_at) VALUES(1,906004,'t',?)",
            (now_iso(),), commit=True)
    aid = q("INSERT INTO articles(admin_id,channel_id,hash,url,title,created_at,status) "
            "VALUES(1,?,?,?,?,?,'ready')", (cid, "ph06hash", "https://example.test/a", "a", now_iso()), commit=True)
    try:
        settle_publication(aid, {"id": cid, "chat_id": 906004}, {"message_id": 1, "link": "https://t.me/x/1"})
        assert gget("last_successful_publish")
    finally:
        q("DELETE FROM articles WHERE id=?", (aid,), commit=True)
        q("DELETE FROM channels WHERE id=?", (cid,), commit=True)
        q("DELETE FROM settings WHERE key='last_successful_publish'", commit=True)


# ── OBS-004/OBS-015/SEC-011/OBS-016 ─────────────────────────────────────────────────
def test_obs004_log_event_keeps_redacted_traceback(caplog):
    import logging
    from newsbot.logutils import log_event
    with caplog.at_level(logging.ERROR, logger="core"):
        try:
            raise RuntimeError("upstream token=deadbeefcafe1234")
        except RuntimeError:
            log_event("ERROR", "publish failed", exc_info=True, error_class="RuntimeError")
    rec = caplog.records[-1]
    text = f"{rec.getMessage()}\n{rec.exc_text or ''}"
    assert "RuntimeError" in text and "deadbeefcafe1234" not in text


def test_obs015_redaction_failure_is_fail_closed(monkeypatch, caplog):
    import logging
    from newsbot import logutils
    def boom(_):
        raise RuntimeError("redactor down")
    monkeypatch.setattr(logutils, "redact", boom)
    with caplog.at_level(logging.ERROR, logger="core"):
        logutils.log.error("secret=topsecretvalue")
    text = caplog.records[-1].getMessage()
    assert "topsecretvalue" not in text
    assert "REDACTION_FAILED" in text


def test_sec011_raw_super_id_is_not_logged(caplog):
    import logging
    from newsbot.logutils import log_event
    with caplog.at_level(logging.INFO, logger="core"):
        log_event("INFO", "admin action", admin_id=987654321)
    msg = caplog.records[-1].getMessage()
    assert "987654321" not in msg and "actor:" in msg


def test_obs016_gemini_key_travels_in_a_header_not_the_url():
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "newsbot" / "ai_models.py").read_text(encoding="utf-8")
    assert 'params={"key": key}' not in src
    assert "x-goog-api-key" in src


# ── OPS-033: a failed column must not get the final “done” marker ────────────────────
def test_ops033_failed_iso_column_is_retried_not_marked_done():
    from newsbot.db import normalize_iso_columns
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT);"
                       "CREATE TABLE users(created_at TEXT);"
                       "INSERT INTO users(created_at) VALUES('2026-01-01T00:00:00+03:30');")

    class Flaky:
        def __init__(self, c):
            self.c = c
            self.fail = True
        def execute(self, sql, params=()):
            if self.fail and "FROM users" in sql and "created_at" in sql:
                self.fail = False
                raise sqlite3.OperationalError("simulated locked table")
            return self.c.execute(sql, params)
        def executemany(self, *a):
            return self.c.executemany(*a)
        def commit(self):
            return self.c.commit()

    fc = Flaky(conn)
    normalize_iso_columns(fc, chunk=10)
    assert conn.execute("SELECT 1 FROM meta WHERE key='iso_norm_v1'").fetchone() is None, \
        "با شکست یک ستون، marker نهایی نباید نوشته شود"
    normalize_iso_columns(fc, chunk=10)
    assert conn.execute("SELECT 1 FROM meta WHERE key='iso_norm_v1:users.created_at'").fetchone(), \
        "ستون ناموفق در اجرای بعدی دوباره پردازش نشد"
    conn.close()
