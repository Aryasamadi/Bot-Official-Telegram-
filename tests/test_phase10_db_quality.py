# -*- coding: utf-8 -*-
"""فاز ۱۰ — کیفیت داده، ابزار مهاجرت و اسکیما (DB-02…DB-26 + BIZ-08).
هر تست شاهدِ یکی از یافته‌های ممیزی فاز ۱۰ است و اگر رگرسیون برگردد می‌شکند.
"""
import asyncio
import inspect
import os
import sqlite3
import time
from datetime import datetime, timedelta, timezone

import pytest

from newsbot import config
from newsbot.db import q, gset, db as dbcon, normalize_iso_columns
from newsbot.timeutils import now_iso, now_utc, iso_fixed, today_str
from newsbot.core_users import usage_reset, channel_posts_today, admin_offset
from newsbot.core_articles import article_insert, get_article, cleanup, _backup_dir

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_migrate_tool():
    import tools.migrate_sqlite_to_d1 as m
    return m


# ── DB-25: نسخهٔ اسکیما تک‌منبع است ──

def test_schema_version_single_source_is_7():
    import newsbot
    import newsbot.db as dbmod
    assert newsbot.SCHEMA_VERSION == 7, "فاز ۱۴: ستون plans.price_num_fa (تعرفهٔ تومانی) ⇒ نسخه ۷"
    assert dbmod.SCHEMA_VERSION == 7, "db.py باید همان نسخهٔ پکیج را import کند، نه تعریف محلی"
    assert q("SELECT value FROM meta WHERE key='schema_version'", one=True)["value"] == "7"


# ── DB-12: ایندکس‌های یکتا با تعریف مشترک + محافظ ردیف تکراری ──

def test_unique_indexes_shared_definition_and_installed():
    import newsbot.db as dbmod
    labels = {l for l, _, _ in dbmod.UNIQUE_INDEXES}
    assert labels == {"channels(chat_id)", "sources(channel_id,url)"}
    names = {r["name"] for r in q("SELECT name FROM sqlite_master WHERE type='index'")}
    assert {"idx_channels_chat_id", "idx_sources_channel_url"} <= names, "ایندکس‌های یکتا در دیتابیس شیزنی نصب نشده‌اند"


def test_unique_index_guard_skips_duplicates_without_deleting_data():
    import newsbot.db as dbmod
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.execute("CREATE TABLE channels(id INTEGER PRIMARY KEY, chat_id INTEGER)")
    con.execute("CREATE TABLE sources(id INTEGER PRIMARY KEY, channel_id INTEGER, url TEXT)")
    con.execute("CREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT)")
    con.execute("INSERT INTO channels(id, chat_id) VALUES(1,5),(2,5)")   # تکراری عمدی
    dbmod._ensure_unique_indexes(con)
    idx = {r["name"] for r in con.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    assert "idx_channels_chat_id" not in idx, "با ردیف تکراری نباید ایندکس یکتا ساخته شود"
    assert "idx_sources_channel_url" in idx
    assert con.execute("SELECT COUNT(*) FROM channels").fetchone()[0] == 2, "داده‌ای نباید حذف شود"
    warn = con.execute("SELECT value FROM settings WHERE key='db_dup_warn'").fetchone()
    assert warn and "channels(chat_id)" in warn["value"], "هشدار ماندگار ثبت نشد"


# ── DB-14: قالب زمانی کانونیکال + کانونی‌سازی یک‌باره ──

def test_iso_fixed_canonical_format():
    d = datetime(2026, 1, 5, 10, 0, 0, tzinfo=timezone.utc)
    assert iso_fixed(d) == "2026-01-05T10:00:00.000000+00:00"
    d2 = datetime(2026, 1, 5, 13, 30, 0, tzinfo=timezone(timedelta(hours=3, minutes=30)))
    assert iso_fixed(d2) == "2026-01-05T10:00:00.000000+00:00", "آفست باید به UTC تبدیل شود"
    naive = datetime(2026, 1, 5, 10, 0, 0)
    assert iso_fixed(naive) == "2026-01-05T10:00:00.000000+00:00", "بیtz باید UTC فرض شود، نه ساعت محلی سیستم"
    assert now_iso().endswith("+00:00") and len(now_iso().split(".")[1]) == len("000000+00:00")


def test_normalize_iso_columns_rewrites_legacy_and_is_idempotent():
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.execute("CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT)")
    con.execute("CREATE TABLE articles(id INTEGER PRIMARY KEY, created_at TEXT)")
    con.execute("INSERT INTO articles(created_at) VALUES('2026-01-05T10:00:00')")         # بدون tz/کسرِ ثانیه
    con.execute("INSERT INTO articles(created_at) VALUES('2026-01-05T13:30:00+03:30')")   # آفست غیر UTC
    n = normalize_iso_columns(con)
    assert n == 2
    vals = [r[0] for r in con.execute("SELECT created_at FROM articles ORDER BY id").fetchall()]
    assert vals == ["2026-01-05T10:00:00.000000+00:00"] * 2
    assert normalize_iso_columns(con) == 0, "اجرای دوم باید no-op باشد (کلید iso_norm_v1)"


def test_assign_writes_persists_canonical_expiry():
    from newsbot import core_plans as cp
    src = inspect.getsource(cp._assign_writes)
    assert "iso_fixed(" in src, "plan_expires باید با قالب کانونیکال نوشته شود (DB-14)"


# ── DB-13: شمارش روزانهٔ کانال ایندکس‌محور است ──

def test_channel_posts_today_uses_indexed_predicate():
    chat_id = -10099100001
    cid = q("INSERT INTO channels(admin_id,chat_id,title,created_at,settings) VALUES(?,?,?,?,?)",
            (900100, chat_id, "t-qa10", now_iso(), "{}"), commit=True)
    q("INSERT OR REPLACE INTO posted(channel_id,hash,posted_at) VALUES(?,?,?)",
      (chat_id, "h-qa10", now_iso()), commit=True)
    assert channel_posts_today(cid, 0) >= 1
    cut = iso_fixed(now_utc())
    plan = " ".join(r["detail"] for r in dbcon().execute(
        "EXPLAIN QUERY PLAN SELECT COUNT(*) c FROM posted WHERE channel_id=? AND posted_at>=?",
        (chat_id, cut)).fetchall())
    assert "idx_posted_ch_at" in plan, f"شمارش باید روی ایندکس باشد، plan فعلی: {plan}"


# ── DB-19: seed پلن‌ها اتمیک/ترمیم‌کننده است ──

def test_seed_plans_idempotent_and_repairs_partial_state():
    from newsbot import core_plans as cp
    assert isinstance(cp._BASE_PLANS, list) and len(cp._BASE_PLANS) >= 2
    cp.seed_plans()
    n1 = q("SELECT COUNT(*) c FROM plans", one=True)["c"]
    cp.seed_plans()
    assert q("SELECT COUNT(*) c FROM plans", one=True)["c"] == n1, "seed دوباره پلن تکراری ساخت"
    for p in cp._BASE_PLANS:
        c = q("SELECT COUNT(*) c FROM plans WHERE name=? AND name_en=?", (p["name"], p["name_en"]), one=True)["c"]
        assert c == 1, f"پلن پایه {p['name']} باید دقیقاً یک نسخه داشته باشد"


# ── DB-17: درج support_map با ستون‌های صریح ──

def test_support_map_insert_uses_explicit_columns():
    from newsbot import core_payments as pay
    src = inspect.getsource(pay.support_map_set)
    assert "support_map(msg_id,user_id,ts)" in src, "درج موقعیتی باید به صریح تغییر کند"


# ── DB-26 / BIZ-08: فهرست سفید usage_reset و معنای دقیق بازنشانی ──

def test_usage_reset_whitelist_and_semantics():
    uid = 900200
    q("INSERT OR REPLACE INTO users(id, role) VALUES(?, 'admin')", (uid,), commit=True)
    day = today_str(admin_offset(uid))
    q("INSERT OR REPLACE INTO usage(admin_id,day,posts,tests,ai) VALUES(?,?,5,5,5)", (uid, day), commit=True)
    usage_reset(uid, "posts")
    r = q("SELECT posts,tests,ai FROM usage WHERE admin_id=? AND day=?", (uid, day), one=True)
    assert (r["posts"], r["tests"], r["ai"]) == (0, 5, 5)
    with pytest.raises(ValueError):
        usage_reset(uid, "credit; DROP TABLE usage")   # قبلاً f-string بدون اعتبارسنجی بود


def test_usage_reset_does_not_touch_channel_caps_biz08():
    uid = 900201
    chat_id = -10099100002
    q("INSERT OR REPLACE INTO users(id, role) VALUES(?, 'admin')", (uid,), commit=True)
    cid = q("INSERT INTO channels(admin_id,chat_id,title,created_at,settings) VALUES(?,?,?,?,?)",
            (uid, chat_id, "t-qa10b", now_iso(), "{}"), commit=True)
    q("INSERT OR REPLACE INTO posted(channel_id,hash,posted_at) VALUES(?,?,?)",
      (chat_id, "h-qa10b", now_iso()), commit=True)
    before = channel_posts_today(cid, 0)
    assert before >= 1
    usage_reset(uid, "posts")   # بازنشانی سهمیهٔ پلن…
    assert channel_posts_today(cid, 0) == before, "BIZ-08: شمارش posted (سقف کانال) نباید تغییر کند"


# ── DB-20: قفل تک‌نسخه روی ویندوز هم کار می‌کند ──

def test_single_instance_lock_blocks_second_window():
    from newsbot import app as appmod
    appmod._acquire_single_instance_lock()
    with pytest.raises(SystemExit):
        appmod._acquire_single_instance_lock()   # نسخهٔ دوم باید رد شود (fcntl/msvcrt)


# ── DB-24: اجرای اسکیما اتمیک است ──

def test_exec_schema_sqlite_and_d1_atomic_batch(monkeypatch):
    import newsbot.db as dbmod
    dbmod._exec_schema(dbcon(), "CREATE TABLE IF NOT EXISTS t_db24(a INTEGER); CREATE INDEX IF NOT EXISTS i_db24 ON t_db24(a)")
    assert q("SELECT name FROM sqlite_master WHERE name='i_db24'", one=True)
    calls = []
    monkeypatch.setattr(dbmod, "_d1_batch", lambda stmts: calls.append(list(stmts)) or [])
    monkeypatch.setattr(dbmod.config, "DB_BACKEND", "d1")
    dbmod._exec_schema(None, "CREATE TABLE a1(x); CREATE TABLE b1(y)")
    assert len(calls) == 1, "روی D1 کل اسکیپت باید در یک درخواست batch برود"
    assert len(calls[0]) == 2


# ── DB-23: آرشیو ۳۶۵روزه با بکاپ اتمیک + بهداشت VACUUM ──

def _mk_article_days_old(days, status="published"):
    aid = article_insert(1, 900300, f"h-arc{time.time()}", "https://x.test/arc", "t-arc", 0, None, status=status)
    old = (now_utc() - timedelta(days=days)).isoformat()
    q("UPDATE articles SET created_at=? WHERE id=?", (old, aid), commit=True)
    return aid


def test_article_older_than_archive_window_deleted_with_backup():
    before = set(os.listdir(_backup_dir())) if os.path.isdir(_backup_dir()) else set()
    aid = _mk_article_days_old(config.ARCHIVE_DAYS + 35)
    gset("last_cleanup", 0)
    cleanup()
    assert get_article(aid) is None, "مقالهٔ قدیمی‌تر از پنجرهٔ آرشیو باید حذف شود (تصمیم ۱۴)"
    after = set(os.listdir(_backup_dir()))
    new_files = after - before
    assert new_files, "پیش از حذف آرشیوی باید بکاپ اتمیک گرفته شود"
    newest = os.path.join(_backup_dir(), sorted(new_files)[-1])
    con = sqlite3.connect(newest)
    try:
        found = con.execute("SELECT COUNT(*) FROM articles WHERE id=?", (aid,)).fetchone()[0]
    finally:
        con.close()
    assert found == 1, "مقالهٔ آرشیوشده باید داخل بکاپ موجود باشد"


def test_maybe_vacuum_thresholds_and_throttle(monkeypatch):
    from newsbot import core_articles as ca
    gset("last_vacuum", 0)
    monkeypatch.setattr(ca, "VACUUM_FREELIST_MIN", 10 ** 9)
    assert ca._maybe_vacuum(dbcon()) is False, "زیر آستانهٔ freelist نباید VACUUM شود"
    monkeypatch.setattr(ca, "VACUUM_FREELIST_MIN", 0)
    assert ca._maybe_vacuum(dbcon()) is True
    assert ca._maybe_vacuum(dbcon()) is False, "VACUUM نباید ماهی بیش از یک‌بار اجرا شود"


# ── DB-02/03/11/21: ابزار مهاجرت ──

def test_migrate_ensure_schema_is_single_batch_with_meta_and_trigger():
    m = _load_migrate_tool()

    class _Resp:
        status_code = 200
        text = ""
        def json(self): return {"success": True, "result": [{"results": [], "meta": {}} for _ in range(300)]}
        def raise_for_status(self): pass

    class _AC:
        def __init__(self): self.payloads = []
        async def post(self, url, json=None, headers=None, timeout=None):
            self.payloads.append(json)
            return _Resp()

    ac = _AC()
    asyncio.run(m.ensure_schema(ac))
    first = ac.payloads[0]
    assert "batch" in first, "DB-24: DDL باید batch اتمیک باشد"
    sqls = [p["sql"] for p in first["batch"]]
    assert any("CREATE TABLE IF NOT EXISTS meta" in s for s in sqls), "DB-02: meta باید داخل همان batch ساخته شود"
    assert any("trg_referral_credit_wallet" in s for s in sqls), "trigger ارجاع باید داخل همان batch باشد"


def test_migrate_check_target_guards(monkeypatch):
    m = _load_migrate_tool()

    async def rows(client, sql, params=()):
        if "COUNT(*)" in sql: return {"results": [{"u": 3, "c": 1, "p": 0, "r": 0}]}
        return {"results": [{"value": "3"}]}
    monkeypatch.setattr(m, "d1", rows)
    with pytest.raises(SystemExit):
        asyncio.run(m.check_target(None, False))     # مقصد دارای داده بدون --force
    asyncio.run(m.check_target(None, True))          # با --force و نسخهٔ قدیمی‌تر ⇒ مجاز

    async def newer(client, sql, params=()):
        if "COUNT(*)" in sql: return {"results": [{"u": 3, "c": 1, "p": 0, "r": 0}]}
        return {"results": [{"value": "99"}]}
    monkeypatch.setattr(m, "d1", newer)
    with pytest.raises(SystemExit):
        asyncio.run(m.check_target(None, True))      # نسخهٔ جدیدتر مقصد ⇒ حتی با --force ممنوع

    async def empty(client, sql, params=()):
        if "COUNT(*)" in sql: return {"results": [{"u": 0, "c": 0, "p": 0, "r": 0}]}
        return {"results": []}
    monkeypatch.setattr(m, "d1", empty)
    asyncio.run(m.check_target(None, False))         # مقصد خالی ⇒ بدون نیرو مجاز


def test_migrate_tool_has_snapshot_and_heartbeat_protocol():
    src = open(os.path.join(ROOT, "tools", "migrate_sqlite_to_d1.py"), encoding="utf-8").read()
    assert "def snapshot_source" in src and ".backup(" in src, "DB-21: snapshot سازگار لازم است"
    assert "def heartbeat_age_sec" in src and "LIVE_HEARTBEAT_SEC" in src, "DB-21: گارد ربات زنده لازم است"
    assert "os.remove(snap)" in src, "snapshot موقت باید پاک شود"


def test_verify_and_restore_use_ordered_rowid_pagination():
    m = _load_migrate_tool()
    assert "ORDER BY rowid" in inspect.getsource(m.verify), "DB-11: صفحه‌بندی verify باید مرتب باشد"
    rsrc = open(os.path.join(ROOT, "tools", "restore_d1_to_sqlite.py"), encoding="utf-8").read()
    assert "ORDER BY rowid" in rsrc, "DB-11: fetch_all restore باید مرتب باشد"
    assert "verify(con, client)" in rsrc, "restore باید پس از کپی verify کند"
    assert "REFERRAL_CREDIT_TRIGGER" in rsrc and "UNIQUE_INDEXES" in rsrc


def test_duplicate_alert_row_candidate_count_matches_migration_helpers():
    """DB-12 — شمارش پلن‌های تکراری در migrate و db یکسان است (هر دو از یک منبع)."""
    import newsbot.db as dbmod
    m = _load_migrate_tool()
    assert m.UNIQUE_INDEXES is dbmod.UNIQUE_INDEXES, "تعریف‌ها باید همان شیء باشند، نه کپی واگرا"
