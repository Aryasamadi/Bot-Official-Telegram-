# -*- coding: utf-8 -*-
"""سیستم ارجاع (Referral) — v5 (فاز ۱۲ · تصمیم ۸/REF-3).

قاعده‌ها: بدون self-referral · انتساب یکتا (UNIQUE روی referred_id) · معرف باید موجود باشد.
پاداش: با **هر پرداخت عددیِ تأییدشدهٔ** فرد دعوت‌شده، درصدی از مبلغ به کیف پول معرف می‌رسد؛
درصد (۰..۵۰) و سقف اختیاریِ مبلغ کل (۰ = نامحدود) از **پنل سوپرادمین** تنظیم می‌شوند
(settings: ref_percent / ref_cap_amount) و پیش‌فرض درصد از config.REFERRAL_PERCENT می‌آید.

اتمیک بودن (ادامهٔ DB-05/PR1-06): افزایش اعتبار ردیف referrals و شارژ کیف پول در یک UPDATE
توسط trigger اتمیک (db.REFERRAL_CREDIT_TRIGGER — نسخهٔ v5 روی increase انجام می‌شود.
پارامتر ref_key (مثلاً «pay:<rid>») تکراری‌بودنِ هر پرداخت را داخل همان تراکنش قفل می‌کند.
"""
from newsbot import config
from newsbot import runtime
from newsbot.db import q, db, _lock, _trx, gget, gset
from newsbot.logutils import log_event
from newsbot.timeutils import now_iso

REF_SET_PERCENT = "ref_percent"
REF_SET_CAP = "ref_cap_amount"
REF_PAID_PREFIX = "refpaid:"


def referral_cfg():
    """جفت زندهٔ (درصد، سقف مبلغ کل) — مقدار پنل و در نبود آن پیش‌فرض config؛ سقف ۰ = نامحدود."""
    pct = gget(REF_SET_PERCENT)
    if pct is None:
        pct = config.REFERRAL_PERCENT
    try:
        pct = float(pct)
    except (TypeError, ValueError):
        pct = float(config.REFERRAL_PERCENT)
    cap = gget(REF_SET_CAP, 0)
    try:
        cap = float(cap)
    except (TypeError, ValueError):
        cap = 0.0
    return max(0.0, min(50.0, pct)), max(0.0, cap)


def set_referral_cfg(percent=None, cap_amount=None):
    """تنظیم از پنل سوپرادمین؛ مقدار نامعتبر ⇒ ValueError با پیام کاربرپسند (به popup می‌رسد)."""
    def num(v):
        try:
            return float(str(v).translate(config._FA_DIGITS).replace(",", "").strip())
        except (TypeError, ValueError):
            raise ValueError("عدد معتبر وارد کنید.")

    if percent is not None:
        p = num(percent)
        if not 0 <= p <= 50:
            raise ValueError("درصد پاداش باید بین ۰ تا ۵۰ باشد.")
        gset(REF_SET_PERCENT, round(p, 2))
    if cap_amount is not None:
        c = num(cap_amount)
        if c < 0:
            raise ValueError("سقف پاداش نمی‌تواند منفی باشد.")
        gset(REF_SET_CAP, round(c, 2))
    return referral_cfg()


def record_referral(referrer_id, referred_id):
    """ثبت انتساب در نخستین /start با پیشوند ref_؛ تکراری‌ها بی‌صدا رد می‌شوند.
    قید UNIQUE و rowcount واقعیِ INSERT OR IGNORE رقابتِ هم‌زمان را هم امن می‌کند."""
    if not config.REFERRAL_ENABLED:
        return False
    try:
        referrer_id, referred_id = int(referrer_id), int(referred_id)
    except (TypeError, ValueError):
        return False
    if referrer_id == referred_id or referrer_id <= 0:
        return False                                                        # self-referral ممنوع
    if q("SELECT 1 FROM referrals WHERE referred_id=?", (referred_id,), one=True):
        return False                                                        # انتساب یکتا
    if not q("SELECT 1 FROM users WHERE id=?", (referrer_id,), one=True):
        return False                                                        # معرف باید وجود داشته باشد
    with _lock:
        cur = db().execute("INSERT OR IGNORE INTO referrals(referrer_id,referred_id,created_at) VALUES(?,?,?)",
                           (referrer_id, referred_id, now_iso()))
        db().commit()
        return cur.rowcount == 1


def credit_referral_on_payment(user_id, paid_amount, cur=None, ref_key=None):
    """پاداش هر خرید: amount = paid × درصد/۱۰۰ (محدود به سقفِ مبلغیِ معرف در همان واحد)
    به credit_amount اضافه می‌شود و trigger اتمیک کیف پولِ معرف را همان واحد شارژ می‌کند.

    PH-01/Q-1c: واحدِ پول صریح است (``cur``) — پاداشِ تومانی در کیف پولِ دلاری خرج نمی‌شود.
    خروجی: (referrer_id, amount, cur) یا None. اگر ref_key تکراری باشد ⇒ None (idempotent per-payment)."""
    if not config.REFERRAL_ENABLED:
        return None
    try:
        user_id = int(user_id)
    except (TypeError, ValueError):
        return None
    paid = float(paid_amount or 0)
    if paid <= 0:
        with _lock:
            db().execute("UPDATE referrals SET credited=1, credit_amount=0, note='no_numeric_price' WHERE referred_id=? AND credited=0", (user_id,))
            db().commit()
        return None
    if not cur:
        # واحدِ پیش‌فرض = واحدِ تعرفهٔ زبانِ خودِ پرداخت‌کننده (Q-1c)
        from newsbot.core_users import user_lang
        from newsbot.core_wallet import currency_of
        cur = currency_of(user_lang(user_id) or "fa")
    pct, cap = referral_cfg()
    amount = round(paid * pct / 100.0, 2)
    if amount <= 0:
        with _lock:
            db().execute("UPDATE referrals SET credited=1, credit_amount=0, note='round_zero' WHERE referred_id=? AND credited=0", (user_id,))
            db().commit()
        return None

    res = _trx(lambda conn: _credit_in_tx(conn, user_id, amount, cur, cap, ref_key))
    if res:
        rid, credited_amount, credited_cur = res
        log_event("INFO", f"referral credit {credited_amount} {credited_cur} to {rid} (from paid user {user_id})", rid)
        return rid, credited_amount
    return None


def _credit_in_tx(conn, user_id, amount, cur, cap=0.0, ref_key=None):
    """اعطای پاداش روی conn داده‌شده — **درونِ تراکنشِ تأییدِ پرداخت** (PH-01/FIN-009).

    FIN-010: سقف با **یک** دستورِ شرطی و بررسیِ rowcount اعمال می‌شود ⇒ در برابرِ رقابت
    قابل عبور نیست. سقف در **همان واحدِ پول** مقایسه می‌شود (نه با جمعِ واحدهای مختلف).
    """
    if ref_key is not None:   # قفل idempotency هر پرداخت — داخل همان تراکنش اتمیک
        if conn.execute("SELECT 1 FROM settings WHERE key=?", (REF_PAID_PREFIX + str(ref_key),)).fetchone():
            return None
    row = conn.execute("SELECT referrer_id, credited FROM referrals WHERE referred_id=?", (user_id,)).fetchone()
    if not row or row["referrer_id"] == user_id:
        return None
    give = amount
    if cap > 0:   # REF-3: سقف مبلغیِ کل برای هر معرف، **به تفکیکِ واحد** (۰ = نامحدود)
        earned = conn.execute("SELECT COALESCE(SUM(credit_amount),0) s FROM referrals "
                              "WHERE referrer_id=? AND credited=1 AND credit_cur=?", (row["referrer_id"], cur)).fetchone()["s"]
        remaining = round(cap - float(earned or 0), 2)
        give = 0.0 if remaining <= 0 else min(amount, remaining)
    if give <= 0:
        if not row["credited"]:
            conn.execute("UPDATE referrals SET credited=1, credit_amount=0, note='cap' WHERE referred_id=? AND credited=0", (user_id,))
        if ref_key is not None:
            conn.execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)", (REF_PAID_PREFIX + str(ref_key), "1"))
        return None
    note = "" if give >= amount else "cap_partial"
    # افزایش credit_amount همین statement؛ trigger اتمیک delta را به کیف پولِ معرف می‌ریزد
    conn.execute("UPDATE referrals SET credited=1, credit_amount=COALESCE(credit_amount,0)+?, note=?, credit_cur=? WHERE referred_id=?",
                 (give, note, cur, user_id))
    if ref_key is not None:
        conn.execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)", (REF_PAID_PREFIX + str(ref_key), "1"))
    return row["referrer_id"], give, cur


def referral_stats(uid):
    """(تعداد دعوت‌شده‌ها, مجموع پاداش کسب‌شده)"""
    invited = q("SELECT COUNT(*) c FROM referrals WHERE referrer_id=?", (uid,), one=True)["c"]
    earned = q("SELECT COALESCE(SUM(credit_amount),0) s FROM referrals WHERE referrer_id=? AND credited=1", (uid,), one=True)["s"]
    return invited, float(earned or 0)


def referral_link(uid):
    return f"https://t.me/{runtime.BOT_USERNAME}?start=ref_{uid}" if runtime.BOT_USERNAME else f"ref_{uid}"
