# -*- coding: utf-8 -*-
"""پلن‌ها (زنجیره‌ای)، اشتراک، proration، کیف پول و بازیابی پرداخت گیرکرده.
FIX v4: recover_stuck_payments() پرداخت‌های گیرکرده در 'processing'
(کرش بین pay_claim و تصمیم نهایی) را پس از PAY_STUCK_MIN دقیقه به 'pending' برمی‌گرداند.
"""
import math
from datetime import timedelta
from newsbot import config
from newsbot.db import q, db, _lock, _safe_fields, _trx
from newsbot.logutils import log_event
from newsbot.timeutils import now_utc, now_iso, parse_dt, iso_fixed
from newsbot.core_users import get_user, admin_limits_invalidate
from newsbot.core_wallet import (currency_of, wallet_get, wallet_total, wallet_rows,
                                 is_spendable, WalletError, WalletInsufficient)
from newsbot.core_wallet import _apply as _wallet_apply


# پلن‌های پایهٔ سیستم (DB-19): هرکدام با INSERT شرطیِ تک‌دستوره seed می‌شود
_BASE_PLANS = [
    dict(name="رایگان", name_en="Free", days=7, daily_posts=1, max_sources=2, max_channels=1, daily_tests=1,
         price="رایگان", price_en="Free", description="۱ پست در روز · ۷ روز", description_en="1 post/day · 7 days", is_free=1, sort=0),
    dict(name="حرفه‌ای", name_en="Pro", days=30, daily_posts=20, max_sources=15, max_channels=3, daily_tests=5,
         price="توافقی", price_en="Contact us", description="۲۰ پست روزانه · ۱۵ منبع · ۳ کانال", description_en="20 posts/day · 15 sources · 3 channels", is_free=0, sort=1),
]

def seed_plans():
    """DB-19: seed اتمیک و idempotent — برخلاف check-then-act قبلی، دو پروسهٔ هم‌زمان پلن تکراری
    نمی‌سازند و دیتابیسِ نیمه‌seed (فقط یک پلن) هم ترمیم می‌شود."""
    for p in _BASE_PLANS:
        q("INSERT INTO plans(name,name_en,days,daily_posts,max_sources,max_channels,daily_tests,price,price_en,description,description_en,is_free,sort,price_num,price_num_fa) "
          "SELECT ?,?,?,?,?,?,?,?,?,?,?,?,?,0,0 WHERE NOT EXISTS(SELECT 1 FROM plans WHERE name=? AND name_en=?)",
          (p["name"], p["name_en"], p["days"], p["daily_posts"], p["max_sources"], p["max_channels"], p["daily_tests"],
           p["price"], p["price_en"], p["description"], p["description_en"], p["is_free"], p["sort"], p["name"], p["name_en"]),
          commit=True)

def list_plans(active_only=True): return q("SELECT * FROM plans " + (" WHERE active=1" if active_only else "") + " ORDER BY sort, id")
def get_plan(pid): return q("SELECT * FROM plans WHERE id=?", (pid,), one=True) if pid else None

def plan_txt(p, field, lang="fa"):
    if not p: return ""
    if lang == "en" and p[f"{field}_en"]: return p[f"{field}_en"]
    return p[field] or ""

def create_plan(**f):
    return q("INSERT INTO plans(name,name_en,days,daily_posts,max_sources,max_channels,daily_tests,price,price_en,description,description_en,is_free,sort,price_num,price_num_fa) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
             (f.get("name", "پلن"), f.get("name_en", ""), f.get("days", 30), f.get("daily_posts", 5), f.get("max_sources", 5), f.get("max_channels", 1), f.get("daily_tests", 2), f.get("price", ""), f.get("price_en", ""), f.get("description", ""), f.get("description_en", ""), f.get("is_free", 0), f.get("sort", 0), f.get("price_num", 0) or 0, f.get("price_num_fa", 0) or 0), commit=True)

def update_plan(pid, **f):
    for k, v in _safe_fields("plans", f).items(): q(f"UPDATE plans SET {k}=? WHERE id=?", (v, pid), commit=True)

def delete_plan(pid):
    """DB-18: پلنِ در استفاده حذف نمی‌شود — اول deactivate؛ حذف فیزیکی فقط وقتی هیچ ارجاعی نماند.
    خروجی: True=حذف شد · False=غیرفعال شد (کاربران فعال/در انتظار داشت)."""
    in_users = q("SELECT COUNT(*) c FROM users WHERE plan_id=? OR next_plan_id=?", (pid, pid), one=True)["c"]
    pending = q("SELECT COUNT(*) c FROM pay_requests WHERE plan_id=? AND status IN ('pending','processing')", (pid,), one=True)["c"]
    if in_users or pending:
        q("UPDATE plans SET active=0 WHERE id=?", (pid,), commit=True)
        return False
    q("DELETE FROM plans WHERE id=?", (pid,), commit=True)
    return True

def _assign_state(uid, pid, days=None):
    """محاسبهٔ حالتِ تصمیمِ واگذاری پلن (بدون نوشتن) تا نوشتن‌ها فقط SQL روی conn باشند."""
    p = get_plan(pid)
    if not p: return None
    u = get_user(uid)
    if not u: return None
    d = int(days if days is not None else p["days"]); now = now_utc()
    cur = parse_dt(u["plan_expires"]) if u["plan_expires"] else None
    active = bool(cur and cur > now and u["plan_id"])
    if active and not p["is_free"] and (get_plan(u["plan_id"]) or {"is_free": 0})["is_free"]: active = False
    return {"p": p, "u": u, "d": d, "now": now, "cur": cur, "active": active}

def _assign_writes(conn, uid, pid, st):
    """نوشتن‌های واگذاری پلن روی conn داده‌شده (درون تراکنش یا standalone).
    خروجی: (تاریخ انقضا/شروع, queued) — همان قرارداد assign_plan."""
    p, u, d, now, cur, active = st["p"], st["u"], st["d"], st["now"], st["cur"], st["active"]
    if active and u["plan_id"] != pid:
        # تغییر پلن: تاریخ پایان دست‌نخورده می‌ماند و فقط پلن و سهمیه‌ها عوض می‌شوند
        # (مبلغ طبق upgrade_quote فقط برای روزهای باقی‌مانده محاسبه شده است).
        conn.execute("UPDATE users SET plan_id=?, next_plan_id=NULL, next_plan_days=NULL, remind_key=NULL, role=CASE WHEN role='user' THEN 'admin' ELSE role END WHERE id=?",
                     (pid, uid))
        return cur, False
    exp = (cur if active else now) + timedelta(days=d)
    conn.execute("UPDATE users SET plan_id=?, plan_expires=?, remind_key=NULL, role=CASE WHEN role='user' THEN 'admin' ELSE role END, free_used=CASE WHEN ?=1 THEN 1 ELSE free_used END WHERE id=?",
                 (pid, iso_fixed(exp), p["is_free"], uid))   # DB-14: قالب کانونیکال برای مقایسهٔ رشته‌ای
    return exp, False

def assign_plan(uid, pid, days=None):
    """واگذاریِ پلن: بدونِ پلنِ فعال → همین حالا · همان پلنِ فعال → روزها اضافه می‌شود ·
    پلنِ متفاوتِ فعال → **پلن جایگزین می‌شود** (تاریخِ پایان دست‌نخورده می‌ماند؛ مبلغ طبق
    upgrade_quote فقط برای روزهای باقی‌مانده محاسبه شده است).

    توجه (FIN-016): ستون‌های ``next_plan_id``/``next_plan_days`` و پرچمِ ``queued`` **هنوز
    پیاده‌سازی نشده‌اند** — «زنجیرهٔ پلن» (پلنِ بعدی پس از پایانِ پلنِ فعلی) یک قابلیتِ
    محصولیِ پیاده‌نشده است، نه رفتارِ فعلی. خروجی: (تاریخ انقضا/شروع, queued=False)"""
    st = _assign_state(uid, pid, days)
    if not st: return None, False
    out = _trx(lambda conn: _assign_writes(conn, uid, pid, st))
    admin_limits_invalidate(uid)   # وگرنه تا ۶۰ ثانیه سهمیهٔ کهنه اعمال می‌شود
    return out

def proration_remaining_days(uid):
    """روزهای باقی‌ماندهٔ واقعیِ پلن فعلی (اعداد کسری برای کمتر از ۲۴ ساعت)؛ اگر active نباشد ۰ است."""
    u = get_user(uid)
    if not u: return 0.0
    cur = parse_dt(u["plan_expires"]) if u["plan_expires"] else None
    now = now_utc()
    if not cur or not u["plan_id"] or cur <= now: return 0.0
    return max(0.0, (cur - now).total_seconds() / 86400.0)

def proration_remaining_value(uid, cur_price_num, cur_days):
    """ارزش باقی‌ماندهٔ پلن فعلی بر اساس spec: current_plan_price × (remaining_time / original_plan_duration).
    اگر قیمت عددیِ پلن فعلی ثبت نشده باشد (0)، ارزش قابل‌محاسبه نیست → ۰ (بدون اعتبار؛ مبلغ کامل جدید)."""
    rem = proration_remaining_days(uid)
    if rem <= 0 or not cur_days or cur_days <= 0 or not cur_price_num:
        return 0.0
    return round(cur_price_num * (rem / cur_days), 2)

def revoke_plan(uid):
    q("UPDATE users SET plan_id=NULL, plan_expires=NULL, next_plan_id=NULL, next_plan_days=NULL, remind_key=NULL, role=CASE WHEN role='admin' THEN 'user' ELSE role END WHERE id=?", (uid,), commit=True)
    admin_limits_invalidate(uid)

def free_claim(uid):
    """CAS: فقط اولین ضربهٔ دکمهٔ رایگان برنده است (رفع TOCTOU دو ضربهٔ هم‌زمان؛ UISEC-10/SEC-04)."""
    with _lock:
        cur = db().execute("UPDATE users SET free_used=1 WHERE id=? AND COALESCE(free_used,0)=0", (uid,))
        db().commit()
        return cur.rowcount == 1

def free_release(uid):
    """برگشت claim پلن رایگان وقتی واگذاری شکست بخورد (جبران side-effect)."""
    with _lock:
        cur = db().execute("UPDATE users SET free_used=0 WHERE id=? AND free_used=1", (uid,))
        db().commit()
        return cur.rowcount == 1

def rollback_expired_benefits():
    """BIZ-03/BIZ-02: نقشِ ادمینِ اعطاشده توسط پلن، پس از انقضا/لغو پایین می‌آید.
    super (بر اساس SUPER_ADMIN_IDS و role='super') هرگز دست نمی‌خورد.
    خروجی: تعداد کاربرانی که نقششان user شد."""
    from newsbot.core_users import is_super, admin_limits_invalidate
    now = now_utc(); n = 0
    for u in q("SELECT id, plan_id, plan_expires FROM users WHERE role='admin'"):
        if is_super(u["id"]): continue
        exp = parse_dt(u["plan_expires"]) if u["plan_expires"] else None
        if not u["plan_id"] or not exp or exp <= now:
            with _lock:
                cur = db().execute("UPDATE users SET role='user' WHERE id=? AND role='admin'", (u["id"],))
                db().commit()
                if cur.rowcount == 1:
                    n += 1
                    admin_limits_invalidate(u["id"])
    if n: log_event("WARN", f"BIZ-03: role rolled back to user for {n} expired plan(s)")
    return n

# ── قیمت عددی (دوواحدی) و کیف پول — فاز ۱۴ · PH-01/Q-1c ──
def plan_num(p, lang="fa"):
    """تعرفهٔ عددیِ *هم‌واحدِ زبان کاربر*: fa → price_num_fa (تومان) · en → price_num ($).
    هیچ fallback بین‌واحدی انجام نمی‌شود؛ نبودِ تعرفهٔ همان زبان یعنی مسیر قیمت متنی (۰)."""
    return tariff(p, lang)[0]

def tariff(p, lang="fa"):
    """(مبلغِ عددیِ تعرفه, واحدِ پول) برای زبانِ داده‌شده.

    واحد از همان قاعده‌ای می‌آید که ستونِ تعرفه را انتخاب می‌کند: fa → تومان · en → دلار.
    اگر تعرفهٔ عددیِ آن زبان ثبت نشده باشد یا نامتناهی/بی‌کران باشد ⇒ (۰, ‹'') یعنی
    «مسیرِ قیمتِ متنی» (پلنِ مذاکره‌ای — Q-4c)."""
    if not p: return 0.0, ""
    key = "price_num_fa" if lang == "fa" else "price_num"
    try: amt = float(p[key] or 0)
    except (KeyError, IndexError, TypeError, ValueError): return 0.0, ""
    if not amt or not math.isfinite(amt): return 0.0, ""
    return amt, currency_of(lang)

def fmt_amt(v, lang="fa"):
    """نمایش مبلغ با واحد زبان کاربر: fa → «3000000 تومان» · en → «$30» (بدون نماد علمی)."""
    x = float(v or 0)
    s = str(int(x)) if x == int(x) else f"{x:.2f}"
    return (s + " تومان") if lang == "fa" else ("$" + s)

def _fmt_price(v): return fmt_amt(v, "en")

def _user_credit(uid, cur=None):
    """موجودیِ کیف پولِ کاربر در واحدِ داده‌شده (بدون واحد ⇒ مجموعِ نمایشی، غیرقابل‌خرج)."""
    if cur: return wallet_get(uid, cur)
    return wallet_total(uid)

def upgrade_quote(uid, p, cur, disc_percent=None, lang="en"):
    """مبلغ ارتقا/تمدید/خرید طبق تعرفهٔ عددیِ هم‌واحدِ زبانِ کاربر.

    PH-01/Q-1c: موجودیِ کیف پول **فقط از همان واحدِ تعرفه** خوانده می‌شود ⇒ پاداشِ یک
    واحد با واحدِ دیگر قابل خرج نیست.
    PH-01/FIN-004: تخفیف روی **ناخالص** اعمال می‌شود و سهمِ کیف پول از مقدارِ تخفیف‌خورده
    مشتق می‌گردد ⇒ ``payable + used_balance == ناخالص × (۱-درصد)`` و مبلغِ نمایش‌داده‌شده
    همان مبلغِ دریافتی است.
    """
    new_num, new_cur = tariff(p, lang)
    new_days = int(p["days"] or 0); rem = proration_remaining_days(uid)
    bal = wallet_get(uid, new_cur) if new_cur else 0.0
    out = {"kind": "fresh", "blocked": False, "rem_days": rem, "credit": 0.0, "new_cost": 0.0, "surplus": 0.0,
           "balance": bal, "used_balance": 0.0, "payable": 0.0, "keep_expiry": False, "payable_num": False,
           "from_plan": (cur["id"] if cur else None), "to_plan": p["id"], "cur": new_cur}
    if new_days <= 0: return out
    same = bool(cur and p and cur["id"] == p["id"])
    cur_num, _ccur = tariff(cur, lang); cur_days = int(cur["days"] or 0) if cur else 0
    try: disc = max(0.0, min(100.0, float(disc_percent))) / 100.0 if disc_percent else 0.0
    except (TypeError, ValueError): disc = 0.0
    if same and rem > 0:                                   # تمدید همان پلن فعال
        out["kind"] = "renew"
        if new_num:
            gross = round(new_num * (1 - disc), 2)
            used = round(min(bal, gross), 2)
            out.update(new_cost=gross, used_balance=used, payable=round(gross - used, 2), payable_num=True)
    elif not new_num:
        return out                                         # بدون تعرفهٔ عددیِ هم‌زبان: مسیر متنی
    elif rem > 0 and cur and cur_num and cur_days > 0:
        new_rate, cur_rate = new_num / new_days, cur_num / cur_days
        if new_rate < cur_rate:                            # نزول پلن: ممنوع
            out.update(kind="blocked", blocked=True, payable_num=True)
            return out
        credit = proration_remaining_value(uid, cur_num, cur_days)
        gross = round(new_num * (rem / new_days), 2)        # هزینهٔ پلن جدید برای روزهای باقی‌مانده
        gross_d = round(gross * (1 - disc), 2)              # تخفیف روی ناخالص
        backend = round(max(0.0, gross_d - credit), 2)
        out.update(kind="upgrade", credit=credit, new_cost=gross_d, surplus=round(max(0.0, credit - gross_d), 2),
                   used_balance=round(min(bal, backend), 2), payable=round(backend - min(bal, backend), 2),
                   keep_expiry=True, payable_num=True)
    else:
        gross = round(new_num * (1 - disc), 2)
        used = round(min(bal, gross), 2)
        out.update(kind="fresh", new_cost=gross, used_balance=used, payable=round(gross - used, 2), payable_num=True)
    return out

def _apply_plan_change_sql(conn, uid, pid, pc, ref=None):
    """نوشتن‌های کیف پول/سابقهٔ تغییر پلن روی conn داده‌شده (درون تراکنش یا standalone).

    PH-01/Q-1c: حرکتِ کیف پول روی **جدولِ wallet** و در **واحدِ تعرفه** انجام می‌شود؛
    موجودیِ کافی نباشد ⇒ استثنا (کل تراکنش برمی‌گردد) به‌جای کلّ اشتباهِ بی‌سروصدا.
    FIN-006: مقدارِ ذخیره‌شده گرد می‌شود و رسید دقیقاً همان مقدار را می‌نویسد ⇒ جمعِ
    رسیدها همیشه با موجودی برابر است.
    FIN-007: سابقهٔ تغییر پلن **همیشه** ثبت می‌شود (حتی وقتی کیف پول جابه‌جا نشده) تا
    سابقهٔ خرید قابل‌بازسازی باشد.
    """
    used = float(pc.get("used_balance") or 0); surplus = float(pc.get("surplus") or 0)
    cur = pc.get("cur") or ""
    if used or surplus:
        if not is_spendable(cur):
            raise WalletError(f"plan change without a spendable currency: {cur!r}")
        _wallet_apply(conn, uid, cur, round(-used + surplus, 2), "plan_change",
                      str(pid) if ref is None else ref)
    conn.execute("INSERT INTO plan_changes(user_id, plan_id, from_plan, kind, credit_used, surplus, paid, rem_days, ref, ts) VALUES(?,?,?,?,?,?,?,?,?,?)",
                 (uid, pid, pc.get("from_plan"), pc.get("kind") or "fresh", used, surplus, pc.get("payable"),
                  pc.get("rem_days"), ("" if ref is None else str(ref)), now_iso()))

def apply_plan_change(uid, pid, pc, ref=None):
    """کیف پول را مصرف می‌کند، مابه‌التفاوت کاهش پلن را برمی‌گرداند و سابقه‌ی تغییر پلن را ثبت می‌کند."""
    # یک تراکنش: یا هم موجودی کم می‌شود و هم سابقه ثبت، یا هیچ‌کدام (قبلاً دو commit جدا بود)
    _trx(lambda conn: _apply_plan_change_sql(conn, uid, pid, pc, ref))

# ── FIX v4: بازیابی پرداخت گیرکرده ──
def recover_stuck_payments():
    """پرداخت‌های گیرکرده در 'processing' (کرش/هنگ بین pay_claim و تصمیم نهایی) پس از
    PAY_STUCK_MIN دقیقه به 'pending' برمی‌گردند تا مدیر کلان دوباره تصمیم بگیرد.
    Idempotent: فقط ردیف‌هایی با claimed_at قدیمی‌تر از cutoff جابه‌جا می‌شوند (CAS روی status).

    PH-01: (۱) cutoff با قالبِ کانونیکالِ DB-14 ساخته می‌شود (مقایسهٔ رشته‌ای هم‌فرمت)؛
    (۲) ردیف‌هایی که ``applied_at`` دارند به «pending» **برنمی‌گردند** — پلنشان اعمال شده و
    برگرداندن به pending یعنی اعمالِ دوباره (FIN-011/FIN-022). آن‌ها به «approved» بسته
    می‌شوند (واقعیت: پرداخت اجرا شده) تا اپراتور بتواند ببندد."""
    cutoff = iso_fixed(now_utc() - timedelta(minutes=config.PAY_STUCK_MIN))
    with _lock:
        # ردیف‌هایی که قبلاً اعمال شده‌اند ⇒ وضعیتِ نهایی (applied) — نه «pending»
        cur = db().execute("UPDATE pay_requests SET status='approved' "
                           "WHERE status='processing' AND applied_at IS NOT NULL")
        db().commit()
        n_applied = cur.rowcount
        cur = db().execute("UPDATE pay_requests SET status='pending', claimed_at=NULL "
                           "WHERE status='processing' AND claimed_at IS NOT NULL AND claimed_at<? "
                           "AND applied_at IS NULL", (cutoff,))
        db().commit()
        n = cur.rowcount
    if n: log_event("WARN", f"{n} stuck payment(s) returned to pending (claimed_at older than {config.PAY_STUCK_MIN}m)")
    if n_applied: log_event("WARN", f"{n_applied} stuck payment(s) closed as approved (already applied)")
    return n
