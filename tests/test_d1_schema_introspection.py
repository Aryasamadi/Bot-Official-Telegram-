# -*- coding: utf-8 -*-
"""DB-27 — تشخیصِ ستون‌های موجود روی SQLite و Cloudflare D1.

رخداد تولید ۲۰۲۶-۱۰-۲۱: «D1 HTTP 400 … duplicate column name: next_plan_id» و در
نتیجه افتادنِ برنامه در استارتاپ (و با Restart=always، حلقهٔ ری‌استارت و ۱۰۰٪ CPU).

ریشه: ``_table_cols()`` روی D1 **مجموعهٔ خالی** برمی‌گرداند — چون
(۱) ``PRAGMA table_info`` روی D1 no-op بود و (۲) خطای واقعیِ خواندنِ schema پنهان و
به «مجموعهٔ خالی» تبدیل می‌شد ⇒ migration ستونِ موجود را دوباره اضافه می‌کرد.

این فایل ثابت می‌کند که:
  * تشخیصِ ستون روی D1 (با هر ترکیبی از توانایی‌های D1) و روی SQLite درست کار می‌کند؛
  * خطای واقعیِ خواندنِ schema **پنهان نمی‌شود** (به مجموعهٔ خالی تبدیل نمی‌شود)؛
  * migration ستونِ موجود را دوباره اضافه نمی‌کند و خطای هم‌زمان را تحمل می‌کند.
"""
import json
import re
import sqlite3

import pytest

import newsbot.db as dbmod
from newsbot.db import D1BatchConn, D1Conn, _apply_migrations, _create_sql_cols, _table_cols


# ── سرورِ ساختگیِ D1 ──────────────────────────────────────────────────────────────────
class _Resp:
    def __init__(self, payload, status=200):
        self.status_code = status
        self.text = json.dumps(payload)          # پیامِ خطا باید متنِ واقعی را داشته باشد
        self._payload = payload

    def json(self):
        return self._payload


class FakeD1:
    """D1 ساختگی با سه تواناییِ مستقل: PRAGMA table_info، تابعِ table-valued و sqlite_master."""

    def __init__(self, tables=None, support=("pragma", "fn", "master"), pragma_empty=False):
        self.tables = {}                        # tbl -> create-sql
        for tbl, cols in (tables or {}).items():
            self.tables[tbl] = "CREATE TABLE %s (%s)" % (tbl, ", ".join("%s TEXT" % c for c in cols))
        self.support = support                  # توانایی‌هایی که این سرور ادعا می‌کند دارد
        self.pragma_empty = pragma_empty        # PRAGMA بی‌صدا خالی برمی‌گرداند (رفتارِ قدیمی)
        self.calls = []
        self.alters = []

    # ── کمکی‌ها ──
    def _cols(self, tbl):
        return _create_sql_cols(self.tables.get(tbl))

    def _table_of(self, sql, params):
        m = re.search(r"pragma_table_info\('([^']*)'\)", sql, re.I)
        if m:
            return m.group(1)
        return (params or [None])[0]

    def post(self, url, json=None, headers=None):
        payload = json or {}
        if "batch" in payload:
            results = []
            for item in payload["batch"]:
                r = self._exec_one(item.get("sql", ""), item.get("params") or [])
                if r.status_code >= 400:
                    return r                      # D1: شکستِ یک دستور ⇒ کل batch رول‌بک
                results.append(r.json()["result"][0])
            return _Resp({"success": True, "result": results})
        return self._exec_one(payload.get("sql", ""), payload.get("params") or [])

    def _exec_one(self, sql, params):
        self.calls.append(sql)
        u = sql.strip().upper()
        if u.startswith("PRAGMA TABLE_INFO"):
            if "pragma" not in self.support:
                return _Resp({"success": False, "errors": [{"message": "PRAGMA not supported"}]}, 400)
            tbl = sql.strip()[len("PRAGMA table_info("):-1].strip()
            if self.pragma_empty:
                return _Resp({"success": True, "result": [{"results": []}]})
            cols = sorted(self._cols(tbl))
            rows = [{"cid": i, "name": c, "type": "TEXT", "notnull": 0, "dflt_value": None, "pk": 0}
                    for i, c in enumerate(cols)]
            return _Resp({"success": True, "result": [{"results": rows}]})
        if u.startswith("SELECT NAME FROM PRAGMA_TABLE_INFO"):
            if "fn" not in self.support:
                return _Resp({"success": False, "errors": [{"message": "no such function"}]}, 400)
            tbl = self._table_of(sql, params)
            rows = [{"name": c} for c in sorted(self._cols(tbl))]
            return _Resp({"success": True, "result": [{"results": rows}]})
        if "FROM SQLITE_MASTER" in u:
            if "master" not in self.support:
                return _Resp({"success": False, "errors": [{"message": "not supported"}]}, 400)
            tbl = (params or [None])[0]
            if tbl not in self.tables:
                return _Resp({"success": True, "result": [{"results": []}]})
            return _Resp({"success": True, "result": [{"results": [{"sql": self.tables[tbl]}]}]})
        if u.startswith("CREATE TABLE"):
            m = re.match(r"CREATE TABLE (?:IF NOT EXISTS )?([A-Za-z_][\w]*)\s*\((.*)\)\s*$",
                         sql.strip(), re.I | re.S)
            if m:
                self.tables[m.group(1)] = "CREATE TABLE %s (%s)" % (m.group(1), m.group(2))
            return _Resp({"success": True, "result": [{"results": [], "meta": {"changes": 0}}]})
        if u.startswith("ALTER TABLE"):
            self.alters.append(sql)
            parts = sql.split()
            tbl, col = parts[2], parts[parts.index("COLUMN") + 1]
            if col in self._cols(tbl):
                return _Resp({"success": False,
                              "errors": [{"code": "SQLITE_ERROR", "message": "duplicate column name: %s" % col}]},
                             400)
            self.tables[tbl] = self.tables.get(tbl, "CREATE TABLE %s ()" % tbl)[:-1] + ", %s TEXT)" % col
            return _Resp({"success": True, "result": [{"results": [], "meta": {"changes": 1}}]})
        return _Resp({"success": True, "result": [{"results": [], "meta": {"changes": 0}}]})


def _d1(fake, monkeypatch):
    monkeypatch.setattr(dbmod, "_d1_client", lambda: fake)
    monkeypatch.setattr(dbmod, "_d1_url", lambda: "https://fake/d1/query")
    return D1Conn()


# ── SQLite ──────────────────────────────────────────────────────────────────────────
def test_table_cols_sqlite_reads_existing_columns():
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.execute("CREATE TABLE users(id INTEGER PRIMARY KEY, username TEXT, next_plan_id INTEGER)")
    assert _table_cols(con, "users") == {"id", "username", "next_plan_id"}


def test_migration_does_not_re_add_existing_column_on_sqlite():
    """رگرسیون SQLite: ستونِ موجود نباید دوباره ADD COLUMN شود (و مهاجرت باید کار کند)."""
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript(dbmod.SCHEMA)
    con.execute("CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT)")
    stmts = []
    con.set_trace_callback(stmts.append)
    _apply_migrations(con)
    adds = [s for s in stmts if "ADD COLUMN" in s.upper()]
    assert not [s for s in adds if "next_plan_id" in s], "ستونِ موجود دوباره اضافه شد"
    assert not [s for s in adds if "next_plan_days" in s], "ستونِ موجود دوباره اضافه شد"
    assert [s for s in adds if "credit" in s], "ستونِ واقعاً غایب باید اضافه شود"


# ── D1 ───────────────────────────────────────────────────────────────────────────────
def test_table_cols_d1_reads_columns_via_pragma(monkeypatch):
    fake = FakeD1({"users": ["id", "username", "next_plan_id"]})
    conn = _d1(fake, monkeypatch)
    assert _table_cols(conn, "users") == {"id", "username", "next_plan_id"}
    assert any("PRAGMA table_info" in c for c in fake.calls), "PRAGMA باید واقعاً به D1 برود"


def test_table_cols_d1_falls_back_to_table_valued_function(monkeypatch):
    """D1 بدون PRAGMA ⇒ تابعِ table-valued."""
    fake = FakeD1({"users": ["id", "next_plan_id"]}, support=("fn", "master"))
    conn = _d1(fake, monkeypatch)
    assert _table_cols(conn, "users") == {"id", "next_plan_id"}


def test_table_cols_d1_falls_back_to_sqlite_master(monkeypatch):
    """D1 بدون PRAGMA و بدون تابع ⇒ تعریفِ CREATE TABLE از sqlite_master."""
    fake = FakeD1({"users": ["id", "next_plan_id"]}, support=("master",))
    conn = _d1(fake, monkeypatch)
    assert _table_cols(conn, "users") == {"id", "next_plan_id"}


def test_table_cols_d1_works_when_pragma_returns_empty(monkeypatch):
    """رفتارِ قدیمیِ D1 (PRAGMA بی‌صدا خالی + تابع خطا) نباید به «ستونِ ناموجود» برسد."""
    fake = FakeD1({"users": ["id", "next_plan_id"]}, support=("master",), pragma_empty=True)
    conn = _d1(fake, monkeypatch)
    assert _table_cols(conn, "users") == {"id", "next_plan_id"}


def test_table_cols_raises_instead_of_returning_empty_on_error(monkeypatch):
    """DB-27: خطای واقعیِ خواندنِ schema نباید به مجموعهٔ خالی تبدیل شود."""
    fake = FakeD1({"users": ["id"]}, support=())
    conn = _d1(fake, monkeypatch)
    with pytest.raises(RuntimeError):
        _table_cols(conn, "users")


def test_table_cols_empty_when_table_missing(monkeypatch):
    """جدولِ ناموجود ⇒ مجموعهٔ خالی (درست) — نه خطا."""
    fake = FakeD1({"other": ["id"]})
    conn = _d1(fake, monkeypatch)
    assert _table_cols(conn, "users") == set()


def test_migration_does_not_re_add_existing_column_on_d1(monkeypatch):
    """رگرسیونِ اصلیِ رخداد: کاربری که next_plan_id را دارد نباید «duplicate column» بخورد."""
    users = ["id", "username", "next_plan_id", "next_plan_days", "lang", "remind_key", "premium", "credit"]
    fake = FakeD1({"users": users})
    conn = _d1(fake, monkeypatch)
    _apply_migrations(conn)
    assert not [a for a in fake.alters if "users" in a.lower()], \
        "ستون‌های موجودِ users نباید دوباره ADD COLUMN شوند: %r" % (fake.alters,)


def test_migration_tolerates_duplicate_column_error(monkeypatch):
    """خطای هم‌زمان/بررسیِ کهنه ⇒ «duplicate column» تحمل می‌شود و مهاجرت ادامه می‌یابد."""
    fake = FakeD1({"users": ["id", "next_plan_id"]})
    conn = _d1(fake, monkeypatch)
    real_table_cols = dbmod._table_cols

    def stale_table_cols(c, tbl):                 # بررسیِ کهنه: ستون را «ناموجود» نشان می‌دهد
        cols = real_table_cols(c, tbl)
        return {x for x in cols if x != "next_plan_id"}

    monkeypatch.setattr(dbmod, "_table_cols", stale_table_cols)
    _apply_migrations(conn)                      # نباید استثنا بدهد
    assert any("next_plan_id" in a for a in fake.alters), "باید تلاش برای افزودن کرده باشد"
    assert any("next_plan_days" in a for a in fake.alters), "مهاجرت باید ادامه پیدا کند"


def test_d1_conn_executes_readonly_pragma_for_real(monkeypatch):
    """PRAGMA فقط‌خواندنی باید واقعاً اجرا شود؛ PRAGMA نوشتنی همچنان no-op بماند."""
    fake = FakeD1({"users": ["id", "next_plan_id"]})
    conn = _d1(fake, monkeypatch)
    rows = conn.execute("PRAGMA table_info(users)").fetchall()
    assert [r["name"] for r in rows] == ["id", "next_plan_id"]
    assert any("PRAGMA table_info" in c for c in fake.calls)
    n = len(fake.calls)
    cur = conn.execute("PRAGMA journal_mode=WAL")     # نوشتنی ⇒ D1 پسش می‌زند ⇒ no-op
    assert cur.fetchall() == []
    assert len(fake.calls) == n, "PRAGMA نوشتنی نباید به D1 ارسال شود"


def test_d1_batch_conn_executes_readonly_pragma_for_real(monkeypatch):
    """در مسیرِ batch هم PRAGMA فقط‌خواندنی باید واقعاً اجرا شود."""
    fake = FakeD1({"users": ["id"]})
    monkeypatch.setattr(dbmod, "_d1_client", lambda: fake)
    monkeypatch.setattr(dbmod, "_d1_url", lambda: "https://fake/d1/query")
    conn = D1BatchConn(D1Conn())
    rows = conn.execute("PRAGMA table_info(users)").fetchall()
    assert [r["name"] for r in rows] == ["id"]
    assert any("PRAGMA table_info" in c for c in fake.calls)


# ── تجزیهٔ CREATE TABLE ─────────────────────────────────────────────────────────────
def test_create_sql_cols_parses_constraints_and_defaults():
    cols = _create_sql_cols("CREATE TABLE IF NOT EXISTS usage(admin_id INTEGER, day TEXT, "
                             "posts INTEGER DEFAULT 0, PRIMARY KEY(admin_id, day))")
    assert cols == {"admin_id", "day", "posts"}


def test_create_sql_cols_handles_quoted_default_with_comma():
    cols = _create_sql_cols("CREATE TABLE t(a TEXT DEFAULT 'x,y', b INTEGER, UNIQUE(a, b))")
    assert cols == {"a", "b"}


def test_create_sql_cols_parses_real_schema_tables():
    cols = _create_sql_cols("CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, username TEXT, "
                             "name TEXT, role TEXT DEFAULT 'user', plan_id INTEGER, free_used INTEGER DEFAULT 0)")
    assert cols == {"id", "username", "name", "role", "plan_id", "free_used"}


# ── سناریوی کاملِ استارتاپ روی D1 ───────────────────────────────────────────────────
def test_ensure_schema_bootstrap_on_d1_succeeds(monkeypatch):
    """رگرسیونِ کاملِ رخداد: بالا آمدنِ برنامه روی D1 نباید به «duplicate column» بخورد.

    همهٔ مسیرِ استارتاپ (_ensure_schema ⇒ SCHEMA ⇒ migration ⇒ trigger ⇒ index ⇒ …) روی
    یک D1 ساختگی اجرا می‌شود؛ هیچ «ALTER TABLE … ADD COLUMN» نباید برای ستونی که از قبل
    هست صادر شود."""
    import newsbot.config as config
    monkeypatch.setattr(config, "DB_BACKEND", "d1")
    monkeypatch.setattr(dbmod, "_SCHEMA_READY", False)
    fake = FakeD1()
    conn = _d1(fake, monkeypatch)
    dbmod._ensure_schema(conn)                      # نباید استثنا بدهد
    assert not [a for a in fake.alters if "next_plan_id" in a], \
        "ستونِ موجود دوباره اضافه شد: %r" % (fake.alters,)
    assert "users" in fake.tables
    assert "next_plan_id" in _create_sql_cols(fake.tables["users"])
