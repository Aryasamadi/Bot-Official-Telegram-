# -*- coding: utf-8 -*-
"""کلاینت HTTP مشترک با محافظت SSRF، آینه‌ی Cloudflare KV و دیپ‌لینک‌ها.
FIX v4:
  • public_url_strict: رزولوشن دوباره‌ی DNS با بررسی سازگاری (پنجره‌ی DNS rebinding میکروثانیه‌ای می‌شود)
  • is_private_host: بررسی مشترک IP خصوصی برای همه‌ی URLهای کاربری (منبع/API/مدل)
  • fetch و _GuardedStream (در publish.py) از public_url_strict استفاده می‌کنند
"""
import asyncio, ipaddress, json, re, secrets, socket, time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlparse
import httpx
from newsbot import config
from newsbot.db import q, arun
from newsbot.logutils import log_event
from newsbot.timeutils import now_iso
from newsbot.concurrency import FETCH_SEM

_http = None
UA_BROWSER = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
UA_FEED = "Mozilla/5.0 (compatible; NewsBot/3.0; +https://t.me) FeedFetcher"

def http():
    global _http
    # کلاینتِ بسته (مثلاً پس از یک خطای شبکه یا restartِ حلقه) دوباره ساخته می‌شود؛
    # وگرنه همهٔ درخواست‌های AI با «client has been closed» می‌مردند.
    if _http is None or getattr(_http, "is_closed", False):
        _http = httpx.AsyncClient(follow_redirects=True, timeout=httpx.Timeout(25, connect=10), limits=httpx.Limits(max_connections=config.FETCH_CONCURRENCY + 8, max_keepalive_connections=8),
                                  headers={"User-Agent": UA_BROWSER, "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8", "Accept-Language": "fa,en;q=0.8", "Accept-Encoding": "identity"})
    return _http

async def close_http():
    """بستن تمیز کلاینت در shutdown (فایل ۳۵: app.py)."""
    global _http
    if _http is not None:
        try: await _http.aclose()
        except Exception: pass
        _http = None

_PRIVATE_HOSTS = ("localhost", "metadata.google.internal")

def _parse_ip_alt(h):
    """ARCH-08 (فاز ۳): قالب‌های جایگزین IP — دهدهی یکپارچه (2130706433)، هجدهی/شانزدهی نقطه‌دار
    (0177.0.0.1 / 0x7f.0.0.1)، هجدهی/شانزدهی بدون‌نقطه (017700000001 / 0x7f000001) و فرم کوتاه
    (127.1) — قاعدهٔ کلاسیک inet_aton. خروجی: ip_address یا None اگر IP نیست.
    PH-02/NET-004: فرمِ کوتاه درست پیاده شد (جزء اول می‌تواند چندبایتی باشد، مثل inet_aton)."""
    parts = h.split(".")
    if not 1 <= len(parts) <= 4 or any(not p for p in parts): return None
    vals = []
    for p in parts:
        pl = p.lower()
        try:
            if pl.startswith("0x"): v = int(pl[2:], 16)
            elif len(pl) > 1 and pl.startswith("0"): v = int(pl, 8)
            else: v = int(pl, 10)
        except ValueError:
            return None
        if v < 0: return None
        vals.append(v)
    n = len(vals)
    if n == 4:
        if any(v > 255 for v in vals): return None
    else:
        # inet_aton: جزء اول byte است و جزء آخر بقیهٔ بایت‌ها را می‌گیرد
        if any(v > 255 for v in vals[:-1]): return None
        if vals[-1] >= (1 << (8 * (5 - n))): return None
    acc = 0
    for v in vals[:-1]: acc = (acc << 8) | v
    acc = (acc << (8 * (5 - n))) + vals[-1]
    try: return ipaddress.ip_address(acc & 0xFFFFFFFF)
    except ValueError: return None

def is_private_host(host):
    """FIX v4: بررسی مشترک میزبان خصوصی/داخلی برای همهٔ URLهای کاربری.
    شامل: localhost، ::1، 0.0.0.0، دامنه‌های .local/.internal، RFC1918، loopback،
    link-local (169.254.x = metadata endpoints)، و هر IP غیرعمومی.
    FIX v5 (ARCH-08): قالب‌های جایگزین IP (دهدهی/هجدهی/شانزدهی/کوتاه) هم گرفته می‌شوند.
    PH-02/NET-004: علاوه بر ``is_global``، حالت‌های multicast / reserved / unspecified
    هم رد می‌شوند (پایتون multicast را «global» گزارش می‌کند)."""
    h = (host or "").strip("[]").lower().rstrip(".")
    if not h: return True
    if h in _PRIVATE_HOSTS or h in ("::1", "0.0.0.0") or h.endswith((".local", ".internal", ".localhost")): return True
    if re.match(r"^(127\.|10\.|192\.168\.|169\.254\.|0\.|172\.(1[6-9]|2\d|3[01])\.)", h): return True
    try:
        return not _ip_is_public(ipaddress.ip_address(h))
    except ValueError:
        pass
    alt = _parse_ip_alt(h)
    if alt is not None: return not _ip_is_public(alt)
    # PH-02: اگر میزبان شبیهِ یک IP دَهی است ولی به IP معتبر تبدیل نمی‌شود
    # (مثل «0x7fffff.1» که inet_aton هم آن را رد می‌کند) ⇒ fail-closed: خصوصی فرض می‌شود.
    if re.fullmatch(r"[0-9a-fx.]+", h) and "." in h: return True
    return False

def _ip_is_public(ip):
    """«عمومی» یعنی: global و نه multicast / reserved / unspecified / loopback / link-local."""
    try:
        return bool(ip.is_global and not ip.is_multicast and not ip.is_reserved
                    and not ip.is_unspecified and not ip.is_loopback and not ip.is_link_local)
    except Exception:
        return False


# ── PH-02: فهرستِ سفیدِ میزبانِ خصوصی (پروایدرهای محلی مثل Ollama / LM Studio) ────
def private_host_allowed(url):
    """آیا این مقصدِ خصوصی در فهرستِ سفیدِ اپراتور هست؟

    ``ALLOW_PRIVATE_MODEL_HOSTS`` یک فهرستِ ``host:port`` است (جداشده با کاما)؛
    مقدارِ ``all`` یعنی «همهٔ میزبان‌های خصوصی مجاز» (پذیرشِ آگاهانهٔ ریسک).
    مقدارِ خالی یعنی هیچ میزبانِ خصوصی مجاز نیست (پیش‌فرضِ امن)."""
    allow = (config.ALLOW_PRIVATE_MODEL_HOSTS or "").strip()
    if not allow: return False
    if allow.lower() == "all": return True
    try: p = urlparse(str(url))
    except Exception: return False
    host = (p.hostname or "").lower().rstrip(".")
    if not host: return False
    try: port = p.port or (443 if p.scheme == "https" else 80)
    except Exception: return False
    for e in allow.split(","):
        e = e.strip().lower()
        if not e: continue
        if ":" in e:
            h, _, pt = e.rpartition(":")
            try: pt = int(pt)
            except ValueError: continue
        else:
            h, pt = e, None
        if h == host and (pt is None or pt == port): return True
    return False


def egress_url_ok(url):
    """آیا این URL برای خروجیِ برنامه (به‌ویژه پروایدرِ AI) مجاز است؟

    میزبانِ خصوصی **فقط** اگر در فهرستِ سفیدِ ``ALLOW_PRIVATE_MODEL_HOSTS`` باشد.
    این فهرست تنها دروازهٔ دسترسی به میزبانِ خصوصی است (PH-02/Q-5)."""
    try: p = urlparse(str(url))
    except Exception: return False
    if p.scheme not in ("http", "https") or not p.hostname: return False
    if is_private_host(p.hostname):
        return private_host_allowed(url)
    return True


async def egress_url_strict(url):
    """همان ``public_url_strict``، ولی میزبانِ خصوصی فقط در صورتِ فهرستِ سفید پذیرفته می‌شود."""
    if not egress_url_ok(url): return False
    try: host = urlparse(str(url)).hostname or ""
    except Exception: return False
    if is_private_host(host): return True          # در فهرستِ سفید ⇒ رزولوشن لازم نیست
    return await asyncio.to_thread(public_url_strict, url)

_DNS_CACHE = {}
_DNS_CACHE_TTL = 5.0
_DNS_CACHE_MAX = 512


def _resolve_addrs(host, fresh=False):
    """Resolve once and cache briefly (PH-07/NET-008); callers still validate every IP."""
    now = time.monotonic()
    if not fresh:
        hit = _DNS_CACHE.get(host)
        if hit and now - hit[0] < _DNS_CACHE_TTL:
            return hit[1]
    addrs = tuple(sorted({i[4][0] for i in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)}))
    if not addrs:
        raise OSError("no addresses")
    _DNS_CACHE[host] = (now, addrs)
    if len(_DNS_CACHE) > _DNS_CACHE_MAX:
        oldest = sorted(_DNS_CACHE.items(), key=lambda kv: kv[1][0])[:len(_DNS_CACHE) - _DNS_CACHE_MAX]
        for k, _ in oldest:
            _DNS_CACHE.pop(k, None)
    return addrs


def _all_public(addrs):
    for a in addrs:
        try: ip = ipaddress.ip_address(a)
        except Exception: return False
        if not ip.is_global: return False
    return True


def public_url(u):
    """آدرس بیرونی و امن است؟ DNS ناموفق یا هر IP غیرعمومی ⇒ رد می‌شود (SSRF fail-closed)."""
    try: p = urlparse(str(u))
    except Exception: return False
    if p.scheme not in ("http", "https") or not p.hostname: return False
    if is_private_host(p.hostname): return False
    try: addrs = _resolve_addrs(p.hostname.rstrip(".").lower())
    except Exception: return False
    return bool(addrs) and _all_public(addrs)


def public_url_strict(u):
    """FIX v4: رزولوشن دوباره‌ی DNS با بررسی سازگاری.
    یک رزولوشنِ تازه با رزولوشنِ cache‌شده مقایسه می‌شود؛ هر دو باید یک مجموعه‌ی
    تماماً عمومی بدهند. این پنجره‌ی DNS rebinding را کوچک نگه می‌دارد و در همان
    زمان از سه lookup به یک lookup تازه در هر فراخوانی می‌رسد (PH-07/NET-008)."""
    try: p = urlparse(str(u))
    except Exception: return False
    if p.scheme not in ("http", "https") or not p.hostname: return False
    if is_private_host(p.hostname): return False
    host = p.hostname.rstrip(".").lower()
    try:
        cached = _resolve_addrs(host)
        fresh = _resolve_addrs(host, fresh=True)
    except Exception:
        return False
    if not cached or cached != fresh: return False
    return _all_public(fresh)

class FetchError(RuntimeError):
    """خطای HTTP با کد وضعیت؛ برای پیام اختصاصی ۴۰۳ هنگام افزودن منبع."""
    def __init__(self, status, url=""): super().__init__(f"HTTP {status}"); self.status = int(status); self.url = url

# هدرهایی که «هویت» دارند و نباید به میزبانِ دیگری نشت کنند
CREDENTIAL_HEADERS = frozenset({"authorization", "x-api-key", "apikey", "cookie", "proxy-authorization"})


def _sse_to_json(body_text):
    """پاسخِ SSE (‎stream=true‎) ⇒ یک پاسخِ JSON معادل (مثل حالتِ non-stream).

    چرا؟ لبه‌هایی مثل Cloudflare اگر *شروع*ِ پاسخ در ۱۰۰ ثانیه نرسد، خطای ۵۲۴ می‌دهند
    (پاسخِ مدلِ کند بعد از آن قطع می‌شود). با جریان، اولین قطعه زود می‌رسد و پاسخِ
    کامل جمع می‌شود ⇒ نه ۵۲۴ و نه قطعِ ناقص."""
    content = []; finish = None; usage = None; role = None
    for line in str(body_text or "").splitlines():
        line = line.strip()
        if not line.startswith("data:"): continue
        payload = line[5:].strip()
        if not payload or payload == "[DONE]": continue
        try: d = json.loads(payload)
        except Exception: continue
        if not isinstance(d, dict): continue
        if d.get("usage"): usage = d.get("usage")
        for ch in (d.get("choices") or []):
            if not isinstance(ch, dict): continue
            if ch.get("finish_reason"): finish = ch.get("finish_reason")
            delta = ch.get("delta") or ch.get("message") or {}
            if not isinstance(delta, dict): continue
            if delta.get("role"): role = delta.get("role")
            piece = delta.get("content")
            if isinstance(piece, str): content.append(piece)
            elif isinstance(piece, list):
                content.extend(p.get("text", "") for p in piece if isinstance(p, dict) and isinstance(p.get("text"), str))
    return {"choices": [{"index": 0, "message": {"role": role or "assistant", "content": "".join(content)},
                          "finish_reason": finish or "stop"}], "usage": usage}


async def fetch_post(url, *, headers=None, timeout=None, max_redirects=5, total_timeout=None, **kw):
    """POST امن به یک میزبانِ **کاربر-پیکربندی‌شده** (پروایدرهای AI).

    PH-02/SEC-001 · NET-001 · NET-002: مسیرِ provider پیش‌تر هیچ اعتبارسنجی‌ای نداشت و
    ``follow_redirects=True`` داشت ⇒ یک ``base_url`` دلخواه می‌توانست به میزبانِ داخلی
    (مثلاً metadata سرور) برسد و کلیدِ API هم همراهِ redirect منتقل می‌شد.

    اینجا: هر مقصد (و هر redirect) دوباره با ``egress_url_strict`` اعتبارسنجی می‌شود — و
    میزبانِ خصوصی **فقط** اگر در فهرستِ سفیدِ ``ALLOW_PRIVATE_MODEL_HOSTS`` باشد — و
    هدرهایِ اعتبار روی تغییرِ میزبان حذف می‌شوند.

    ``total_timeout``: سقفِ زمانیِ کلِ درخواست. پیش‌تر همیشه ``FETCH_TOTAL_SEC`` (بودجهٔ
    خواندنِ منبع) بود و همان روی درخواستِ AI هم اعمال می‌شد ⇒ یک مدلِ سالمِ ولی کند،
    خطای ۵۰۴ِ کاذب می‌داد. پروایدرِ AI سقفِ خودش را می‌دهد.

    بدنه به‌صورتِ *جریانی* خوانده می‌شود (‎stream‎ روی سمتِ کلاینت) تا اولین بایتِ
    پاسخ همان لحظه برسد؛ این برای لبه‌هایی مثل Cloudflare (خطای ۵۲۴) حیاتی است.
    اگر درخواست ``stream=true`` خواسته باشد و پاسخ SSE باشد، به JSON معادل تبدیل
    می‌شود تا بقیهٔ کدِ پروایدرها بدونِ تغییر کار کند."""
    current = str(url)
    headers = dict(headers or {})
    timeout = timeout if timeout is not None else httpx.Timeout(60, connect=10)
    total = float(total_timeout) if total_timeout is not None else float(config.FETCH_TOTAL_SEC)
    total = max(1.0, total)
    req_kw = dict(kw); req_kw.pop("follow_redirects", None)
    wants_stream = bool(isinstance(req_kw.get("json"), dict) and req_kw["json"].get("stream"))
    for _ in range(max(1, int(max_redirects))):
        if not await egress_url_strict(current):
            raise FetchError(403, current)
        previous_host = (urlparse(current).hostname or "").lower().rstrip(".")
        try:
            async with asyncio.timeout(total):
                async with http().stream("POST", current, headers=headers, timeout=timeout, follow_redirects=False, **req_kw) as resp:
                    raw = bytearray()
                    async for chunk in resp.aiter_bytes(65536):
                        raw.extend(chunk)
                        if len(raw) > config.MAX_PAGE_BYTES * 8: raise FetchError(413, current)
                    body = bytes(raw)
                    ctype = (resp.headers.get("content-type") or "").lower()
                    if wants_stream and "text/event-stream" in ctype:
                        try: merged = _sse_to_json(body.decode("utf-8", "replace"))
                        except Exception: merged = None
                        if merged is not None:
                            r = httpx.Response(resp.status_code, headers=resp.headers, json=merged, request=resp.request)
                            return r
                    r = httpx.Response(resp.status_code, headers=resp.headers, content=body, request=resp.request)
        except TimeoutError as e:
            raise FetchError(504, current) from e
        if r.status_code in (301, 302, 303, 307, 308):
            location = r.headers.get("location")
            if not location: return r
            nxt = urljoin(current, location)
            if not await egress_url_strict(nxt): raise FetchError(403, nxt)
            next_host = (urlparse(nxt).hostname or "").lower().rstrip(".")
            if previous_host != next_host:
                headers = {k: v for k, v in headers.items() if k.lower() not in CREDENTIAL_HEADERS}
            current = nxt
            continue
        return r
    raise FetchError(310, current)

def retry_after_seconds(value):
    """ARCH-02/PLB-10 (فاز ۷): هدر Retry-After (ثانیه یا HTTP-date) → ثانیه؛ خالی/نامعتبر/گذشته ⇒ None.
    مشترک بین retry کشف خوراک (discover) و دانلود رسانه (publish)."""
    v = str(value or "").strip()
    if not v: return None
    try:
        secs = float(v)
        return secs if secs > 0 else None
    except ValueError: pass
    try:
        when = parsedate_to_datetime(v)
        if when.tzinfo is None: when = when.replace(tzinfo=timezone.utc)
        secs = (when - datetime.now(timezone.utc)).total_seconds()
        return secs if secs > 0 else None
    except Exception: return None

async def fetch(url, **kw):
    """GET امن با redirect محدود؛ هر مقصد redirect دوباره اعتبارسنجی و اندازه پاسخ واقعاً محدود می‌شود.
    ARCH-02 (فاز ۷): علاوه بر timeout هر خواندنِ httpx، سقف زمانیِ کلِ درخواست (FETCH_TOTAL_SEC)
    هم اعمال می‌شود؛ سرورِ کندپاسخ (slow-loris) دیگر منابع را بی‌نهایت قفل نمی‌کند ⇒ FetchError(504)."""
    current = str(url)
    req_kw = dict(kw); req_kw.pop("follow_redirects", None)
    headers = dict(req_kw.pop("headers", {}) or {})
    history = []
    deadline = time.monotonic() + config.FETCH_TOTAL_SEC
    for _ in range(6):
        remaining = deadline - time.monotonic()
        if remaining <= 0: raise FetchError(504, current)
        if not await asyncio.to_thread(public_url_strict, current): raise FetchError(403, current)
        previous_host = urlparse(current).hostname or ""
        async with FETCH_SEM:
            try:
                async with asyncio.timeout(remaining):
                    async with http().stream("GET", current, headers=headers, follow_redirects=False, **req_kw) as r:
                        if r.status_code in (301, 302, 303, 307, 308):
                            location = r.headers.get("location")
                            if not location: return httpx.Response(r.status_code, headers=r.headers, content=b"", request=r.request)
                            nxt = urljoin(current, location)
                            if not await asyncio.to_thread(public_url_strict, nxt): raise FetchError(403, nxt)
                            next_host = urlparse(nxt).hostname or ""
                            if previous_host.lower().rstrip(".") != next_host.lower().rstrip("."):
                                headers = {k: v for k, v in headers.items() if k.lower() not in {"authorization", "x-api-key", "apikey", "cookie"}}
                            history.append(r)
                            current = nxt
                            continue
                        try:
                            if int(r.headers.get("content-length") or 0) > config.MAX_PAGE_BYTES: raise FetchError(413, current)
                        except (TypeError, ValueError): pass
                        chunks = bytearray()
                        async for chunk in r.aiter_bytes(262144):
                            chunks.extend(chunk)
                            if len(chunks) > config.MAX_PAGE_BYTES: raise FetchError(413, current)
                        return httpx.Response(r.status_code, headers=r.headers, content=bytes(chunks), request=r.request, extensions=r.extensions, history=history)
            except TimeoutError as e:
                raise FetchError(504, current) from e
    raise FetchError(310, current)

# ── Cloudflare KV (آینه‌ی اختیاری «بیشتر») ──
CF_BASE = f"https://api.cloudflare.com/client/v4/accounts/{config.CF_ACCOUNT_ID}/storage/kv/namespaces/{config.CF_KV_NAMESPACE_ID}/values/"

async def kv_put(key, value):
    try:
        r = await http().put(CF_BASE + key, content=value.encode(), headers={"Authorization": f"Bearer {config.CF_API_TOKEN}", "Content-Type": "text/plain"}); return r.status_code == 200
    except Exception as e: log_event("ERROR", f"KV put: {e}"); return False

async def kv_get(key):
    try:
        r = await http().get(CF_BASE + key, headers={"Authorization": f"Bearer {config.CF_API_TOKEN}"}); return r.text if r.status_code == 200 else None
    except Exception as e: log_event("ERROR", f"KV get: {e}"); return None

def gen_deeplink_key():
    """PUB-04: تولید کلید بدون ذخیره — ردیف فقط پس از موفقیت انتشار نوشته می‌شود."""
    return secrets.token_urlsafe(6)

async def save_deeplink(key, admin_id, payload):
    """payload: {short, full, title, url, show_source, media:{url,kind}|None, ts}
    مرجع محلی `deeplinks` است؛ KV فقط آینه‌ی اختیاری برای بازیابی پس از تعویض VPS."""
    data = json.dumps(payload, ensure_ascii=False)
    if config.CF_ENABLED: await kv_put(key, data)
    await arun(q, "INSERT INTO deeplinks(key,admin_id,created_at,local_json) VALUES(?,?,?,?)", (key, admin_id, now_iso(), data), commit=True)
    return key

async def store_deeplink(admin_id, payload):
    return await save_deeplink(gen_deeplink_key(), admin_id, payload)

async def load_deeplink(key):
    r = await arun(q, "SELECT value FROM kv_cache WHERE key=?", (key,), one=True)   # ردیف‌های قدیمی همچنان خوانده می‌شوند
    if r: return json.loads(r["value"])
    row = await arun(q, "SELECT local_json FROM deeplinks WHERE key=?", (key,), one=True); data = row["local_json"] if row and row["local_json"] else (await kv_get(key) if config.CF_ENABLED else None)
    return json.loads(data) if data else None
