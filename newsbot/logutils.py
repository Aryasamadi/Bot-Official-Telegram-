# -*- coding: utf-8 -*-
"""لاگ‌گذاری امن: redact در گلوگاه، زمینه‌ی per-task، شمارنده‌های کارکرد.
FIX v4: توکن ربات (قالب 123456789:AA...) هم ماسک می‌شود."""
import logging
import re
from contextvars import ContextVar

logging.basicConfig(format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("core")

_logctx = ContextVar("logctx", default=None)   # {op, channel, article, source, model, stage}

def redact(x):
    """رازها را از متن پاک می‌کند پیش از ذخیره یا نمایش.
    ترتیب مهم است: اول طرح‌های اعتبارسنجی (Bearer/Basic) تا خودِ توکن پاک شود نه کلمهٔ طرح."""
    t = str(x if x is not None else "")
    t = re.sub(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9-._~+/=]{8,}", r"\1 •••", t)
    t = re.sub(r"(?i)\b(apikey|api[-_]?key|access[-_]?token|auth[-_]?token|secret|password|passwd|authorization|token)([=:\s]+)[^\s&\"',;]{6,}", r"\1\2•••", t)
    t = re.sub(r"(?i)\bsk-[A-Za-z0-9_-]{12,}|\bAIza[0-9A-Za-z_-]{25,}", "•••", t)
    t = re.sub(r"\b\d{6,}:[A-Za-z0-9_-]{30,}", "•••", t)   # FIX v4: توکن ربات تلگرام
    t = re.sub(r"(?<!\d)(?:\d[ -]?){14,18}\d(?!\d)", "•••", t)   # شماره کارت
    return re.sub(r"(?i)([?](?:key|api_key|apikey|token|access_token)[&](?:key|api_key|apikey|token|access_token)=)[^&\s]+", r"\1•••", t)

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

def log_event(level, msg, admin_id=None):
    d = _logctx.get() or {}
    ctx = " ".join(f"{k}={v}" for k, v in d.items())
    (log.error if level == "ERROR" else log.warning if level == "WARN" else log.info)(redact(f"[{admin_id}]{f' [{ctx}]' if ctx else ''} {msg}"))
