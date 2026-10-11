# -*- coding: utf-8 -*-
"""سیستم ارجاع (Referral) — NEW v4.
قاعده‌ها: بدون self-referral · بدون اعتبار تکراری (UNIQUE روی referred_id + پرچم credited) ·
سقف اعتبار برای هر معرف (REFERRAL_MAX_PER_USER) · اعتبار فقط پس از پرداخت عددی تأییدشده.
"""
from newsbot import config
from newsbot.db import q, _trx
from newsbot.logutils import log_event
from newsbot.timeutils import now_iso


def record_referral(referrer_id, referred_id):
    """ثبت انتساب در نخستین /start با پیشوند ref_؛ تکراری‌ها بی‌صدا رد می‌شوند."""
    if not config.REFERRAL_ENABLED: return False
    try: referrer_id, referred_id = int(referrer_id), int(referred_id)
    except (TypeError, ValueError): return False
    if referrer_id == referred_id or referrer_id <= 0: return False          # self-referral ممنوع
    if q("SELECT 1 FROM referrals WHERE referred_id=?", (referred_id,), one=True): return False   # انتساب یکتا
    if not q("SELECT 1 FROM users WHERE id=?", (referrer_id,), one=True): return False            # معرف باید وجود داشته باشد
    q("INSERT OR IGNORE INTO referrals(referrer_id,referred_id,created_at) VALUES(?,?,?)", (referrer_id, referred_id, now_iso()), commit=True)
    return True

def credit_referral_on_payment(user_id, paid_amount):
    """پس از تأیید پرداخت: اعتبار معرف به‌صورت اتمیک ثبت می‌شود. خروجی: (referrer_id, amount) یا None.
    Idempotent: فقط ردیف‌های credited=0 حرکت می‌کنند؛ پس تأیید دوباره اعتبار دوباره نمی‌سازد."""
    if not config.REFERRAL_ENABLED: return None
    row = q("SELECT * FROM referrals WHERE referred_id=? AND credited=0", (user_id,), one=True)
    if not row: return None
    rid = row["referrer_id"]
    if rid == user_id:
        q("UPDATE referrals SET credited=1, credit_amount=0, note='self' WHERE id=?", (row["id"],), commit=True)
        return None
    paid = float(paid_amount or 0)
    if paid <= 0:
        q("UPDATE referrals SET credited=1, credit_amount=0, note='no_numeric_price' WHERE id=?", (row["id"],), commit=True)
        return None
    n = q("SELECT COUNT(*) c FROM referrals WHERE referrer_id=? AND credited=1 AND credit_amount>0", (rid,), one=True)["c"]
    if n >= config.REFERRAL_MAX_PER_USER:
        q("UPDATE referrals SET credited=1, credit_amount=0, note='cap' WHERE id=?", (row["id"],), commit=True)
        return None
    amount = round(paid * config.REFERRAL_PERCENT / 100.0, 2)
    if amount <= 0:
        q("UPDATE referrals SET credited=1, credit_amount=0, note='round_zero' WHERE id=?", (row["id"],), commit=True)
        return None
    def _go(conn):
        conn.execute("UPDATE users SET credit=COALESCE(credit,0)+? WHERE id=?", (amount, rid))
        conn.execute("UPDATE referrals SET credited=1, credit_amount=? WHERE id=? AND credited=0", (amount, row["id"]))
    _trx(_go)
    log_event("INFO", f"referral credit {amount} to {rid} (from paid user {user_id})", rid)
    return rid, amount

def referral_stats(uid):
    """(تعداد دعوت‌شده‌ها, مجموع اعتبار کسب‌شده)"""
    invited = q("SELECT COUNT(*) c FROM referrals WHERE referrer_id=?", (uid,), one=True)["c"]
    earned = q("SELECT COALESCE(SUM(credit_amount),0) s FROM referrals WHERE referrer_id=? AND credited=1", (uid,), one=True)["s"]
    return invited, float(earned or 0)

def referral_link(uid):
    return f"https://t.me/{config.BOT_USERNAME}?start=ref_{uid}" if config.BOT_USERNAME else f"ref_{uid}"
