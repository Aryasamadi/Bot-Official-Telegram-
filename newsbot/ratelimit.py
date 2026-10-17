# -*- coding: utf-8 -*-
"""محدودکننده‌ی نرخ درون‌حافظه + ماندگاری اختیاری کلیدهای حساس.
FIX v4: کلیدهای حساس (تست/پرداخت/توکن/دستور…) در دیتابیس هم نوشته می‌شوند تا
پس از restart هم برقرار بمانند و نتوان با ری‌استارت سقف‌ها را دور زد.
"""
import time
from newsbot.config import RATE_PERSIST_PREFIXES
from newsbot.logutils import log

_rl = {}
_win = {}      # SEC-03: شمارندهٔ پنجره‌ای (کلید → لیست زمان‌ها)؛ در حافظه، بدون ماندگاری DB
_PERSIST_MIN_GAP = 10.0   # OPS-21: حداقل فاصلهٔ نوشتن دوبارهٔ یک کلید در دیتابیس (کاهش فشار I/O)
_last_persist = {}        # کلید ← آخرین ts نوشته‌شده در DB (throttle جدای از منطق سقف‌ها)

def _rate_persistable(key): return any(key.startswith(p) for p in RATE_PERSIST_PREFIXES)

def _rate_persist(key, ts):
    if not _rate_persistable(key): return
    # OPS-21: کاربر پرتعامل نباید به ازای هر پیام/دستور یک INSERT بزند؛ حداکثر هر ۱۰ ثانیه یک نوشتن per-key.
    # Trade-off (مستند در SECURITY/ROADMAP): با crash، حداکثر ~۱۰ ثانیه از آخرین ts یک کلید از دست می‌رود
    # و سقف پس از ری‌استارت کمی بخشنده‌تر می‌شود — در برابر فشار نوشتن، معاوضه‌ی پذیرفته‌شده است.
    last = _last_persist.get(key)
    if last is not None and ts - last < _PERSIST_MIN_GAP: return
    try:
        from newsbot.db import q
        q("INSERT OR REPLACE INTO rate_limits(key, ts) VALUES(?,?)", (key, ts), commit=True)
        _last_persist[key] = ts
        if len(_last_persist) > 20000:
            for k in sorted(_last_persist, key=_last_persist.get)[:10000]: _last_persist.pop(k, None)
    except Exception:
        log.debug("rate persist failed key=%s", key, exc_info=True)

def _rate_load_persisted():
    """بارگذاری کلیدهای ماندگار در startup (بقا پس از ری‌استارت)."""
    try:
        from newsbot.db import q
        for r in q("SELECT key, ts FROM rate_limits"):
            if time.time() - float(r["ts"]) < 3600: _rl[r["key"]] = float(r["ts"])
    except Exception:
        log.debug("rate load failed", exc_info=True)

def _evict():
    """تخلیه بر اساس کهنه‌ترین «آخرین استفاده» (بازدرج جای کلید را تازه نمی‌کند ⇒ حذف از ابتدا)."""
    if len(_rl) > 20000:
        for k in sorted(_rl, key=_rl.get)[:10000]: _rl.pop(k, None)

def rate_ok(key, min_gap):
    """True اگر از آخرین رخداد این کلید حداقل min_gap ثانیه گذشته باشد (و زمان را به‌روز می‌کند)."""
    t = time.time(); last = _rl.get(key, 0)
    if t - last < min_gap: return False
    _rl[key] = t; _rate_persist(key, t); _evict()
    return True

def rate_free(key, min_gap):
    """فقط بررسی می‌کند و چیزی ثبت نمی‌کند (برای مواردی که محدودیت باید فقط بعد از موفقیت اعمال شود)."""
    return (time.time() - _rl.get(key, 0)) >= min_gap

def rate_mark(key):
    _rl[key] = time.time(); _rate_persist(key, _rl[key]); _evict()

def rate_clear(key):
    _rl.pop(key, None); _last_persist.pop(key, None)   # OPS-21: پاک‌شدن کلید، throttle آن را هم پاک می‌کند
    if _rate_persistable(key):
        try:
            from newsbot.db import q
            q("DELETE FROM rate_limits WHERE key=?", (key,), commit=True)
        except Exception: pass

def rate_left(key, min_gap): return max(0, int(min_gap - (time.time() - _rl.get(key, 0))))

def rate_window(key, limit, window, now=None):
    """SEC-03: حداکثر limit رخداد در بازهٔ window ثانیه؛ بیش از آن False (و رخداد ثبت نمی‌شود).
    در حافظه و per-key؛ برای محدودیت سراسری per-user (global_rate_ok)."""
    t = time.time() if now is None else now
    hits = [x for x in _win.get(key, ()) if t - x < window]
    if len(hits) >= limit:
        _win[key] = hits
        return False
    hits.append(t)
    _win[key] = hits
    if len(_win) > 20000:
        for k in [k for k, v in _win.items() if not v or t - max(v) > window]: _win.pop(k, None)
    return True

def global_rate_ok(uid):
    """محدودیت نرخ سراسری per-user: در پنجرهٔ GLOBAL_RATE_WINDOW حداکثر GLOBAL_RATE_MAX
    به‌روزرسانی از هر نوع (دستور/کال‌بک/پیام). کاربرِ super معاف است."""
    if uid is None: return True
    from newsbot import config
    if uid in getattr(config, "SUPER_ADMIN_IDS", ()): return True
    return rate_window(f"g:{uid}", config.GLOBAL_RATE_MAX, config.GLOBAL_RATE_WINDOW)

def rate_cleanup(now_w=None):
    """پاک‌سازی دوره‌ای کلیدهای بی‌استفاده (فراخوان از cleanup())."""
    now_w = time.time() if now_w is None else now_w
    for k in [k for k, v in list(_rl.items()) if now_w - v > 3600]: _rl.pop(k, None)
    for k in [k for k, v in list(_win.items()) if not v or now_w - max(v) > 3600]: _win.pop(k, None)
