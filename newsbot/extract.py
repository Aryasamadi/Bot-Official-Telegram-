# -*- coding: utf-8 -*-
"""استخراج متن مقاله — trafilatura → بلوک‌های HTML → محتوای فید (در ترد جدا تا حلقهٔ رویداد مسدود نشود).
کمک‌های رسانه (_mk_media/_bad_media_url/_ext_kind) از discover.py می‌آیند (یک منبع حقیقت).
"""
import asyncio, json, re
from datetime import datetime
from urllib.parse import urljoin
import trafilatura
from bs4 import BeautifulSoup
from newsbot import config
from newsbot.discover import _get, _mk_media, _bad_media_url, _ext_kind, clean_url
from newsbot.timeutils import parse_dt


def _ld_image(soup):
    for sc in soup.find_all("script", attrs={"type": re.compile("ld\\+json", re.I)})[:6]:
        try: data = json.loads(sc.string or sc.get_text() or "{}")
        except Exception: continue
        nodes = data if isinstance(data, list) else [data]
        if isinstance(data, dict) and isinstance(data.get("@graph"), list): nodes = data["@graph"]
        for node in nodes:
            if not isinstance(node, dict): continue
            c = node.get("image") or node.get("thumbnailUrl")
            if isinstance(c, dict): c = c.get("url")
            if isinstance(c, list) and c: c = c[0].get("url") if isinstance(c[0], dict) else c[0]
            if isinstance(c, str) and c.strip(): return c.strip()
    return ""

def _img_src(tag):
    u = tag.get("src") or tag.get("data-src") or tag.get("data-original") or tag.get("data-lazy-src") or ""
    if not u:
        ss = tag.get("srcset") or tag.get("data-srcset") or ""
        u = ss.split(",")[0].strip().split(" ")[0] if ss else ""
    return u

def _meta(soup, *names, base=None):
    """مقدار یک meta را با اولویت property و سپس name برمی‌گرداند؛ اگر base داده شود آدرس مطلق می‌شود."""
    for n in names:
        t = soup.find("meta", property=n) or soup.find("meta", attrs={"name": n})
        if t and t.get("content"): return urljoin(base, t["content"].strip()) if base else t["content"].strip()
    return ""

def _alt_media(soup, base, primary, n=2):
    """تا n تصویر جانشین دیگر از همان صفحه (برای وقتی که منبع اصلی قابل دریافت نیست)."""
    out, seen = [], {str(primary).split("?")[0]}
    for tag in soup.find_all("img")[:40]:
        u = _img_src(tag)
        if not u: continue
        u = urljoin(base, u)
        if _bad_media_url(u) or u.split("?")[0] in seen: continue
        try: w = int(re.sub(r"\D", "", str(tag.get("width") or "0")) or 0)
        except Exception: w = 0
        if w and w < 400: continue
        if not re.search(r"\.(jpe?g|png|webp|gif)(?=$|\?)", u, re.I) and w < 600: continue
        seen.add(u.split("?")[0]); out.append(u)
        if len(out) >= n: return out
    return out

def _find_media(soup, base):
    """ویدیو (og:video / <video>) → تصویر (og:image / twitter / link / JSON-LD / بزرگ‌ترین تصویر متن). لوگو و آواتار و پیکسل رد می‌شوند."""
    vt = (_meta(soup, "og:video:type", base=base) or "").lower(); v = _meta(soup, "og:video:secure_url", "og:video:url", "og:video", "twitter:player:stream", base=base)
    if v and (re.search(r"\.(mp4|mov|webm|m4v)(?=$|\?)", v, re.I) or "mp4" in vt or "video/" in vt):
        got = _mk_media(v, "video", base)
        if got: return got
    for tag in soup.find_all(["video", "source"]):
        s = tag.get("src") or tag.get("data-src") or ""
        if s and re.search(r"\.(mp4|webm|mov|m4v)(?=$|\?)", s, re.I):
            got = _mk_media(urljoin(base, s), "video", base)
            if got: return got
    img = _meta(soup, "og:image:secure_url", "og:image", "twitter:image", "twitter:image:src", base=base)
    if not img:
        l = soup.find("link", rel="image_src"); img = urljoin(base, l["href"]) if l and l.get("href") else ""
    if not img:
        c = _ld_image(soup); img = urljoin(base, c) if c else ""
    if not img:
        for tag in soup.find_all("img")[:60]:
            u = _img_src(tag)
            if not u: continue
            u = urljoin(base, u)
            if _bad_media_url(u): continue
            try: w = int(re.sub(r"\D", "", str(tag.get("width") or "0")) or 0)
            except Exception: w = 0
            if w >= 300 or re.search(r"\.(jpe?g|png|webp|gif)(?=$|\?)", u, re.I): img = u; break
    if img:
        got = _mk_media(img, _ext_kind(img, "photo"), base)     # .gif → animation · .mp4 → video (نه عکس ثابت)
        if got:
            alts = _alt_media(soup, base, img)                  # اگر عکس اصلی نیامد، نامزدهای بعدی
            if alts: got["alt"] = alts
            return got
    return None

def _soup_text(soup):
    """کل متن صفحه، نه فقط ابتدای آن: همهٔ بلوک‌های محتوایی با هم ادغام می‌شوند (پاراگراف، بولت، زیرتیتر) و تکراری‌ها حذف می‌شوند."""
    for t in soup(["script", "style", "noscript", "nav", "header", "footer", "aside", "form", "iframe"]): t.decompose()
    cands = soup.find_all(["article", "main"]) + [soup.find(attrs={"role": "main"})] + soup.find_all(attrs={"itemprop": "articleBody"}) + soup.find_all("div", class_=re.compile(r"(article|post|entry|content|body|story|text|news|detail)", re.I))[:30]
    def blocks(c):
        out = []
        for x in c.find_all(["p", "li", "h2", "h3", "h4", "blockquote"]):
            t = x.get_text(" ", strip=True)
            if not t: continue
            if x.name in ("h2", "h3", "h4"):
                if 3 <= len(t) <= 160: out.append(t)
            elif x.name == "li":
                if len(t) > 25: out.append("• " + t)
            elif len(t) > 25: out.append(t)
        return out
    seen, merged = set(), []
    for c in sorted([c for c in cands if c], key=lambda c: len(c.get_text(" ", strip=True)), reverse=True)[:6]:
        for t in blocks(c):
            k = t[:90]
            if k in seen: continue
            seen.add(k); merged.append(t)
    best = "\n\n".join(merged)
    if len(best) >= 400: return best
    alt = "\n\n".join(t for t in blocks(soup) if t)
    return max(best, alt, key=len)

def _next_page_url(soup, base):
    """صفحهٔ بعدی مقالات چندصفحه‌ای (فقط rel=next که استاندارد و بی‌خطر است)."""
    t = soup.find("link", rel="next") or soup.find("a", rel="next")
    u = (t.get("href") if t else "") or ""
    if not u: return ""
    u = urljoin(base, u.strip())
    return u if u.startswith("http") and clean_url(u) != clean_url(base) else ""

def _traf(raw, url):
    try: data = trafilatura.extract(raw, url=url, include_comments=False, include_tables=True, include_formatting=False, output_format="json", with_metadata=True, favor_recall=True)
    except Exception: data = None
    try: return json.loads(data) if data else {}
    except Exception: return {}

def _parse_page(raw, url):
    d = _traf(raw, url); soup = BeautifulSoup(raw, "lxml")
    title = (d.get("title") or _meta(soup, "og:title", "twitter:title") or (soup.h1.get_text(" ", strip=True) if soup.h1 else "") or (soup.title.string if soup.title and soup.title.string else "") or "").strip()
    pub = None
    if d.get("date"):
        try: pub = datetime.strptime(d["date"][:10], "%Y-%m-%d").replace(tzinfo=config.UTC).isoformat()
        except Exception: pub = None
    if not pub:
        for n in ("article:published_time", "og:published_time", "pubdate", "publishdate", "publish-date", "date", "DC.date.issued", "article:modified_time", "og:updated_time"):
            v = parse_dt(_meta(soup, n))
            if v: pub = v.isoformat(); break
    if not pub:
        t = soup.find("time", datetime=True); v = parse_dt(t["datetime"]) if t else None
        if not v:
            m = re.search(r'"datePublished"\s*:\s*"([^"]+)"', raw); v = parse_dt(m.group(1)) if m else None
        pub = v.isoformat() if v else None
    media = _find_media(soup, url); nxt = _next_page_url(soup, url); text = (d.get("text") or "").strip()
    soup_t = _soup_text(soup)
    if len(soup_t) > max(400, len(text) * 1.3): text = soup_t
    if len(text) < 250: text = max(text, soup_t, key=len)
    return {"title": re.sub(r"\s+", " ", title)[:300], "text": text, "published": pub, "media": media, "author": d.get("author"), "site": d.get("sitename"), "next": nxt}

def _html_to_text(h):
    if not h: return ""
    soup = BeautifulSoup(h, "lxml")
    for t in soup(["script", "style", "noscript"]): t.decompose()
    return re.sub(r"\n{3,}", "\n\n", soup.get_text("\n", strip=True))

def _merge_text(a, b):
    """چسباندن متن صفحهٔ بعدی بدون تکرار پاراگراف‌ها."""
    seen = {p.strip()[:90] for p in a.split("\n\n") if p.strip()}; out = [a]
    for p in b.split("\n\n"):
        k = p.strip()[:90]
        if len(k) < 20 or k in seen: continue
        seen.add(k); out.append(p.strip())
    return "\n\n".join(out)

async def extract_article(url, fallback_html=""):
    """کل متن خبر خوانده می‌شود: اگر مقاله چندصفحه‌ای باشد (rel=next) تا MAX_ARTICLE_PAGES صفحه دنبال و به هم دوخته می‌شود."""
    raw = None
    try:
        r = await _get(url, feed=False)
        if r.status_code == 200 and "html" in r.headers.get("content-type", "text/html").lower(): raw = r.text
    except Exception: pass
    page = await asyncio.to_thread(_parse_page, raw, url) if raw else {"title": "", "text": "", "published": None, "media": None, "author": None, "site": None, "next": ""}
    seen_pages, nxt, hops = {clean_url(url)}, page.get("next") or "", 0
    while nxt and hops < max(0, config.MAX_ARTICLE_PAGES - 1) and len(page["text"]) < config.MAX_ARTICLE_CHARS:
        hops += 1
        if clean_url(nxt) in seen_pages: break
        seen_pages.add(clean_url(nxt))
        try:
            r2 = await _get(nxt, feed=False)
            if r2.status_code != 200: break
            p2 = await asyncio.to_thread(_parse_page, r2.text, nxt)
        except Exception: break
        if not p2 or len(p2["text"]) < 120: break
        page["text"] = _merge_text(page["text"], p2["text"])
        if not page.get("media") and p2.get("media"): page["media"] = p2["media"]
        nxt = p2.get("next") or ""
    # محتوای فید (content:encoded) همیشه با متن صفحه ادغام می‌شود، نه فقط وقتی صفحه خالی است:
    # فیدها اغلب نسخهٔ کامل خبر را دارند و استخراج صفحه می‌تواند ناقص باشد. هدف: اجتماع محتوا.
    if fallback_html:
        fb = await asyncio.to_thread(_html_to_text, fallback_html)
        if len(fb) >= 120:
            page["text"] = _merge_text(page["text"], fb) if len(page["text"]) >= 120 else fb
    if len(page["text"]) < 120: return None
    page["url"] = url; page["pages"] = hops + 1; page["text"] = page["text"][:config.MAX_ARTICLE_CHARS]; return page
