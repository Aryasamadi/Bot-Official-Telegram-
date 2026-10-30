# -*- coding: utf-8 -*-
"""مدل‌های هوش مصنوعی — هر سرویسی با Base URL + کلید + نام مدل (نوع خودکار تشخیص داده می‌شود).
FIX v4: بودجه‌ی هر مقاله (تعداد فراخوانی + زمان) با ContextVar ایزوله است؛
دو چرخه‌ی هم‌زمان دیگر بودجه‌ی یکدیگر را مصرف نمی‌کنند.
"""
import asyncio, html, json, re, time
from contextvars import ContextVar
from datetime import timedelta
from urllib.parse import urlparse
import httpx
from newsbot import config
from newsbot import net_http as _net_http
from newsbot.db import q, gget, gset, arun, _safe_fields
from newsbot.secret_store import decrypt as _sec_decrypt, encrypt as _sec_encrypt   # SEC-09 (فاز ۱۲)
from newsbot.logutils import log, log_event, bump, redact
from newsbot.timeutils import now_utc, now_iso, parse_dt
from newsbot.concurrency import AI_SEM, model_lock, model_busy
from newsbot.notify import notify_super
from newsbot.core_users import user_lang, ai_claim
from newsbot.text_format import strip_tags, INJ_RESIDUE
from newsbot.net_http import FetchError, retry_after_seconds

_ai_events = ContextVar("ai_events", default=None)

# ── P0-B: خطاهای «نهاییِ» AI ────────────────────────────────────────────────────
# وقتی generate() همهٔ تلاش‌های مجازِ مدل‌ها را مصرف کرد و خطا برگرداند، چرخه نباید
# نامزدِ بعدی را به همان پروایدرِ ناسالم بفرستد (هزینهٔ تکراری بدونِ فایده).
# خطاهای عادیِ محتوا (امتیاز/تبلیغ/اعتبارسنجیِ مقاله) کدِ خطا ندارند و اصلاً
# در این فهرست نیستند ⇒ آن‌ها فقط همان مقاله را رد می‌کنند.
AI_FATAL_CODES = frozenset({
    "ai_timeout",   # پروایدر پاسخ نداد / ۵۰۴
    "ai_busy",      # ۴۲۹/۵xx یا همهٔ مدل‌ها در backoff
    "ai_none",      # هیچ مدلِ قابل‌استفاده‌ای نبود
    "ai_all_failed",# همهٔ مدل‌ها شکست خوردند
    "ai_invalid",   # خروجیِ نامعتبر
    "ai_tokens",    # بودجهٔ توکنِ مدل تمام شد
    "ai_empty",     # پاسخِ خالی
    "ai_auth",      # احراز هویت/پیکربندی
    "ai_budget",    # سقفِ فراخوانیِ مقاله پر شد
    "ai_limit",     # سقفِ روزانهٔ مصرف
    "ai_error",     # خطای نامشخصِ سرویس
})


def is_ai_fatal(code):
    """آیا این کد خطا یعنی «تولیدِ AI برای این چرخه تمام شد»؟"""
    return str(code or "") in AI_FATAL_CODES


# ── P0-D: قرنطینهٔ مدلِ خراب در همان چرخه ────────────────────────────────────────
# یک مدل که پاسخِ خالی/نامعتبر می‌دهد یا خطای سمتِ سرویس می‌گیرد نباید برای
# مقاله‌های بعدیِ *همان چرخه* دوباره صدا زده شود. این قرنطینه فقط درونِ چرخه است
# (ContextVar) ⇒ مدل برای مدتِ طولانی از کار نمی‌افتد (مسیرِ fallback پیش‌بینی‌پذیر
# و قابل‌تست می‌ماند) و «رد شدنِ عادیِ یک مقاله» اصلاً قرنطینه نمی‌سازد.
_AI_QUARANTINE = ContextVar("ai_quarantine", default=None)


def cycle_quarantine_reset():
    """قرنطینهٔ چرخه را خالی می‌کند (شروعِ هر چرخه — ایزوله per-task)."""
    d = {}
    _AI_QUARANTINE.set(d)
    return d


def cycle_quarantine():
    d = _AI_QUARANTINE.get()
    if d is None:
        d = cycle_quarantine_reset()
    return d


def cycle_quarantine_add(mid, code, reason=""):
    """مدل را برای ادامهٔ همین چرخه کنار می‌گذارد (بدونِ خاموشیِ طولانی)."""
    try:
        cycle_quarantine()[int(mid)] = {"code": str(code or ""), "reason": str(reason or "")[:80]}
    except Exception:
        pass


def cycle_quarantine_has(mid):
    try:
        return int(mid) in cycle_quarantine()
    except Exception:
        return False


def cycle_quarantine_code(mid):
    try:
        return (cycle_quarantine().get(int(mid)) or {}).get("code") or ""
    except Exception:
        return ""


# ── P0/OBS: آمارِ فراخوانیِ AI (منطقی و واقعی) ──────────────────────────────────
_AI_STATS = ContextVar("ai_stats", default=None)


def ai_stats_reset():
    """آمارِ این چرخه را صفر می‌کند: فراخوانی‌های منطقی، مقاله‌ها و خطاها."""
    st = {"logical": 0, "articles": 0, "models": {}, "errors": {}}
    _AI_STATS.set(st)
    _net_http.post_state_reset()
    return st


def ai_stats():
    st = _AI_STATS.get()
    if st is None:
        st = ai_stats_reset()
    return st


def ai_posts_total():
    """تعدادِ POSTهای *واقعیِ* ارائه‌دهنده (از محلِ ارسالِ درخواست)."""
    try:
        return int(_net_http.post_state().get("posts") or 0)
    except Exception:
        return 0

def hostname(u):
    try: return urlparse(u).netloc.replace("www.", "") or u
    except Exception: return str(u)

def detect_kind(base_url):
    b = (base_url or "").lower()
    if "anthropic.com" in b: return "anthropic"
    if "generativelanguage.googleapis.com" in b: return "gemini"
    return "openai"     # OpenAI-compatible: OpenAI, OpenRouter, Groq, DeepSeek, Together, Mistral, xAI, Ollama, LM Studio, …

def list_models(active_only=False, owner_id=None):
    """owner_id=None ⇒ مدل‌های عمومیِ مدیر کلان · owner_id=<uid> ⇒ مدل‌های شخصیِ همان مدیر"""
    sql = "SELECT * FROM ai_models WHERE " + ("owner_id=?" if owner_id else "owner_id IS NULL")
    if active_only: sql += " AND active=1"
    return q(sql + " ORDER BY priority, id", (owner_id,) if owner_id else ())

def get_model(mid): return q("SELECT * FROM ai_models WHERE id=?", (mid,), one=True)

def add_model(base_url, api_key, model, name="", priority=10, temperature=0.5, max_tokens=2500, owner_id=None):
    base_url = str(base_url).strip().rstrip("/"); model = str(model).strip(); kind = detect_kind(base_url)
    name = (name or f"{hostname(base_url) or kind}").strip()[:40]
    return q("INSERT INTO ai_models(name,kind,base_url,api_key,model,priority,temperature,max_tokens,owner_id) VALUES(?,?,?,?,?,?,?,?,?)", (name, kind, base_url, _sec_encrypt(api_key.strip()), model, priority, temperature, max_tokens, owner_id), commit=True)

def update_model(mid, **f):
    if "base_url" in f: f["base_url"] = str(f["base_url"]).strip().rstrip("/"); f["kind"] = detect_kind(f["base_url"])
    if "api_key" in f: f["api_key"] = _sec_encrypt(str(f["api_key"]).strip())   # SEC-09: کلید همیشه رمزنگاری‌شده ذخیره می‌شود
    _safe_fields("ai_models", f)
    q("UPDATE ai_models SET " + ", ".join(f"{k}=?" for k in f) + " WHERE id=?", (*f.values(), mid), commit=True)

def delete_model(mid): q("DELETE FROM ai_models WHERE id=?", (mid,), commit=True)

def _err_text(r):
    try:
        j = r.json(); e = j.get("error"); msg = (e.get("message") if isinstance(e, dict) else e) or j.get("message") or j.get("detail") or r.text
    except Exception: msg = r.text
    # متنِ خامِ صفحهٔ خطا (HTMLِ لبه) هم فشرده و خوانا می‌شود؛ رازها همچنان پاک می‌شوند
    clean = re.sub(r"<[^>]+>", " ", str(msg))
    clean = re.sub(r"\s+", " ", clean).strip()
    return redact(f"HTTP {r.status_code}: {clean[:160]}")   # منبعِ خطا پاک‌سازی می‌شود، نه مصرف‌کننده‌هایش

class AIResponseError(RuntimeError):
    pass

class AIContentInvalid(AIResponseError):
    """خروجیِ provider ناقص/بدشکل است ⇒ خطای محتوا (پروایدر سالم است، نباید down شود)."""

class AIOutputTruncated(AIContentInvalid):
    """پاسخِ provider ناقص است (finish_reason=length) ⇒ محتوای ناقص هرگز منتشر نمی‌شود.

    ``empty=True`` یعنی مدل کلِ بودجهٔ توکنش را صرفِ «تفکر» کرده و هیچ محتوایی تولید
    نکرده (در تولیدِ واقعی همین حالت برای مدل‌های استدلالی دیده شد)."""

    def __init__(self, message, empty=False):
        super().__init__(message); self.empty = bool(empty)

class AIProviderError(AIResponseError):
    """خطای HTTP از پروایدر؛ ``transient`` یعنی گذرا (۴۲۹/۵xx/timeout) و باید دوباره امتحان شود."""

    def __init__(self, message, status=None, transient=False):
        super().__init__(message); self.status = status; self.transient = bool(transient)

# وضعیت‌هایی که گذرا هستند ⇒ تلاشِ دوباره (با تأخیر) مجاز و لازم است.
# ۵۲۰–۵۲۴ هم‌خانوادهٔ Cloudflare هستند (مبدأ پاسخ نداد / تایم‌اوت لبه) ⇒ گذرا.
_TRANSIENT_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504, 520, 521, 522, 523, 524})

def _body_is_json(r):
    try: r.json(); return True
    except Exception: return False

def _is_transient_response(r):
    """آیا این پاسخِ HTTP گذراست؟

    پاسخِ ساختاریافتهٔ provider (JSON با ‎error‎) دائمی است (کلید/مدل/درخواست غلط)؛
    ولی صفحه‌ی خطای لبه/پروکسی (متن ساده مثل «404 page not found») گذراست — در تولیدِ
    واقعی همین حالت از لبهٔ NVIDIA دیده شد (همان کلید و همان مدل، چند دقیقه بعد ۲۰۰ دادند)."""
    st = int(getattr(r, "status_code", 0) or 0)
    if st in _TRANSIENT_STATUS: return True
    if st in (400, 404, 405, 422) and not _body_is_json(r): return True
    return False

def _error_is_transient(text):
    """آیا پیامِ خطای provider مربوط به سمتِ سرور است (گذرا) یا سمتِ درخواست (دائمی)؟

    پیامِ «This model is currently unavailable. Please try again later.» در تولیدِ واقعی
    از vyceai.com دیده شد ⇒ گذراست (وگرنه مدل ساعت‌ها خاموش می‌ماند)."""
    t = str(text or "").lower()
    return any(k in t for k in ("timeout", "timed out", "overload", "upstream", "temporar",
                                "too many requests", "rate limit", "service unavailable",
                                "currently unavailable", "model unavailable", "try again later",
                                "internal error", "bad gateway", "try again"))

def _error_message(err):
    if isinstance(err, dict):
        return err.get("message") or err.get("detail") or err.get("type") or json.dumps(err, ensure_ascii=False)[:200]
    return str(err or "")[:200]

def _body_is_unusable(r):
    """پاسخِ ۲۰۰ ولی بدونِ محتوا (خطای میانِ جریان یا جریانِ خالی) ⇒ گذراست.

    در تولیدِ واقعی دیده شد: پروایدر جریان را با خطای بالادست بست و بدنه ۲۰۰ بود ولی
    محتوایی نداشت ⇒ بدونِ این بررسی، مقاله با «خروجی نامعتبر» رد می‌شد."""
    try: j = r.json()
    except Exception: return False
    if not isinstance(j, dict): return False
    if j.get("error"): return _error_is_transient(_error_message(j.get("error")))
    chs = j.get("choices")
    if isinstance(chs, list) and chs and isinstance(chs[0], dict):
        msg = chs[0].get("message") or chs[0].get("delta") or {}
        if isinstance(msg, dict) and msg.get("content") is None and not msg.get("refusal"):
            return True
    return False

def _provider_json(r):
    if r.status_code >= 400: raise AIProviderError(_err_text(r), status=r.status_code, transient=_is_transient_response(r))
    try: j = r.json()
    except ValueError as e: raise AIContentInvalid("malformed provider JSON") from e
    if not isinstance(j, dict): raise AIContentInvalid("malformed provider envelope")
    if j.get("error"):
        msg = _error_message(j.get("error"))
        raise AIProviderError(f"provider error: {msg}", status=r.status_code, transient=_error_is_transient(msg))
    return j

def _ai_text(value):
    if not isinstance(value, str): raise AIContentInvalid("invalid response text")
    value = value.strip()
    if not value: raise AIContentInvalid("empty response")
    return value

def _openai_content(j):
    choices = j.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise AIContentInvalid("malformed provider choices")
    ch = choices[0]; msg = ch.get("message") or ch.get("delta") or {}
    if not isinstance(msg, dict): raise AIContentInvalid("malformed provider message")
    if msg.get("refusal"): raise AIContentInvalid("provider refused response")
    c = msg.get("content") or ch.get("text")
    if isinstance(c, list):
        if any(not isinstance(p, dict) or not isinstance(p.get("text"), str) for p in c):
            raise AIContentInvalid("malformed provider content")
        c = " ".join(p["text"] for p in c)
    if str(ch.get("finish_reason") or "") == "length":
        # پاسخِ بریده‌شده؛ اگر محتوایی تولید نشده ⇒ مدل کلِ بودجهٔ توکنش را صرفِ استدلال کرده
        if not bool(str(c or "").strip()):
            raise AIOutputTruncated("model exhausted its token budget without producing output", empty=True)
        raise AIOutputTruncated("truncated provider response")
    if c is None:
        # جریانِ بدونِ محتوا (قطعِ بالادست یا پاسخِ خالی) ⇒ خطای گذرا تا یک‌بار دوباره
        # امتحان شود؛ «refusal» بالا دائمی ماند ✓
        raise AIProviderError("provider returned an empty response", transient=True)
    return _ai_text(c)

async def _post(url, **kw): return await http_post(url, **kw)

async def http_post(url, **kw):
    """PH-02/SEC-001 · NET-001 · NET-002: درخواست به پروایدرِ AI از مسیرِ محافظت‌شده می‌رود.

    پیش‌تر این تنها مسیرِ خروجیِ برنامه بود که هیچ اعتبارسنجیِ میزبان نداشت و
    ``follow_redirects=True`` داشت ⇒ یک ``base_url`` دلخواه (که هر کاربرِ دارای نقشِ
    admin می‌تواند ثبت کند) می‌توانست به میزبانِ داخلی/متادیتای سرور برسد و کلیدِ API هم
    همراهِ redirect منتقل می‌شد.

    حالا: هر مقصد با ``egress_url_strict`` اعتبارسنجی می‌شود (میزبانِ خصوصی فقط اگر در
    فهرستِ سفیدِ ``ALLOW_PRIVATE_MODEL_HOSTS`` باشد) و هدرهایِ اعتبار روی تغییرِ میزبان
    حذف می‌شوند."""
    from newsbot.net_http import fetch_post
    # سقفِ خواندن هم با بودجهٔ پروایدر یکی است (پیش‌تر ۱۲۰ ثانیهٔ ثابت بود و پیش از
    # سقفِ کل می‌پرید ⇒ خطای ۵۰۴ِ کاذب برای مدلِ سالمِ ولی کند)
    _t = max(30.0, _provider_timeout())
    return await fetch_post(url, timeout=httpx.Timeout(_t, connect=15), total_timeout=_provider_timeout(), **kw)

def _budget_left():
    """باقیماندهٔ بودجهٔ زمانیِ همین مقاله (ثانیه) یا None اگر بودجه‌ای فعال نبود."""
    b = config.AI_BUDGET.get()
    if not b: return None
    return config.AI_ARTICLE_TIME_BUDGET - (time.monotonic() - b["t0"])

def budget_calls_left():
    """تعدادِ فراخوانی‌های باقیمانده در بودجهٔ همین مقاله (None ⇒ بودجه‌ای فعال نیست).

    P0-C: این عدد *پیش* از هر فراخوانیِ خلاصه‌سازی/chunk بررسی می‌شود ⇒ متنِ بلند
    قطع می‌شود به‌جای آنکه ده‌ها درخواستِ ترتیبی ساخته شود."""
    b = config.AI_BUDGET.get()
    if not b: return None
    return max(0, int(config.AI_MAX_CALLS_PER_ARTICLE) - int(b.get("calls") or 0))

# ── P0-E: deadline اختصاصیِ probe (کوتاه) ───────────────────────────────────────
_PROBE_DEADLINE = ContextVar("ai_probe_deadline", default=None)

def _provider_timeout():
    """سقفِ زمانیِ یک درخواستِ پروایدر — هرگز بیشتر از بودجهٔ باقیماندهٔ مقاله."""
    t = float(config.AI_PROVIDER_TIMEOUT_SEC)
    left = _budget_left()
    if left is not None: t = min(t, max(5.0, float(left)))
    d = _PROBE_DEADLINE.get()
    if d is not None:
        rem = d - time.monotonic()
        if rem > 0: t = min(t, max(2.0, rem))
    return t

def _retry_delay(base, headers=None):
    """تأخیرِ تلاشِ دوباره: Retry-Afterِ پروایدر (اگر باشد) وگرنه نمایی با سقف.

    سقفِ تأخیر کوتاه است (‎AI_RETRY_MAX_DELAY_SEC‎): یک ‎Retry-After‎ِ ۶۰ثانیه‌ای در
    بودجهٔ ۲۴۰ثانیه‌ایِ مقاله جا نمی‌شود و کلِ فرصتِ تولید را می‌بلعند."""
    ra = retry_after_seconds((headers or {}).get("retry-after")) if headers else None
    d = ra if ra else base
    return max(0.2, min(float(d), float(config.AI_RETRY_MAX_DELAY_SEC), float(config.RETRY_AFTER_MAX)))

async def _post_retry(url, skip_status=(), **kw):
    """درخواستِ پروایدر با تلاشِ محدود برای خطاهای گذرا.

    پیش‌تر یک خطای گذرا (۴۲۹/۵xx/timeout) بلافاصله کلِ فراخوانی را می‌کشت و حتی
    ``FetchError`` (که ``RuntimeError`` است) از لایهٔ AI بیرون می‌پرید و کلِ چرخه را
    می‌کشت. حالا: خطای گذرا ⇒ حداکثر ``AI_TRANSIENT_RETRIES`` تلاشِ دوباره با تأخیرِ
    مناسب (و احترام به Retry-After)؛ خطای دائمی (۴۰۱/۴۰۳/کلیدِ غلط) ⇒ بدونِ تکرار.
    ``skip_status``: وضعیت‌هایی که باید بی‌درنگ برگردند (مثلاً ۴۰۴ وقتی مسیرِ جایگزین داریم)."""
    attempts = max(1, int(config.AI_TRANSIENT_RETRIES) + 1)
    base = max(0.2, float(config.AI_RETRY_BASE_SEC))
    skip = {int(s) for s in (skip_status or ())}
    last = None
    for i in range(attempts):
        try:
            r = await _post(url, **kw)
        except FetchError as e:
            if e.status != 504: raise          # ۴۰۳ِ دروازهٔ خروج/۳۱۰ ⇒ دائمی
            last = e
        except (httpx.TimeoutException, httpx.NetworkError, asyncio.TimeoutError) as e:
            last = e
        except Exception as e:
            # خطای غیرمنتظرهٔ لایهٔ انتقال هم نباید از AI بیرون بپرد (پیش‌تر کلِ چرخه را می‌کشت)
            last = AIProviderError(f"provider transport failure ({type(e).__name__})", transient=True)
            last.__cause__ = e
        else:
            transient = _is_transient_response(r) or _body_is_unusable(r)
            if i >= attempts - 1 or int(r.status_code) in skip or not transient: return r
            left = _budget_left()
            if left is not None and left <= 5: return r     # بودجهٔ مقاله تمام ⇒ ادامه فایده ندارد
            wait = _retry_delay(base, r.headers)
            if left is not None and wait >= left: return r  # تأخیر جایی در بودجهٔ مقاله ندارد
            log.warning("ai provider transient HTTP %s — retry %d/%d in %.1fs", r.status_code, i + 1, attempts - 1, wait)
            await asyncio.sleep(wait); base = min(base * 2, float(config.RETRY_AFTER_MAX)); continue
        if i >= attempts - 1: raise last
        left = _budget_left()
        if left is not None and left <= 5: raise last
        wait = _retry_delay(base)
        if left is not None and wait >= left: raise last
        log.warning("ai provider transport error (%s: %s) — retry %d/%d in %.1fs",
                    type(last).__name__, redact(str(last))[:120], i + 1, attempts - 1, wait)
        await asyncio.sleep(wait); base = min(base * 2, float(config.RETRY_AFTER_MAX))
    raise last if last else AIProviderError("ai provider request failed")

def _usage_dict(u, ptkey, ctkey):
    """استخراج مصرف توکن از بدنه‌ی provider (PLB-03)؛ نامعتبر/صفر ⇒ None."""
    if not isinstance(u, dict): return None
    try:
        pt = int(u.get(ptkey) or 0); ct = int(u.get(ctkey) or 0)
    except Exception: return None
    return {"pt": pt, "ct": ct} if (pt or ct) else None

def _record_usage(mid, usage):
    """PLB-03 (فاز ۷): ثبت متریک مصرف توکن در جدول ai_usage (روز UTC). خطای ثبت هرگز مسیر تولید را نمی‌شکند."""
    if not usage: return
    try:
        q("INSERT INTO ai_usage(day,model_id,calls,ptok,ctok) VALUES(?,?,1,?,?) ON CONFLICT(day,model_id) DO UPDATE SET calls=calls+1, ptok=ptok+excluded.ptok, ctok=ctok+excluded.ctok",
          (now_utc().strftime("%Y-%m-%d"), mid, int(usage.get("pt") or 0), int(usage.get("ct") or 0)), commit=True)
    except Exception: log.warning("ai_usage record failed", exc_info=True)

def tokens_today():
    """جمع مصرف امروز (UTC) — تعداد فراخوانی و توکن‌های ورودی/خروجی (نمایش در گزارش مالک)."""
    try:
        r = q("SELECT COALESCE(SUM(calls),0) c, COALESCE(SUM(ptok),0) p, COALESCE(SUM(ctok),0) t FROM ai_usage WHERE day=?", (now_utc().strftime("%Y-%m-%d"),), one=True)
        return {"calls": int(r["c"] or 0), "prompt": int(r["p"] or 0), "completion": int(r["t"] or 0)}
    except Exception: return {"calls": 0, "prompt": 0, "completion": 0}

def _mentions_stream_options(r):
    """آیا خطای provider مشخصاً از پارامترِ «stream_options» شکایت کرده است؟

    تفکیک لازم است: اگر provider فقط پارامترِ مصرف را رد کند، جریان (stream) را نگه
    می‌داریم؛ اگر خودِ جریان رد شده، به حالتِ غیرجریانی می‌رویم."""
    try: t = (getattr(r, "text", "") or "").lower()
    except Exception: t = ""
    return "stream_options" in t or "stream options" in t


async def _call_model(m, system, user):
    """فراخوانیِ واقعیِ پروایدر.

    شمارندهٔ «POSTهای واقعی» با شناسهٔ همین مدل علامت‌گذاری می‌شود ⇒ در لاگ می‌توان
    دید هر مدل چند POST واقعی (با retry و fallback) ساخته است."""
    tok = _net_http.post_model_set(m["id"])
    try:
        return await _call_model_impl(m, system, user)
    finally:
        _net_http.post_model_reset(tok)

async def _call_model_impl(m, system, user):
    kind = m["kind"] or detect_kind(m["base_url"]); base = (m["base_url"] or "").rstrip("/"); key = _sec_decrypt(m["api_key"] or ""); model = m["model"]; temp = 0.5 if m["temperature"] is None else float(m["temperature"]); mx = int(m["max_tokens"] or 2500)
    if kind == "anthropic":
        url = base + ("/messages" if base.endswith("/v1") else "/v1/messages")
        r = await _post_retry(url, headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"}, json={"model": model, "max_tokens": mx, "temperature": temp, "system": system, "messages": [{"role": "user", "content": user}]})
        j = _provider_json(r); blocks = j.get("content")
        if not isinstance(blocks, list) or any(not isinstance(b, dict) for b in blocks): raise AIContentInvalid("malformed provider content")
        if str(j.get("stop_reason") or "") == "max_tokens": raise AIOutputTruncated("truncated provider response")
        parts = [b.get("text") for b in blocks if b.get("type") == "text"]
        if any(not isinstance(p, str) for p in parts): raise AIContentInvalid("invalid response text")
        return _ai_text(" ".join(parts)), _usage_dict(j.get("usage"), "input_tokens", "output_tokens")
    if kind == "gemini":
        model = model.replace("models/", ""); root = base if re.search(r"/v1(beta)?$", base) else base + "/v1beta"
        # SEC-011/OBS-016: کلید در هدر می‌رود، نه query string (URL در لاگ/پروکسی ثبت می‌شود).
        r = await _post_retry(f"{root}/models/{model}:generateContent", headers={"x-goog-api-key": key}, json={"system_instruction": {"parts": [{"text": system}]}, "contents": [{"role": "user", "parts": [{"text": user}]}], "generationConfig": {"temperature": temp, "maxOutputTokens": mx}})
        j = _provider_json(r); cands = j.get("candidates")
        if not isinstance(cands, list) or not cands or not isinstance(cands[0], dict): raise AIContentInvalid("malformed provider candidates")
        if str(cands[0].get("finishReason") or "") == "MAX_TOKENS": raise AIOutputTruncated("truncated provider response")
        content = cands[0].get("content")
        if not isinstance(content, dict): raise AIContentInvalid("provider blocked response")
        parts = content.get("parts")
        if not isinstance(parts, list) or any(not isinstance(p, dict) or not isinstance(p.get("text"), str) for p in parts): raise AIContentInvalid("malformed provider parts")
        return _ai_text(" ".join(p["text"] for p in parts)), _usage_dict(j.get("usageMetadata"), "promptTokenCount", "candidatesTokenCount")
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    if "openrouter" in base: headers.update({"HTTP-Referer": "https://t.me", "X-Title": "NewsBot"})
    body = {"model": model, "temperature": temp, "max_tokens": mx, "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
    url = base if base.endswith("/chat/completions") else base + "/chat/completions"
    # اگر base_url نسخه ندارد، مسیرِ ‎/v1‎ هم محتمل است ⇒ ۴۰۴ دوباره تکرار نمی‌شود
    fallback = not re.search(r"/v\d", base)
    # NET-004: پاسخِ «جریانی» (stream) ⇒ اولین بایت زود می‌رسد. لبه‌هایی مثل Cloudflare اگر
    # شروعِ پاسخ در ۱۰۰ ثانیه نرسد خطای ۵۲۴ می‌دهند و پاسخِ کاملِ مدلِ کند هدر می‌رود
    # (اندازه‌گیری: vyceai.com بدون stream ⇒ ۵۲۴ بعد از ۱۲۵ ثانیه؛ با stream ⇒ پاسخِ کامل
    # در ۱۱۰ ثانیه با JSONِ درست). اگر پروایدر جریان را نپذیرفت ⇒ یک‌بار بدون stream.
    sbody = dict(body); sbody["stream"] = True
    # مصرفِ توکن در پاسخِ جریانی فقط وقتی برمی‌گردد که provider این پارامتر را بپذیرد.
    # بدونِ آن، هر فراخوانیِ جریانی مصرفش *ثبت نمی‌شد* ⇒ «توکنِ ۲۴ساعته» و حسابداریِ
    # هزینه از زمانِ فعال‌شدنِ stream بی‌صدا خالی ماندند.
    sbody["stream_options"] = {"include_usage": True}
    skip4 = (400, 405, 415, 422)          # خطای درخواست ⇒ تکرارِ جریانی فایده ندارد
    skip = tuple(sorted(set(skip4) | ({404} if fallback else set())))
    r = await _post_retry(url, skip_status=skip, headers=headers, json=sbody)
    if r.status_code == 404 and fallback:
        r = await _post_retry(base + "/v1/chat/completions", skip_status=skip, headers=headers, json=sbody)
    if r.status_code in skip4:
        if _mentions_stream_options(r):
            # پروایدر فقط پارامترِ مصرف را رد کرد ⇒ جریان را نگه می‌داریم
            # (جریان تنها وقتی رها می‌شود که خودِ stream هم رد شده باشد).
            sbody2 = dict(body); sbody2["stream"] = True
            log.warning("ai provider rejected stream_options — retrying without it")
            r = await _post_retry(url, skip_status=skip, headers=headers, json=sbody2)
            if r.status_code == 404 and fallback:
                r = await _post_retry(base + "/v1/chat/completions", skip_status=skip, headers=headers, json=sbody2)
    if r.status_code in skip4:
        log.warning("ai provider rejected streaming — retrying without stream")
        r = await _post_retry(url, skip_status=skip, headers=headers, json=body)
        if r.status_code == 404 and fallback:
            r = await _post_retry(base + "/v1/chat/completions", skip_status=skip, headers=headers, json=body)
    j = _provider_json(r)
    return _openai_content(j), _usage_dict(j.get("usage"), "prompt_tokens", "completion_tokens")

def _is_transient_error(e):
    """خطای گذرا (۴۲۹/۵xx/timeout/قطعِ شبکه) در برابرِ دائمی (کلیدِ غلط/دسترسی/درخواستِ نامعتبر)."""
    if isinstance(e, AIContentInvalid): return False          # خطای محتوا ⇒ پروایدر سالم است
    if isinstance(e, AIProviderError): return e.transient
    if isinstance(e, FetchError): return int(e.status) in (408, 425, 429, 500, 502, 503, 504)
    if isinstance(e, (httpx.TimeoutException, httpx.NetworkError, asyncio.TimeoutError)): return True
    return False

async def _mark_fail(m, err):
    cur = get_model(m["id"]) or m   # اسنپ‌شاتِ ورودی ممکن است کهنه باشد؛ شمارنده از سطر فعلی خوانده می‌شود
    fc = (cur["fail_count"] or 0) + 1
    transient = _is_transient_error(err)
    # خطای گذرا ⇒ خاموشیِ کوتاه (پیش‌تر ۵·۲^fc دقیقه ⇒ ۲۰ دقیقه برای خطای دوم؛ با یک
    # مدلِ فعال یعنی ۲۰ دقیقه توقفِ کاملِ تولیدِ محتوا). خطای دائمی ⇒ خاموشیِ طولانی.
    mins = (min(config.MODEL_TRANSIENT_BACKOFF_MIN * fc, config.MODEL_TRANSIENT_BACKOFF_MAX_MIN)
            if transient else min(5 * (2 ** min(fc, 6)), config.MODEL_BACKOFF_MAX_MIN))
    ntry = (now_utc() + timedelta(minutes=mins)).isoformat()
    update_model(m["id"], fail_count=fc, last_error=redact(err)[:300], last_fail=now_iso(), next_try_after=ntry,
                 status="down" if fc >= 2 else cur["status"])
    if fc >= 2 and cur["status"] != "down":
        if transient:
            log_event("WARN", f"مدل «{m['name']}» با خطای موقت از دسترس خارج شد ({mins} دقیقه): {redact(err)}")
            await notify_super(f"🟡 مدل <b>{html.escape(m['name'])}</b> (<code>{html.escape(m['model'])}</code>) با خطای موقت از دسترس خارج شد؛ "
                               f"تا <code>{mins}</code> دقیقهٔ دیگر دوباره تلاش می‌شود.\n<code>{html.escape(redact(err)[:200])}</code>")
        else:
            log_event("ERROR", f"مدل «{m['name']}» از کار افتاد: {redact(err)}")
            await notify_super(f"🔴 مدل <b>{html.escape(m['name'])}</b> (<code>{html.escape(m['model'])}</code>) از کار افتاد.\n<code>{html.escape(redact(err)[:200])}</code>")

async def _mark_ok(m):
    cur = get_model(m["id"]) or m   # ok_count/fail_count از سطر فعلی، نه از اسنپ‌شات احتمالاً کهنه
    update_model(m["id"], fail_count=0, status="ok", last_ok=now_iso(), ok_count=(cur["ok_count"] or 0) + 1, next_try_after="")
    if cur["status"] == "down": log_event("INFO", f"مدل «{m['name']}» دوباره فعال شد."); await notify_super(f"🟢 مدل <b>{html.escape(m['name'])}</b> دوباره فعال شد.")

# ── حریم خصوصی مدل‌ها: هیچ نام مدل/آدرس/کلیدی به مدیر میانی نمی‌رسد؛ فقط یک کد کوتاه و بی‌خطر ──
AI_ERR = {"ai_busy": ("سرویس هوش مصنوعی موقتاً شلوغ است", "AI service is busy right now"), "ai_auth": ("دسترسی سرویس هوش مصنوعی برقرار نشد", "AI service access failed"),
          "ai_timeout": ("سرویس هوش مصنوعی پاسخ نداد", "AI service did not respond"), "ai_empty": ("پاسخ خالی از سرویس هوش مصنوعی", "Empty response from AI service"),
          "ai_none": ("سرویس هوش مصنوعی در دسترس نیست", "AI service unavailable"), "ai_error": ("خطای موقت سرویس هوش مصنوعی", "Temporary AI service error"),
          "ai_exception": ("خطای داخلیِ غیرمنتظره در مسیرِ تولید با AI", "Unexpected internal error in the AI generation path"),
          "ai_budget": ("سقف هزینهٔ هوش مصنوعی برای این مقاله پر شد", "AI cost budget reached for this article"),
          "ai_tokens": ("بودجهٔ توکنِ مدل تمام شد و خروجی تولید نشد (مدل یا سقف توکن را عوض کنید)", "The model exhausted its token budget without producing output (change the model or its token budget)"),
          "ai_limit": ("سقف روزانهٔ مصرف هوش مصنوعی تمام شد", "Daily AI usage limit reached"),
          "ai_invalid": ("خروجی مدل‌ها نامعتبر بود", "The model output was rejected as invalid"),
          "ai_all_failed": ("هیچ‌یک از مدل‌ها پاسخ نداد", "None of the models responded")}

def err_code(e):
    t = str(e or "").lower()
    if "token budget" in t or "exhausted its token" in t: return "ai_tokens"
    if "429" in t or "rate limit" in t or "quota" in t or "overload" in t or "busy" in t: return "ai_busy"
    if "401" in t or "403" in t or "api key" in t or "unauthor" in t or "permission" in t or "credential" in t: return "ai_auth"
    if "timeout" in t or "timed out" in t or "connect" in t or "network" in t or "unreachable" in t: return "ai_timeout"
    if "504" in t or "gateway" in t: return "ai_timeout"          # پایانِ زمانِ درخواست ⇒ موقت
    if "500" in t or "502" in t or "503" in t or "server error" in t: return "ai_busy"   # خطای موقتِ سمتِ پروایدر
    if "empty" in t or "no candidates" in t: return "ai_empty"
    if "no_models" in t or "no model" in t: return "ai_none"
    return "ai_error"

def ai_err_text(code, lang="fa"): return AI_ERR.get(code if code in AI_ERR else "ai_error")[1 if lang == "en" else 0]

def _model_ready(m):
    """مدل قابل انتخاب: سالم (status=ok)، یا افتاده ولی backoff تمام‌شده (next_try_after <= now)."""
    if m["status"] != "down": return True
    nta = parse_dt(m["next_try_after"]) if "next_try_after" in m.keys() else None
    if nta: return now_utc() >= nta
    lf = parse_dt(m["last_fail"]); return bool(not lf or (now_utc() - lf) >= timedelta(minutes=config.MODEL_PROBE_MIN))

def is_url_like(x):
    x = (x or "").strip().lower()
    return x.startswith("http") or ("." in x and " " not in x and "/" not in x)

def _model_label(m, owner_id=None):
    lang = user_lang(owner_id) if owner_id else "fa"
    if owner_id and m["owner_id"] and m["owner_id"] != owner_id:
        return "Public model" if lang == "en" else "مدل عمومی"
    nm = (m["name"] or "").strip()
    if nm and is_url_like(nm): nm = ""
    return (nm or (m["model"] or "").strip()) or ("Public model" if lang == "en" else "مدل عمومی")

async def _ai_event(event, label="", code=None):
    callback = _ai_events.get()
    if callback: await callback(event, label, code)

def _validate_output(text):
    text = _ai_text(text)
    if text.startswith(("{", "[")):
        try: j = json.loads(text)
        except ValueError as e: raise AIContentInvalid("malformed AI output") from e
        if not isinstance(j, dict) or j.get("error"): raise AIContentInvalid("invalid AI output")
    return text

def _validate_ready(text):
    if _ai_text(text).upper() != "READY": raise AIContentInvalid("invalid health-check output")

def _validate_generation(text):
    _validate_output(text)
    if INJ_RESIDUE.search(text): raise AIContentInvalid("injection residue in AI output")   # خروجی هم معنایی چک می‌شود، نه فقط ساختار JSON
    j = parse_json(text)
    if not isinstance(j, dict): raise AIContentInvalid("invalid AI JSON")
    if j.get("is_ad") is True: return
    post = j.get("post")
    scores = j.get("scores")
    if not isinstance(post, str) or not strip_tags(post).strip(): raise AIContentInvalid("invalid empty post")
    if not isinstance(scores, dict) or not scores: raise AIContentInvalid("missing scores")

def _validate_salvageable(text):
    """PLB-01 (فاز ۷): قراردادِ نرمِ مسیر تولید.

    JSON کاملاً بدشکل («malformed AI output») هنوز محتوای بی‌مصرف است و مدل بعدی امتحان می‌شود؛
    ولی پاسخِ prose یا JSONِ سالم با فیلدهای ناقص به generate می‌رسد تا مسیر salvage آن را
    واقعاً نجات دهد (پیش‌تر قرارداد سختِ missing scores/output مانع هر salvage موفق می‌شد)."""
    _validate_output(text)
    if INJ_RESIDUE.search(text): raise AIContentInvalid("injection residue in AI output")
    j = parse_json(text)
    if isinstance(j, dict):
        if j.get("error"): raise AIContentInvalid("invalid AI output")
        if j.get("is_ad") is not True:
            post = j.get("post")
            if post is not None and (not isinstance(post, str) or not strip_tags(post).strip()):
                raise AIContentInvalid("invalid empty post")
    return text

async def _try_models(groups, system, user, owner_id=None, on_queue=None, validate=None, explicit_id=None):
    def _sort_key(m):
        fc = m["fail_count"] or 0; okc = m["ok_count"] or 0
        ratio = (okc + 1.0) / (okc + fc + 2.0)
        return (ratio, -fc, -(okc), m["priority"] or 99, m["id"])
    tried = set(); last = "ai_none"; attempts = 0; content_fails = 0; stop = False; quarantined = 0; q_codes = []
    for stage, models in groups:
        if stage == "test" and not models: await _ai_event("no_test")
        rest = sorted(models, key=_sort_key)
        while rest:
            if attempts >= config.AI_MAX_MODEL_ATTEMPTS:
                log.warning("model attempt cap reached (%d) — not calling more providers", config.AI_MAX_MODEL_ATTEMPTS)
                if not tried: last = "ai_none"      # کدِ واقعیِ خطا (اگر وجود دارد) پاک نمی‌شود
                stop = True; break
            free = [m for m in rest if not model_busy(m["id"])]
            if explicit_id is not None and any(m["id"] == explicit_id for m in rest):
                m = next(m for m in rest if m["id"] == explicit_id)
            else:
                m = (free or rest)[0]
            rest.remove(m)
            if m["id"] in tried: continue
            # P0-D: مدلی که در همین چرخه خروجیِ غیرقابل‌استفاده داد دوباره صدا زده نمی‌شود.
            if cycle_quarantine_has(m["id"]):
                quarantined += 1
                _qc = cycle_quarantine_code(m["id"]) or "unusable"
                q_codes.append(_qc)
                log.info("model %s skipped (quarantined for this cycle: %s)", m["name"], _qc)
                continue
            tried.add(m["id"]); attempts += 1
            label = _model_label(m, owner_id); content_fail = False
            if model_busy(m["id"]) or AI_SEM.locked():
                await _ai_event("busy", label)
                if on_queue: await on_queue()
            try:
                async with model_lock(m["id"]):
                    current = get_model(m["id"])
                    if not current or current["owner_id"] != m["owner_id"] or (m["id"] != explicit_id and (not current["active"] or not _model_ready(current))):
                        await _ai_event("unavailable", label); continue
                    async with AI_SEM:
                        await _ai_event("start", label, stage)
                        raw_out, usage = await _call_model(current, system, user)
                        content_fail = True                       # از این نقطه هر خطا مربوط به محتواست نه provider
                        # بررسی ساختارِ خروجی هم «خطای محتوا» است: قبلاً پیش از این پرچم اجرا می‌شد و یک JSON
                        # کمی خراب، مدل سالم را down می‌کرد و هزینهٔ مدل بعدی را هم تحمیل می‌کرد.
                        text = await asyncio.to_thread(_validate_output, raw_out)
                        if validate: await asyncio.to_thread(validate, text)
                        content_fail = False
                        _record_usage(current["id"], usage)       # PLB-03: مصرف توکنِ فراخوانی موفق
                        await _mark_ok(current)
                        await _ai_event("success", label)
                        return text, label, None
            except (httpx.HTTPError, asyncio.TimeoutError, AIResponseError, FetchError) as e:
                last = "ai_timeout" if isinstance(e, (httpx.TimeoutException, asyncio.TimeoutError)) else err_code(e)
                # خطای اعتبارسنجیِ محتوا به حساب provider نوشته نمی‌شود؛ وگرنه یک قرارداد/پرامپتِ سخت‌گیرانه
                # همهٔ مدل‌های سالم را پشت‌سرهم down می‌کرد. مدل بعدی همچنان امتحان می‌شود.
                # «محتوای ناقص» (پاسخِ بریده‌شده) هم خطای محتوا است ⇒ هرگز منتشر نمی‌شود.
                if content_fail or isinstance(e, AIContentInvalid):
                    content_fails += 1
                    # کدِ دقیق: «بودجهٔ توکنِ مدل تمام شد» در برابرِ «خروجی نامعتبر»
                    last = "ai_tokens" if (isinstance(e, AIOutputTruncated) and getattr(e, "empty", False)) else "ai_invalid"
                    # P0-D: خروجیِ خالی/نامعتبر ⇒ این مدل در ادامهٔ همین چرخه صدا زده نمی‌شود
                    # (ولی برای مدتِ طولانی از کار نمی‌افتد و «ردِ عادیِ مقاله» آن را نمی‌بندد).
                    cycle_quarantine_add(m["id"], last, str(e)[:80])
                    log.info("model %s returned unusable content (%s); provider NOT marked down", m["name"], e)
                    if content_fails > config.AI_CONTENT_RETRY_MODELS:
                        # خروجی نامعتبر ربطی به provider ندارد؛ فرستادن همان پرامپت بزرگ به مدل‌های بعدی فقط هزینه است.
                        log.warning("content rejected by %d model(s) — aborting instead of paying another model", content_fails)
                        last = "ai_invalid"; stop = True; break
                else:
                    # خطای سمتِ سرویس (۵xx/timeout/شبکه) ⇒ این پروایدر در همین چرخه کنار گذاشته می‌شود
                    # تا مقاله‌های بعدی دوباره به همان ارائه‌دهندهٔ ناسالم فرستاده نشوند.
                    cycle_quarantine_add(m["id"], last, str(e)[:80])
                    bump("model_fail", sub=last)   # شکست provider به تفکیک کد خطا (نمایش در پنل مالک)
                    await _mark_fail(m, e)
                    await _ai_event("failure", label, last)
        if stop: break
    # هر مدل فقط یک‌بار امتحان می‌شود (بدون تکرار)؛ این‌جا یعنی همهٔ مدل‌های کاربر و مدیر تمام شدند.
    # با یک مدل، کدِ دقیقِ خطا (مثلاً ai_timeout/ai_busy/ai_auth) نگه داشته می‌شود تا کاربر
    # دلیلِ واقعی را ببیند؛ با چند مدل ⇒ «هیچ‌یک پاسخ نداد».
    if not tried and q_codes:
        # همهٔ مدل‌ها در همین چرخه قرنطینه شده بودند ⇒ دلیلِ واقعی همان است
        last = q_codes[0]
    code = last if (last == "ai_none" or len(tried) <= 1) else "ai_all_failed"
    log.warning("all models exhausted (tried=%d, quarantined=%d, last=%s) — stopping", len(tried), quarantined, last)
    await _ai_event("failed", code=code)
    return None, None, code

# ── FIX v4: بودجه‌ی per-article با ContextVar (نه global) ──
def budget_reset(hard=False):
    """آغازِ یک کارِ تازه: بودجه‌ی فراخوانی/زمانِ همین تسک صفر می‌شود.

    PH-08/PIPE-001: ``hard=True`` یعنی «این یک مقاله است» ⇒ در میانهٔ مقاله **هرگز**
    پنجرهٔ بودجه ریست نمی‌شود. پیش‌تر ریستِ زمانی، شمارندهٔ فراخوانی را هم صفر می‌کرد و
    سقفِ «N فراخوانی برای هر مقاله» عملاً بی‌اثر می‌شد."""
    config.AI_BUDGET.set({"calls": 0, "t0": time.monotonic(), "hard": bool(hard)})

def _budget():
    b = config.AI_BUDGET.get()
    if b is None:
        budget_reset(); b = config.AI_BUDGET.get()
    return b

async def ai_chat(system, user, on_queue=None, owner_id=None, selected_id=None, validate=None):
    b = _budget()
    # P0/OBS: شمارشِ «فراخوانیِ منطقی» (جدا از POSTهای واقعیِ شبکه)
    _st = ai_stats(); _st["logical"] = int(_st.get("logical") or 0) + 1
    # PH-08/PIPE-001: ریستِ زمانی فقط برای فراخوانیِ خارج از مسیرِ مقاله مجاز است.
    if not b.get("hard") and (time.monotonic() - b["t0"]) > config.AI_ARTICLE_TIME_BUDGET * 2:
        budget_reset(); b = _budget()   # تماس بیرون از مسیر مقاله: پنجرهٔ تازه
    b["calls"] += 1
    if b["calls"] > config.AI_MAX_CALLS_PER_ARTICLE:
        log.warning("AI call budget exhausted for one article (%d calls) — stopping", config.AI_MAX_CALLS_PER_ARTICLE); return None, None, "ai_budget"
    if (time.monotonic() - b["t0"]) > config.AI_ARTICLE_TIME_BUDGET:
        log.warning("AI time budget exhausted for one article (%.0fs) — stopping", time.monotonic() - b["t0"]); return None, None, "ai_timeout"
    # PH-08/PIPE-006 · PIPE-007: هر فراخوانیِ واقعیِ مدل یک واحد از سقفِ روزانه می‌گیرد.
    # برای یک مقالهٔ معمولی (یک فراخوانی) این دقیقاً مثل قبل است؛ فقط مقاله‌هایِ خیلی بلند
    # که چند تکه خلاصه می‌شوند، واحدهای بیشتری می‌گیرند ⇒ هزینه واقعاً کران‌دار می‌شود.
    # مسیرِ تستِ مدلِ شخصی هم از همین دروازه رد می‌شود (پیش‌تر هزینهٔ آن سقف نداشت).
    if owner_id is not None and not ai_claim(owner_id):
        log.warning("AI daily cap reached user=%s", owner_id); return None, None, "ai_limit"
    personal_all = [m for m in list_models(active_only=True, owner_id=owner_id)] if owner_id else []
    personal = [m for m in personal_all if _model_ready(m)]
    if selected_id is not None:
        selected = get_model(selected_id)
        if not selected or selected["owner_id"] != owner_id or owner_id is None:
            return None, None, "ai_none"
        personal = [selected] + [m for m in personal if m["id"] != selected_id]
    shared_all = list_models(active_only=True)
    shared = [m for m in shared_all if _model_ready(m)]
    test_id = await arun(gget, "test_model_id")
    test = [m for m in shared if m["id"] == test_id]
    shared = [m for m in shared if m["id"] != test_id]
    if not personal and not shared and not test:
        # مدل هست ولی همه در backoff ⇒ خطای «موقت» (نه «بدون مدل»): کاربر باید بداند
        # سرویس بعداً برمی‌گردد، نه اینکه پیکربندی خراب است.
        # (دامنهٔ مدل‌ها باید همانِ این کاربر باشد: مدلِ شخصیِ خودش + مدل‌های عمومی)
        if personal_all or shared_all:
            log.warning("every AI model is in backoff — no provider call made")
            return None, None, "ai_busy"
    return await _try_models([("personal", personal), ("test", test), ("public", shared)], system, user,
                             owner_id, on_queue, validate, selected_id)

async def test_model(mid):
    m = get_model(mid); t = time.monotonic()
    if not m: return False, "no model", 0.0
    out, label, err = await _try_models([("direct", [m])], "You are a health-check. Reply with exactly one word.",
                                        "Say: READY", validate=_validate_ready, explicit_id=mid)
    return out is not None, out[:100] if out else err, round(time.monotonic() - t, 1)

async def probe_down_models(max_models=1):
    """سقفِ probe: حداکثر یک مدل در هر اجرا، حداقل ~۱۰ دقیقه فاصله، timeout کوتاه.

    پیش‌تر هر تیک (۱۲۰ ثانیه) تا ۳ مدلِ down با timeoutِ کاملِ پروایدر (۲۴۰ ثانیه!)
    امتحان می‌شدند ⇒ یک probe می‌توانست scheduler را چند دقیقه بلوکه کند و هزینه‌اش
    را هر بار پرداخت کند. حالا: یک مدل، با فاصلهٔ زمانی، فقط وقتی backoffش تمام
    شده (توجیهِ عملیاتی)، با timeout اختصاصیِ کوتاه، و بدونِ retry loop."""
    limit = max(1, int(max_models or 1))
    # فاصلهٔ زمانیِ سراسری بین probeهای *واقعی*
    try:
        last = await arun(gget, "last_probe_at")
    except Exception:
        last = None
    if last:
        try:
            since = (now_utc() - parse_dt(last)).total_seconds()
            if since < config.MODEL_PROBE_MIN * 60:
                log.debug("probe skipped — only %.0fs since the last probe (min %ds)", since, config.MODEL_PROBE_MIN * 60)
                return 0
        except Exception:
            pass
    rows = await arun(q, "SELECT * FROM ai_models WHERE active=1 AND status='down' ORDER BY COALESCE(last_fail,'') LIMIT ?", (max(limit * 4, 8),))
    target = None
    for m in rows:
        lf = parse_dt(m["last_fail"])
        if lf and (now_utc() - lf) < timedelta(minutes=config.MODEL_PROBE_MIN): continue
        nta = parse_dt(m["next_try_after"]) if "next_try_after" in m.keys() else None
        if nta and now_utc() < nta: continue      # backoff هنوز تمام نشده ⇒ توجیهِ عملیاتی ندارد
        target = m; break
    if target is None:
        log.debug("probe skipped — no down model has an expired backoff")
        return 0
    # زمان‌نگار پیش از probe ثبت می‌شود تا crash هم فاصله را نشکند
    await arun(gset, "last_probe_at", now_iso())
    budget = float(config.AI_PROBE_TIMEOUT_SEC)
    _tok = _PROBE_DEADLINE.set(time.monotonic() + budget)
    try:
        ok, _out, _sec = await asyncio.wait_for(test_model(target["id"]), timeout=budget)
        # پیش‌تر این خط همیشه «result=ok» می‌نوشت، حتی وقتی خودِ probe شکست خورده بود ⇒
        # لاگِ تولید دربارهٔ سلامتِ provider گمراه‌کننده بود.
        log.info("probe done: model_id=%s result=%s", target["id"], "ok" if ok else "failed")
    except asyncio.TimeoutError:
        log.warning("probe timed out after %.0fs: model_id=%s", budget, target["id"])
    except asyncio.CancelledError:
        raise
    except Exception as e:
        log.warning("probe failed: model_id=%s error=%s", target["id"], type(e).__name__)
    finally:
        try: _PROBE_DEADLINE.reset(_tok)
        except Exception: pass
    return 1

def any_model_available(owner_id=None): return any(_model_ready(m) for m in list_models(active_only=True, owner_id=owner_id)) or (bool(owner_id) and any(_model_ready(m) for m in list_models(active_only=True)))

def _scan_json(t):
    """پیمایش یک‌رشته‌ایِ JSON: خط/تبِ خام داخل رشته‌ها اصلاح می‌شود — خطای رایج مدل‌ها در JSON چندخطی —
    و هم‌زمان اندیسِ پایانِ شیء شمرده می‌شود. خروجی: (متنِ اصلاح‌شده، اندیسِ پایان یا -1)."""
    out, ins, esc, depth, end = [], False, False, 0, -1
    for idx, ch in enumerate(t):
        if ins:
            if esc: esc = False
            elif ch == "\\": esc = True
            elif ch == '"': ins = False
            elif ch == "\n": out.append("\\n"); continue
            elif ch == "\r": continue
            elif ch == "\t": out.append("\\t"); continue
        else:
            if ch == '"': ins = True
            elif ch == "{": depth += 1
            elif ch == "}":
                depth -= 1
                if depth <= 0 and end < 0: end = idx
        out.append(ch)
    return "".join(out), end

def parse_json(text):
    if not text: return None
    t = re.sub(r"```(?:json|JSON)?", "", text).strip(); s, e = t.find("{"), t.rfind("}")
    if s < 0 or e < 0: return None
    body = t[s:e + 1]
    base = [body, re.sub(r",\s*([}\]])", r"\1", body)]
    fixed = [_scan_json(c)[0] for c in base]
    for cand in base + fixed + [re.sub(r",\s*([}\]])", r"\1", f) for f in fixed]:
        for strict in (True, False):
            try: return json.loads(cand, strict=strict)
            except Exception: continue
    return None

