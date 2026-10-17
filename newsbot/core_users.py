# -*- coding: utf-8 -*-
"""کاربران، نقش‌ها، زبان، سهمیه‌ها و کووتاهای روزانه.
FIX v4: channel_posts_today بر «مرز نیمه‌شب محلی کانال» حساب می‌کند (DST-aware).
DB-13 (فاز ۱۰): شمارش با predicate ایندکس‌دار posted_at>=? در SQL انجام می‌شود (بدون اسکن کامل کانال)؛
پس از کانونیکال‌سازی DB-14، ترتیب الفبایی رشته‌های ISO دقیقاً ترتیب زمانی است.
"""
import json, time
from datetime import datetime, timedelta
from newsbot import config
from newsbot.db import q, db, _lock, _trx
from newsbot.logutils import log
from newsbot.timeutils import now_utc, now_iso, parse_dt, today_str, zone_of, offset_now, iso_fixed


def is_super(uid): return uid in config.SUPER_ADMIN_IDS

def get_user(uid): return q("SELECT * FROM users WHERE id=?", (uid,), one=True)

def ensure_user(uid, username="", name="", premium=None):
    u = get_user(uid)
    if not u:
        # DB-10/BIZ-07 (فاز ۸): درجِ کاربر با INSERT OR IGNORE — دو عملیات همزمانِ یک کاربر جدید
        # نباید IntegrityError بدهند؛ اگر رقیب زودتر درج کرده باشد، همان ردیف دوباره خوانده و استفاده می‌شود.
        q("INSERT OR IGNORE INTO users(id,username,name,role,premium,created_at,last_seen) VALUES(?,?,?,?,?,?,?)",
          (uid, username or "", name or "", "super" if is_super(uid) else "user", 1 if premium else 0, now_iso(), now_iso()), commit=True)
        u = get_user(uid)
    if u:
        # last_seen حداکثر هر ۵ دقیقه نوشته می‌شود تا فشار نوشتن پایین بماند
        ls = parse_dt(u["last_seen"])
        if not ls or (now_utc() - ls).total_seconds() > 300 or (premium is not None and int(bool(premium)) != (u["premium"] or 0)) or (username and username != u["username"]):
            q("UPDATE users SET username=?,name=?,last_seen=?,premium=?,role=CASE WHEN ?=1 THEN 'super' ELSE role END WHERE id=?",
              (username or u["username"], name or u["name"], now_iso(), (1 if premium else 0) if premium is not None else (u["premium"] or 0), 1 if is_super(uid) else 0, uid), commit=True)
        else:
            return u
    return get_user(uid)

def role_of(uid):
    if is_super(uid): return "super"
    u = get_user(uid); return u["role"] if u else "user"

def user_lang(uid):
    u = get_user(uid); return u["lang"] if u and u["lang"] in config.LANGS else None

def set_lang(uid, lang): q("UPDATE users SET lang=? WHERE id=?", (lang if lang in config.LANGS else "fa", uid), commit=True)

def list_users(role=None, limit=200000):
    return q("SELECT * FROM users " + (" WHERE role=?" if role else "") + " ORDER BY created_at DESC LIMIT ?", ((role, limit) if role else (limit,)))

def list_admins(): return q("SELECT * FROM users WHERE role IN ('admin','super') AND banned=0")

def count_users(): return q("SELECT role, COUNT(*) c FROM users GROUP BY role")

def can_admin(uid): return role_of(uid) in ("admin", "super")

# ── کش کوتاه‌مدت سهمیه‌ها (یک تیک/تعامل) تا کاهش ترافیک read در حلقه‌های scheduler/publish ──
_LIMITS_TTL = 60.0
_limits_cache = {}

def admin_limits_cached(uid, max_age=_LIMITS_TTL):
    ent = _limits_cache.get(uid)
    if ent and (time.monotonic() - ent[0]) < max_age: return ent[1]
    val = admin_limits(uid); _limits_cache[uid] = (time.monotonic(), val)
    return val

def admin_limits_invalidate(uid=None):
    if uid is None: _limits_cache.clear(); return
    _limits_cache.pop(uid, None)

def admin_limits(uid):
    from newsbot.core_plans import get_plan   # lazy: جلوگیری از وابستگی دایره‌ای core_users <-> core_plans
    if is_super(uid):
        return dict(daily_posts=None, max_sources=None, max_channels=None, daily_tests=None, plan=None, plan_id=None, expires=None, active=True, next_plan=None)
    u = get_user(uid); p = get_plan(u["plan_id"]) if u and u["plan_id"] else None
    exp = parse_dt(u["plan_expires"]) if u else None
    active = bool(p and exp and exp > now_utc() and not u["banned"])
    return dict(daily_posts=p["daily_posts"] if p else 0, max_sources=p["max_sources"] if p else 0,
                max_channels=p["max_channels"] if p else 0, daily_tests=p["daily_tests"] if p else 0,
                plan=p, plan_id=p["id"] if p else None, expires=exp, active=active,
                next_plan=get_plan(u["next_plan_id"]) if u and u["next_plan_id"] else None)

def admin_offset(uid):
    """آفست جاریِ منطقهٔ کانال نخستِ ادمین — ARCH-10: از نام IANA (یا آفست قدیمی) برای همین لحظه مشتق می‌شود."""
    ch = q("SELECT settings FROM channels WHERE admin_id=? ORDER BY id LIMIT 1", (uid,), one=True)
    try:
        st = json.loads(ch["settings"]) if ch and ch["settings"] else {}
        val = st.get("tz") or st.get("utc_offset")
        return offset_now(val) if val is not None else config.DEFAULT_UTC_OFFSET
    except Exception: return config.DEFAULT_UTC_OFFSET

def usage_today(uid):
    r = q("SELECT posts,tests,ai FROM usage WHERE admin_id=? AND day=?", (uid, today_str(admin_offset(uid))), one=True)
    if not r: return {"posts": 0, "tests": 0, "ai": 0}
    try: ai = int(r["ai"] or 0)
    except (IndexError, KeyError, TypeError): ai = 0
    return {"posts": r["posts"], "tests": r["tests"], "ai": ai}

def ai_cap_for(uid):
    """BIZ-01: سقف روزانهٔ مصرف هوش مصنوعی — ابرادمین نامحدود؛ بدون پلن فعال = سقف رایگان."""
    if is_super(uid): return None
    lim = admin_limits(uid)
    if not lim["active"]: return config.AI_DAILY_FREE
    p = lim["plan"]
    return config.AI_DAILY_FREE if (not p or p["is_free"]) else config.AI_DAILY_PAID

def ai_used_today(uid):
    r = q("SELECT ai FROM usage WHERE admin_id=? AND day=?", (uid, today_str(admin_offset(uid))), one=True)
    try: return int(r["ai"] or 0) if r else 0
    except (IndexError, KeyError, TypeError): return 0

def ai_claim(uid):
    """ادعای اتمیک یک واحد از سقف روزانهٔ AI (BIZ-01). False ⇒ سقف تمام شده است.
    مثل reserve_publication با یک UPDATE شرطی CAS درون تراکنش؛ دو فراخوانی هم‌زمان هرگز از دروازه رد نمی‌شوند."""
    cap = ai_cap_for(uid)
    if cap is None: return True
    day = today_str(admin_offset(uid))
    def _go(conn):
        cur = conn.execute("INSERT INTO usage(admin_id,day,posts,tests,ai) VALUES(?,?,0,0,1) ON CONFLICT(admin_id,day) DO UPDATE SET ai=ai+1 WHERE ai < ?", (uid, day, cap))
        return cur.rowcount > 0
    return bool(_trx(_go))

_RESET_FIELDS = ("tests", "posts", "ai")

def usage_reset(uid, field="tests"):
    """DB-26: صفر کردن سهمیهٔ امروز — نام ستون فقط از فهرست سفید (قبلاً f-string بدون اعتبارسنجی).
    توجه (BIZ-08): این تابع تنها سهمیهٔ روزانهٔ *پلنِ* ادمین (جدول usage) را صفر می‌کند؛
    سقف روزانهٔ هر کانال از جدول posted مشتق می‌شود و جداگانه محاسبه می‌شود — بازنشانی آن از این‌جا
    عمداً انجام نمی‌شود چون ردیف‌های posted سند دقیقاً-یک‌بارِ انتشارند (حذفشان قواعد یکتایی را می‌شکند)."""
    if field not in _RESET_FIELDS: raise ValueError(f"unknown usage field: {field}")
    q(f"UPDATE usage SET {field}=0 WHERE admin_id=? AND day=?", (uid, today_str(admin_offset(uid))), commit=True)

def channel_posts_today(cid, off):
    """تعداد پست‌های منتشرشده‌ی «امروزِ» خودِ این کانال (بر پایه‌ی ساعت محلی کانال) از جدول posted.
    سهمیه‌ی usage سراسریِ ادمین است؛ سقف روزانه‌ی کانال باید از شمارش همان کانال دربیاید.
    FIX v4: مقایسه بر مرز نیمه‌شب محلی (DST-aware) است.
    DB-13: شمارش در SQL با predicate روی posted_at انجام می‌شود و از idx_posted_ch_at(channel_id، posted_at)
    استفاده می‌کند؛ posted_at ذخیره‌شده کانونیکال UTC است (DB-14) و مرز با iso_fixed ساخته می‌شود."""
    try:
        from newsbot.core_channels import get_channel
        ch = get_channel(cid)
        if not ch: return 0
        z = zone_of(off)   # ARCH-10: نام IANA یا آفست؛ نیمه‌شب محلی DST-aware
        loc = now_utc().astimezone(z)
        cut = iso_fixed(datetime(loc.year, loc.month, loc.day, tzinfo=z))
        r = q("SELECT COUNT(*) c FROM posted WHERE channel_id=? AND posted_at>=?", (ch["chat_id"], cut), one=True)
        return int(r["c"] or 0)
    except Exception:
        log.debug("channel_posts_today failed cid=%s", cid, exc_info=True)
        return 0

def remaining(uid, field="posts", cid=None):
    """مانده‌ی سهمیه‌ی امروز. برای پست‌ها سقفِ روزانه‌ی همان کانال هم اعمال می‌شود (min با سقف پلن).
    cid=None یعنی نمای کلی کاربر → مثل قبل سقف کانال نخست مبنا است."""
    from newsbot.core_channels import list_channels, get_settings, get_channel
    lim = admin_limits_cached(uid)
    if field != "posts":
        cap = lim["daily_tests"]
        return None if cap is None else max(0, cap - usage_today(uid)["tests"])
    left = None
    if lim["daily_posts"] is not None: left = max(0, lim["daily_posts"] - usage_today(uid)["posts"])
    if cid is None:
        chs = list_channels(uid); cid = chs[0]["id"] if chs else None
    if cid is not None:
        try: ch_cap = get_settings(cid).get("daily_posts_cap")
        except Exception: ch_cap = None
        if ch_cap:
            # مرز «امروزِ» سقف روزانه باید بر پایهٔ ساعت محلیِ خودِ کانال باشد، نه آفست ادمینی که چرخه را اجرا کرده؛
            # فقط اگر کانال منطقه‌ای نداشت، به آفست ادمین/پیش‌فرض برمی‌گردیم. (ARCH-10: نام IANA هم پذیرفته می‌شود)
            try:
                ch_st = json.loads((get_channel(cid) or {})["settings"] or "{}")
                ch_off = ch_st.get("tz") or ch_st.get("utc_offset")
            except Exception: ch_off = None
            if ch_off is None: ch_off = admin_offset(uid)
            ch_left = max(0, int(ch_cap) - channel_posts_today(cid, ch_off))
            left = ch_left if left is None else min(left, ch_left)
    return left

def expiring_users():
    """کاربران غیرممنوعی که پلنِ فعالِشان (بر اساس plan_expires واقعی، نه مدت پلن) به بازه‌های هشدار رسیده است:
    ۴۸ ساعت (کلید «48h») و ۲۴ ساعت (کلید «24h»؛ کلید قدیمی «24» هم سرکوب‌کنندهٔ آن است).
    نزدیک‌ترین بازهٔ رسیده، هر تیک یک‌بار رزرو می‌شود تا اگر ربات offline بوده، صفِ هشدارِ قدیمی ارسال نشود.
    رزرو یک‌سویه است → تیک‌های مکرر و ری‌استارت تکرار نمی‌کنند؛ ارتقا/تمدید remind_key را NULL می‌کنند و هشدارهای
    انقضای تازهٔ جدید دوباره فعال می‌شوند. خروجی: [(u, key, left_h), ...]"""
    out, now = [], now_utc()
    # پنجره در SQL بسته می‌شود تا هر تیک کل جدول کاربران خوانده نشود؛ همه‌ی نویسنده‌های plan_expires
    # مقدار را با isoformat روی دیت‌تایم UTC-aware ذخیره می‌کنند ⇒ مقایسه‌ی رشته‌ای دقیقاً ترتیب زمانی است.
    soon = (now + timedelta(hours=48)).isoformat()
    for u in q("SELECT * FROM users WHERE plan_expires IS NOT NULL AND banned=0 AND plan_expires >? AND plan_expires <=?",
               (now.isoformat(), soon)):
        exp = parse_dt(u["plan_expires"])
        if not exp or exp <= now: continue
        left = (exp - now).total_seconds() / 3600
        rk = u["remind_key"]
        if left <= 24:
            key = "24h"
            if rk in ("24h", "24"): continue
        elif left <= 48:
            key = "48h"
            if rk == "48h": continue
        else:
            continue
        cur_k = u["remind_key"]
        # ادعای اتمی با محافظ «خوش‌بینانه»: فقط اگر از زمانی که خواندیم کسی مقدار را عوض نکرده باشد.
        # حیاتی است که کلیدِ جدید (key) در شرط نیاید؛ وگرنه وقتی کارگری مقدار را روی key می‌گذارد،
        # کارگرهای بعدی همان مقدار را مطابق می‌بینند و همه با هم ادعا می‌کنند (هشدار تکراری).
        with _lock:
            cur = db().execute("UPDATE users SET remind_key=? WHERE id=? AND (remind_key IS NULL OR remind_key=?)",
                               (key, u["id"], cur_k))
            db().commit()
        if cur.rowcount:
            out.append((u, key, left))
    return out
