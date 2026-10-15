# -*- coding: utf-8 -*-
"""نشست‌های پشتیبانی + درخواست‌های پرداخت.
FIX v4: pay_claim زمان claim را در claimed_at ثبت می‌کند؛ ردیف‌های گیرکرده در
'processing' توسط core_plans.recover_stuck_payments() به 'pending' برمی‌گردند.
PHASE 4: pay_approve_atomic — تأیید پرداخت در یک تراکنش (PAY-1/DB-04) با
نشانِ applied_at برای idempotency بازیابی (PAY-3).
"""
import json
from newsbot import config
from newsbot.db import q, db, _lock, _trx
from newsbot.timeutils import now_iso


class PayError(Exception):
    """خطای کنترل‌شدهٔ تأیید پرداخت؛ code برای نمایش UI است."""
    code = "pay_error"

class PayAlreadyDecided(PayError):
    code = "seen"            # قبلاً تأیید/رد شده یا claim دیگری برنده شده

class PayDiscountExhausted(PayError):
    code = "disc_exhausted"  # ظرفیت کد تخفیف تمام شده

class PayNoPlan(PayError):
    code = "notfound"        # پلن نامعتبر/رایگان/غیرفعال


def support_open(uid): q("INSERT OR REPLACE INTO support(user_id,open,opened_at) VALUES(?,1,?)", (uid, now_iso()), commit=True)
def support_close(uid): q("UPDATE support SET open=0 WHERE user_id=?", (uid,), commit=True)
def support_is_open(uid):
    r = q("SELECT open FROM support WHERE user_id=?", (uid,), one=True); return bool(r and r["open"])
def support_map_set(msg_key, uid): q("INSERT OR REPLACE INTO support_map VALUES(?,?,?)", (msg_key, uid, now_iso()), commit=True)
def support_map_get(msg_key):
    r = q("SELECT user_id FROM support_map WHERE msg_id=?", (msg_key,), one=True); return r["user_id"] if r else None

def pay_pending_for(uid, pid): return q("SELECT * FROM pay_requests WHERE user_id=? AND plan_id=? AND status='pending'", (uid, pid), one=True)

def pay_create(uid, pid, receipt_chat=None, receipt_msg=None, note="", discount=None, final_price=None, pc=None):
    return q("INSERT INTO pay_requests(user_id,plan_id,created_at,receipt_chat,receipt_msg,note,discount,final_price,pc) VALUES(?,?,?,?,?,?,?,?,?)",
             (uid, pid, now_iso(), receipt_chat, receipt_msg, (note or "")[:500], discount, final_price,
              json.dumps(pc, ensure_ascii=False) if pc else None), commit=True)

def pay_pending(): return q("SELECT r.*, u.username, u.name, p.name plan_name, p.price plan_price FROM pay_requests r JOIN users u ON u.id=r.user_id JOIN plans p ON p.id=r.plan_id WHERE r.status='pending' ORDER BY r.id")

def pay_get(rid): return q("SELECT * FROM pay_requests WHERE id=?", (rid,), one=True)

def pay_set(rid, status): q("UPDATE pay_requests SET status=?, decided_at=? WHERE id=?", (status, now_iso(), rid), commit=True)

def pay_claim(rid):
    """CAS: only the first decider of a pending payment wins (prevents double-approve double-assignment).
    FIX v4: claimed_at ثبت می‌شود تا بازیابیِ پرداخت گیرکرده ممکن باشد."""
    with _lock:
        cur = db().execute("UPDATE pay_requests SET status='processing', claimed_at=? WHERE id=? AND status='pending'", (now_iso(), rid))
        db().commit()
        return cur.rowcount == 1


# ── PHASE 4: تأیید اتمیک (PAY-1/DB-04/PAY-3) ──
def _apply_applied_at(rid):
    """نشان applied_at: فقط اولین بار ست می‌شود (idempotency بازیابی؛ PAY-3)."""
    with _lock:
        cur = db().execute("UPDATE pay_requests SET applied_at=? WHERE id=? AND applied_at IS NULL", (now_iso(), rid))
        db().commit()
        return cur.rowcount == 1

def pay_approve_atomic(rid):
    """تأیید کاملِ پرداخت: claim + مصرف تخفیف + کیف پول/سابقه + واگذاری پلن + وضعیت approved.

    sqlite (بک‌اند تولید): همهٔ نوشتن‌ها در یک `_trx` (BEGIN IMMEDIATE) — کرش در هر نقطه
    رول‌بک کامل می‌کند (DB-04) و دابل‌کلیک با rowcount CAS رد می‌شود (GAP-06).
    d1 (غیر-گواهی‌شده): توالی CAS تک‌دستوره + نشان applied_at — کرشِ بین واگذاری و تأییدِ
    نهایی دیگر باعث دوباره‌اعمال نمی‌شود (بازیابی فقط approved را می‌بندد).

    خروجی: (exp, queued) — همان قرارداد assign_plan. خطا: PayError با code نمایشی.
    """
    from newsbot.core_plans import _assign_state, _assign_writes, _apply_plan_change_sql
    from newsbot.core_users import admin_limits_invalidate
    r = pay_get(rid)
    if not r or r["status"] != "pending": raise PayAlreadyDecided(rid)
    p = q("SELECT * FROM plans WHERE id=?", (r["plan_id"],), one=True)
    if not p or p["is_free"] or ("active" in p.keys() and not p["active"]): raise PayNoPlan(rid)
    try: pc = json.loads(r["pc"]) if ("pc" in r.keys() and r["pc"]) else {}
    except Exception: pc = {}
    # بازیابیِ re-approve: پلن قبلاً اعمال شده ⇒ فقط بستن وضعیت (idempotent؛ PAY-3)
    if "applied_at" in r.keys() and r["applied_at"]:
        pay_set(rid, "approved")
        u = q("SELECT plan_expires FROM users WHERE id=?", (r["user_id"],), one=True)
        from newsbot.timeutils import parse_dt
        exp = parse_dt(u["plan_expires"]) if u and u["plan_expires"] else None
        admin_limits_invalidate(r["user_id"])
        return exp, False
    if config.DB_BACKEND == "d1":
        return _pay_approve_d1(rid, r, pc)
    st = _assign_state(r["user_id"], r["plan_id"])
    if not st: raise PayNoPlan(rid)
    def _go(conn):
        cur = conn.execute("UPDATE pay_requests SET status='processing', claimed_at=? WHERE id=? AND status='pending'", (now_iso(), rid))
        if cur.rowcount != 1: raise PayAlreadyDecided(rid)       # دابل‌کلیک/هم‌زمان (GAP-06)
        if r["discount"]:
            cur = conn.execute("UPDATE discounts SET used=used+1 WHERE code=? AND (max_uses=0 OR used<max_uses)", (str(r["discount"]).strip().upper(),))
            if cur.rowcount != 1: raise PayDiscountExhausted(rid)  # رول‌بک کامل ⇒ تخفیف مصرف‌نشده می‌ماند
        _apply_plan_change_sql(conn, r["user_id"], r["plan_id"], pc)
        exp, queued = _assign_writes(conn, r["user_id"], r["plan_id"], st)
        if exp is None: raise PayNoPlan(rid)
        conn.execute("UPDATE pay_requests SET status='approved', decided_at=?, applied_at=? WHERE id=?", (now_iso(), now_iso(), rid))
        return exp, queued
    out = _trx(_go)
    admin_limits_invalidate(r["user_id"])
    return out

def _pay_approve_d1(rid, r, pc):
    """مسیر d1: CASهای تک‌دستوره (هر کدام auto-commit اتمیک روی D1) + نشان applied_at."""
    from newsbot.core_plans import assign_plan, apply_plan_change
    from newsbot.core_discounts import disc_use, disc_refund
    if not pay_claim(rid): raise PayAlreadyDecided(rid)
    if r["discount"] and not disc_use(r["discount"]):
        pay_set(rid, "pending"); raise PayDiscountExhausted(rid)
    try:
        apply_plan_change(r["user_id"], r["plan_id"], pc)
        exp, queued = assign_plan(r["user_id"], r["plan_id"])
    except Exception:
        if r["discount"]:
            disc_refund(r["discount"])
        pay_set(rid, "pending")
        raise
    if exp is None:
        if r["discount"]:
            disc_refund(r["discount"])
        pay_set(rid, "pending"); raise PayNoPlan(rid)
    _apply_applied_at(rid)
    pay_set(rid, "approved")
    return exp, queued
