# -*- coding: utf-8 -*-
"""کشف مقاله — فید (RSS/Atom/RDF/JSON Feed) → سایت‌مپ → لینک‌های HTML → API سفارشی.
کمک‌های مشترک رسانه (_bad_media_url/_mk_media/_ext_kind) این‌جا定义 می‌شوند و
extract.py و publish.py از همین‌جا import می‌کنند (یک منبع حقیقت برای فیلتر رسانه).
"""
import asyncio, html, json, re
from datetime import timedelta
from urllib.parse import urljoin, urlparse, parse_qsl, urlencode, urlunparse
import feedparser
from bs4 import BeautifulSoup
from newsbot import config
from newsbot.net_http import fetch, FetchError, UA_FEED, retry_after_seconds
from newsbot.core_sources import normalize_url, source_ok, source_fail, get_source, set_source_active
from newsbot.text_format import strip_tags
from newsbot.timeutils import now_utc, parse_dt
from newsbot.ui_i18n import tr
from newsbot.ai_models import hostname

FEED_ACCEPT = "application/rss+xml, application/atom+xml, application/rdf+xml, application/xml;q=0.9, text/xml;q=0.9, */*;q=0.5"
FEED_PATHS = ("/feed", "/feed/", "/rss", "/rss/", "/rss.xml", "/feed.xml", "/atom.xml", "/index.xml", "/feeds/posts/default?alt=rss", "/?feed=rss2", "/feed/rss", "/feed/atom", "/rss/all", "/rss/news", "/news/rss", "/news/feed", "/blog/feed", "/fa/rss", "/en/rss", "/feeds")
SITEMAP_PATHS = ("/news-sitemap.xml", "/sitemap-news.xml", "/sitemap_news.xml", "/post-sitemap.xml", "/sitemap-posts.xml", "/sitemap.xml", "/sitemap_index.xml")
# PLB-08 (فاز ۷): پارامترهای tracking/سهمیه — دو نشر مختلفِ یک مقاله نباید دو آیتم شوند
_TRACK_PARAMS = ("utm_", "fbclid", "gclid", "dclid", "gbraid", "wbraid", "msclkid", "yclid", "igshid", "mc_", "ref", "smid",
                 "cmpid", "xtor", "ftag", "campaign", "wt_mc", "ref_src", "ito", "_hsenc", "_hsmi", "s_kwcid", "epik",
                 "s_cid", "adid", "at_", "share_id", "output", "amp")

def clean_url(u):
    """canonical‌سازی آدرس (PLB-08): حذف پارامترهای tracking، پیشوند www.، پورت پیش‌فرض، fragment
    و پسوند مسیر /amp — تا URLهای مختلفِ یک مقاله یک hash/Dedup واحد بدهند."""
    try:
        p = urlparse(str(u).strip())
        qs = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if not k.lower().startswith(_TRACK_PARAMS)]
        netloc = p.netloc
        try:
            host = (p.hostname or "").lower().rstrip(".")
            if host.startswith("www."): host = host[4:]
            if host:
                cred = ""
                if p.username or p.password: cred = (p.username or "") + ((":" + p.password) if p.password else "") + "@"
                port = p.port
                keep = port and not ((p.scheme.lower() == "http" and port == 80) or (p.scheme.lower() == "https" and port == 443))
                netloc = cred + host + (f":{port}" if keep else "")
        except Exception: pass
        path = re.sub(r"/amp/?$", "", p.path, flags=re.I) or "/"
        return normalize_url(urlunparse(p._replace(scheme=p.scheme.lower(), netloc=netloc, path=path, params="", query=urlencode(qs), fragment="")))
    except Exception: return normalize_url(u)

def _struct_dt(st):
    try: return datetime_from_struct(st[:6]) if st else None
    except Exception: return None

def datetime_from_struct(t):
    from datetime import datetime
    return datetime(*t, tzinfo=config.UTC).isoformat()

def _entry_dt(e, tz=None):
    """تاریخ آیتم فید. struct_timeها UTC هستند (feedparser در تبدیل با تقویم خودش UTC می‌سازد)،
    ولی رشته‌های بی‌tz با timezone کانال برچسب می‌خورند (ARCH-11: «ساعت محلی سایت» = tz منبع)."""
    for k in ("published_parsed", "updated_parsed", "created_parsed"):
        d = _struct_dt(e.get(k))
        if d: return d
    for k in ("published", "updated", "dc_date", "date"):
        d = parse_dt(e.get(k), tz)
        if d: return d.isoformat()
    return None

# ── کمک‌های مشترک رسانه (فیلتر + سازنده + نوع از روی پسوند) ──
BAD_MEDIA = re.compile(r"(logo|sprite|placeholder|avatar|profile|icon|favicon|blank|default|noimage|no-image|pixel|spacer|1x1|advert|banner|watermark)", re.I)

def _bad_media_url(u):
    """رسانه‌ی نامرتبط (لوگو، آواتار، بنر، پیکسل شفاف…) نباید به‌عنوان تصویر مقاله فرستاده شود."""
    if not u or not str(u).startswith("http"): return True
    p = urlparse(str(u)).path.lower()
    if p.endswith((".svg", ".ico")): return True
    return bool(BAD_MEDIA.search(p))

def _mk_media(u, kind, page=""):
    return None if _bad_media_url(u) else {"url": str(u), "kind": kind, "page": page or ""}

def _ext_kind(u, fallback="photo"):
    e = os_path_ext(u)
    if e in (".mp4", ".m4v", ".mov", ".webm"): return "video"
    if e == ".gif": return "animation"
    if e in (".jpg", ".jpeg", ".png", ".webp", ".bmp"): return "photo"
    return fallback

def os_path_ext(u):
    import os
    return os.path.splitext(urlparse(str(u)).path)[1].lower()

def _entry_media(e, page=""):
    vids = []
    for m in (e.get("media_content") or []) + (e.get("media_thumbnail") or []):
        u = m.get("url"); t = (m.get("type") or "").lower()
        if u and ("video" in t or re.search(r"\.(mp4|webm|mov|m4v)(?=$|\?)", u, re.I)): vids.append(u)
        if u and (m.get("medium") == "image" or "image" in t or re.search(r"\.(jpe?g|png|webp|gif)(?=$|\?)", u, re.I)):
            got = _mk_media(u, "animation" if u.lower().split("?")[0].endswith(".gif") else "photo", page)
            if got: return got
    for enc in e.get("enclosures") or []:
        u = enc.get("href") or enc.get("url"); t = (enc.get("type") or "").lower()
        if u and t.startswith("video/"): vids.append(u)
        if u and t.startswith("image/"):
            got = _mk_media(u, "photo", page)
            if got: return got
    h = _entry_html(e)
    for m in re.finditer(r'<img[^>]+src=["\']([^"\']+)["\']', h or ""):
        got = _mk_media(m.group(1), "photo", page)
        if got: return got
    for u in vids:
        got = _mk_media(u, "video", page)
        if got: return got
    return None

def _entry_html(e):
    c = e.get("content")
    if c and isinstance(c, list) and c[0].get("value"): return c[0]["value"]
    return e.get("summary") or e.get("description") or ""

def _feed_items(feed, base, limit, tz=None):
    items, seen = [], set()
    for e in feed.entries:
        link = e.get("link") or next((l.get("href") for l in e.get("links", []) if l.get("rel") == "alternate"), None) or e.get("id") or ""
        if not link: continue
        if not str(link).startswith("http"): link = urljoin(base, link)
        link = clean_url(link)
        if link in seen or not urlparse(link).netloc: continue
        seen.add(link); items.append({"url": link, "title": re.sub(r"\s+", " ", strip_tags(e.get("title") or "")).strip(), "published": _entry_dt(e, tz), "html": _entry_html(e), "media": _entry_media(e, link)})
    items.sort(key=lambda x: x["published"] or "", reverse=True)
    return items[:limit]

def _sitemap_items(xml, limit, tz=None):
    out, since = [], now_utc() - timedelta(days=7)   # مرز زمانی به‌صورت datetime آگاهانه نگه داشته می‌شود
    for m in re.finditer(r"<url>(.*?)</url>", xml, re.S):
        blk = m.group(1); loc = re.search(r"<loc>\s*(.*?)\s*</loc>", blk, re.S)
        if not loc: continue
        d = re.search(r"<(?:news:)?publication_date>\s*(.*?)\s*<", blk) or re.search(r"<lastmod>\s*(.*?)\s*</lastmod>", blk)
        t = re.search(r"<news:title>\s*(.*?)\s*</news:title>", blk, re.S)
        pub = parse_dt(d.group(1), tz) if d else None
        if pub and pub < since: continue   # مقایسهٔ datetime آگاهانه (نه رشتهٔ ISO) تا اختلاف آفست در مرز ۷ روز، آیتم را اشتباهاً حذف نکند
        out.append({"url": clean_url(html.unescape(loc.group(1))), "title": html.unescape(strip_tags(t.group(1))).strip() if t else "", "published": pub.isoformat() if pub else None, "html": "", "media": None})
    out.sort(key=lambda x: x["published"] or "", reverse=True); return out[:limit]

def _json_feed_items(content, base, limit, tz=None):
    """فید JSON (JSON Feed: content-type application/feed+json) با کلیدهای version/title/items[]."""
    try: j = json.loads(content.decode("utf-8", "ignore") if isinstance(content, (bytes, bytearray)) else content)
    except Exception: return None
    if not isinstance(j, dict) or not isinstance(j.get("items"), list): return None
    out, seen = [], set()
    for d in j["items"]:
        if not isinstance(d, dict): continue
        u = str(d.get("url") or d.get("external_url") or d.get("id") or "").strip()
        if not u.startswith("http"): u = urljoin(base, u) if u.startswith("/") else ""
        if not u.startswith("http"): continue
        u = clean_url(u)
        if u in seen or not urlparse(u).netloc: continue
        seen.add(u)
        pd = parse_dt(d.get("date_published") or d.get("date_modified"), tz)
        body = d.get("content_html") or d.get("content_text") or d.get("summary") or ""
        out.append({"url": u, "title": re.sub(r"\s+", " ", strip_tags(d.get("title") or "")).strip(),
                    "published": pd.isoformat() if pd else None, "html": body if "<" in body else html.escape(body), "media": None})
    out.sort(key=lambda x: x["published"] or "", reverse=True); return out[:limit]

def _parse_any(content, base, limit, tz=None):
    """بایت‌های پاسخ → آیتم‌ها (فید RSS/JSON یا سایت‌مپ) یا None"""
    head = content[:4096].lower()
    if b"<sitemapindex" in head: return None
    if b"<urlset" in head: return _sitemap_items(content.decode("utf-8", "ignore"), limit, tz) or None
    if b"<html" in head and not (b"<rss" in head or b"<feed" in head or b"<rdf" in head): return None
    if head.lstrip()[:1] in (b"{", b"["):
        jf = _json_feed_items(content, base, limit, tz)
        if jf: return jf
    try: f = feedparser.parse(content)
    except Exception: return None
    return _feed_items(f, base, limit, tz) if f.entries else None

async def _get(url, cond=None, feed=False):
    h = {"Accept": FEED_ACCEPT} if feed else {}
    if cond: h.update(cond)
    r = await fetch(url, headers=h)
    if r.status_code in (403, 406, 429, 503):
        p = urlparse(url); ref = f"{p.scheme}://{p.netloc}/"
        # PLB-10 (فاز ۷): برای 429/503 به Retry-After سرور احترام می‌گذاریم (با سقف FEED_RETRY_AFTER_MAX)
        # تا بنر «بیش از حد درخواست» با تلاش فوری دوباره تشدید نشود؛ 403/406 فوری می‌ماند (فیلتر محتوایی است، نه نرخ).
        delay = retry_after_seconds(r.headers.get("retry-after")) if r.status_code in (429, 503) else None
        for extra in ({"User-Agent": UA_FEED}, {"Referer": ref, "Accept-Language": "en-US,en;q=0.9,fa;q=0.8", "Sec-Fetch-Mode": "navigate"}):
            try:
                if delay: await asyncio.sleep(min(delay, config.FEED_RETRY_AFTER_MAX))
                r2 = await fetch(url, headers={**h, **extra})
                if r2.status_code == 200: return r2
                if r2.status_code in (429, 503): delay = retry_after_seconds(r2.headers.get("retry-after"))
            except Exception: pass
    return r

def _html_links(soup, base_url, limit):
    base = hostname(base_url); scored, seen = [], set()
    for a in soup.find_all("a", href=True):
        try: href = clean_url(urljoin(base_url, a["href"]))
        except Exception: continue
        p = urlparse(href); path = p.path.strip("/")
        if hostname(href) != base or href in seen or href.rstrip("/") == base_url.rstrip("/") or len(path) < 8: continue
        if re.search(r"^(tag|tags|category|categories|author|page|login|search|feed|rss|wp-|about|contact|privacy|terms)(/|$)|/(tag|category|author|page)/|\.(jpe?g|png|gif|pdf|mp4|zip|xml)$", path, re.I): continue
        title = a.get_text(" ", strip=True)
        if not title and a.img: title = (a.img.get("alt") or a.img.get("title") or "").strip()
        title = re.sub(r"\s+", " ", title)
        if len(title) < 12: continue
        sc = 0
        if re.search(r"\d{4}/\d{1,2}", path): sc += 3
        if re.search(r"/(news|article|articles|post|posts|blog|story|stories|\d{4,})(/|$)", path): sc += 2
        if a.find_parent(["article", "h1", "h2", "h3"]) or a.find(["h1", "h2", "h3"]): sc += 3
        if len(title) >= 25: sc += 2
        if "/" in path: sc += 1
        if re.search(r"-\w+-\w+", path): sc += 1
        if a.find_parent(["nav", "header", "footer", "aside"]): sc -= 3
        if sc < 4: continue
        seen.add(href); scored.append((sc, {"url": href, "title": title[:200], "published": None, "html": "", "media": None}))
    scored.sort(key=lambda x: -x[0]); return [x[1] for x in scored[:limit]]

# ── منابع API (برای سایت‌هایی که مدیر خودش API می‌دهد) — نقشه‌بردار عمومی: هر ساختار JSON را می‌فهمد ──
API_ARRAY_KEYS = ("articles", "items", "results", "data", "posts", "news", "response", "docs", "entries", "stories", "hits", "records", "list", "feed")

def _api_pick_list(j, depth=0):
    """آرایه‌ی مقالات را در هر ساختار JSON پیدا می‌کند (NewsAPI، GNews، WordPress، Strapi، سرویس‌های سفارشی…)."""
    if isinstance(j, list): return j if (j and isinstance(j[0], dict)) else []
    if not isinstance(j, dict) or depth > 4: return []
    for k in API_ARRAY_KEYS:
        v = j.get(k)
        if isinstance(v, list) and v and isinstance(v[0], dict): return v
        if isinstance(v, dict):
            got = _api_pick_list(v, depth + 1)
            if got: return got
    best = []
    for v in j.values():
        if isinstance(v, list) and v and isinstance(v[0], dict) and len(v) > len(best): best = v
        elif isinstance(v, dict) and not best:
            got = _api_pick_list(v, depth + 1)
            if got: best = got
    return best

def _api_first(d, *names):
    for n in names:
        v = d.get(n)
        if isinstance(v, dict): v = v.get("rendered") or v.get("url") or v.get("href") or v.get("src") or v.get("value") or v.get("full") or v.get("large") or v.get("source_url")
        if isinstance(v, list) and v: v = v[0].get("url") or v[0].get("src") or v[0].get("href") if isinstance(v[0], dict) else v[0]
        if isinstance(v, (str, int, float)) and str(v).strip(): return str(v).strip()
    return ""

def _api_items(raw, base, limit, tz=None):
    try: j = json.loads(raw) if isinstance(raw, (str, bytes, bytearray)) else raw
    except Exception: return []
    out, seen = [], set()
    for d in _api_pick_list(j):
        if not isinstance(d, dict): continue
        u = _api_first(d, "url", "link", "canonical_url", "webUrl", "web_url", "permalink", "guid", "source_url", "shortlink")
        if not u.startswith("http"): u = urljoin(base, u) if u.startswith("/") else ""
        if not u.startswith("http"): continue
        u = clean_url(u)
        if u in seen: continue
        seen.add(u)
        title = strip_tags(_api_first(d, "title", "headline", "name", "heading", "subject"))
        body = _api_first(d, "content", "body", "text", "description", "summary", "excerpt", "abstract", "trailText")
        pub = _api_first(d, "publishedAt", "published_at", "published", "pubDate", "date", "date_published", "created_at", "createdAt", "updated_at", "webPublicationDate")
        pd = parse_dt(pub, tz); img = _api_first(d, "urlToImage", "image", "image_url", "imageUrl", "thumbnail", "thumb", "enclosure", "cover", "featured_image", "picture", "media")
        vid = _api_first(d, "video", "video_url", "videoUrl", "mp4")
        media = (_mk_media(vid, "video", u) if vid else None) or (_mk_media(img, "photo", u) if img else None)
        out.append({"url": u, "title": re.sub(r"\s+", " ", title)[:300], "published": pd.isoformat() if pd else None, "html": body if "<" in body else html.escape(body), "media": media})
    out.sort(key=lambda x: x["published"] or "", reverse=True); return out[:limit]

def _has_api(src):
    try: return bool(src["api_url"])
    except Exception: return False

async def _api_discover(src, limit, tz=None):
    """کلید API هم داخل آدرس ({key}) و هم در هدرهای رایج فرستاده می‌شود تا با اکثر سرویس‌ها بی‌تنظیمِ اضافه کار کند."""
    api = str(src["api_url"]); key = (src["api_key"] or "").strip()
    url = api
    for ph in ("{key}", "{apikey}", "{api_key}", "{token}", "{APIKEY}"): url = url.replace(ph, key)
    h = {"Accept": "application/json, text/json, */*;q=0.5"}
    if key and key not in url: h.update({"Authorization": f"Bearer {key}", "X-Api-Key": key, "apikey": key})
    r = await fetch(url, headers=h)
    if r.status_code != 200: raise FetchError(r.status_code, url)
    items = await asyncio.to_thread(_api_items, r.text, url, limit, tz)
    if not items: raise RuntimeError("API: no items")
    source_ok(src["id"]); return items, True, "api"

async def discover_source(src, limit=25, use_cache=True, tz=None):
    """خروجی: (items, changed, method) — method: api | feed | sitemap | html ؛ changed=False یعنی 304 (بدون تغییر)
    tz (ARCH-11): منطقه‌ای که تاریخ‌های بی‌tz فید/سایت‌مپ/API با آن برچسب می‌خورند (منطقهٔ کانال)."""
    cond = {}
    if src["etag"]: cond["If-None-Match"] = src["etag"]
    if src["last_modified"]: cond["If-Modified-Since"] = src["last_modified"]
    try:
        if _has_api(src): return await _api_discover(src, limit, tz)
        if src["feed_url"] and use_cache:
            r = await _get(src["feed_url"], cond, feed=True)
            if r.status_code == 304: source_ok(src["id"], src["feed_url"], src["etag"], src["last_modified"]); return [], False, "feed"
            if r.status_code == 200:
                items = await asyncio.to_thread(_parse_any, r.content, src["feed_url"], limit, tz)
                if items: source_ok(src["id"], src["feed_url"], r.headers.get("etag"), r.headers.get("last-modified")); return items, True, "sitemap" if b"<urlset" in r.content[:4096].lower() else "feed"
        r = await _get(src["url"], feed=False)
        if r.status_code != 200: raise FetchError(r.status_code, src["url"])
        items = await asyncio.to_thread(_parse_any, r.content, src["url"], limit, tz)
        if items: source_ok(src["id"], src["url"], r.headers.get("etag"), r.headers.get("last-modified")); return items, True, "feed"
        soup = await asyncio.to_thread(BeautifulSoup, r.text, "lxml"); p = urlparse(src["url"]); origin = f"{p.scheme}://{p.netloc}"; cands = []
        for tag in soup.find_all("link", attrs={"type": re.compile(r"application/(rss|atom|rdf)\+xml|application/feed\+json", re.I)}):
            if tag.get("href"): cands.append(urljoin(src["url"], tag["href"]))
        for a in soup.find_all("a", href=True):
            h = urljoin(src["url"], a["href"])
            if re.search(r"(/|\.)(rss|feed|atom)s?(/|\.xml|\?|$)", h, re.I) and (hostname(h) == hostname(src["url"]) or "feedburner" in h or "feeds." in h): cands.append(h)
        if p.path.strip("/"): cands += [src["url"] + g for g in ("/feed", "/rss", "/feed/", "/rss.xml", "/atom.xml")]
        cands += [origin + g for g in FEED_PATHS]
        seen = set()
        for fu in [c for c in cands if not (c in seen or seen.add(c))][:14]:
            try: rr = await _get(fu, feed=True)
            except Exception: continue
            if rr.status_code == 200:
                items = await asyncio.to_thread(_parse_any, rr.content, fu, limit, tz)
                if items: source_ok(src["id"], fu, rr.headers.get("etag"), rr.headers.get("last-modified")); return items, True, "feed"
        smaps = list(SITEMAP_PATHS)
        try:
            rb = await fetch(origin + "/robots.txt", timeout=10)
            if rb.status_code == 200: smaps = [m.strip() for m in re.findall(r"(?im)^sitemap:\s*(\S+)", rb.text)][:3] + smaps
        except Exception: pass
        for sp in smaps[:9]:
            su = sp if sp.startswith("http") else origin + sp
            try: rr = await _get(su, feed=True)
            except Exception: continue
            if rr.status_code != 200: continue
            head = rr.content[:4096].lower()
            if b"<urlset" in head:
                items = await asyncio.to_thread(_sitemap_items, rr.content.decode("utf-8", "ignore"), limit, tz)
                if items: source_ok(src["id"], su); return items, True, "sitemap"
            elif b"<sitemapindex" in head:
                kids = re.findall(r"<sitemap>.*?<loc>\s*(.*?)\s*</loc>", rr.content.decode("utf-8", "ignore"), re.S)
                pref = [k for k in kids if re.search(r"news|post|article|blog|\d{4}", k, re.I)] or kids
                for ku in pref[-2:]:
                    try: rk = await _get(html.unescape(ku), feed=True)
                    except Exception: continue
                    if rk.status_code == 200 and b"<urlset" in rk.content[:4096].lower():
                        items = await asyncio.to_thread(_sitemap_items, rk.content.decode("utf-8", "ignore"), limit, tz)
                        if items: source_ok(src["id"], html.unescape(ku)); return items, True, "sitemap"
        items = await asyncio.to_thread(_html_links, soup, src["url"], limit)
        if items: source_ok(src["id"])
        else: source_fail(src["id"], "no_items")
        return items, True, "html"
    except Exception as e: source_fail(src["id"], e); raise

def _probe_status(e):
    """کد وضعیت HTTP را از هر خطایی که بالا آمده بیرون می‌کشد (۰ اگر HTTP نبود)."""
    st = getattr(e, "status", None)
    if isinstance(st, int): return st
    r = getattr(e, "response", None); st = getattr(r, "status_code", None)
    return int(st) if isinstance(st, int) else 0

async def probe_source(src, lang="fa", added=False):
    """تست بارگذاری؛ خطای موقت منبع را غیرفعال نمی‌کند و فقط پس از شکست‌های متوالی خاموش می‌شود."""
    off = tr(lang, "src_added_off" if added else "src_now_off")
    try:
        items, _, m = await discover_source(src, use_cache=False); err_key = None
    except Exception as e:
        items, m, err_key = None, None, "src_403" if _probe_status(e) in (401, 403, 406, 451) else "src_dead"
    if items: return True, tr(lang, "src_added", n=len(items), m=m)
    current = get_source(src["id"]); disabled = int((current["fail_count"] if current else 0) or 0) >= config.SOURCE_FAIL_LIMIT
    if disabled: set_source_active(src["id"], False, "active")
    return False, tr(lang, err_key or "src_dead") + (off if disabled else "")
