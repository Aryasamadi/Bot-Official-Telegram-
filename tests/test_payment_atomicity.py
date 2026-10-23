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
                                   PayAlreadyDecided, PayDiscountExhausted, PayNoPlan, PayQuoteStale)
from newsbot.core_discounts import disc_create, disc_get


def _mk_user(uid, credit=0.0, cur="usd"):
    """PH-01: موجودی در **کیف پولِ واحد‌دار** نگه‌داری می‌شود (نه users.credit)."""
    q("INSERT OR IGNORE INTO users(id,username,name,role,created_at,last_seen) VALUES(?,?,?,?,?,?)",
      (uid, f"u{uid}", f"U{uid}", "user", now_iso(), now_iso()), commit=True)
    if credit:
        from newsbot.core_wallet import wallet_credit
        wallet_credit(uid, cur, credit, "test", None)

def _paid_plan(price_num=20.0, days=30):
    return create_plan(name=f"PA{price_num}", days=days, price_num=price_num, is_free=0, price="20")

def _quote(uid, pid, disc_pct=None):
    """همان quote‌ای که مسیرِ واقعیِ خرید می‌سازد — تا بازمحاسبهٔ قیمت (Q-2a) سازگار باشد."""
    from newsbot.core_plans import upgrade_quote
    return upgrade_quote(uid, get_plan(pid), None, disc_pct, lang="en")

def _req(uid, pid, disc=None, cur="usd"):
    """درخواستِ پرداخت به همان شکلی که محصول می‌سازد (pc یخ‌زده + واحد)."""
    pct = None
    if disc:
        d = disc_get(disc)
        pct = d["percent"] if d else None
    qt = _quote(uid, pid, pct)
    rid = pay_create(uid, pid, discount=disc, final_price=str(qt["payable"]), pc=qt, cur=qt.get("cur") or cur)
    return rid, qt


def test_double_approve_pays_exactly_once():
    """GAP-06: دو تأیید هم‌زمان روی یک rid — دقیقاً یک برنده؛ کیف پول و پلن دقیقاً یک‌بار."""
    uid = 410001; _mk_user(uid, credit=50.0)
    pid = _paid_plan(20.0)
    rid, qt = _req(uid, pid)
    assert qt["payable"] == 0.0 and qt["used_balance"] == 20.0   # اعتبارِ ۵۰ دلاری کلِ ۲۰ دلار را می‌پوشاند
    results, errors = [], []
    def go():
        try: results.append(pay_approve_atomic(rid))
        except Exception as e: errors.append(e)
    ts = [threading.Thread(target=go) for _ in range(2)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert len(results) == 1 and len(errors) == 1
    assert isinstance(errors[0], PayAlreadyDecided)
    assert pay_get(rid)["status"] == "approved"
    u = q("SELECT plan_id FROM users WHERE id=?", (uid,), one=True)
    from newsbot.core_wallet import wallet_get
    assert u["plan_id"] == pid and wallet_get(uid, "usd") == 30.0     # دقیقاً یک کسور ۲۰ دلاری
    assert q("SELECT COUNT(*) c FROM plan_changes WHERE user_id=?", (uid,), one=True)["c"] == 1


def test_crash_mid_approve_rolls_back_fully():
    """GAP-01/DB-04: شکست تزریق‌شده وسط تراکنش ⇒ رول‌بک کامل؛ هیچ نیمه‌کاره‌ای نمی‌ماند."""
    uid = 410002; _mk_user(uid, credit=10.0)
    pid = _paid_plan(30.0)
    code = disc_create("CRASH10", 10, (now_utc() + timedelta(days=1)).isoformat(), max_uses=5)
    rid, qt = _req(uid, pid, disc=code)
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
    u = q("SELECT plan_id FROM users WHERE id=?", (uid,), one=True)
    from newsbot.core_wallet import wallet_get
    assert wallet_get(uid, "usd") == 10.0 and u["plan_id"] is None    # کیف پول دست‌نخورده
    assert q("SELECT COUNT(*) c FROM plan_changes WHERE user_id=?", (uid,), one=True)["c"] == 0
    assert disc_get(code)["used"] == used_before                  # تخفیف مصرف‌نشده برگشت


def test_stuck_recovery_reapprove_no_double_apply():
    """PAY-3/PH-01: بازیابیِ پرداختِ گیرکرده — ردیفی که قبلاً اعمال شده «pending» **نمی‌شود**
    (اعمالِ دوباره) بلکه به «approved» بسته می‌شود؛ روزهای پلن هم اضافه نمی‌شود."""
    uid = 410003; _mk_user(uid, credit=0.0)
    pid = _paid_plan(20.0)
    rid, qt = _req(uid, pid)
    pay_approve_atomic(rid)
    u1 = q("SELECT plan_expires FROM users WHERE id=?", (uid,), one=True)
    # شبیه‌سازی کرش پس از اعمال و پیش از بستنِ نهایی (مسیر d1/بازیابی)
    q("UPDATE pay_requests SET status='processing', claimed_at=? WHERE id=?",
      ((now_utc() - timedelta(minutes=config.PAY_STUCK_MIN + 1)).isoformat(), rid), commit=True)
    from newsbot.core_plans import recover_stuck_payments
    recover_stuck_payments()
    # applied_at ست است ⇒ به pending برنمی‌گردد (وگرنه اعمالِ دوباره)؛ به approved بسته می‌شود
    assert pay_get(rid)["status"] == "approved"
    u2 = q("SELECT plan_expires FROM users WHERE id=?", (uid,), one=True)
    assert u1["plan_expires"] == u2["plan_expires"]                 # روز اضافه نشده است
    # FIN-007: سابقهٔ خرید همیشه ثبت می‌شود ⇒ یک ردیف (از تأییدِ اول) و نه بیشتر
    assert q("SELECT COUNT(*) c FROM plan_changes WHERE user_id=?", (uid,), one=True)["c"] == 1


def test_stuck_recovery_without_apply_returns_to_pending():
    """PH-01/FIN-022: پرداختی که اصلاً اعمال نشده (applied_at خالی) ⇒ به pending برمی‌گردد
    تا اپراتور بتواند دوباره تصمیم بگیرد."""
    uid = 410015; _mk_user(uid, credit=0.0)
    pid = _paid_plan(20.0)
    rid, qt = _req(uid, pid)
    q("UPDATE pay_requests SET status='processing', claimed_at=? WHERE id=?",
      ((now_utc() - timedelta(minutes=config.PAY_STUCK_MIN + 1)).isoformat(), rid), commit=True)
    from newsbot.core_plans import recover_stuck_payments
    recover_stuck_payments()
    assert pay_get(rid)["status"] == "pending"


def test_discount_exhausted_full_rollback():
    """GAP-14: ظرفیت تخفیف تمام ⇒ PayDiscountExhausted؛ هیچ اثری باقی نمی‌ماند."""
    uid = 410004; _mk_user(uid, credit=0.0)
    pid = _paid_plan(20.0)
    code = disc_create("EXH1", 10, (now_utc() + timedelta(days=1)).isoformat(), max_uses=1)
    q("UPDATE discounts SET used=1 WHERE code=?", (code,), commit=True)
    rid, qt = _req(uid, pid, disc=code)
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
def test_transfer_token_is_reused_while_valid_and_cleared_on_revert():
    """PH-05/SEC-008: توکنِ معتبرِ مصرف‌نشده **دوباره استفاده** می‌شود (تلاشِ مکررِ یک
    هم‌ادمین نباید توکنِ در انتظارِ مالک را باطل کند)؛ با بازگشتِ مالکیت پاک می‌شود."""
    from newsbot.core_channels import issue_transfer_token, revert_channel_owner, check_transfer_token
    q("INSERT OR IGNORE INTO channels(id,chat_id,title,username,admin_id,verified_by,created_at,settings) VALUES(?,?,?,?,?,?,?,?)",
      (-9001, -1009001, "CH", "ch9001", 5001, 5001, now_iso(), "{}"), commit=True)
    t1 = issue_transfer_token(-9001, 5001)
    t2 = issue_transfer_token(-9001, 5001)     # توکنِ معتبر ⇒ همان توکن دوباره برگردانده می‌شود
    assert t1 and t2 and t1 == t2
    assert check_transfer_token(-9001, t1) is not None      # توکن هنوز معتبر است
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


# ── BIZ-05 / REF-3: قفل هر کاربر + شفافیت سقف ارجاع ──
def test_user_op_lock_serializes_same_user():
    import asyncio
    from newsbot.concurrency import user_op_lock
    order = []
    async def worker(tag, delay):
        async with user_op_lock(410099):
            order.append(f"{tag}:in")
            await asyncio.sleep(delay)
            order.append(f"{tag}:out")
    async def main():
        await asyncio.gather(worker("a", 0.05), worker("b", 0.01))
    asyncio.run(main())
    assert order.index("a:out") < order.index("b:in")   # هرگز هم‌پوشانی نداشتند

def test_referral_view_uses_live_config_without_hidden_stats():
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "newsbot" / "ui_views_user.py").read_text(encoding="utf-8")
    assert "referral_cfg" in src and "referral_link" in src
    assert "_referral_message" in src
    # آمار و سقف در متن دعوت جدید نمایش داده نمی‌شوند؛ خرابی آمار نباید صفحه را بشکند.
    assert "referral_stats" not in src


# ── PH-01/Q-2a: مبلغِ نمایش‌داده‌شده ثابت می‌ماند، ولی قیمت پیش از تأیید بازمحاسبه می‌شود ──
def test_approve_revalidates_quote_and_refuses_on_material_difference():
    """PH-01/Q-2a — اگر کیف پول یا وضعیتِ کاربر بین درخواست و تأیید عوض شده باشد،
    تأیید **متوقف** می‌شود (PayQuoteStale) تا مبلغِ نمایش‌داده‌شده دوباره گرفته شود."""
    uid = 410014; _mk_user(uid, credit=10.0)
    pid = _paid_plan(20.0)
    rid, qt = _req(uid, pid)
    assert qt["payable"] == 10.0                       # ۲۰ دلار منهای ۱۰ دلار اعتبار
    # اعتبارِ کیف پول کم می‌شود ⇒ مبلغِ بازمحاسبه‌شده با مبلغِ یخ‌زده فرق می‌کند
    from newsbot.core_wallet import wallet_debit
    wallet_debit(uid, "usd", 10.0, "test", None)
    with pytest.raises(PayQuoteStale):
        pay_approve_atomic(rid)
    r = pay_get(rid)
    assert r["status"] == "pending" and not r["applied_at"]   # هیچ اثری باقی نماند
    assert q("SELECT plan_id FROM users WHERE id=?", (uid,), one=True)["plan_id"] is None


def test_approve_uses_persisted_final_price_not_requote():
    """قراردادِ جدید: مبلغِ نمایش‌داده‌شده از ردیفِ پرداخت می‌آید و هرگز بازنویسی نمی‌شود؛
    اما **پیش از تأیید** با کیف پولِ زنده بازمحاسبه و مقایسه می‌شود (Q-2a)."""
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "newsbot" / "ui_views_super.py").read_text(encoding="utf-8")
    # تصمیم تأیید از pay_approve_atomic استفاده می‌کند و final_price از ردیف r می‌آید
    assert "pay_approve_atomic" in src and 'r["final_price"]' in src
    assert "upgrade_quote" not in src.split("async def decide_pay")[1]   # در مسیر تصمیم، quote دوباره زده نمی‌شود
    # بازمحاسبهٔ قیمت داخلِ خودِ تأیید انجام می‌شود (نه در لایهٔ UI)
    pay_src = (Path(__file__).resolve().parent.parent / "newsbot" / "core_payments.py").read_text(encoding="utf-8")
    assert "_revalidate_quote" in pay_src and "PayQuoteStale" in pay_src
    # رسید: مبلغ در لحظهٔ ثبت persist می‌شود (st["final"]) و بعداً overwrite نمی‌شود
    inp = (Path(__file__).resolve().parent.parent / "newsbot" / "ui_inputs.py").read_text(encoding="utf-8")
    assert 'st.get("final")' in inp and "pay_pending_for" in inp

def test_double_receipt_blocked_by_pending_guard():
    """دو رسید برای یک پلن: دومی توسط pay_pending_for رد می‌شود (guard ثبت رسید)."""
    uid = 410013; _mk_user(uid)
    pid = _paid_plan(28.0)
    from newsbot.core_payments import pay_pending_for
    assert pay_pending_for(uid, pid) is None
    pay_create(uid, pid, note="first")
    assert pay_pending_for(uid, pid) is not None            # guard دومی را می‌بندد

def test_unknown_approval_error_is_not_reported_as_already_handled():
    from newsbot.ui_i18n import tr
    from newsbot.ui_views_super import _pay_failure_text
    assert _pay_failure_text("fa", PayAlreadyDecided(1)) == tr("fa", "s_pay_seen")
    assert _pay_failure_text("fa", PayQuoteStale(1)) == tr("fa", "s_pay_stale")
    failed = _pay_failure_text("fa", RuntimeError("injected"))
    assert failed == tr("fa", "s_pay_failed")
    assert failed != tr("fa", "s_pay_seen")

def test_fresh_processing_receipt_is_not_called_already_handled():
    from newsbot.ui_i18n import tr
    from newsbot.ui_views_super import _pay_decision_ready
    uid = 410016; _mk_user(uid)
    pid = _paid_plan(20.0)
    rid, _ = _req(uid, pid)
    q("UPDATE pay_requests SET status='processing', claimed_at=? WHERE id=?", (now_iso(), rid), commit=True)
    r, why = _pay_decision_ready("fa", rid)
    assert r is None and why == tr("fa", "s_pay_processing") and why != tr("fa", "s_pay_seen")

def test_stale_processing_receipt_recovers_and_can_be_approved():
    from newsbot.ui_views_super import _pay_decision_ready
    uid = 410017; _mk_user(uid)
    pid = _paid_plan(20.0)
    rid, _ = _req(uid, pid)
    stale = (now_utc() - timedelta(minutes=config.PAY_STUCK_MIN + 1)).isoformat()
    q("UPDATE pay_requests SET status='processing', claimed_at=? WHERE id=?", (stale, rid), commit=True)
    r, why = _pay_decision_ready("fa", rid)
    assert why is None and r["status"] == "pending"
    pay_approve_atomic(rid)
    assert pay_get(rid)["status"] == "approved"
