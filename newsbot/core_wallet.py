# -*- coding: utf-8 -*-
"""کیف پولِ واحد‌دار (PH-01 · Q-1c).

پیش از این فاز، موجودیِ کاربر در یک ستونِ بدون‌واحد (``users.credit``) نگه‌داری می‌شد و
پاداشِ ارجاع با **واحدِ پرداخت‌کننده** محاسبه و ۱:۱ در آن ریخته می‌شد؛ نتیجه: یک پاداشِ
تومانی می‌توانست در برابرِ تعرفهٔ دلاری خرج شود (FIN-001 — تأییدشده با اجرا).

تصمیمِ مالک (Q-1 → گزینهٔ c): **موجودی به تفکیکِ واحد پول نگه‌داری می‌شود و هیچ تبدیلی
انجام نمی‌شود.** یعنی:

* موجودیِ یک واحد فقط در برابرِ تعرفهٔ **همان واحد** قابل خرج است.
* موجودی‌های پیشین (که واحدشان نامعلوم است) در سطلِ ``legacy`` **منجمد** می‌شوند: دیده
  می‌شوند، خرج نمی‌شوند و **به‌طور خودکار تبدیل نمی‌گردند**. تخصیصِ دستیِ آن‌ها یک
  اسکریپتِ جداگانه و صریحاً مجاز است (PH-01 بیرون از محدودهٔ تغییرِ کد است).

این ماژول تنها جایی است که موجودیِ کیف پول خوانده/نوشته می‌شود؛ بنابراین «کیف پول» و
«رسیدِ حرکتِ کیف پول» همیشه با هم و در **یک تراکنش** به‌روز می‌شوند.
"""
from newsbot.db import q, _trx
from newsbot.timeutils import now_iso

# واحدهای پول. کد کوتاه و پایدار (در settings و پایگاه‌داده ذخیره می‌شود).
CUR_TOMAN = "toman"
CUR_USD = "usd"
# سطلِ منجمدِ موجودی‌های قدیمی (واحد نامعلوم) — هرگز قابل خرج نیست.
CUR_LEGACY = "legacy"

# نگاشتِ واحد ← زبانِ کاربر. «قیمت عددیِ» هر زبان با واحدِ خودش همراه است:
#   fa → price_num_fa (تومان) · en → price_num (دلار)
CUR_BY_LANG = {"fa": CUR_TOMAN, "en": CUR_USD}

# نگاشتِ معکوس: واحدِ یک پرداخت ⇒ زبانِ تعرفه‌ای که با آن بسته شده است.
LANG_BY_CUR = {CUR_TOMAN: "fa", CUR_USD: "en"}


def currency_of(lang):
    """واحدِ پولِ زبانِ داده‌شده (پیش‌فرض: تومان)."""
    return CUR_BY_LANG.get(lang or "fa", CUR_TOMAN)


def lang_of_currency(cur):
    """زبانِ متناظرِ یک واحدِ پول (برای بازسازیِ تعرفهٔ یک پرداختِ قدیمی)."""
    return LANG_BY_CUR.get(cur or "")


def is_spendable(cur):
    """آیا این واحد در برابرِ تعرفه قابل خرج است؟ (``legacy`` نیست.)"""
    return cur in (CUR_TOMAN, CUR_USD)


def wallet_get(uid, cur):
    """موجودیِ **قابل‌خرج**ِ کاربر در یک واحد (۰ اگر نبود یا واحد غیرقابل‌خرج باشد).

    سطلِ ``legacy`` (موجودیِ با واحدِ نامعلوم) عمداً قابل‌خرج نیست ⇒ اینجا ۰ برمی‌گردد.
    برای دیدنِ آن از ``wallet_balance`` استفاده کنید."""
    if not cur or not is_spendable(cur):
        return 0.0
    r = q("SELECT amount FROM wallet WHERE user_id=? AND cur=?", (uid, cur), one=True)
    try:
        return round(float(r["amount"] or 0), 2) if r else 0.0
    except (TypeError, ValueError):
        return 0.0


def wallet_balance(uid, cur):
    """موجودیِ خامِ یک واحد — شاملِ سطلِ منجمدِ ``legacy`` (فقط برای نمایش/ممیزی)."""
    r = q("SELECT amount FROM wallet WHERE user_id=? AND cur=?", (uid, cur), one=True)
    try:
        return round(float(r["amount"] or 0), 2) if r else 0.0
    except (TypeError, ValueError):
        return 0.0


def wallet_rows(uid):
    """همهٔ موجودی‌های کاربر: [(cur, amount), …] — برای نمایش و گزارش."""
    out = []
    for r in q("SELECT cur, amount FROM wallet WHERE user_id=? ORDER BY cur", (uid,)):
        try:
            amt = round(float(r["amount"] or 0), 2)
        except (TypeError, ValueError):
            amt = 0.0
        if amt:
            out.append((r["cur"], amt))
    return out


def wallet_total(uid):
    """جمعِ همهٔ واحدها — فقط برای نمایش؛ این عدد **قابل خرج** نیست (واحدها قابل جمع نیستند)."""
    return round(sum(a for _c, a in wallet_rows(uid)), 2)


def _apply(conn, uid, cur, delta, reason, ref):
    """یک حرکتِ کیف پول روی conn داده‌شده: موجودی به‌روز + رسید در همان تراکنش.

    مقدارِ ذخیره‌شده **همیشه** گرد می‌شود (۲ رقم) و رسید دقیقاً همان مقدارِ ذخیره‌شده را
    می‌نویسد ⇒ جمعِ رسیدها همیشه با موجودی برابر است (FIN-006).

    خروجی: موجودیِ جدید. اگر حرکتی باعث شود موجودی منفی شود، استثنا می‌دهد (کل تراکنش
    برگشت می‌خورد) — به‌جای اینکه مثل قبل با MAX(0,…) بی‌سروصدا کلّ اشتباه شود (FIN-002).
    """
    delta = round(float(delta or 0), 2)
    row = conn.execute("SELECT amount FROM wallet WHERE user_id=? AND cur=?", (uid, cur)).fetchone()
    old = round(float(row["amount"] or 0), 2) if row else 0.0
    new = round(old + delta, 2)
    if new < 0:
        raise WalletInsufficient(cur=cur, have=old, need=-delta)
    if row is None:
        conn.execute("INSERT INTO wallet(user_id,cur,amount,updated_at) VALUES(?,?,?,?)", (uid, cur, new, now_iso()))
    else:
        conn.execute("UPDATE wallet SET amount=?, updated_at=? WHERE user_id=? AND cur=?", (new, now_iso(), uid, cur))
    if new != old or delta:
        conn.execute("INSERT INTO credit_log(user_id,delta,balance_after,reason,ref,cur,ts) VALUES(?,?,?,?,?,?,?)",
                     (uid, round(new - old, 2), new, reason, ("" if ref is None else str(ref)), cur, now_iso()))
    return new


class WalletError(Exception):
    """خطای کنترل‌شدهٔ کیف پول."""
    code = "wallet_error"

class WalletInsufficient(WalletError):
    """موجودیِ کافی در آن واحد وجود ندارد."""
    code = "wallet_insufficient"
    def __init__(self, cur="", have=0.0, need=0.0):
        super().__init__(f"insufficient wallet: {cur} have={have} need={need}")
        self.cur = cur; self.have = have; self.need = need


def wallet_credit(uid, cur, amount, reason="credit", ref=None):
    """افزودن به کیف پول (تراکنش مستقل)."""
    if not cur or not is_spendable(cur):
        raise WalletError(f"non-spendable currency: {cur!r}")
    return _trx(lambda conn: _apply(conn, uid, cur, abs(float(amount or 0)), reason, ref))


def wallet_debit(uid, cur, amount, reason="debit", ref=None):
    """برداشت از کیف پول (تراکنش مستقل) — اگر موجودی کافی نباشد، استثنا."""
    if not cur or not is_spendable(cur):
        raise WalletError(f"non-spendable currency: {cur!r}")
    amt = abs(float(amount or 0))
    return _trx(lambda conn: _apply(conn, uid, cur, -amt, reason, ref))
