# -*- coding: utf-8 -*-
"""کدهای تخفیف: مصرف و برگشت اتمیک."""
import re
from newsbot import config
from newsbot.db import q, db, _lock, _safe_fields
from newsbot.timeutils import now_utc, now_iso, parse_dt


def disc_list(): return q("SELECT * FROM discounts ORDER BY created_at DESC")
def disc_get(code): return q("SELECT * FROM discounts WHERE code=?", (str(code).strip().upper(),), one=True) if code else None

def disc_create(code, percent, expires_iso, max_uses=0):
    code = re.sub(r"\s+", "", str(code)).upper()
    if not code or disc_get(code): return None
    q("INSERT INTO discounts(code,percent,expires,max_uses,used,active,created_at) VALUES(?,?,?,?,0,1,?)", (code, max(1, min(100, int(percent))), expires_iso, int(max_uses or 0), now_iso()), commit=True); return code

def disc_update(code, **f):
    for k, v in _safe_fields("discounts", f).items(): q(f"UPDATE discounts SET {k}=? WHERE code=?", (v, code), commit=True)

def disc_delete(code): q("DELETE FROM discounts WHERE code=?", (code,), commit=True)

def disc_valid(code):
    """خروجی: (row|None, reason) — reason: ok | notfound | inactive | expired | exhausted"""
    d = disc_get(code)
    if not d: return None, "notfound"
    if not d["active"]: return None, "inactive"
    e = parse_dt(d["expires"])
    if e and e <= now_utc(): return None, "expired"
    if d["max_uses"] and d["used"] >= d["max_uses"]: return None, "exhausted"
    return d, "ok"

def disc_use(code):
    """مصرف اتمیک: فقط اگر ظرفیت باقی مانده باشد used یک واحد بالا می‌رود؛ rowcount=0 ⇒ «ظرفیت تمام شده»."""
    with _lock:
        cur = db().execute("UPDATE discounts SET used=used+1 WHERE code=? AND (max_uses=0 OR used<max_uses)", (str(code).strip().upper(),))
        n = cur.rowcount
        db().commit()
        return n == 1

def disc_refund(code):
    """برگشتِ یک مصرف تخفیف (جبرانِ تأیید نیمه‌کارهٔ پرداخت): used یک واحد کم می‌شود ولی هرگز منفی نمی‌شود."""
    with _lock:
        cur = db().execute("UPDATE discounts SET used=MAX(0, used-1) WHERE code=?", (str(code).strip().upper(),))
        n = cur.rowcount
        db().commit()
        return n == 1

def discount_price(price_text, percent):
    """اگر قیمت عدد داشته باشد، مبلغ پس از تخفیف را با همان واحد برمی‌گرداند؛ در غیر این‌صورت «قیمت (−٪ تخفیف)»."""
    t = str(price_text or "").translate(config._FA_DIGITS); m = re.search(r"\d[\d,٬.]*", t)
    if not m: return f"{price_text} (-{percent}%)"
    try: n = float(m.group(0).replace(",", "").replace("٬", ""))
    except Exception: return f"{price_text} (-{percent}%)"
    v = n * (100 - percent) / 100; vs = f"{int(v):,}" if v == int(v) else f"{v:,.2f}"
    return t[:m.start()] + vs + t[m.end():]
