# -*- coding: utf-8 -*-
"""پلن‌ها (زنجیره‌ای)، اشتراک، proration، کیف پول و بازیابی پرداخت گیرکرده.
FIX v4: recover_stuck_payments() پرداخت‌های گیرکرده در 'processing'
(کرش بین pay_claim و تصمیم نهایی) را پس از PAY_STUCK_MIN دقیقه به 'pending' برمی‌گرداند.
"""
from datetime import timedelta
from newsbot import config
from newsbot.db import q, db, _lock, _safe_fields, _trx
from newsbot.logutils import log_event
from newsbot.timeutils import now_utc, now_iso, parse_dt
from newsbot.core_users import get_user, admin_limits_invalidate


def seed_plans():
    if not q("SELECT 1 FROM plans LIMIT 1"):
        create_plan(name="رایگان", name_en="Free", days=7, daily_posts=1, max_sources=2, max_channels=1, daily_tests=1, price="رایگان", price_en="Free", description="۱ پست در روز · ۷ روز", description_en="1 post/day · 7 days", is_free=1, sort=0)
        create_plan(name="حرفه‌ای", name_en="Pro", days=30, daily_posts=20, max_sources=15, max_channels=3, daily_tests=5, price="توافقی", price_en="Contact us", description="۲۰ پست روزانه · ۱۵ منبع · ۳ کانال", description_en="20 posts/day · 15 sources · 3 channels", is_free=0, sort=1)

def list_plans(active_only=True): return q("SELECT * FROM plans " + (" WHERE active=1" if active_only else "") + " ORDER BY sort, id")
def get_plan(pid): return q("SELECT * FROM plans WHERE id=?", (pid,), one=True) if pid else None

def plan_txt(p, field, lang="fa"):
    if not p: return ""
    if lang == "en" and p[f"{field}_en"]: return p[f"{field}_en"]
    return p[field] or ""

def create_plan(**f):
    return q("INSERT INTO plans(name,name_en,days,daily_posts,max_sources,max_channels,daily_tests,price,price_en,description,description_en,is_free,sort,price_num) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
             (f.get("name", "پلن"), f.get("name_en", ""), f.get("days", 30), f.get("daily_posts", 5), f.get("max_sources", 5), f.get("max_channels", 1), f.get("daily_tests", 2), f.get("price", ""), f.get("price_en", ""), f.get("description", ""), f.get("description_en", ""), f.get("is_free", 0), f.get("sort", 0), f.get("price_num", 0) or 0), commit=True)

def update_plan(pid, **f):
    for k, v in _safe_fields("plans", f).items(): q(f"UPDATE plans SET {k}=? WHERE id=?", (v, pid), commit=True)

def delete_plan(pid): q("DELETE FROM plans WHERE id=?", (pid,), commit=True)

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
                 (pid, exp.isoformat(), p["is_free"], uid))
    return exp, False

def assign_plan(uid, pid, days=None):
    """
    زنجیره‌ای: بدون پلن فعال → همین حالا · همان پلن فعال → روزها اضافه می‌شود · پلن متفاوت فعال → «پلن بعدی» (ارتقا از رایگان فوری است).
    خروجی: (تاریخ انقضا/شروع, queued)
    """
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

# ── قیمت عددی و کیف پول ──
def _price_num(p):
    """قیمت عددیِ پلن (برای proration)؛ اگر ستون price_num وجود نداشته باشد یا خالی باشد ۰ (proration انجام نمی‌شود)."""
    try:
        return float(p["price_num"] or 0)
    except (KeyError, IndexError, TypeError, ValueError):
        return 0.0

def _user_credit(uid):
    """موجودی کیف پول کاربر (از مابه‌التفاوت کاهش پلن)."""
    try:
        r = q("SELECT credit FROM users WHERE id=?", (uid,), one=True); return float(r["credit"] or 0) if r else 0.0
    except (KeyError, IndexError, TypeError, ValueError): return 0.0

def _fmt_price(v):
    s = f"{v:g}"
    return s + "$"

def upgrade_quote(uid, p, cur, disc_percent=None):
    """مبلغ ارتقا/خرید طبق قاعده‌ی محصول:
    قابل پرداخت = (قیمت پلن جدید ÷ روزهای دوره‌ی جدید) × روزهای باقی‌مانده
    − اعتبار باقی‌مانده‌ی پلن فعلی − موجودی کیف پول
    تاریخ پایان دوره دست‌نخورده می‌ماند و فقط پلن و سهمیه‌ها عوض می‌شوند.
    اگر پلن جدید برای روزهای باقی‌مانده ارزان‌تر از اعتبار پلن فعلی باشد، مابه‌التفاوت به کیف پول می‌رود (کاهش پلن).
    اگر قیمت عددیِ پلن ثبت نشده باشد، محاسبه انجام نمی‌شود و قیمت متنی نمایش داده می‌شود."""
    new_num = _price_num(p); new_days = int(p["days"] or 0); rem = proration_remaining_days(uid); bal = _user_credit(uid)
    out = {"kind": "fresh", "rem_days": rem, "credit": 0.0, "new_cost": 0.0, "surplus": 0.0,
           "balance": bal, "used_balance": 0.0, "payable": 0.0, "keep_expiry": False, "payable_num": False,
           "from_plan": (cur["id"] if cur else None), "to_plan": p["id"]}
    if not new_num or new_days <= 0:
        return out
    if rem > 0 and cur and _price_num(cur) and int(cur["days"] or 0) > 0:
        credit = proration_remaining_value(uid, _price_num(cur), int(cur["days"]))
        new_cost = round(new_num * (rem / new_days), 2)          # هزینه‌ی پلن جدید فقط برای روزهای باقی‌مانده
        backend = round(max(0.0, new_cost - credit), 2)
        out.update(kind="upgrade", credit=credit, new_cost=new_cost, surplus=round(max(0.0, credit - new_cost), 2),
                   used_balance=round(min(bal, backend), 2), payable=round(backend - min(bal, backend), 2),
                   keep_expiry=True, payable_num=True)
    else:
        out.update(kind="fresh", new_cost=new_num, used_balance=round(min(bal, new_num), 2),
                   payable=round(new_num - min(bal, new_num), 2), payable_num=True)
    if disc_percent: out["payable"] = round(out["payable"] * (100 - disc_percent) / 100, 2)
    return out

def _apply_plan_change_sql(conn, uid, pid, pc):
    """نوشتن‌های کیف پول/سابقهٔ تغییر پلن روی conn داده‌شده (درون تراکنش یا standalone)."""
    used = float(pc.get("used_balance") or 0); surplus = float(pc.get("surplus") or 0)
    if used or surplus:
        conn.execute("UPDATE users SET credit=MAX(0, COALESCE(credit,0) - ? + ?) WHERE id=?", (used, surplus, uid))
        conn.execute("INSERT INTO plan_changes(user_id, plan_id, from_plan, kind, credit_used, surplus, paid, rem_days, ts) VALUES(?,?,?,?,?,?,?,?,?)",
                     (uid, pid, pc.get("from_plan"), pc.get("kind") or "fresh", used, surplus, pc.get("payable"), pc.get("rem_days"), now_iso()))

def apply_plan_change(uid, pid, pc):
    """کیف پول را مصرف می‌کند، مابه‌التفاوت کاهش پلن را برمی‌گرداند و سابقه‌ی تغییر پلن را ثبت می‌کند."""
    # یک تراکنش: یا هم موجودی کم می‌شود و هم سابقه ثبت، یا هیچ‌کدام (قبلاً دو commit جدا بود)
    _trx(lambda conn: _apply_plan_change_sql(conn, uid, pid, pc))

# ── FIX v4: بازیابی پرداخت گیرکرده ──
def recover_stuck_payments():
    """پرداخت‌های گیرکرده در 'processing' (کرش/هنگ بین pay_claim و تصمیم نهایی) پس از
    PAY_STUCK_MIN دقیقه به 'pending' برمی‌گردند تا مدیر کلان دوباره تصمیم بگیرد.
    Idempotent: فقط ردیف‌هایی با claimed_at قدیمی‌تر از cutoff جابه‌جا می‌شوند (CAS روی status)."""
    cutoff = (now_utc() - timedelta(minutes=config.PAY_STUCK_MIN)).isoformat()
    with _lock:
        cur = db().execute("UPDATE pay_requests SET status='pending', claimed_at=NULL WHERE status='processing' AND claimed_at IS NOT NULL AND claimed_at<?", (cutoff,))
        db().commit()
        n = cur.rowcount
    if n: log_event("WARN", f"{n} stuck payment(s) returned to pending (claimed_at older than {config.PAY_STUCK_MIN}m)")
    return n
