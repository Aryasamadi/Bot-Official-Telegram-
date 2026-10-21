# -*- coding: utf-8 -*-
"""DB-27 — رخدادِ تولیدی ۲۰۲۶-۱۰-۲۱: «no such column: super_notified_at».

سرویس روی D1 بالا نمی‌آمد، چون ترتیبِ اجرای اسکیما اشتباه بود:

    _exec_schema(conn, SCHEMA + "\\n" + DB_INDEXES)   # ← ایندکس‌ها اینجا
    ...
    _apply_migrations(conn)                            # ← ستون‌های جدید اینجا (دیر!)

``CREATE TABLE IF NOT EXISTS`` روی جدولِ موجود، ستون اضافه نمی‌کند ⇒ روی یک دیتابیسِ
قدیمی، ``pay_requests`` هنوز ``super_notified_at`` ندارد ⇒ ایندکسِ
``idx_pay_superunnot`` که روی همان ستون است با «no such column» شکست می‌خورد ⇒
(روی D1) کل batch رول‌بک می‌شود و (روی SQLite) تراکنش ⇒ ``init_core()`` می‌میرد ⇒ سرویس بالا نمی‌آید.

این تست همان وضعیتِ «دیتابیسِ قدیمی» را می‌سازد و می‌خواهد:
  ۱) ستون‌های افزایشی **قبل** از ایندکس ساخته شوند (ترتیب درست)؛
  ۲) مهاجرت **افزایشی** باشد (هیچ داده‌ای حذف/بازنشانی نشود)؛
  ۳) **قابل‌تکرار** باشد (اجرای دوباره خطا ندهد).
"""
import json
import re
import sqlite3

import pytest


def _old_schema_db(path):
    """دیتابیسِ «قبل از PH-03»: جدولِ pay_requests بدونِ ستون‌های جدید."""
    con = sqlite3.connect(str(path))
    con.row_factory = sqlite3.Row
    con.execute(
        "CREATE TABLE pay_requests(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, plan_id INTEGER, "
        "status TEXT DEFAULT 'pending', created_at TEXT, receipt_chat INTEGER, receipt_msg INTEGER, "
        "note TEXT, decided_at TEXT, discount TEXT, final_price TEXT, cur TEXT DEFAULT '', "
        "decided_by INTEGER, notified_at TEXT)")
    con.execute("INSERT INTO pay_requests(user_id,plan_id,status,created_at,final_price) VALUES(7,1,'pending','2026-10-01','100')")
    con.commit()
    return con


def _run_schema(con, monkeypatch):
    """اسکیمای دیتابیسِ دلخواه را اجرا می‌کند (پرچمِ «یک‌بار در هر پروسه» را صفر می‌کند)."""
    from newsbot import db as dbmod
    monkeypatch.setattr(dbmod, "_SCHEMA_READY", False)
    dbmod._ensure_schema(con)


def test_old_database_gets_the_new_columns_before_the_indexes_are_created(tmp_path, monkeypatch):
    con = _old_schema_db(tmp_path / "old.db")
    try:
        _run_schema(con, monkeypatch)      # با ترتیبِ درست نباید خطا بدهد
        cols = {r[1] for r in con.execute("PRAGMA table_info(pay_requests)")}
        assert {"super_notified_at", "notify_attempts"} <= cols, "ستون‌های افزایشی ساخته نشدند"
        idx = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='index'")}
        assert "idx_pay_superunnot" in idx, "ایندکسِ وابسته به ستونِ جدید ساخته نشد"
        # دادهٔ قبلی سرِ جایش مانده (مهاجرت افزایشی است، نه بازنویسی)
        row = con.execute("SELECT user_id, final_price FROM pay_requests").fetchone()
        assert row["user_id"] == 7 and row["final_price"] == "100"
        # جدولِ اعلان‌ها هم باید ساخته شده باشد
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "notifications" in tables
    finally:
        con.close()


def test_migration_is_idempotent_on_an_old_database(tmp_path, monkeypatch):
    """اجرای دوبارهٔ مهاجرت روی همان دیتابیسِ قدیمی ⇒ هیچ خطایی، هیچ تغییری."""
    from newsbot.db import _apply_migrations
    con = _old_schema_db(tmp_path / "old.db")
    try:
        _run_schema(con, monkeypatch)
        before = {r[1] for r in con.execute("PRAGMA table_info(pay_requests)")}
        _apply_migrations(con)       # دوباره ⇒ باید بی‌اثر و بی‌خطا باشد
        after = {r[1] for r in con.execute("PRAGMA table_info(pay_requests)")}
        assert before == after
        assert con.execute("SELECT COUNT(*) c FROM pay_requests").fetchone()["c"] == 1
    finally:
        con.close()


def test_index_creation_does_not_precede_the_column_migration():
    """قراردادِ ترتیب: ایندکس‌ها **بعد** از مهاجرتِ افزایشی ساخته می‌شوند.

    این تست همان چیزی را نگه می‌دارد که خرابیِ تولید را ساخت: اگر کسی ترتیب را
    برگرداند، این تست می‌افتد (چون روی دیتابیسِ قدیمی، ایندکس شکست می‌خورد)."""
    import inspect
    from newsbot import db as dbmod
    src = inspect.getsource(dbmod._ensure_schema)
    i_schema = src.find("_exec_schema(conn, SCHEMA)")
    i_mig = src.find("_apply_migrations(conn)")
    i_idx = src.find("_exec_schema(conn, DB_INDEXES)")
    assert i_schema != -1 and i_mig != -1 and i_idx != -1, "ترتیبِ اجرا در کد پیدا نشد"
    assert i_schema < i_mig < i_idx, (
        "ترتیبِ اجرا غلط است: باید جدول‌ها → مهاجرتِ افزایشی → ایندکس‌ها باشد "
        "(وگرنه ایندکسِ وابسته به ستونِ جدید روی دیتابیسِ قدیمی «no such column» می‌دهد)")


def test_the_d1_migration_tool_also_creates_the_indexes_after_the_columns():
    """ابزارِ مهاجرتِ D1 هم باید همین ترتیب را داشته باشد."""
    import inspect
    from tools import migrate_sqlite_to_d1 as tool
    src = inspect.getsource(tool.ensure_schema)
    i_batch = src.find("d1_batch(")
    i_mig = src.find("for tbl, col, decl in MIGRATIONS")
    i_idx = src.find("d1_batch(client, [(s, ()) for s in idx_stmts])")
    assert i_batch != -1 and i_mig != -1 and i_idx != -1, "ترتیبِ ابزار پیدا نشد"
    assert i_batch < i_mig < i_idx, "ابزار هم باید ایندکس‌ها را بعد از مهاجرت بسازد"


# ── همان سناریو، روی مسیرِ D1 (با یک D1 ساختگی که مثلِ واقعیت خطا می‌دهد) ────────────
class _Resp:
    def __init__(self, payload, status=200):
        self.status_code = status
        self.text = json.dumps(payload)
        self._payload = payload

    def json(self):
        return self._payload


class FakeD1:
    """D1 ساختگی: جدول‌ها را نگه می‌دارد و مثلِ D1ِ واقعی، «no such column» می‌دهد."""

    def __init__(self, tables):
        self.tables = {t: list(c) for t, c in tables.items()}   # tbl -> ستون‌ها
        self.calls = []

    def _columns_of(self, tbl):
        return self.tables.get(tbl, [])

    def _exec_one(self, sql, params):
        self.calls.append(sql)
        # کامنت‌هایِ ابتدای دستور (که در SCHEMA هست) در SQLite/D1 بی‌اثرند
        stmt = "\n".join(l for l in sql.splitlines() if not l.strip().startswith("--")).strip()
        u = stmt.upper()
        if u.startswith("CREATE TABLE"):
            m = re.match(r"CREATE TABLE (?:IF NOT EXISTS )?([A-Za-z_][\w]*)\s*\((.*)\)\s*$", stmt, re.I | re.S)
            if m:
                cols = [c.strip().split()[0] for c in m.group(2).split(",") if c.strip()]
                self.tables.setdefault(m.group(1), cols)
            return _Resp({"success": True, "result": [{"results": [], "meta": {"changes": 0}}]})
        if u.startswith("CREATE INDEX"):
            m = re.search(r"ON\s+([A-Za-z_][\w]*)\s*\(([^)]*)\)", stmt, re.I)
            if m:
                tbl = m.group(1)
                for col in [c.strip() for c in m.group(2).split(",")]:
                    if col and col not in self._columns_of(tbl):
                        # D1 واقعی: «D1 HTTP 400: no such column: <col>»
                        return _Resp({"success": False, "errors": [{"code": "SQLITE_ERROR",
                                                                   "message": "no such column: %s" % col}]}, 400)
            return _Resp({"success": True, "result": [{"results": [], "meta": {"changes": 0}}]})
        if u.startswith("ALTER TABLE"):
            parts = stmt.split()
            tbl, col = parts[2], parts[parts.index("COLUMN") + 1]
            if col in self._columns_of(tbl):
                return _Resp({"success": False, "errors": [{"message": "duplicate column name: %s" % col}]}, 400)
            self.tables.setdefault(tbl, []).append(col)
            return _Resp({"success": True, "result": [{"results": [], "meta": {"changes": 1}}]})
        if u.startswith("PRAGMA TABLE_INFO"):
            m = re.search(r"TABLE_INFO\(([A-Za-z_][\w]*)\)", stmt, re.I)
            tbl = m.group(1) if m else ""
            rows = [{"name": c} for c in self._columns_of(tbl)]
            return _Resp({"success": True, "result": [{"results": rows}]})
        if u.startswith("SELECT NAME FROM PRAGMA_TABLE_INFO"):
            m = re.search(r"PRAGMA_TABLE_INFO\('([^']*)'\)", stmt, re.I)
            tbl = m.group(1) if m else ""
            rows = [{"name": c} for c in self._columns_of(tbl)]
            return _Resp({"success": True, "result": [{"results": rows}]})
        if "FROM SQLITE_MASTER" in u:
            params = params or []
            tbl = params[0] if params else ""
            if tbl in self.tables:
                cols = ", ".join("%s TEXT" % c for c in self.tables[tbl])
                return _Resp({"success": True, "result": [{"results": [{"sql": "CREATE TABLE %s (%s)" % (tbl, cols)}]}]})
            return _Resp({"success": True, "result": [{"results": []}]})
        if u.startswith("SELECT") or u.startswith("PRAGMA"):
            return _Resp({"success": True, "result": [{"results": []}]})
        return _Resp({"success": True, "result": [{"results": [], "meta": {"changes": 0}}]})

    def post(self, url, json=None, headers=None):
        payload = json or {}
        if "batch" in payload:
            results = []
            for item in payload["batch"]:
                r = self._exec_one(item.get("sql", ""), item.get("params") or [])
                if r.status_code >= 400:
                    return r                     # D1: شکستِ یک دستور ⇒ کل batch رول‌بک
                results.append(r.json()["result"][0])
            return _Resp({"success": True, "result": results})
        return self._exec_one(payload.get("sql", ""), payload.get("params") or [])


OLD_PAY_REQUESTS = ["id", "user_id", "plan_id", "status", "created_at", "receipt_chat", "receipt_msg",
                    "note", "decided_at", "discount", "final_price", "cur", "decided_by", "notified_at"]


def test_d1_startup_on_an_old_database_no_longer_fails(monkeypatch):
    """بازتولیدِ دقیقِ خرابیِ تولید روی D1: اسکیمای قدیمی ⇒ بالا آمدنِ سرویس."""
    from newsbot import db as dbmod
    from newsbot import config
    fake = FakeD1({"pay_requests": list(OLD_PAY_REQUESTS)})
    monkeypatch.setattr(dbmod, "_d1_client", lambda: fake)
    monkeypatch.setattr(dbmod, "_d1_url", lambda: "https://fake/d1/query")
    monkeypatch.setattr(config, "DB_BACKEND", "d1")
    monkeypatch.setattr(dbmod, "_SCHEMA_READY", False)
    conn = dbmod.D1Conn()
    dbmod._ensure_schema(conn)      # نباید استثنا بدهد
    assert "super_notified_at" in fake.tables["pay_requests"], "ستونِ افزایشی ساخته نشد"
    assert "notify_attempts" in fake.tables["pay_requests"]
    assert "notifications" in fake.tables, "جدولِ اعلان‌ها ساخته نشد"


def test_d1_old_database_reproduction_before_the_fix(monkeypatch):
    """شاهدِ منفی: با ترتیبِ قدیمی (ایندکس‌ها *قبل* از مهاجرت) همان خطای تولید رخ می‌دهد.

    همان دستوری که در تولید سرویس را زمین گذاشت: ``SCHEMA + DB_INDEXES`` یک‌جا، روی
    دیتابیسی که ستونِ ``super_notified_at`` را ندارد."""
    from newsbot import db as dbmod
    from newsbot import config
    fake = FakeD1({"pay_requests": list(OLD_PAY_REQUESTS)})
    monkeypatch.setattr(dbmod, "_d1_client", lambda: fake)
    monkeypatch.setattr(dbmod, "_d1_url", lambda: "https://fake/d1/query")
    monkeypatch.setattr(config, "DB_BACKEND", "d1")
    conn = dbmod.D1Conn()
    with pytest.raises(sqlite3.OperationalError) as ei:
        dbmod._exec_schema(conn, dbmod.SCHEMA + "\n" + dbmod.DB_INDEXES)   # ترتیبِ قدیمی
    assert "no such column: super_notified_at" in str(ei.value)
