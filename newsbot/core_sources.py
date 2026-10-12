# -*- coding: utf-8 -*-
"""منابع خبری (مستقل برای هر کانال) + شمارش شکست و backoff نمایی."""
import sqlite3
from datetime import timedelta
from newsbot import config
from newsbot.db import q
from newsbot.logutils import log, bump, redact
from newsbot.timeutils import now_utc, now_iso


def normalize_url(u):
    u = str(u).strip()
    if not u.startswith(("http://", "https://")): u = "https://" + u
    return u.split("#")[0].rstrip("/")

def list_sources(cid, active_only=False): return q("SELECT * FROM sources WHERE channel_id=? " + (" AND active=1" if active_only else "") + " ORDER BY id", (cid,))
def count_sources(uid): return q("SELECT COUNT(*) c FROM sources WHERE admin_id=?", (uid,), one=True)["c"]

def add_source(uid, cid, url, title="", api_url="", api_key="", api_note=""):
    url = normalize_url(url)
    if q("SELECT 1 FROM sources WHERE channel_id=? AND url=?", (cid, url), one=True): return None
    try:
        return q("INSERT INTO sources(admin_id,channel_id,url,title,api_url,api_key,api_note) VALUES(?,?,?,?,?,?,?)", (uid, cid, url, title, api_url or None, api_key or None, api_note or ""), commit=True)
    except sqlite3.Error:   # ایندکس یکتای (channel_id,url): ثبت تکراری هم‌زمان → مانند پیش‌بررسی، None
        log.warning("add_source duplicate url ignored: cid=%s url=%s", cid, url); return None

def get_source(sid): return q("SELECT * FROM sources WHERE id=?", (sid,), one=True)

def source_of_article(a):
    sid = a["source_id"] if a and "source_id" in a.keys() else None
    return get_source(sid) if sid else None

def del_source(sid, uid): q("DELETE FROM sources WHERE id=? AND admin_id=?", (sid, uid), commit=True)

def set_source_active(sid, val, col="active"):
    if col not in ("active", "bot_active"): return None
    q(f"UPDATE sources SET {col}=? WHERE id=?", (1 if val else 0, sid), commit=True); return bool(val)

def toggle_source(sid, uid, col="active"):
    """col='active' ⇒ منبعِ محتوای کانال · col='bot_active' ⇒ منبعِ محتوای ربات (نسخه‌ی کامل داخل ربات)"""
    if col not in ("active", "bot_active"): col = "active"
    q(f"UPDATE sources SET {col}=1-COALESCE({col},1) WHERE id=? AND admin_id=?", (sid, uid), commit=True); s = get_source(sid); return bool(s and s[col])

def set_source_api(sid, api_url="", api_key="", api_note=""):
    """ثبت/حذف API یک منبع. خالی‌بودن api_url ⇒ حذف API و برگشت به مسیر عادی RSS/HTML."""
    q("UPDATE sources SET api_url=?, api_key=?, api_note=? WHERE id=?", ((api_url or "").strip() or None, (api_key or "").strip() or None, (api_note or "").strip(), sid), commit=True)
    return get_source(sid)

def source_ok(sid, feed_url=None, etag=None, last_modified=None):
    q("UPDATE sources SET last_fetch=?, fail_count=0, last_error=NULL, etag=?, last_modified=?, feed_url=COALESCE(?, feed_url) WHERE id=?", (now_iso(), etag, last_modified, feed_url, sid), commit=True)

def _source_fail_count(sid):
    r = q("SELECT fail_count c FROM sources WHERE id=?", (sid,), one=True); return int(r["c"]) if r else 0

def source_fail(sid, err):
    """شمارش شکست + backoff نمایی: next_try_after = now + min(2^(fc-1)*SOURCE_PAUSE_MIN, 12h)."""
    bump("source_fail")
    wait = min(config.SOURCE_PAUSE_MIN * (2 ** max(0, _source_fail_count(sid))), 720)
    until = (now_utc() + timedelta(minutes=wait)).isoformat()
    q("UPDATE sources SET last_fetch=?, fail_count=fail_count+1, last_error=?, next_try_after=? WHERE id=?",
      (now_iso(), redact(err)[:200], until, sid), commit=True)
