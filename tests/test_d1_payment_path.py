# -*- coding: utf-8 -*-
"""TEST-006 / FIN-011 — مسیرِ D1ِ تأییدِ پرداخت.

D1 تراکنش تعاملی ندارد ⇒ کلِ «اعمال» (مصرفِ تخفیف، حرکتِ کیف پول، واگذاریِ پلن،
پاداشِ ارجاع، نشانِ ``applied_at`` و وضعیتِ approved) باید در **یک batch** برود تا
کرشِ میانِ دستورها به «دوباره‌کسری/دوباره‌اعمال» نینجامد (FIN-011).

D1 ساختگی اینجا دستورها را روی یک SQLiteِ واقعی اجرا می‌کند (ترجمهٔ REST ⇒ SQL) ⇒
``rowcount``، شرط‌های ``WHERE`` و رول‌بکِ واقعیِ batch سنجیده می‌شوند، نه شبیه‌سازی."""
import json
import sqlite3

import pytest

import newsbot.config as config
import newsbot.db as dbmod
from newsbot.core_payments import PayAlreadyDecided, pay_approve_atomic
from newsbot.core_plans import recover_stuck_payments
from newsbot.core_wallet import CUR_TOMAN
from newsbot.timeutils import now_iso, now_utc


class _Resp:
    def __init__(self, payload, status=200):
        self.status_code = status
        self.text = json.dumps(payload)
        self._payload = payload

    def json(self):
        return self._payload


class SqliteD1:
    """D1 ساختگی که هر درخواستِ REST را روی SQLiteِ واقعی اجرا می‌کند."""

    def __init__(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.isolation_level = None          # کنترلِ دستیِ تراکنش (مثلِ batchِ D1)
        self.batches = []
        self.singles = []

    def _run(self, sql, params):
        cur = self.conn.execute(sql, params or ())
        rows = []
        if cur.description:
            rows = [dict(r) for r in cur.fetchall()]
        changes = cur.rowcount if (cur.rowcount or 0) > 0 else 0
        return {"results": rows, "meta": {"changes": changes, "last_row_id": cur.lastrowid}}

    def post(self, url, json=None, headers=None):
        payload = json or {}
        if "batch" in payload:
            self.batches.append([it.get("sql", "") for it in payload["batch"]])
            try:
                self.conn.execute("BEGIN")
                results = [self._run(it.get("sql", ""), it.get("params") or []) for it in payload["batch"]]
                self.conn.execute("COMMIT")
            except Exception as e:
                self.conn.execute("ROLLBACK")
                return _Resp({"success": False, "errors": [{"message": str(e)}]}, 400)
            return _Resp({"success": True, "result": results})
        self.singles.append(payload.get("sql", ""))
        try:
            res = self._run(payload.get("sql", ""), payload.get("params") or [])
        except Exception as e:
            return _Resp({"success": False, "errors": [{"message": str(e)}]}, 400)
        return _Resp({"success": True, "result": [res]})


def _setup(fake):
    """پلن، کاربر، موجودیِ کیف پول و یک پرداختِ «تازه» (مبلغ از کیف پول)."""
    pc = {"kind": "fresh", "payable": 0, "used_balance": 1000, "cur": CUR_TOMAN, "from_plan": None}
    fake.conn.execute("INSERT INTO plans(id,name,days,daily_posts,max_sources,max_channels,daily_tests,"
                      "price_num,price_num_fa,is_free,active) VALUES(1,'Pro',30,5,5,5,2,10,1000,0,1)")
    fake.conn.execute("INSERT INTO users(id,username,role) VALUES(5,'u5','user')")
    fake.conn.execute("INSERT INTO wallet(user_id,cur,amount,updated_at) VALUES(5,?,1000,?)",
                      (CUR_TOMAN, now_iso()))
    fake.conn.execute("INSERT INTO pay_requests(id,user_id,plan_id,status,created_at,final_price,pc,cur) "
                      "VALUES(1,5,1,'pending',?,'0',?,?)", (now_iso(), json.dumps(pc), CUR_TOMAN))


@pytest.fixture
def d1_pay(monkeypatch):
    fake = SqliteD1()
    monkeypatch.setattr(config, "DB_BACKEND", "d1")
    monkeypatch.setattr(config, "CF_ACCOUNT_ID", "acct-test")
    monkeypatch.setattr(config, "CF_D1_DATABASE_ID", "db-test")
    monkeypatch.setattr(config, "CF_D1_TOKEN", "token-test")
    monkeypatch.setattr(dbmod, "_d1_client", lambda: fake)
    monkeypatch.setattr(dbmod, "_d1_url", lambda: "https://fake/d1/query")
    monkeypatch.setattr(dbmod, "_SCHEMA_READY", False)
    monkeypatch.setattr(dbmod._tls, "conn", None, raising=False)
    dbmod.db()                      # ⇒ _ensure_schema روی D1 (batchهای اتمیک) اجرا می‌شود
    _setup(fake)
    return fake


def _row(fake, sql, params=()):
    return fake.conn.execute(sql, params).fetchone()


def test_d1_approval_applies_everything_in_one_atomic_batch(d1_pay):
    """FIN-011: کلِ اعمال در یک batch ⇒ نه دوباره‌کسری، نه دوباره‌اعمال."""
    exp, queued, ref = pay_approve_atomic(1, by_uid=1)
    assert exp is not None
    pr = _row(d1_pay, "SELECT status, applied_at, decided_by FROM pay_requests WHERE id=1")
    assert pr["status"] == "approved"
    assert pr["applied_at"], "نشانِ applied_at باید ثبت شود (idempotency بازیابی)"
    assert pr["decided_by"] == 1
    assert _row(d1_pay, "SELECT amount FROM wallet WHERE user_id=5 AND cur=?", (CUR_TOMAN,))["amount"] == 0
    assert _row(d1_pay, "SELECT plan_id FROM users WHERE id=5")["plan_id"] == 1
    assert _row(d1_pay, "SELECT COUNT(*) c FROM plan_changes WHERE ref='pay:1'")["c"] == 1
    # حرکتِ کیف پول و بستنِ پرداخت در *همان* batch
    apply_batches = [b for b in d1_pay.batches if any(s.startswith("UPDATE wallet SET amount=?") for s in b)]
    assert len(apply_batches) == 1, "کلِ اعمال باید در یک batch باشد: %r" % (apply_batches,)
    batch = apply_batches[0]
    assert any("UPDATE pay_requests SET status='approved'" in s for s in batch)
    assert any("UPDATE users SET plan_id=?" in s for s in batch)


def test_d1_approval_failure_leaves_nothing_applied_and_restores_the_discount(d1_pay, monkeypatch):
    """کرشِ میانِ دستورها ⇒ نه برداشتِ کیف پول، نه اعمالِ پلن، نه مصرفِ تخفیف."""
    d1_pay.conn.execute("INSERT INTO discounts(code,percent,expires,max_uses,used,active) "
                        "VALUES('SAVE10',10,NULL,5,0,1)")
    d1_pay.conn.execute("UPDATE pay_requests SET discount='SAVE10' WHERE id=1")
    import newsbot.core_plans as plansmod

    def boom(*a, **k):
        raise RuntimeError("crash right after the wallet debit")

    monkeypatch.setattr(plansmod, "_assign_writes", boom)
    with pytest.raises(RuntimeError):
        pay_approve_atomic(1, by_uid=1)
    # کیف پول دست‌نخورده، پلن اعمال‌نشده، پرداخت برگشته به pending
    assert _row(d1_pay, "SELECT amount FROM wallet WHERE user_id=5 AND cur=?", (CUR_TOMAN,))["amount"] == 1000
    assert _row(d1_pay, "SELECT plan_id FROM users WHERE id=5")["plan_id"] is None
    pr = _row(d1_pay, "SELECT status FROM pay_requests WHERE id=1")
    assert pr["status"] == "pending", "پرداخت نباید در «processing» گیر کند"
    # تخفیف برگردانده شد (جبران)
    assert _row(d1_pay, "SELECT used FROM discounts WHERE code='SAVE10'")["used"] == 0
    # رسیدِ کیف پول هم نوشته نشد (batch رول‌بک شد)
    assert _row(d1_pay, "SELECT COUNT(*) c FROM credit_log")["c"] == 0


def test_d1_second_approver_cannot_apply_the_payment_twice(d1_pay):
    """TEST-006: دو تأییدِ هم‌زمان ⇒ فقط یکی اعمال می‌شود."""
    pay_approve_atomic(1, by_uid=1)
    with pytest.raises(PayAlreadyDecided):
        pay_approve_atomic(1, by_uid=2)
    assert _row(d1_pay, "SELECT amount FROM wallet WHERE user_id=5 AND cur=?", (CUR_TOMAN,))["amount"] == 0
    assert _row(d1_pay, "SELECT COUNT(*) c FROM credit_log")["c"] == 1
    assert _row(d1_pay, "SELECT COUNT(*) c FROM plan_changes WHERE ref='pay:1'")["c"] == 1


def test_d1_stuck_payment_with_applied_at_is_closed_as_approved(d1_pay):
    """FIN-011/FIN-022: ردیفِ 'processing' که applied_at دارد ⇒ به pending برنمی‌گردد."""
    d1_pay.conn.execute("UPDATE pay_requests SET status='processing', applied_at=?, claimed_at=? WHERE id=1",
                        (now_iso(), now_iso()))
    recover_stuck_payments()
    assert _row(d1_pay, "SELECT status FROM pay_requests WHERE id=1")["status"] == "approved"


def test_d1_stuck_payment_without_applied_at_returns_to_pending(d1_pay):
    """ردیفِ 'processing'ِ قدیمیِ بدونِ applied_at ⇒ به pending برمی‌گردد تا دوباره بررسی شود."""
    old = (now_utc() - __import__("datetime").timedelta(minutes=60)).isoformat()
    d1_pay.conn.execute("UPDATE pay_requests SET status='processing', applied_at=NULL, claimed_at=? WHERE id=1", (old,))
    recover_stuck_payments()
    assert _row(d1_pay, "SELECT status FROM pay_requests WHERE id=1")["status"] == "pending"
