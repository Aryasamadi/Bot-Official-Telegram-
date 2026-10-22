# -*- coding: utf-8 -*-
"""لاگ‌گذاری امن: redact در گلوگاه، زمینه‌ی per-task، شمارنده‌های کارکرد.
FIX v4: توکن ربات (قالب 123456789:AA...) هم ماسک می‌شود.
FIX v5 (SEC-02/OPS-14): ماسک رازها روی RecordFactory سراسری اجرا می‌شود —
همهٔ فراخوان‌های log.* (نه فقط log_event) و متن استثناها/stack پیش از هر handler ماسک می‌شوند."""
import hashlib
import logging
import re
import traceback
from contextvars import ContextVar

logging.basicConfig(format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO)
# OBS-016: کتابخانه‌های پرحرف ممکن است URL/هدر حساس را در DEBUG چاپ کنند؛ سقفِ سخت.
for _noisy in ("httpx", "httpcore", "urllib3", "telegram", "telegram.ext", "apscheduler"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)
log = logging.getLogger("core")

_logctx = ContextVar("logctx", default=None)   # {op, channel, article, source, model, stage}

def redact(x):
    """رازها را از متن پاک می‌کند پیش از ذخیره یا نمایش.
    ترتیب مهم است:
      ۱) توکن ربات تلگرام (قالب «عدد:رشته») — پیش از الگوی کلیدها، وگرنه الگوی کلیدِ عمومی
         مقدار «عدد:رشته» را ناقص می‌خورد و پسوندِ توکن (مثل «:AAabcdef») بی‌ماسک می‌ماند.
      ۲) طرح‌های اعتبارسنجی (Bearer/Basic) — خودِ توکن پاک می‌شود نه کلمهٔ طرح.
      ۳) کلیدهای نام‌دار (token=، api_key=، ...) — با کوتاه‌ترین پوشش ممکن تا عددِ پیشوندی توکن گیر نکند.
    """
    t = str(x if x is not None else "")
    # ۱) توکن ربات تلگرام: هم قالب کامل (۳۰+ کاراکتر) و هم قالب کوتاهِ آزمایشی/نمونه.
    t = re.sub(r"\b\d{6,}:[A-Za-z0-9_-]{6,}", "•••", t)
    # ۲) Bearer/Basic: کل مقدار پس از طرح ماسک می‌شود (طرح دست‌نخورده می‌ماند)
    t = re.sub(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9\-._~+/=]{8,}", r"\1 •••", t)
    # ۳) کلیدهای نام‌دار: پس از «=/:» دقیقاً تا مرز جداسازنده ماسک می‌کند — بدون خوردنِ کاراکترِ بیش از حد.
    t = re.sub(r"(?i)\b(apikey|api[-_]?key|access[-_]?token|auth[-_]?token|secret|password|passwd|authorization|token|bot[-_]?token)(\s*[=:]\s*)([^\s&\"',;]{4,})", r"\1\2•••", t)
    # ۴) طرح‌های کلیدِ رایج
    t = re.sub(r"(?i)\bsk-[A-Za-z0-9_-]{12,}|\bAIza[0-9A-Za-z_-]{25,}", "•••", t)
    # ۵) شماره کارت
    t = re.sub(r"(?<!\d)(?:\d[ -]?){14,18}\d(?!\d)", "•••", t)
    # ۶) کلیدهای داخل query-string — *هر* جفتِ حساس، نه فقط آخری.
    # نسخهٔ قبلی الگوی «?key=…&token=…» را یک‌تکه می‌دید و فقط مقدارِ جفتِ *آخر* را ماسک می‌کرد؛
    # نتیجه: در «?key=AAA&token=BBB» مقدارِ AAA بی‌ماسک می‌ماند (نشت واقعی).
    return re.sub(r"(?i)([?&](?:key|api[-_]?key|apikey|token|access[-_]?token|auth[-_]?token|secret|password)=)[^&\s\"']+", r"\1•••", t)

# ── SEC-02/OPS-14: گلوگاه سراسری ماسک رازها ──
# redact قبلاً فقط داخل log_event اجرا می‌شد؛ هر log.warning/error مستقیم (و traceback هر
# استثنای log.exception) بی‌ماسک در handler/ژورنال می‌نشست. RecordFactory روی *همهٔ*
# رکوردها — پیش از رسیدن به هر handler (شامل caplog در تست) — اجرا می‌شود.
_orig_record_factory = logging.getLogRecordFactory()

def _redact_record(*args, **kwargs):
    rec = _orig_record_factory(*args, **kwargs)
    # OBS-015: مسیر ماسک نباید fail-open باشد. اگر هر مرحله خطا داد، متنِ امنِ جایگزین
    # می‌گذاریم — نه پیامِ خامِ حاوی راز.
    try:
        try:
            original = rec.getMessage()
        except Exception:
            original = str(rec.msg)
        try:
            rec.msg = redact(original)
        except Exception:
            rec.msg = "[REDACTION_FAILED]"
        rec.args = ()
        if rec.exc_info and rec.exc_info[0] is not None:
            # exc_text از قبل ست می‌شود تا formatter خودش traceback خام نسازد (exc_info حفظ می‌شود)
            try:
                rec.exc_text = redact("".join(traceback.format_exception(*rec.exc_info)))
            except Exception:
                rec.exc_text = "[REDACTION_FAILED]"
                rec.exc_info = None
        if rec.stack_info:
            try:
                rec.stack_info = redact(rec.stack_info)
            except Exception:
                rec.stack_info = "[REDACTION_FAILED]"
    except Exception:
        rec.msg = "[REDACTION_FAILED]"
        rec.args = ()
        rec.exc_text = "[REDACTION_FAILED]"
        rec.exc_info = None
        rec.stack_info = ""
    return rec

if not getattr(logging, "_newsbot_redact_factory", False):
    logging.setLogRecordFactory(_redact_record)
    logging._newsbot_redact_factory = True

def log_ctx(**kw):
    """فیلدهای زمینه را برای لاگ‌های همین تسک می‌چسباند (ContextVar ⇒ بین کانال‌ها قاطی نمی‌شود)."""
    d = dict(_logctx.get() or {}); d.update({k: v for k, v in kw.items() if v is not None}); _logctx.set(d)

_metrics = {"posts_ok": 0, "posts_fail": 0, "source_fail": 0, "model_fail": {}, "cycle_ms": 0, "cycle_n": 0, "queue": 0}

def bump(key, n=1, sub=None):
    """شمارندهٔ ارزان در حافظه؛ snapshot در پایان هر چرخه در settings ذخیره می‌شود."""
    if sub is None: _metrics[key] = _metrics.get(key, 0) + n
    else: _metrics[key][sub] = _metrics[key].get(sub, 0) + n

def metrics_text(lang="fa"):
    from newsbot.db import gget   # import داخلی: جلوگیری از وابستگی دایره‌ای
    d = gget("metrics") or _metrics
    fails = sorted((d.get("model_fail") or {}).items(), key=lambda x: -x[1])[:3]
    cyc = (d.get("cycle_ms", 0) / d["cycle_n"]) if d.get("cycle_n") else 0
    if lang == "en":
        return (f"📈 <b>Throughput</b> ✅{d.get('posts_ok', 0)} ❌{d.get('posts_fail', 0)} · 🌐 src-fail {d.get('source_fail', 0)}\n"
                f"🤖 model fails {', '.join(f'{k}:{v}' for k, v in fails) or '—'} · ⏱ cycle ~{cyc / 1000:.1f}s · 📝 queue {d.get('queue', 0)}")
    return (f"📈 <b>کارکرد</b> ✅{d.get('posts_ok', 0)} ❌{d.get('posts_fail', 0)} · 🌐 شکست منبع {d.get('source_fail', 0)}\n"
            f"🤖 شکست مدل {', '.join(f'{k}:{v}' for k, v in fails) or '—'} · ⏱ چرخه ~{cyc / 1000:.1f}s · 📝 صف {d.get('queue', 0)}")

def _actor_ref(admin_id):
    """SEC-011: شناسهٔ خامِ مدیر در لاگ نباید بنشیند؛ یک اثرانگشتِ کوتاه کافی است."""
    if admin_id is None:
        return "-"
    try:
        return "actor:" + hashlib.sha256(str(admin_id).encode("utf-8")).hexdigest()[:12]
    except Exception:
        return "actor:?"


def log_event(level, msg, admin_id=None, exc_info=False, error_class=None):
    """لاگِ ساختاریافته با زمینه؛ traceback اختیاری و کلاسِ خطا (OBS-004).

    ``exc_info=True`` را در بلوک ``except`` صدا بزنید تا stack trace (ماسک‌شده) ثبت شود.
    """
    d = _logctx.get() or {}
    ctx = " ".join(f"{k}={v}" for k, v in d.items())
    tail = f" error_class={error_class}" if error_class else ""
    prefix = f"[{_actor_ref(admin_id)}]{f' [{ctx}]' if ctx else ''} {msg}{tail}"
    try:
        text = redact(prefix)
    except Exception:
        text = "[REDACTION_FAILED]"
    fn = log.error if level == "ERROR" else log.warning if level == "WARN" else log.info
    try:
        fn(text, exc_info=exc_info)
    except Exception:
        log.error("[LOG_EVENT_FAILED]")
