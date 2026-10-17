# -*- coding: utf-8 -*-
"""ابزارهای زمان و هش‌های پایدار.

ARCH-10 (فاز ۷ · تصمیم ۱۱ «الف: zoneinfo ثابت»): مناطق زمانی با نام‌های IANA و ZoneInfo نگه‌داری
می‌شوند، نه آفست ثابت؛ بنابراین گذرهای DST (لندن/برلین/نیویورک/…) درست محاسبه می‌شوند و آفستِ
نمایش‌داده‌شده در هر لحظه از خود منطقه مشتق می‌شود.
ARCH-11 (فاز ۷): قرارداد tz در parse_dt صریح است — تاریخِ بی‌tz با منطقه‌ی خواسته‌شده (پیش‌فرض UTC)
برچسب می‌خورد تا «ساعت محلی سایت» با UTC اشتباه نشود؛ ago_text هرگز مقدار منفی نمی‌دهد.
"""
import hashlib
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo

from newsbot import config
from newsbot.config import DEFAULT_UTC_OFFSET, UTC

# جفت‌های (کلید نمایشی، نام IANA) — ترتیب مهم است: مهاجرتِ آفست‌های قدیمی به اولین تطابق می‌رسد
# (۳٫۵→تهران، ۳→استانبول، ۱→برلین، ۰→UTC).
TZ_ZONES = [("tehran", "Asia/Tehran"), ("istanbul", "Europe/Istanbul"), ("dubai", "Asia/Dubai"), ("kabul", "Asia/Kabul"),
            ("karachi", "Asia/Karachi"), ("delhi", "Asia/Kolkata"), ("moscow", "Europe/Moscow"), ("berlin", "Europe/Berlin"),
            ("london", "Europe/London"), ("beijing", "Asia/Shanghai"), ("tokyo", "Asia/Tokyo"), ("sydney", "Australia/Sydney"),
            ("newyork", "America/New_York"), ("losangeles", "America/Los_Angeles"), ("saopaulo", "America/Sao_Paulo"), ("utc", "UTC")]
_TZ_KEY = {iana: key for key, iana in TZ_ZONES}
_FALLBACK_IANA = "Asia/Tehran"

def now_utc(): return datetime.now(UTC)
def now_iso(): return now_utc().isoformat()

def zone_of(val):
    """tzinfo از «نام IANA»، رشته‌ی عددی («3.5») یا آفست عددی.
    ورودی نامعتبر ⇒ آفست پیش‌فرض پروژه (fail-safe، نه استثنا)."""
    if isinstance(val, str):
        t = val.strip()
        if t:
            try: return ZoneInfo(t)
            except Exception: pass
            try: return timezone(timedelta(hours=float(t)))
            except Exception: pass
    elif isinstance(val, (int, float)):
        try: return timezone(timedelta(hours=float(val)))
        except Exception: pass
    elif val is not None:
        try:
            if val.utcoffset(now_utc()) is not None: return val
        except Exception: pass
    return timezone(timedelta(hours=DEFAULT_UTC_OFFSET))

tz_of = zone_of   # سازگاری با نام قدیمی

def _sample_offsets(iana):
    try:
        z = ZoneInfo(iana)
        return {datetime(2026, 1, 15, tzinfo=UTC).astimezone(z).utcoffset(),
                datetime(2026, 7, 15, tzinfo=UTC).astimezone(z).utcoffset()}
    except Exception:
        return set()

_MIGRATE_CACHE = {}

def tz_name_for_offset(off):
    """نام IANA متناظر با آفست عددیِ ردیف‌های قدیمیِ تنظیمات (مهاجرت ARCH-10).
    آفست‌های زمستان/تابستان هر منطقه نمونه‌برداری می‌شود؛ اولین تطابق در TZ_ZONES برنده است."""
    try: target = timedelta(hours=float(off))
    except Exception: return _FALLBACK_IANA
    if target == timedelta(0): return "UTC"
    for key, iana in TZ_ZONES:
        if key not in _MIGRATE_CACHE: _MIGRATE_CACHE[key] = _sample_offsets(iana)
        if target in _MIGRATE_CACHE[key]: return iana
    return _FALLBACK_IANA

def tz_key_of(val):
    """کلید نمایشی منطقه (کلید TZ_NAMES) از نام IANA، آفست عددی یا رشته‌ی عددی."""
    if isinstance(val, str) and val.strip() in _TZ_KEY: return _TZ_KEY[val.strip()]
    return _TZ_KEY.get(tz_name_for_offset(val))

def offset_now(val):
    """آفست جاریِ منطقه (ساعت اعشاری) — از zoneinfo برای همین لحظه، پس DST-aware."""
    od = zone_of(val).utcoffset(now_utc())
    return round(od.total_seconds() / 3600, 3) if od is not None else DEFAULT_UTC_OFFSET

def _default_iana():
    name = (config.DEFAULT_TZ or "").strip()
    if name:
        try:
            ZoneInfo(name); return name
        except Exception: pass
    return tz_name_for_offset(DEFAULT_UTC_OFFSET)

DEFAULT_TZ = _default_iana()

def parse_dt(s, tz=None):
    """تاریخ را از هر قالبی که فید/سایت‌مپ می‌دهد می‌فهمد: ISO، RFC-822 و قالب‌های رایج.
    ARCH-11: تاریخِ بدون آفست با منطقه‌ی `tz` (نام IANA/آفست) برچسب می‌خورد؛ پیش‌فرض UTC است."""
    if not s: return None
    zone = zone_of(tz) if tz is not None and str(tz).strip() else UTC
    t = str(s).strip()
    try:
        d = datetime.fromisoformat(t.replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=zone)
    except Exception: pass
    for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S %Z", "%a, %d %b %Y %H:%M %z",
                "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M",
                "%d %b %Y %H:%M:%S %z", "%d %b %Y %H:%M:%S", "%d.%m.%Y %H:%M", "%Y-%m-%d", "%Y/%m/%d", "%d-%m-%Y"):
        try:
            d = datetime.strptime(t, fmt)
            return d if d.tzinfo else d.replace(tzinfo=zone)
        except Exception: continue
    try:
        d = parsedate_to_datetime(t)
        return d if d.tzinfo else d.replace(tzinfo=zone)
    except Exception: return None

def off_label(val):
    """برچسب زیبای آفست از عدد یا نام منطقه؛ برای نام، DST-aware است."""
    try: off = float(val)
    except Exception:
        od = zone_of(val).utcoffset(now_utc())
        off = od.total_seconds() / 3600 if od is not None else DEFAULT_UTC_OFFSET
    h = int(abs(off)); m = int(round((abs(off) - h) * 60))
    return f"UTC{'+' if off >= 0 else '-'}{h:02d}:{m:02d}"

def utc_clock(): return now_utc().strftime("%H:%M")
def local_clock(val): return datetime.now(zone_of(val)).strftime("%H:%M")
def today_str(val=DEFAULT_UTC_OFFSET): return datetime.now(zone_of(val)).strftime("%Y-%m-%d")

def fmt_date(d, off=DEFAULT_UTC_OFFSET, with_time=False):
    if not d: return "—"
    if isinstance(d, str): d = parse_dt(d)
    return d.astimezone(zone_of(off)).strftime("%Y-%m-%d %H:%M" if with_time else "%Y-%m-%d") if d else "—"

def ago_text(iso, lang="fa"):
    d = parse_dt(iso)
    if not d: return "—"
    s = max(0, int((now_utc() - d).total_seconds()))   # ARCH-11: تاریخ کمی در آینده ⇒ «منفی» نمایش داده نمی‌شود
    if lang == "en": return f"{s}s" if s < 60 else f"{s//60}m" if s < 3600 else f"{s//3600}h" if s < 86400 else f"{s//86400}d"
    return f"{s} ثانیه" if s < 60 else f"{s//60} دقیقه" if s < 3600 else f"{s//3600} ساعت" if s < 86400 else f"{s//86400} روز"

def url_hash(u): return hashlib.sha1(u.strip().encode()).hexdigest()[:20]
