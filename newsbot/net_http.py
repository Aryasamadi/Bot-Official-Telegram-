# -*- coding: utf-8 -*-
"""کلاینت HTTP مشترک با محافظت SSRF، آینه‌ی Cloudflare KV و دیپ‌لینک‌ها.
FIX v4:
  • public_url_strict: رزولوشن دوباره‌ی DNS با بررسی سازگاری (پنجره‌ی DNS rebinding میکروثانیه‌ای می‌شود)
  • is_private_host: بررسی مشترک IP خصوصی برای همه‌ی URLهای کاربری (منبع/API/مدل)
  • fetch و _GuardedStream (در publish.py) از public_url_strict استفاده می‌کنند
"""
import asyncio, ipaddress, json, re, secrets, socket
from urllib.parse import urljoin, urlparse
import httpx
from newsbot import config
from newsbot.db import q
from newsbot.logutils import log_event
from newsbot.timeutils import now_iso
from newsbot.concurrency import FETCH_SEM

_http = None
UA_BROWSER = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
UA_FEED = "Mozilla/5.0 (compatible; NewsBot/3.0; +https://t.me) FeedFetcher"

def http():
    global _http
    if _http is None:
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
    (127.1) — قاعدهٔ کلاسیک inet_aton. خروجی: ip_address یا None اگر IP نیست."""
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
    if len(vals) == 4:
        if any(v > 255 for v in vals): return None
        acc = 0
        for v in vals: acc = (acc << 8) | v
        try: return ipaddress.ip_address(acc)
        except ValueError: return None
    # فرم کوتاه: جزء آخر بقیهٔ بایت‌ها را می‌پُشد (127.1 ⇒ 127.0.0.1)
    *head, last = vals
    if any(v > 255 for v in head) or last >= 256 ** (4 - len(vals) + 1): return None
    acc = 0
    for v in head: acc = (acc << 8) | v
    acc = (acc << (8 * (4 - len(vals)))) + last
    try: return ipaddress.ip_address(acc)
    except ValueError: return None

def is_private_host(host):
    """FIX v4: بررسی مشترک میزبان خصوصی/داخلی برای همهٔ URLهای کاربری.
    شامل: localhost، ::1، 0.0.0.0، دامنه‌های .local/.internal، RFC1918، loopback،
    link-local (169.254.x = metadata endpoints)، و هر IP غیرعمومی.
    FIX v5 (ARCH-08): قالب‌های جایگزین IP (دهدهی/هجدهی/شانزدهی/کوتاه) هم گرفته می‌شوند."""
    h = (host or "").strip("[]").lower().rstrip(".")
    if not h: return True
    if h in _PRIVATE_HOSTS or h in ("::1", "0.0.0.0") or h.endswith((".local", ".internal", ".localhost")): return True
    if re.match(r"^(127\.|10\.|192\.168\.|169\.254\.|0\.|172\.(1[6-9]|2\d|3[01])\.)", h): return True
    try:
        return not ipaddress.ip_address(h).is_global
    except ValueError:
        pass
    alt = _parse_ip_alt(h)
    if alt is not None: return not alt.is_global
    return False

def public_url(u):
    """آدرس بیرونی و امن است؟ DNS ناموفق یا هر IP غیرعمومی ⇒ رد می‌شود (SSRF fail-closed)."""
    try: p = urlparse(str(u))
    except Exception: return False
    if p.scheme not in ("http", "https") or not p.hostname: return False
    if is_private_host(p.hostname): return False
    try: addrs = [i[4][0] for i in socket.getaddrinfo(p.hostname.rstrip(".").lower(), None, type=socket.SOCK_STREAM)]
    except Exception: return False
    if not addrs: return False
    for a in addrs:
        try: ip = ipaddress.ip_address(a)
        except Exception: return False
        if not ip.is_global: return False
    return True

def public_url_strict(u):
    """FIX v4: رزولوشن دوباره‌ی DNS با بررسی سازگاری.
    دو رزولوشن مستقل باید مجموعه‌ی IP یکسان و تماماً عمومی بدهند؛ وگرنه رد می‌شود.
    این کار پنجره‌ی DNS rebinding را از «نامحدود» به میکروثانیه کاهش می‌دهد و
    جابه‌جایی رکورد در همان لحظه‌ی بررسی را هم تشخیص می‌دهد."""
    if not public_url(u): return False
    try:
        host = urlparse(str(u)).hostname.rstrip(".").lower()
        a1 = sorted({i[4][0] for i in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)})
        a2 = sorted({i[4][0] for i in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)})
    except Exception: return False
    if not a1 or a1 != a2: return False
    for a in a1:
        try: ip = ipaddress.ip_address(a)
        except Exception: return False
        if not ip.is_global: return False
    return True

class FetchError(RuntimeError):
    """خطای HTTP با کد وضعیت؛ برای پیام اختصاصی ۴۰۳ هنگام افزودن منبع."""
    def __init__(self, status, url=""): super().__init__(f"HTTP {status}"); self.status = int(status); self.url = url

async def fetch(url, **kw):
    """GET امن با redirect محدود؛ هر مقصد redirect دوباره اعتبارسنجی و اندازه پاسخ واقعاً محدود می‌شود."""
    current = str(url)
    req_kw = dict(kw); req_kw.pop("follow_redirects", None)
    headers = dict(req_kw.pop("headers", {}) or {})
    history = []
    for _ in range(6):
        if not await asyncio.to_thread(public_url_strict, current): raise FetchError(403, current)
        previous_host = urlparse(current).hostname or ""
        async with FETCH_SEM:
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
    q("INSERT INTO deeplinks(key,admin_id,created_at,local_json) VALUES(?,?,?,?)", (key, admin_id, now_iso(), data), commit=True)
    return key

async def store_deeplink(admin_id, payload):
    return await save_deeplink(gen_deeplink_key(), admin_id, payload)

async def load_deeplink(key):
    r = q("SELECT value FROM kv_cache WHERE key=?", (key,), one=True)   # ردیف‌های قدیمی همچنان خوانده می‌شوند
    if r: return json.loads(r["value"])
    row = q("SELECT local_json FROM deeplinks WHERE key=?", (key,), one=True); data = row["local_json"] if row and row["local_json"] else (await kv_get(key) if config.CF_ENABLED else None)
    return json.loads(data) if data else None
