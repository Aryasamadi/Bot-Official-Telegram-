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
from newsbot.db import q, gget, _safe_fields
from newsbot.secret_store import decrypt as _sec_decrypt, encrypt as _sec_encrypt   # SEC-09 (فاز ۱۲)
from newsbot.logutils import log, log_event, bump, redact
from newsbot.timeutils import now_utc, now_iso, parse_dt
from newsbot.concurrency import AI_SEM, model_lock, model_busy
from newsbot.notify import notify_super
from newsbot.core_users import user_lang, ai_claim
from newsbot.text_format import strip_tags, INJ_RESIDUE

_ai_events = ContextVar("ai_events", default=None)

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
    return redact(f"HTTP {r.status_code}: {re.sub(r'<[^>]+>', '', str(msg))[:160]}")   # منبعِ خطا پاک‌سازی می‌شود، نه مصرف‌کننده‌هایش

class AIResponseError(RuntimeError):
    pass

def _provider_json(r):
    if r.status_code >= 400: raise AIResponseError(_err_text(r))
    try: j = r.json()
    except ValueError as e: raise AIResponseError("malformed provider JSON") from e
    if not isinstance(j, dict): raise AIResponseError("malformed provider envelope")
    if j.get("error"): raise AIResponseError("provider error response")
    return j

def _ai_text(value):
    if not isinstance(value, str): raise AIResponseError("invalid response text")
    value = value.strip()
    if not value: raise AIResponseError("empty response")
    return value

def _openai_content(j):
    choices = j.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise AIResponseError("malformed provider choices")
    ch = choices[0]; msg = ch.get("message") or ch.get("delta") or {}
    if not isinstance(msg, dict): raise AIResponseError("malformed provider message")
    if msg.get("refusal"): raise AIResponseError("provider refused response")
    c = msg.get("content") or ch.get("text")
    if isinstance(c, list):
        if any(not isinstance(p, dict) or not isinstance(p.get("text"), str) for p in c):
            raise AIResponseError("malformed provider content")
        c = " ".join(p["text"] for p in c)
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
    return await fetch_post(url, timeout=httpx.Timeout(120, connect=15), **kw)

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

async def _call_model(m, system, user):
    kind = m["kind"] or detect_kind(m["base_url"]); base = (m["base_url"] or "").rstrip("/"); key = _sec_decrypt(m["api_key"] or ""); model = m["model"]; temp = 0.5 if m["temperature"] is None else float(m["temperature"]); mx = int(m["max_tokens"] or 2500)
    if kind == "anthropic":
        url = base + ("/messages" if base.endswith("/v1") else "/v1/messages")
        r = await _post(url, headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"}, json={"model": model, "max_tokens": mx, "temperature": temp, "system": system, "messages": [{"role": "user", "content": user}]})
        j = _provider_json(r); blocks = j.get("content")
        if not isinstance(blocks, list) or any(not isinstance(b, dict) for b in blocks): raise AIResponseError("malformed provider content")
        parts = [b.get("text") for b in blocks if b.get("type") == "text"]
        if any(not isinstance(p, str) for p in parts): raise AIResponseError("invalid response text")
        return _ai_text(" ".join(parts)), _usage_dict(j.get("usage"), "input_tokens", "output_tokens")
    if kind == "gemini":
        model = model.replace("models/", ""); root = base if re.search(r"/v1(beta)?$", base) else base + "/v1beta"
        r = await _post(f"{root}/models/{model}:generateContent", params={"key": key}, json={"system_instruction": {"parts": [{"text": system}]}, "contents": [{"role": "user", "parts": [{"text": user}]}], "generationConfig": {"temperature": temp, "maxOutputTokens": mx}})
        j = _provider_json(r); cands = j.get("candidates")
        if not isinstance(cands, list) or not cands or not isinstance(cands[0], dict): raise AIResponseError("malformed provider candidates")
        content = cands[0].get("content")
        if not isinstance(content, dict): raise AIResponseError("provider blocked response")
        parts = content.get("parts")
        if not isinstance(parts, list) or any(not isinstance(p, dict) or not isinstance(p.get("text"), str) for p in parts): raise AIResponseError("malformed provider parts")
        return _ai_text(" ".join(p["text"] for p in parts)), _usage_dict(j.get("usageMetadata"), "promptTokenCount", "candidatesTokenCount")
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    if "openrouter" in base: headers.update({"HTTP-Referer": "https://t.me", "X-Title": "NewsBot"})
    body = {"model": model, "temperature": temp, "max_tokens": mx, "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
    url = base if base.endswith("/chat/completions") else base + "/chat/completions"
    r = await _post(url, headers=headers, json=body)
    if r.status_code == 404 and not re.search(r"/v\d", base): r = await _post(base + "/v1/chat/completions", headers=headers, json=body)
    j = _provider_json(r)
    return _openai_content(j), _usage_dict(j.get("usage"), "prompt_tokens", "completion_tokens")

async def _mark_fail(m, err):
    cur = get_model(m["id"]) or m   # اسنپ‌شاتِ ورودی ممکن است کهنه باشد؛ شمارنده از سطر فعلی خوانده می‌شود
    fc = (cur["fail_count"] or 0) + 1
    ntry = (now_utc() + timedelta(minutes=min(5 * (2 ** min(fc, 6)), config.MODEL_BACKOFF_MAX_MIN))).isoformat()
    update_model(m["id"], fail_count=fc, last_error=redact(err)[:300], last_fail=now_iso(), next_try_after=ntry,
                 status="down" if fc >= 2 else cur["status"])
    if fc >= 2 and cur["status"] != "down":
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
          "ai_budget": ("سقف هزینهٔ هوش مصنوعی برای این مقاله پر شد", "AI cost budget reached for this article"),
          "ai_limit": ("سقف روزانهٔ مصرف هوش مصنوعی تمام شد", "Daily AI usage limit reached"),
          "ai_invalid": ("خروجی مدل‌ها نامعتبر بود", "The model output was rejected as invalid"),
          "ai_all_failed": ("هیچ‌یک از مدل‌ها پاسخ نداد", "None of the models responded")}

def err_code(e):
    t = str(e or "").lower()
    if "429" in t or "rate limit" in t or "quota" in t or "overload" in t or "busy" in t: return "ai_busy"
    if "401" in t or "403" in t or "api key" in t or "unauthor" in t or "permission" in t or "credential" in t: return "ai_auth"
    if "timeout" in t or "timed out" in t or "connect" in t or "network" in t or "unreachable" in t: return "ai_timeout"
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
        except ValueError as e: raise AIResponseError("malformed AI output") from e
        if not isinstance(j, dict) or j.get("error"): raise AIResponseError("invalid AI output")
    return text

def _validate_ready(text):
    if _ai_text(text).upper() != "READY": raise AIResponseError("invalid health-check output")

def _validate_generation(text):
    _validate_output(text)
    if INJ_RESIDUE.search(text): raise AIResponseError("injection residue in AI output")   # خروجی هم معنایی چک می‌شود، نه فقط ساختار JSON
    j = parse_json(text)
    if not isinstance(j, dict): raise AIResponseError("invalid AI JSON")
    if j.get("is_ad") is True: return
    post = j.get("post")
    scores = j.get("scores")
    if not isinstance(post, str) or not strip_tags(post).strip(): raise AIResponseError("invalid empty post")
    if not isinstance(scores, dict) or not scores: raise AIResponseError("missing scores")

def _validate_salvageable(text):
    """PLB-01 (فاز ۷): قراردادِ نرمِ مسیر تولید.

    JSON کاملاً بدشکل («malformed AI output») هنوز محتوای بی‌مصرف است و مدل بعدی امتحان می‌شود؛
    ولی پاسخِ prose یا JSONِ سالم با فیلدهای ناقص به generate می‌رسد تا مسیر salvage آن را
    واقعاً نجات دهد (پیش‌تر قرارداد سختِ missing scores/output مانع هر salvage موفق می‌شد)."""
    _validate_output(text)
    if INJ_RESIDUE.search(text): raise AIResponseError("injection residue in AI output")
    j = parse_json(text)
    if isinstance(j, dict):
        if j.get("error"): raise AIResponseError("invalid AI output")
        if j.get("is_ad") is not True:
            post = j.get("post")
            if post is not None and (not isinstance(post, str) or not strip_tags(post).strip()):
                raise AIResponseError("invalid empty post")
    return text

async def _try_models(groups, system, user, owner_id=None, on_queue=None, validate=None, explicit_id=None):
    def _sort_key(m):
        fc = m["fail_count"] or 0; okc = m["ok_count"] or 0
        ratio = (okc + 1.0) / (okc + fc + 2.0)
        return (ratio, -fc, -(okc), m["priority"] or 99, m["id"])
    tried = set(); last = "ai_none"; attempts = 0; content_fails = 0; stop = False
    for stage, models in groups:
        if stage == "test" and not models: await _ai_event("no_test")
        rest = sorted(models, key=_sort_key)
        while rest:
            if attempts >= config.AI_MAX_MODEL_ATTEMPTS:
                log.warning("model attempt cap reached (%d) — not calling more providers", config.AI_MAX_MODEL_ATTEMPTS); last = "ai_none"; stop = True; break
            free = [m for m in rest if not model_busy(m["id"])]
            if explicit_id is not None and any(m["id"] == explicit_id for m in rest):
                m = next(m for m in rest if m["id"] == explicit_id)
            else:
                m = (free or rest)[0]
            rest.remove(m)
            if m["id"] in tried: continue
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
            except (httpx.HTTPError, asyncio.TimeoutError, AIResponseError) as e:
                last = "ai_timeout" if isinstance(e, (httpx.TimeoutException, asyncio.TimeoutError)) else err_code(e)
                # خطای اعتبارسنجیِ محتوا به حساب provider نوشته نمی‌شود؛ وگرنه یک قرارداد/پرامپتِ سخت‌گیرانه
                # همهٔ مدل‌های سالم را پشت‌سرهم down می‌کرد. مدل بعدی همچنان امتحان می‌شود.
                if content_fail:
                    content_fails += 1
                    log.info("model %s returned unusable content (%s); provider NOT marked down", m["name"], e)
                    if content_fails > config.AI_CONTENT_RETRY_MODELS:
                        # خروجی نامعتبر ربطی به provider ندارد؛ فرستادن همان پرامپت بزرگ به مدل‌های بعدی فقط هزینه است.
                        log.warning("content rejected by %d model(s) — aborting instead of paying another model", content_fails)
                        last = "ai_invalid"; stop = True; break
                else:
                    bump("model_fail", sub=last)   # شکست provider به تفکیک کد خطا (نمایش در پنل مالک)
                    await _mark_fail(m, e)
                    await _ai_event("failure", label, last)
        if stop: break
    # هر مدل فقط یک‌بار امتحان می‌شود (بدون تکرار)؛ این‌جا یعنی همهٔ مدل‌های کاربر و مدیر تمام شدند.
    code = last if last == "ai_none" else "ai_all_failed"
    log.warning("all models exhausted (tried=%d, last=%s) — stopping", len(tried), last)
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
    personal = [m for m in list_models(active_only=True, owner_id=owner_id) if _model_ready(m)] if owner_id else []
    if selected_id is not None:
        selected = get_model(selected_id)
        if not selected or selected["owner_id"] != owner_id or owner_id is None:
            return None, None, "ai_none"
        personal = [selected] + [m for m in personal if m["id"] != selected_id]
    shared = [m for m in list_models(active_only=True) if _model_ready(m)]
    test_id = gget("test_model_id")
    test = [m for m in shared if m["id"] == test_id]
    shared = [m for m in shared if m["id"] != test_id]
    return await _try_models([("personal", personal), ("test", test), ("public", shared)], system, user,
                             owner_id, on_queue, validate, selected_id)

async def test_model(mid):
    m = get_model(mid); t = time.monotonic()
    if not m: return False, "no model", 0.0
    out, label, err = await _try_models([("direct", [m])], "You are a health-check. Reply with exactly one word.",
                                        "Say: READY", validate=_validate_ready, explicit_id=mid)
    return out is not None, out[:100] if out else err, round(time.monotonic() - t, 1)

async def probe_down_models():
    """چند مدل خراب در هر تیک آزمایش می‌شود تا بازیابی به چند ده دقیقه کشیده نشود."""
    rows = q("SELECT * FROM ai_models WHERE active=1 AND status='down' ORDER BY COALESCE(last_fail,'') LIMIT ?", (config.MODEL_PROBE_BATCH,))
    for m in rows:
        lf = parse_dt(m["last_fail"])
        if lf and (now_utc() - lf) < timedelta(minutes=config.MODEL_PROBE_MIN): continue
        try: await test_model(m["id"])
        except Exception: log.exception("model probe failed id=%s", m["id"])

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

