# -*- coding: utf-8 -*-
"""فاز ۴ — یکپارچگی مالی: تأیید اتمیک پرداخت، دابل‌پرداخت موازی، قطع حین تراکنش، idempotency بازیابی.
پوشش: PAY-1 · DB-04 · DB-16 (حذف rollback دستی MAX(id)) · PAY-3 · GAP-01 · GAP-06 · GAP-14 · UISEC-10 (free_claim)."""
import threading
import pytest
from datetime import timedelta

from newsbot import config
from newsbot.db import q
from newsbot.timeutils import now_utc, now_iso
from newsbot.core_plans import create_plan, get_plan
import newsbot.core_plans as core_plans
from newsbot.core_payments import (pay_create, pay_get, pay_approve_atomic,
                                   PayAlreadyDecided, PayDiscountExhausted, PayNoPlan)
from newsbot.core_discounts import disc_create, disc_get


def _mk_user(uid, credit=0.0):
    q("INSERT OR IGNORE INTO users(id,username,name,role,credit,created_at,last_seen) VALUES(?,?,?,?,?,?,?)",
      (uid, f"u{uid}", f"U{uid}", "user", credit, now_iso(), now_iso()), commit=True)

def _paid_plan(price_num=20.0, days=30):
    return create_plan(name=f"PA{price_num}", days=days, price_num=price_num, is_free=0, price="20")


def test_double_approve_pays_exactly_once():
    """GAP-06: دو تأیید هم‌زمان روی یک rid — دقیقاً یک برنده؛ کیف پول و پلن دقیقاً یک‌بار."""
    uid = 410001; _mk_user(uid, credit=50.0)
    pid = _paid_plan(20.0)
    rid = pay_create(uid, pid, pc={"used_balance": 5.0, "surplus": 0.0, "kind": "fresh", "payable": 15.0})
    results, errors = [], []
    def go():
        try: results.append(pay_approve_atomic(rid))
        except Exception as e: errors.append(e)
    ts = [threading.Thread(target=go) for _ in range(2)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert len(results) == 1 and len(errors) == 1
    assert isinstance(errors[0], PayAlreadyDecided)
    assert pay_get(rid)["status"] == "approved"
    u = q("SELECT credit, plan_id FROM users WHERE id=?", (uid,), one=True)
    assert u["plan_id"] == pid and float(u["credit"]) == 45.0     # دقیقاً یک کسور ۵ دلاری
    assert q("SELECT COUNT(*) c FROM plan_changes WHERE user_id=?", (uid,), one=True)["c"] == 1


def test_crash_mid_approve_rolls_back_fully():
    """GAP-01/DB-04: شکست تزریق‌شده وسط تراکنش ⇒ رول‌بک کامل؛ هیچ نیمه‌کاره‌ای نمی‌ماند."""
    uid = 410002; _mk_user(uid, credit=10.0)
    pid = _paid_plan(30.0)
    code = disc_create("CRASH10", 10, (now_utc() + timedelta(days=1)).isoformat(), max_uses=5)
    rid = pay_create(uid, pid, discount=code, pc={"used_balance": 10.0, "surplus": 0.0, "kind": "fresh", "payable": 17.0})
    used_before = disc_get(code)["used"]
    real = core_plans._assign_writes
    def boom(conn, u, p, st): raise RuntimeError("injected crash")
    core_plans._assign_writes = boom
    try:
        with pytest.raises(RuntimeError): pay_approve_atomic(rid)
    finally:
        core_plans._assign_writes = real
    r = pay_get(rid)
    assert r["status"] == "pending" and not r["applied_at"]
    u = q("SELECT credit, plan_id FROM users WHERE id=?", (uid,), one=True)
    assert float(u["credit"]) == 10.0 and u["plan_id"] is None    # کیف پول دست‌نخورده
    assert q("SELECT COUNT(*) c FROM plan_changes WHERE user_id=?", (uid,), one=True)["c"] == 0
    assert disc_get(code)["used"] == used_before                  # تخفیف مصرف‌نشده برگشت


def test_stuck_recovery_reapprove_no_double_apply():
    """PAY-3: بازیابیِ re-approve پس از اعمالِ موفق (applied_at ست است) دوباره اعمال نمی‌کند."""
    uid = 410003; _mk_user(uid, credit=0.0)
    pid = _paid_plan(20.0)
    rid = pay_create(uid, pid, pc={"used_balance": 0.0, "surplus": 0.0, "kind": "fresh", "payable": 20.0})
    pay_approve_atomic(rid)
    u1 = q("SELECT plan_expires FROM users WHERE id=?", (uid,), one=True)
    # شبیه‌سازی کرش پس از اعمال و پیش از بستنِ نهایی (مسیر d1/بازیابی)
    q("UPDATE pay_requests SET status='processing', claimed_at=? WHERE id=?",
      ((now_utc() - timedelta(minutes=config.PAY_STUCK_MIN + 1)).isoformat(), rid), commit=True)
    from newsbot.core_plans import recover_stuck_payments
    recover_stuck_payments()
    assert pay_get(rid)["status"] == "pending"
    pay_approve_atomic(rid)                                        # re-approve: فقط بستن وضعیت
    assert pay_get(rid)["status"] == "approved"
    u2 = q("SELECT plan_expires FROM users WHERE id=?", (uid,), one=True)
    assert u1["plan_expires"] == u2["plan_expires"]                 # روز اضافه نشده است
    assert q("SELECT COUNT(*) c FROM plan_changes WHERE user_id=?", (uid,), one=True)["c"] == 0


def test_discount_exhausted_full_rollback():
    """GAP-14: ظرفیت تخفیف تمام ⇒ PayDiscountExhausted؛ هیچ اثری باقی نمی‌ماند."""
    uid = 410004; _mk_user(uid, credit=0.0)
    pid = _paid_plan(20.0)
    code = disc_create("EXH1", 10, (now_utc() + timedelta(days=1)).isoformat(), max_uses=1)
    q("UPDATE discounts SET used=1 WHERE code=?", (code,), commit=True)
    rid = pay_create(uid, pid, discount=code, pc={"kind": "fresh", "payable": 18.0})
    with pytest.raises(PayDiscountExhausted): pay_approve_atomic(rid)
    assert pay_get(rid)["status"] == "pending"
    assert q("SELECT plan_id FROM users WHERE id=?", (uid,), one=True)["plan_id"] is None
    assert disc_get(code)["used"] == 1                              # تغییری نکرد


def test_free_or_missing_plan_rejected():
    """GAP-14: پلن رایگان/غیرفعال هرگز از مسیر تأیید پولی رد نمی‌شود."""
    uid = 410005; _mk_user(uid)
    free_pid = create_plan(name="FREEX", days=7, is_free=1, price="0")
    rid = pay_create(uid, free_pid)
    with pytest.raises(PayNoPlan): pay_approve_atomic(rid)
    assert pay_get(rid)["status"] == "pending"


def test_corrupt_pc_treated_as_empty_still_atomic():
    """GAP-14: pc خراب ⇒ {} بدون شکست؛ تراکنش سالم می‌ماند."""
    uid = 410006; _mk_user(uid, credit=0.0)
    pid = _paid_plan(15.0)
    rid = pay_create(uid, pid)
    q("UPDATE pay_requests SET pc='NOT-JSON{{{' WHERE id=?", (rid,), commit=True)
    pay_approve_atomic(rid)
    assert pay_get(rid)["status"] == "approved"
    assert q("SELECT plan_id FROM users WHERE id=?", (uid,), one=True)["plan_id"] == pid


def test_free_claim_cas_only_once():
    """UISEC-10/SEC-04: claim اتمیک پلن رایگان — فقط اولین ضربه برنده است (رفع TOCTOU)."""
    uid = 410007; _mk_user(uid)
    pid = create_plan(name="FREE2", days=7, is_free=1, price="0")
    wins = []
    def go():
        from newsbot.core_plans import free_claim
        wins.append(free_claim(uid))
    ts = [threading.Thread(target=go) for _ in range(4)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert wins.count(True) == 1 and wins.count(False) == 3
    from newsbot.core_plans import assign_plan
    exp1, _ = assign_plan(uid, pid)
    u = q("SELECT plan_expires FROM users WHERE id=?", (uid,), one=True)
    assert u["plan_expires"] == exp1.isoformat()


# ── DB-15: توکن انتقال / بازگردانی مالکیت اتمیک ──
def test_transfer_token_single_active_and_revert_clears():
    from newsbot.core_channels import issue_transfer_token, revert_channel_owner, check_transfer_token
    q("INSERT OR IGNORE INTO channels(id,chat_id,title,username,admin_id,verified_by,created_at,settings) VALUES(?,?,?,?,?,?,?,?)",
      (-9001, -1009001, "CH", "ch9001", 5001, 5001, now_iso(), "{}"), commit=True)
    t1 = issue_transfer_token(-9001, 5001)
    t2 = issue_transfer_token(-9001, 5001)     # باطل‌سازی t1 در همان تراکنشِ صدور t2
    assert t1 and t2 and t1 != t2
    assert check_transfer_token(-9001, t1) is None      # توکن قدیمی فعال نیست
    assert check_transfer_token(-9001, t2) is not None
    old = revert_channel_owner(-9001, 6001)
    assert old == 5001
    assert check_transfer_token(-9001, t2) is None      # همهٔ توکن‌ها با مالکیت پاک شدند
    assert q("SELECT admin_id FROM channels WHERE id=?", (-9001,), one=True)["admin_id"] == 6001


# ── DB-18: حذف پلن در استفاده ──
def test_delete_plan_in_use_deactivates_not_deletes():
    from newsbot.core_plans import delete_plan
    uid = 410008; _mk_user(uid)
    pid = _paid_plan(25.0)
    q("UPDATE users SET plan_id=? WHERE id=?", (pid, uid), commit=True)
    assert delete_plan(pid) is False
    assert get_plan(pid) is not None and not get_plan(pid)["active"]   # غیرفعال شد، حذف نشد
    q("UPDATE users SET plan_id=NULL WHERE id=?", (uid,), commit=True)
    assert delete_plan(pid) is True
    assert get_plan(pid) is None

def test_delete_plan_with_pending_payment_deactivates():
    from newsbot.core_plans import delete_plan
    uid = 410009; _mk_user(uid)
    pid = _paid_plan(26.0)
    pay_create(uid, pid)                                          # پرداخت pending
    assert delete_plan(pid) is False
    assert get_plan(pid) is not None


# ── BIZ-03: رول‌بک نقش پلن منقضی ──
def test_expired_plan_role_rolled_back_super_untouched():
    from newsbot.core_plans import rollback_expired_benefits
    uid = 410010; _mk_user(uid)
    pid = _paid_plan(27.0)
    q("UPDATE users SET role='admin', plan_id=?, plan_expires=? WHERE id=?",
      (pid, (now_utc() - timedelta(days=1)).isoformat(), uid), commit=True)
    # کاربر فعال (پلن منقضی‌نشده) نباید تغییر کند
    uid2 = 410011; _mk_user(uid2)
    q("UPDATE users SET role='admin', plan_id=?, plan_expires=? WHERE id=?",
      (pid, (now_utc() + timedelta(days=5)).isoformat(), uid2), commit=True)
    n = rollback_expired_benefits()
    assert n >= 1
    assert q("SELECT role FROM users WHERE id=?", (uid,), one=True)["role"] == "user"
    assert q("SELECT role FROM users WHERE id=?", (uid2,), one=True)["role"] == "admin"   # فعال ماند

def test_revoke_plan_downgrades_role():
    from newsbot.core_plans import revoke_plan
    uid = 410012; _mk_user(uid)
    q("UPDATE users SET role='admin' WHERE id=?", (uid,), commit=True)
    revoke_plan(uid)
    assert q("SELECT role FROM users WHERE id=?", (uid,), one=True)["role"] == "user"
