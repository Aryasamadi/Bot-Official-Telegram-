# -*- coding: utf-8 -*-
"""سیستم ارجاع (Referral) — NEW v4.
قاعده‌ها: بدون self-referral · بدون اعتبار تکراری (UNIQUE روی referred_id + پرچم credited) ·
سقف اعتبار برای هر معرف (REFERRAL_MAX_PER_USER) · اعتبار فقط پس از پرداخت عددی تأییدشده.
"""
from newsbot import config
from newsbot.db import q, db, _lock
from newsbot.logutils import log_event
from newsbot.timeutils import now_iso


def record_referral(referrer_id, referred_id):
    """ثبت انتساب در نخستین /start؛ قید UNIQUE و rowcount رقابت هم‌زمان را امن می‌کنند."""
    if not config.REFERRAL_ENABLED: return False
    try: referrer_id, referred_id = int(referrer_id), int(referred_id)
    except (TypeError, ValueError): return False
    if referrer_id == referred_id or referrer_id <= 0: return False
    if q("SELECT 1 FROM referrals WHERE referred_id=?", (referred_id,), one=True): return False
    if not q("SELECT 1 FROM users WHERE id=?", (referrer_id,), one=True): return False
    with _lock:
        cur = db().execute(
            "INSERT OR IGNORE INTO referrals(referrer_id,referred_id,created_at) VALUES(?,?,?)",
            (referrer_id, referred_id, now_iso()))
        db().commit()
        return cur.rowcount == 1


def credit_referral_on_payment(user_id, paid_amount):
    """اعتبار معرف با یک UPDATE شرطی و trigger اتمیک ثبت می‌شود.
    اگر چند تأیید هم‌زمان برسد، فقط یک UPDATE می‌تواند claim کند؛ trigger همان statement کیف پول را زیاد می‌کند.
    """
    if not config.REFERRAL_ENABLED: return None
    try: user_id = int(user_id)
    except (TypeError, ValueError): return None
    paid = float(paid_amount or 0)
    if paid <= 0:
        with _lock:
            db().execute(
                "UPDATE referrals SET credited=1, credit_amount=0, note='no_numeric_price' WHERE referred_id=? AND credited=0",
                (user_id,))
            db().commit()
        return None
    amount = round(paid * config.REFERRAL_PERCENT / 100.0, 2)
    if amount <= 0:
        with _lock:
            db().execute(
                "UPDATE referrals SET credited=1, credit_amount=0, note='round_zero' WHERE referred_id=? AND credited=0",
                (user_id,))
            db().commit()
        return None

    cap = config.REFERRAL_MAX_PER_USER
    sql = """
        UPDATE referrals
        SET credited=1,
            credit_amount=CASE
                WHEN referrer_id=? THEN 0
                WHEN (SELECT COUNT(*) FROM referrals r2
                      WHERE r2.referrer_id=referrals.referrer_id
                        AND r2.credited=1 AND r2.credit_amount>0) < ? THEN ?
                ELSE 0 END,
            note=CASE
                WHEN referrer_id=? THEN 'self'
                WHEN (SELECT COUNT(*) FROM referrals r2
                      WHERE r2.referrer_id=referrals.referrer_id
                        AND r2.credited=1 AND r2.credit_amount>0) < ? THEN ''
                ELSE 'cap' END
        WHERE referred_id=? AND credited=0
        RETURNING referrer_id, credit_amount
    """
    with _lock:
        cur = db().execute(sql, (user_id, cap, amount, user_id, cap, user_id))
        row = cur.fetchone()
        db().commit()
    if not row: return None
    rid, credited_amount = row["referrer_id"], float(row["credit_amount"] or 0)
    if credited_amount <= 0: return None
    log_event("INFO", f"referral credit {credited_amount} to {rid} (from paid user {user_id})", rid)
    return rid, credited_amount

def referral_stats(uid):
    """(تعداد دعوت‌شده‌ها, مجموع اعتبار کسب‌شده)"""
    invited = q("SELECT COUNT(*) c FROM referrals WHERE referrer_id=?", (uid,), one=True)["c"]
    earned = q("SELECT COALESCE(SUM(credit_amount),0) s FROM referrals WHERE referrer_id=? AND credited=1", (uid,), one=True)["s"]
    return invited, float(earned or 0)

def referral_link(uid):
    return f"https://t.me/{config.BOT_USERNAME}?start=ref_{uid}" if config.BOT_USERNAME else f"ref_{uid}"
