# -*- coding: utf-8 -*-
"""ابزارهای زمان بر پایه‌ی آفست UTC + هش‌های پایدار."""
import hashlib
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from newsbot.config import DEFAULT_UTC_OFFSET, UTC

TZ_ZONES = [("tehran", 3.5), ("istanbul", 3), ("dubai", 4), ("kabul", 4.5), ("karachi", 5), ("delhi", 5.5), ("moscow", 3), ("berlin", 1), ("london", 0),
            ("beijing", 8), ("tokyo", 9), ("sydney", 10), ("newyork", -5), ("losangeles", -8), ("saopaulo", -3), ("utc", 0)]

def now_utc(): return datetime.now(UTC)
def now_iso(): return now_utc().isoformat()

def parse_dt(s):
    """تاریخ را از هر قالبی که فید/سایت‌مپ می‌دهد می‌فهمد: ISO، RFC-822 و قالب‌های رایج."""
    if not s: return None
    t = str(s).strip()
    try:
        d = datetime.fromisoformat(t.replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=UTC)
    except Exception: pass
    for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S %Z", "%a, %d %b %Y %H:%M %z",
                "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M",
                "%d %b %Y %H:%M:%S %z", "%d %b %Y %H:%M:%S", "%d.%m.%Y %H:%M", "%Y-%m-%d", "%Y/%m/%d", "%d-%m-%Y"):
        try:
            d = datetime.strptime(t, fmt)
            return d if d.tzinfo else d.replace(tzinfo=UTC)
        except Exception: continue
    try:
        d = parsedate_to_datetime(t)
        return d if d.tzinfo else d.replace(tzinfo=UTC)
    except Exception: return None

def tz_of(off):
    try: return timezone(timedelta(hours=float(off)))
    except Exception: return timezone(timedelta(hours=DEFAULT_UTC_OFFSET))

def off_label(off):
    try: off = float(off)
    except Exception: off = DEFAULT_UTC_OFFSET
    h = int(abs(off)); m = int(round((abs(off) - h) * 60))
    return f"UTC{'+' if off >= 0 else '-'}{h:02d}:{m:02d}"

def utc_clock(): return now_utc().strftime("%H:%M")
def local_clock(off): return datetime.now(tz_of(off)).strftime("%H:%M")
def today_str(off=DEFAULT_UTC_OFFSET): return datetime.now(tz_of(off)).strftime("%Y-%m-%d")

def fmt_date(d, off=DEFAULT_UTC_OFFSET, with_time=False):
    if not d: return "—"
    if isinstance(d, str): d = parse_dt(d)
    return d.astimezone(tz_of(off)).strftime("%Y-%m-%d %H:%M" if with_time else "%Y-%m-%d") if d else "—"

def ago_text(iso, lang="fa"):
    d = parse_dt(iso)
    if not d: return "—"
    s = int((now_utc() - d).total_seconds())
    if lang == "en": return f"{s}s" if s < 60 else f"{s//60}m" if s < 3600 else f"{s//3600}h" if s < 86400 else f"{s//86400}d"
    return f"{s} ثانیه" if s < 60 else f"{s//60} دقیقه" if s < 3600 else f"{s//3600} ساعت" if s < 86400 else f"{s//86400} روز"

def url_hash(u): return hashlib.sha1(u.strip().encode()).hexdigest()[:20]
