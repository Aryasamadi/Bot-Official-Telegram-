# -*- coding: utf-8 -*-
"""نشست‌های پشتیبانی + درخواست‌های پرداخت.
FIX v4: pay_claim زمان claim را در claimed_at ثبت می‌کند؛ ردیف‌های گیرکرده در
'processing' توسط core_plans.recover_stuck_payments() به 'pending' برمی‌گردند.
"""
import json
from newsbot.db import q, db, _lock
from newsbot.timeutils import now_iso


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
