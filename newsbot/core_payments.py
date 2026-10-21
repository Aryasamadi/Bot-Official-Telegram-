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

class PayQuoteStale(PayError):
    """PH-01/Q-2a — کیف پول یا وضعیتِ کاربر بین درخواست و تأیید عوض شده ⇒ مبلغِ
    نمایش‌داده‌شده دیگر معتبر نیست ⇒ تأیید متوقف می‌شود تا قیمت دوباره گرفته شود."""
    code = "quote_stale"

class PayDiscountInvalid(PayError):
    """کدِ تخفیف بین درخواست و تأیید غیرفعال/منقضی/تکمیل شده (FIN-008)."""
    code = "disc_invalid"


def support_open(uid): q("INSERT OR REPLACE INTO support(user_id,open,opened_at) VALUES(?,1,?)", (uid, now_iso()), commit=True)
def support_close(uid): q("UPDATE support SET open=0 WHERE user_id=?", (uid,), commit=True)
def support_is_open(uid):
    r = q("SELECT open FROM support WHERE user_id=?", (uid,), one=True); return bool(r and r["open"])
def support_map_set(msg_key, uid):
    # DB-17: ستون‌ها صریح‌اند تا افزودن ستون به جدول، درج موقعیتی را بی‌صدا جابه‌جا نکند
    q("INSERT OR REPLACE INTO support_map(msg_id,user_id,ts) VALUES(?,?,?)", (msg_key, uid, now_iso()), commit=True)
def support_map_get(msg_key):
    r = q("SELECT user_id FROM support_map WHERE msg_id=?", (msg_key,), one=True); return r["user_id"] if r else None

def pay_pending_for(uid, pid): return q("SELECT * FROM pay_requests WHERE user_id=? AND plan_id=? AND status='pending'", (uid, pid), one=True)

def pay_create(uid, pid, receipt_chat=None, receipt_msg=None, note="", discount=None, final_price=None, pc=None, cur=""):
    """ثبتِ درخواستِ پرداخت. ``cur`` واحدِ پولِ مبلغ است (Q-1c) تا پاداشِ ارجاع و
    بررسیِ مبلغ در همان واحد انجام شود."""
    return q("INSERT INTO pay_requests(user_id,plan_id,created_at,receipt_chat,receipt_msg,note,discount,final_price,pc,cur) VALUES(?,?,?,?,?,?,?,?,?,?)",
             (uid, pid, now_iso(), receipt_chat, receipt_msg, (note or "")[:500], discount, final_price,
              json.dumps(pc, ensure_ascii=False) if pc else None, cur or ""), commit=True)

def pay_pending(): return q("SELECT r.*, u.username, u.name, p.name plan_name, p.price plan_price FROM pay_requests r JOIN users u ON u.id=r.user_id JOIN plans p ON p.id=r.plan_id WHERE r.status='pending' ORDER BY r.id")

def pay_approved_recent(limit=8):
    """پرداخت‌های اخیرِ تأییدشده — برای «بازگشتِ وجه» در پنل سوپرادمین (PH-01/FIN-013)."""
    return q("SELECT r.*, u.username, u.name, p.name plan_name FROM pay_requests r "
             "JOIN users u ON u.id=r.user_id JOIN plans p ON p.id=r.plan_id "
             "WHERE r.status='approved' ORDER BY r.id DESC LIMIT ?", (int(limit or 8),))

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

def pay_approve_atomic(rid, by_uid=None):
    """تأیید کاملِ پرداخت: claim + مصرفِ معتبرِ تخفیف + بازمحاسبهٔ قیمت + کیف پول/سابقه
    + واگذاریِ پلن + پاداشِ ارجاع + وضعیت approved — همه در **یک** تراکنش.

    sqlite (بک‌اند تولید): همهٔ نوشتن‌ها در یک `_trx` (BEGIN IMMEDIATE) — کرش در هر نقطه
    رول‌بک کامل می‌کند (DB-04) و دابل‌کلیک با rowcount CAS رد می‌شود (GAP-06).
    d1 (غیر-گواهی‌شده): توالی CAS تک‌دستوره + نشان applied_at.

    PH-01/Q-2a: پیش از هر نوشتنی، **قیمت دوباره محاسبه می‌شود** با کیف پولِ زنده؛ اگر
    مبلغِ نهایی با مبلغِ یخ‌زده (که به مشتری نشان داده شده) اختلافِ مؤثر داشته باشد،
    تأیید **متوقف** می‌شود (``PayQuoteStale``) — هرگز عددی که مشتری ندیده دریافت نمی‌کند.
    PH-01/FIN-009: پاداشِ ارجاع **داخلِ همین تراکنش** اعطا می‌شود ⇒ کرش دیگر پاداش را
    برای همیشه از بین نمی‌برد.

    خروجی: (exp, queued, ref_reward) — ref_reward = (referrer_id, amount, cur) یا None.
    """
    from newsbot.core_plans import _assign_state, _assign_writes, _apply_plan_change_sql
    from newsbot.core_users import admin_limits_invalidate
    from newsbot.core_discounts import disc_use_tx
    from newsbot.core_referrals import _credit_in_tx, referral_cfg
    from newsbot.core_wallet import lang_of_currency
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
        return exp, False, None
    if config.DB_BACKEND == "d1":
        return _pay_approve_d1(rid, r, pc)
    cur_code = (r["cur"] if "cur" in r.keys() else "") or (pc.get("cur") or "")
    lang = lang_of_currency(cur_code) or "fa"
    def _go(conn):
        cur = conn.execute("UPDATE pay_requests SET status='processing', claimed_at=? WHERE id=? AND status='pending'", (now_iso(), rid))
        if cur.rowcount != 1: raise PayAlreadyDecided(rid)       # دابل‌کلیک/هم‌زمان (GAP-06)
        # ── بازمحاسبهٔ قیمت با کیف پولِ زنده (Q-2a) ────────────────────────────────────
        _revalidate_quote(conn, r, p, pc, lang)
        if r["discount"]:
            ok, why = disc_use_tx(conn, r["discount"])
            if not ok: raise PayDiscountInvalid(rid) if why in ("inactive", "expired", "notfound") else PayDiscountExhausted(rid)
        st = _assign_state(r["user_id"], r["plan_id"])           # داخلِ تراکنش ⇒ بدون TOCTOU (FIN-003)
        if not st: raise PayNoPlan(rid)
        _apply_plan_change_sql(conn, r["user_id"], r["plan_id"], pc, ref=f"pay:{rid}")
        exp, queued = _assign_writes(conn, r["user_id"], r["plan_id"], st)
        if exp is None: raise PayNoPlan(rid)
        # ── پاداشِ ارجاع: درونِ همین تراکنش (FIN-009/OBS-005) ─────────────────────────
        ref_reward = None
        paid_num = _paid_amount(r, p, pc, lang)
        if paid_num:
            _pct, cap = referral_cfg()
            amount = round(paid_num * _pct / 100.0, 2)
            if amount > 0:
                got = _credit_in_tx(conn, r["user_id"], amount, cur_code, cap, ref_key=f"pay:{rid}")
                if got: ref_reward = got
        conn.execute("UPDATE pay_requests SET status='approved', decided_at=?, applied_at=?, decided_by=? WHERE id=?",
                     (now_iso(), now_iso(), by_uid, rid))
        return exp, queued, ref_reward
    out = _trx(_go)
    admin_limits_invalidate(r["user_id"])
    return out


def _paid_amount(r, p, pc, lang):
    """مبلغِ نقدیِ پرداخت‌شده (پایهٔ پاداشِ ارجاع) — عدد یا None."""
    try: v = float(str(r["final_price"] or "").strip())
    except (TypeError, ValueError): v = None
    if v is None:
        try: v = float(pc.get("payable") or 0) or None
        except (TypeError, ValueError): v = None
    if v is None:
        from newsbot.core_plans import plan_num
        v = plan_num(p, lang) or None
    return v


def _revalidate_quote(conn, r, p, pc, lang):
    """PH-01/Q-2a — قیمت را با کیف پولِ زنده دوباره حساب کن و با مبلغِ یخ‌زده مقایسه کن.

    * اگر «نوعِ» خرید عوض شده (fresh ↔ renew ↔ upgrade) ⇒ ردّ قیمت.
    * اگر مبلغِ نهایی بیش از آستانه اختلاف داشته باشد ⇒ ردّ قیمت (``PayQuoteStale``).
    * آستانه درصدی است (واحد-مستقل) ⇒ برای دو واحدِ پول یکسان معنی دارد.
    """
    from newsbot.core_plans import upgrade_quote, get_plan, get_user
    frozen_payable = round(float(pc.get("payable") or 0), 2)
    frozen_kind = pc.get("kind") or "fresh"
    if not pc or not frozen_payable:
        return                                    # ردیفِ قدیمی بدونِ قیمتِ یخ‌زده: تغییری داده نمی‌شود
    u = get_user(r["user_id"]); cur = get_plan(u["plan_id"]) if u and u["plan_id"] else None
    disc_pct = None
    if r["discount"]:
        from newsbot.core_discounts import disc_get
        d = disc_get(r["discount"])
        disc_pct = d["percent"] if d else None
    q2 = upgrade_quote(r["user_id"], p, cur, disc_pct, lang=lang)
    if q2["cur"] and pc.get("cur") and q2["cur"] != pc.get("cur"):
        raise PayQuoteStale(r["id"])
    if (q2["kind"] or "fresh") != frozen_kind:
        raise PayQuoteStale(r["id"])
    tol = round(abs(frozen_payable) * (config.PAY_QUOTE_TOLERANCE_PCT / 100.0), 2)
    if abs(round(float(q2["payable"] or 0), 2) - frozen_payable) > tol:
        raise PayQuoteStale(r["id"])

def _pay_approve_d1(rid, r, pc):
    """مسیر d1: CASهای تک‌دستوره (هر کدام auto-commit اتمیک روی D1) + نشان applied_at."""
    from newsbot.core_plans import assign_plan, apply_plan_change
    from newsbot.core_discounts import disc_use, disc_refund
    if not pay_claim(rid): raise PayAlreadyDecided(rid)
    if r["discount"] and not disc_use(r["discount"]):
        pay_set(rid, "pending"); raise PayDiscountExhausted(rid)
    try:
        apply_plan_change(r["user_id"], r["plan_id"], pc, ref=f"pay:{rid}")
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
    return exp, queued, None


def pay_mark_notified(rid):
    """PH-01/FIN-021 — علامت‌گذاریِ «نتیجه به مشتری اعلام شد»."""
    q("UPDATE pay_requests SET notified_at=? WHERE id=? AND notified_at IS NULL", (now_iso(), rid), commit=True)


def pay_unnotified(limit=50):
    """پرداخت‌های تأییدشده‌ای که مشتری هنوز مطلع نشده (برای ارسالِ دوباره)."""
    return q("SELECT * FROM pay_requests WHERE status='approved' AND notified_at IS NULL "
             "ORDER BY id LIMIT ?", (int(limit or 50),))


# ── PH-03/OBS-013: اعلامِ رسید به *مدیر کلان* ──────────────────────────────────────
def pay_mark_super_notified(rid):
    """رسید به دستِ حداقل یک مدیر کلان رسید."""
    q("UPDATE pay_requests SET super_notified_at=? WHERE id=? AND super_notified_at IS NULL",
      (now_iso(), rid), commit=True)


def pay_mark_super_failed(rid, err=""):
    """اعلام ناموفق بود ⇒ شمارشِ تلاش؛ سطر «اعلام‌نشده» می‌ماند تا زمان‌بند دوباره بفرستد."""
    q("UPDATE pay_requests SET notify_attempts=notify_attempts+1 WHERE id=?", (rid,), commit=True)


def pay_unnotified_super(limit=20):
    """رسیدهایی که هنوز هیچ مدیر کلانی ندیده (OBS-013) — زمان‌بند دوباره اعلام می‌کند."""
    return q("SELECT * FROM pay_requests WHERE status='pending' AND super_notified_at IS NULL "
             "ORDER BY id LIMIT ?", (int(limit or 20),))


def pay_super_unnotified_count():
    """شمارِ رسیدهای «اعلام‌نشده به مدیر» — در بخشِ سلامت (OBS-013)."""
    try:
        return int(q("SELECT COUNT(*) c FROM pay_requests WHERE status='pending' AND super_notified_at IS NULL",
                     one=True)["c"] or 0)
    except Exception:
        return 0


def pay_receipt_caption(rid, who, uid, plan_name, plan_price, price, disc_code, note_text, lang):
    """PH-03/UI-006 — کپشنِ رسید: HTML معتبر و **همیشه** ≤ ۱۰۲۴ کاراکتر.

    پیش از این، متنِ ساخته‌شده با ``header[:1000]`` بریده می‌شد و برش **داخلِ تگ**
    (<b>، <code>) اتفاق می‌افتاد ⇒ تلگرام کپشن را رد می‌کرد ⇒ رسید به هیچ مدیری نمی‌رسید
    و مشتری هم «رسید دریافت شد» می‌دید.

    حالا: یادداشتِ مشتری **در سطحِ فیلد** به ۳۰۰ کاراکتر کوتاه می‌شود، بقیهٔ فیلدها
    escape می‌شوند و خودِ متن با ``fit_html`` بسته می‌شود (برشِ امن، تگ‌ها بسته می‌شوند)."""
    import html as _html
    from newsbot.text_format import fit_html
    from newsbot.ui_i18n import tr
    disc = f"\n🎟 {_html.escape(str(disc_code))}" if disc_code else ""
    note = f"\n📝 {_html.escape(str(note_text)[:300])}" if note_text else ""
    header = tr(lang, "s_pay_new", id=rid, who=_html.escape(str(who or "")), uid=uid,
                plan=_html.escape(str(plan_name or "")), plan_price=_html.escape(str(plan_price or "—")),
                price=_html.escape(str(price or "")), disc=disc, note=note)
    return fit_html(header, 1024)[0]


def refund_payment(rid, amount=None, reason="refund"):
    """PH-01/FIN-013 — بازگرداندنِ مبلغ به کیف پولِ مشتری، با رسیدِ رسمی.

    بدونِ این مسیر، هر خطای مالی فقط با SQL دستی جبران می‌شد و «دفترِ کیف پول» بی‌ردّ
    می‌ماند. مبلغ پیش‌فرض = سهمِ کیف پولِ مصرف‌شده در همان پرداخت.

    گاردها (هر دو **داخلِ همان تراکنش** ⇒ در برابرِ کلیکِ دوباره امن است):
      * پرداخت باید «approved» باشد.
      * هر پرداخت فقط **یک‌بار** قابل بازگشت است (نشانِ ``refund:pay:<id>`` در settings).
      * مبلغِ بازگشتی هرگز از سهمِ واقعیِ مصرف‌شده بیشتر نمی‌شود.
    """
    from newsbot.core_wallet import _apply as _wallet_apply, is_spendable
    r = pay_get(rid)
    if not r: return False
    if r["status"] != "approved": return False        # فقط پرداختِ تأییدشده قابل بازگشت است
    cur = (r["cur"] if "cur" in r.keys() else "") or ""
    if not is_spendable(cur): return False
    try: pc = json.loads(r["pc"]) if ("pc" in r.keys() and r["pc"]) else {}
    except Exception: pc = {}
    used = round(float(pc.get("used_balance") or 0), 2)
    amt = round(float(amount if amount is not None else used), 2)
    if amt <= 0: return False
    if used > 0: amt = min(amt, used)                 # بیش از سهمِ مصرف‌شده برنگردان
    marker = f"refund:pay:{rid}"
    def _go(conn):
        if conn.execute("SELECT 1 FROM settings WHERE key=?", (marker,)).fetchone():
            return False                                # قبلاً بازگشت داده شده ⇒ idempotent
        _wallet_apply(conn, r["user_id"], cur, amt, reason, f"pay:{rid}")
        conn.execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)", (marker, "1"))
        conn.execute("INSERT INTO plan_changes(user_id, plan_id, from_plan, kind, credit_used, surplus, paid, rem_days, ref, ts) "
                     "VALUES(?,?,?,?,?,?,?,?,?,?)",
                     (r["user_id"], r["plan_id"], pc.get("from_plan"), reason, 0, amt, 0, 0, f"pay:{rid}", now_iso()))
        return True
    return bool(_trx(_go))
