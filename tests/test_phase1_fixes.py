# -*- coding: utf-8 -*-
"""فاز ۱ — اعتبارسنجی اصلاحات تأییدشدهٔ بازبینی PR#1 روی main.

این تست‌ها مستقیماً رفع‌های زیر را می‌سنجند (بدون نیاز به شبکه/Cloudflare):
  * DB-05/PR1-06: claim شرطیِ اعتبار ارجاع + trigger اتمیک کیف پول (بدون credit دوباره در رقابت)
  * PR1-07:       کرسر D1 هم‌رفتار با sqlite (fetchone پیشروی می‌کند)
  * DB-01 (مسیر تک‌دستور): last_row_id از meta همان پاسخ (بدون درخواست جداگانهٔ last_insert_rowid)
  * PR1-08:       ابزار migrate جدول meta را می‌سازد (ورنگرانه: تست با شبیه‌ساز client)
  * PR1-09:       verify با fingerprint محتوایی سطرها، نه شمارشِ صرف
"""
import asyncio
import importlib.util
import os

from newsbot import config
from newsbot.db import q, REFERRAL_CREDIT_TRIGGER
from newsbot.core_referrals import credit_referral_on_payment, record_referral, referral_stats
from newsbot.timeutils import now_iso

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ── DB-05/PR1-06: اتمیک بودن اعتبار ارجاع ──

def _referral_trigger_installed():
    return q("SELECT name FROM sqlite_master WHERE type='trigger' AND name='trg_referral_credit_wallet'", one=True) is not None


def test_referral_credit_trigger_installed():
    """trigger اتمیک کیف پول باید بخشی از اسکیمای عادی باشد (v3)."""
    assert _referral_trigger_installed(), "trigger trg_referral_credit_wallet نصب نیست (اسکیمای v3)"


def test_referral_race_and_cap_credit_wallet_exactly_once(monkeypatch):
    """رقابتِ هم‌زمان + سقفِ ۱: فقط یک‌بار credit به کیف پول اضافه می‌شود."""
    from concurrent.futures import ThreadPoolExecutor

    referrer, a, b = 88101, 88102, 88103
    for uid, name in ((referrer, "R"), (a, "A"), (b, "B")):
        q("INSERT OR IGNORE INTO users(id,username,name,role,created_at,last_seen) VALUES(?,?,?,?,?,?)",
          (uid, name.lower(), name, "user", now_iso(), now_iso()), commit=True)
    q("DELETE FROM referrals WHERE referred_id IN (?,?,?)", (a, b, 88104), commit=True)
    q("UPDATE users SET credit=0 WHERE id=?", (referrer,), commit=True)

    # انتساب‌ها (یکتا، قبلاً تست شده) — فقط برای ساختِ ردیف‌های credited=0
    assert record_referral(referrer, a) is True
    assert record_referral(referrer, b) is True

    monkeypatch.setattr(config, "REFERRAL_MAX_PER_USER", 1)
    calls = [a] * 4 + [b] * 4
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda uid: credit_referral_on_payment(uid, 100.0), calls))

    expected = round(100.0 * config.REFERRAL_PERCENT / 100, 2)
    credited = [x for x in results if x is not None]
    assert len(credited) == 1, f"با سقف ۱ فقط یک credit مجاز است؛ {len(credited)} ثبت شد"
    rid, amt = credited[0]
    assert rid == referrer and amt == expected

    wallet = q("SELECT credit FROM users WHERE id=?", (referrer,), one=True)
    assert round(float(wallet["credit"] or 0), 2) == expected, "کیف پول باید دقیقاً یک‌بار شارژ شده باشد"

    rows = q("SELECT referred_id, credited, credit_amount FROM referrals WHERE referred_id IN (?,?)", (a, b))
    assert len(rows) == 2 and all(r["credited"] == 1 for r in rows)
    assert round(sum(float(r["credit_amount"] or 0) for r in rows), 2) == expected
    assert referral_stats(referrer)[0] == 2

    # idempotency: تأیید مجدد هیچ credit تازه‌ای نمی‌سازد
    assert credit_referral_on_payment(a, 100.0) is None
    assert round(float(q("SELECT credit FROM users WHERE id=?", (referrer,), one=True)["credit"] or 0), 2) == expected


def test_referral_claim_is_single_statement_claim():
    """ساختار SQL: claim باید یک UPDATE شرطیِ تک‌دستوره با RETURNING باشد (نه read+write جدا)."""
    import inspect
    from newsbot import core_referrals
    src = inspect.getsource(core_referrals.credit_referral_on_payment)
    assert "RETURNING" in src and "WHERE referred_id=? AND credited=0" in src
    assert "_trx" not in src, "crediting نباید به read-then-write قدیمی با _trx برگردد"


# ── PR1-07: کرسر D1 ──

def test_d1_cursor_fetch_advances_like_sqlite():
    from newsbot.db import D1Cur, D1Row
    cur = D1Cur([D1Row({"n": 1}), D1Row({"n": 2}), D1Row({"n": 3})])
    assert cur.fetchone()["n"] == 1
    assert [row["n"] for row in cur.fetchall()] == [2, 3]
    assert cur.fetchone() is None
    assert cur.fetchall() == []


# ── DB-01 (مسیر تک‌دستور D1): last_row_id از meta همان پاسخ ──

def test_d1_insert_uses_meta_last_row_id_in_single_request(monkeypatch):
    import newsbot.db as dbmod

    class _Resp:
        status_code = 200
        text = ""
        @staticmethod
        def json():
            return {"success": True, "result": [{"results": [], "meta": {"changes": 1, "last_row_id": 123}}]}

    class _Client:
        def __init__(self): self.calls = []
        def post(self, url, json=None, headers=None):
            self.calls.append(json)
            return _Resp()

    client = _Client()
    monkeypatch.setattr(dbmod, "_d1_client", lambda: client)
    monkeypatch.setattr(dbmod, "_d1_url", lambda: "https://fake/d1/query")

    cur = dbmod.D1Conn().execute("INSERT INTO channels(admin_id) VALUES(?)", (1,))
    assert cur.lastrowid == 123
    assert cur.rowcount == 1
    assert cur.fetchall() == []                 # رفتار sqlite برای INSERT بدون RETURNING
    assert len(client.calls) == 1, "نباید درخواست جداگانهٔ last_insert_rowid زده شود"
    assert client.calls[0]["sql"].startswith("INSERT")


def test_d1_update_returning_keeps_rows(monkeypatch):
    """UPDATE ... RETURNING (claim اعتبار ارجاع) نباید مثل INSERT خالی شود."""
    import newsbot.db as dbmod

    class _Resp:
        status_code = 200
        text = ""
        @staticmethod
        def json():
            return {"success": True, "result": [{"results": [{"referrer_id": 7, "credit_amount": 10.0}],
                                                 "meta": {"changes": 1}}]}

    class _Client:
        def __init__(self): self.calls = []
        def post(self, url, json=None, headers=None):
            self.calls.append(json)
            return _Resp()

    client = _Client()
    monkeypatch.setattr(dbmod, "_d1_client", lambda: client)
    monkeypatch.setattr(dbmod, "_d1_url", lambda: "https://fake/d1/query")

    cur = dbmod.D1Conn().execute("UPDATE referrals SET credited=1 WHERE referred_id=? AND credited=0 RETURNING referrer_id, credit_amount", (1,))
    row = cur.fetchone()
    assert row is not None and row["referrer_id"] == 7
    assert len(client.calls) == 1


# ── PR1-08 / PR1-09: ابزار migrate ──

def _load_migrate_tool():
    path = os.path.join(ROOT, "tools", "migrate_sqlite_to_d1.py")
    spec = importlib.util.spec_from_file_location("migrate_sqlite_to_d1_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_migrate_row_fingerprint_is_order_insensitive_and_content_sensitive():
    m = _load_migrate_tool()
    a = {"id": 1, "name": "x"}
    b = {"name": "x", "id": 1}
    assert m._row_fingerprint(a) == m._row_fingerprint(b), "ترتیب کلیدها نباید اثر کند"
    assert m._row_fingerprint(a) != m._row_fingerprint({"id": 1, "name": "y"}), "تغییر محتوا باید hash را عوض کند"
    assert m._row_fingerprint(a) != m._row_fingerprint({"id": 2, "name": "x"})


def test_migrate_ensure_schema_creates_meta_and_trigger():
    m = _load_migrate_tool()

    class _Resp:
        status_code = 200
        text = ""
        def json(self): return {"success": True, "result": [{"results": [], "meta": {}}]}
        def raise_for_status(self): pass

    class _AC:
        def __init__(self): self.sqls = []
        async def post(self, url, json=None, headers=None, timeout=None):
            self.sqls.append((json or {}).get("sql", ""))
            return _Resp()

    ac = _AC()
    asyncio.run(m.ensure_schema(ac))
    assert any("CREATE TABLE IF NOT EXISTS meta" in s for s in ac.sqls), "PR1-08: جدول meta ساخته نشد"
    assert any("trg_referral_credit_wallet" in s for s in ac.sqls), "trigger ارجاع به D1 فرستاده نشد"
    assert any("CREATE TABLE IF NOT EXISTS users" in s for s in ac.sqls)
    assert REFERRAL_CREDIT_TRIGGER.strip().splitlines()[1].strip() in "\n".join(ac.sqls)


def test_migrate_tables_include_meta():
    m = _load_migrate_tool()
    assert "meta" in m.TABLES
