# -*- coding: utf-8 -*-
"""موتور قالب‌بندی مشترک پیام‌های ربات و پست‌های کانال.

این ماژول «ساختار» محتوا را از «ظاهر» جدا می‌کند:

  • ورودی: متن خام (HTML تلگرام، مارک‌داون یا متن ساده) به‌همراه نوع محتوا.
  • خروجی: یک ``Doc`` از بلوک‌های معنایی (تیتر، پاراگراف، فهرست، نقل‌قول،
    جدول، جداکننده، بخش بازشونده) که با دو رندرر بیرون داده می‌شود:
      - ``render_rich``  → HTML پیام بومی تلگرام (Rich Message): تیتر واقعی،
        جدول واقعی (``<table>``)، فهرست واقعی، ``<details>``، ``<hr/>``.
      - ``render_legacy`` → HTML کلاسیک تلگرام (``send_message``) که همان
        قالبِ فعلیِ پروژه را بازتولید می‌کند (تیتر بولد، «•»، ``<blockquote>``).
      - ``render_plain``  → متن ساده برای آخرین حالتِ fallback.

قراردادهای این ماژول:

  1. هیچ محتوایی حذف یا بازنویسی نمی‌شود؛ فقط گروه‌بندی و قالب‌بندی می‌شود.
  2. متنِ فارسی، نیم‌فاصله (U+200C)، ایموجی و لینک‌ها دست‌نخورده می‌مانند.
  3. هر کاراکترِ Markdown در متنِ ساده escape می‌شود تا به‌عنوان نشانه‌گذاری
     تفسیر نشود.
  4. اگر تجزیه چیزی را از قلم بیندازد، ``Doc.faithful`` False می‌شود و لایهٔ ارسال
     به‌جای پیام بومی، همان متنِ اصلی را با مسیرِ کلاسیک می‌فرستد.
"""
import html
import re

from newsbot.text_format import tidy_html, strip_tags, split_inline_html

RICH_CHAR_LIMIT = 32768
RICH_BLOCK_LIMIT = 500
RICH_COLUMN_LIMIT = 20
RICH_NEST_LIMIT = 16
SAFE_RICH_CHARS = 32000

KIND_NEWS = "news"
KIND_ANALYSIS = "analysis"
KIND_REVIEW = "review"
KIND_COMPARISON = "comparison"
KIND_STATS = "stats"
KIND_REPORT = "report"
KIND_SUMMARY = "summary"
KIND_WARNING = "warning"
KIND_PLAIN = "plain"

KINDS = (KIND_NEWS, KIND_ANALYSIS, KIND_REVIEW, KIND_COMPARISON, KIND_STATS,
         KIND_REPORT, KIND_SUMMARY, KIND_WARNING, KIND_PLAIN)

_INLINE_ALLOWED = {"b", "i", "u", "s", "code", "a", "tg-spoiler", "mark", "sub", "sup"}
_INLINE_ALIAS = {"strong": "b", "em": "i", "del": "s", "ins": "u", "strike": "s"}

_DIVIDER_RE = re.compile(r"^(?:-{3,}|_{3,}|\*{3,}|—{3,}|─{3,})$")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_BOLD_LINE_RE = re.compile(r"^<(?:b|strong)>(.+)</(?:b|strong)>$", re.I)
_QUOTE_RE = re.compile(r"^>\s?(.*)$")
_BULLET_RE = re.compile(r"^(?:[-*+•◦▸◆‣⁃▪])\s+(.*)$")
_NUMBER_RE = re.compile(r"^([0-9۰-۹٠-٩]{1,3})[.)]\s+(.*)$")
_TABLE_SEP_RE = re.compile(r"^\|?\s*:?-{2,}:?\s*(?:\|\s*:?-{2,}:?\s*)*\|?$")
_HTML_TAG_RE = re.compile(r"</?[a-zA-Z][a-zA-Z0-9-]*(?:\s[^<>]*)?/?>")
_HTML_BLOCK_RE = re.compile(
    r"<blockquote\b[^>]*>.*?</blockquote\s*>"
    r"|<details\b[^>]*>.*?</details\s*>"
    r"|<table\b[^>]*>.*?</table\s*>"
    r"|<ul\b[^>]*>.*?</ul\s*>"
    r"|<ol\b[^>]*>.*?</ol\s*>"
    r"|<pre\b[^>]*>.*?</pre\s*>"
    r"|<h([1-6])\b[^>]*>.*?</h\1\s*>"
    r"|<p\b[^>]*>.*?</p\s*>"
    r"|<hr\s*/?>",
    re.I | re.S)
_LINK_RE = re.compile(r"\[([^\]\n]{1,300})\]\(((?:https?://|tg://)[^\s)]{1,600})\)")
_RTL_RE = re.compile(r"[\u0590-\u08FF\uFB1D-\uFDFF\uFE70-\uFEFF]")
_BIDI_CTRL_RE = re.compile(r"[\u200e\u200f\u202a-\u202e\u2066-\u2069]")


class Heading:
    """تیترِ ساختاریافته (``<h1>`` … ``<h6>`` در پیام بومی)."""

    kind = "heading"

    def __init__(self, text, level=2):
        self.text = text
        self.level = max(1, min(6, int(level or 2)))


class Para:
    """پاراگراف؛ ``text`` همان HTML درون‌خطیِ امن است."""

    kind = "para"

    def __init__(self, text):
        self.text = text


class ListBlock:
    """فهرستِ نقطه‌ای یا شماره‌دار."""

    kind = "list"

    def __init__(self, items, ordered=False):
        self.items = [i for i in (items or []) if str(i).strip()]
        self.ordered = bool(ordered)


class Quote:
    """نقل‌قولِ بلوکی (همان ``<blockquote>`` تلگرام)."""

    kind = "quote"

    def __init__(self, text, expandable=False):
        self.text = text
        self.expandable = bool(expandable)


class TableBlock:
    """جدول؛ در پیام بومی به ``<table>`` واقعی تبدیل می‌شود."""

    kind = "table"

    def __init__(self, headers, rows):
        self.headers = [str(h) for h in (headers or [])]
        self.rows = [[str(c) for c in row] for row in (rows or [])]


class Divider:
    """جداکنندهٔ افقی (``<hr/>``)."""

    kind = "divider"


class Details:
    """بخشِ بازشونده (``<details>``) با بدنهٔ بلوک‌ها."""

    kind = "details"

    def __init__(self, summary, body=None):
        self.summary = summary
        self.body = list(body or [])


class Pre:
    """بلوکِ کدِ ازپیش‌قالب‌بندی‌شده."""

    kind = "pre"

    def __init__(self, text, lang=""):
        self.text = text
        self.lang = lang or ""


class Doc:
    """مجموعهٔ بلوک‌ها + نوع محتوا + پرچمِ وفاداری به متنِ ورودی."""

    def __init__(self, blocks=None, kind=KIND_PLAIN, faithful=True):
        self.blocks = list(blocks or [])
        self.kind = kind if kind in KINDS else KIND_PLAIN
        self.faithful = faithful

    def __iter__(self):
        return iter(self.blocks)

    def __len__(self):
        return len(self.blocks)

    def __bool__(self):
        return bool(self.blocks)

    def plain(self):
        return render_plain(self)


def detect_rtl(text):
    """آیا متن عمدتاً راست‌به‌چپ است؟ (برای پرچم ``is_rtl`` پیام بومی)"""
    s = str(text or "")
    if not s:
        return False
    rtl = len(_RTL_RE.findall(s))
    latin = len(re.findall(r"[A-Za-z]", s))
    return rtl > 0 and rtl * 2 >= latin


def _extract_links(text):
    """لینک‌های مارک‌داون را جدا می‌کند تا در هر دو مسیر (HTML و متنِ ساده) سالم بمانند."""
    links = []

    def _grab(m):
        links.append((m.group(1), m.group(2)))
        return "\x00L%d\x00" % (len(links) - 1)

    return _LINK_RE.sub(_grab, str(text)), links


def _restore_links(text, links):
    for i, (label, url) in enumerate(links):
        text = text.replace("\x00L%d\x00" % i,
                            '<a href="%s">%s</a>' % (html.escape(url, quote=True),
                                                    html.escape(label, quote=False)))
    return text


def inline_html(text):
    """متنِ ساده/مارک‌داون → HTML درون‌خطیِ امن تلگرام.

    escape کامل انجام می‌شود؛ سپس نشانه‌های مارک‌داونِ شناخته‌شده به تگ تبدیل
    می‌شوند. هیچ کاراکترِ ویژه‌ای (Markdown، پیکان، نیم‌فاصله) از بین نمی‌رود.
    """
    if text is None:
        return ""
    s, links = _extract_links(text)
    s = html.escape(s, quote=False)
    s = re.sub(r"`([^`\n]{1,300})`", r"<code>\1</code>", s)
    s = re.sub(r"\*\*([^*\n]{1,600})\*\*", r"<b>\1</b>", s)
    s = re.sub(r"__([^_\n]{1,600})__", r"<u>\1</u>", s)
    s = re.sub(r"~~([^~\n]{1,600})~~", r"<s>\1</s>", s)
    s = re.sub(r"(?<![\w*])\*(?!\s)([^*\n]{1,600}?)(?<!\s)\*(?![\w*])", r"<i>\1</i>", s)
    s = re.sub(r"\|\|([^|\n]{1,600})\|\|", r"<tg-spoiler>\1</tg-spoiler>", s)
    return _restore_links(s, links)


def sanitize_inline_html(text):
    """HTML موجود را به تگ‌های درون‌خطیِ مجازِ تلگرام محدود می‌کند."""
    if not text:
        return ""
    out, stack = [], []
    for part in re.split(r"(<[^>]+>)", str(text)):
        m = re.match(r"<(/?)([a-zA-Z-]+)([^>]*)>", part)
        if not m:
            out.append(html.escape(html.unescape(part), quote=False))
            continue
        closing, tag, attrs = m.group(1), m.group(2).lower(), m.group(3)
        tag = _INLINE_ALIAS.get(tag, tag)
        if tag not in _INLINE_ALLOWED:
            continue
        if closing:
            if tag in stack:
                while stack:
                    t = stack.pop()
                    out.append("</%s>" % t)
                    if t == tag:
                        break
            continue
        if tag == "a":
            h = re.search(r'href=["\']([^"\']+)["\']', attrs)
            if not h:
                continue
            url = html.unescape(h.group(1).strip())
            if not url.lower().startswith(("http://", "https://", "tg://")):
                continue
            out.append('<a href="%s">' % html.escape(url, quote=True))
        else:
            out.append("<%s>" % tag)
        stack.append(tag)
    while stack:
        out.append("</%s>" % stack.pop())
    return "".join(out)


def normalize_inline(text):
    """اگر ورودی HTML است فقط تگ‌های درون‌خطیِ مجاز نگه داشته می‌شود؛
    وگرنه متنِ ساده/مارک‌داون به HTML درون‌خطی تبدیل می‌شود.

    لینک‌های مارک‌داون در هر دو حالت به ``<a href>`` معتبر تبدیل می‌شوند.
    """
    if text is None:
        return ""
    s, links = _extract_links(text)
    if _HTML_TAG_RE.search(s):
        out = sanitize_inline_html(s)
    else:
        out = inline_html(s)
    return _restore_links(out, links)


def _split_row(line):
    s = str(line).strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    cells = [c.strip() for c in s.split("|")]
    return [normalize_inline(c) for c in cells]


def _parse_table(lines, i):
    headers = _split_row(lines[i])
    i += 2
    rows = []
    while i < len(lines):
        raw = lines[i]
        if not raw.strip() or "|" not in raw:
            break
        if _TABLE_SEP_RE.match(raw.strip()):
            i += 1
            continue
        rows.append(_split_row(raw))
        i += 1
    width = len(headers)
    for row in rows:
        width = max(width, len(row))
    headers = (headers + [""] * width)[:width]
    rows = [(row + [""] * width)[:width] for row in rows]
    return headers, rows, i


def _parse_html_table(raw):
    rows = []
    for tr in re.findall(r"<tr\b[^>]*>(.*?)</tr\s*>", raw, re.I | re.S):
        cells = re.findall(r"<(th|td)\b[^>]*>(.*?)</\1\s*>", tr, re.I | re.S)
        if cells:
            rows.append([normalize_inline(re.sub(r"<[^>]+>", "", c[1]).strip()) for c in cells])
    if not rows:
        return None
    headers = rows[0]
    body = rows[1:]
    width = max([len(headers)] + [len(r) for r in body]) if body else len(headers)
    headers = (headers + [""] * width)[:width]
    body = [(r + [""] * width)[:width] for r in body]
    return TableBlock(headers, body)


def _parse_html_list(raw, ordered):
    items = []
    for li in re.findall(r"<li\b[^>]*>(.*?)</li\s*>", raw, re.I | re.S):
        items.append(normalize_inline(re.sub(r"<[^>]+>", "", li).strip()))
    if not items:
        return None
    return ListBlock(items, ordered=ordered)


def _parse_details(raw):
    m = re.search(r"<summary\b[^>]*>(.*?)</summary\s*>", raw, re.I | re.S)
    summary = normalize_inline(re.sub(r"<[^>]+>", "", m.group(1)).strip()) if m else ""
    body_raw = re.sub(r"<summary\b[^>]*>.*?</summary\s*>", "", raw, flags=re.I | re.S)
    body_raw = re.sub(r"^<details\b[^>]*>|</details\s*>$", "", body_raw.strip(), flags=re.I)
    inner = _parse_body(body_raw)
    return Details(summary, inner)


def _parse_text_segment(text, out):
    lines = text.split("\n")
    i = 0
    buf = []

    def flush():
        if buf:
            out.append(Para(normalize_inline("\n".join(buf))))
            del buf[:]

    while i < len(lines):
        raw_line = lines[i]
        s = raw_line.strip()
        if not s:
            flush()
            i += 1
            continue
        if _DIVIDER_RE.match(s):
            flush()
            out.append(Divider())
            i += 1
            continue
        m = _HEADING_RE.match(s)
        if m:
            flush()
            out.append(Heading(normalize_inline(m.group(2)), len(m.group(1))))
            i += 1
            continue
        if "|" in s and i + 1 < len(lines) and _TABLE_SEP_RE.match(lines[i + 1].strip()) \
                and "|" in lines[i + 1]:
            flush()
            headers, rows, i = _parse_table(lines, i)
            out.append(TableBlock(headers, rows))
            continue
        m = _QUOTE_RE.match(s)
        if m:
            flush()
            qlines = [m.group(1)]
            i += 1
            while i < len(lines):
                mm = _QUOTE_RE.match(lines[i].strip())
                if not mm:
                    break
                qlines.append(mm.group(1))
                i += 1
            out.append(Quote(normalize_inline("\n".join(qlines))))
            continue
        m = _BULLET_RE.match(s)
        if m:
            flush()
            items = []
            while i < len(lines):
                mm = _BULLET_RE.match(lines[i].strip())
                if not mm:
                    break
                items.append(normalize_inline(mm.group(1)))
                i += 1
            if items:
                out.append(ListBlock(items, ordered=False))
            continue
        m = _NUMBER_RE.match(s)
        if m:
            flush()
            items = []
            while i < len(lines):
                mm = _NUMBER_RE.match(lines[i].strip())
                if not mm:
                    break
                items.append(normalize_inline(mm.group(2)))
                i += 1
            if items:
                out.append(ListBlock(items, ordered=True))
            continue
        m = _BOLD_LINE_RE.match(s)
        if m:
            flush()
            out.append(Heading(normalize_inline(m.group(1)), 2))
            i += 1
            continue
        buf.append(raw_line.rstrip())
        i += 1
    flush()


def _parse_body(text):
    out = []
    pos = 0
    for m in _HTML_BLOCK_RE.finditer(text or ""):
        if m.start() > pos:
            _parse_text_segment(text[pos:m.start()], out)
        raw = m.group(0)
        low = raw.lower()
        if low.startswith("<blockquote"):
            inner = re.sub(r"^<blockquote\b[^>]*>|</blockquote\s*>$", "", raw, flags=re.I)
            expandable = "expandable" in raw[:raw.find(">")].lower()
            out.append(Quote(normalize_inline(re.sub(r"<[^>]+>", "", inner).strip()), expandable))
        elif low.startswith("<details"):
            blk = _parse_details(raw)
            if blk:
                out.append(blk)
        elif low.startswith("<table"):
            blk = _parse_html_table(raw)
            if blk:
                out.append(blk)
        elif low.startswith("<ul"):
            blk = _parse_html_list(raw, False)
            if blk:
                out.append(blk)
        elif low.startswith("<ol"):
            blk = _parse_html_list(raw, True)
            if blk:
                out.append(blk)
        elif low.startswith("<pre"):
            inner = re.sub(r"^<pre\b[^>]*>|</pre\s*>$", "", raw, flags=re.I)
            inner = re.sub(r"^<code\b[^>]*>|</code\s*>$", "", inner, flags=re.I)
            out.append(Pre(html.escape(inner.strip(), quote=False)))
        elif low.startswith("<h"):
            inner = re.sub(r"^<h[1-6]\b[^>]*>|</h[1-6]\s*>$", "", raw, flags=re.I)
            out.append(Heading(normalize_inline(re.sub(r"<[^>]+>", "", inner).strip()), 2))
        elif low.startswith("<p"):
            inner = re.sub(r"^<p\b[^>]*>|</p\s*>$", "", raw, flags=re.I)
            out.append(Para(normalize_inline(inner.strip())))
        elif low.startswith("<hr"):
            out.append(Divider())
        pos = m.end()
    if pos < len(text or ""):
        _parse_text_segment(text[pos:], out)
    return out


def detect_kind(text):
    """نوع محتوا را حدس می‌زند؛ الگوها راهنما هستند نه قانون."""
    s = str(text or "")
    if not s.strip():
        return KIND_PLAIN
    low = s.lower()
    if re.search(r"(⚠️|🚨|❌|هشدار|اخطار|خطای|مرورگر|failed|error|warning)", low[:400]):
        return KIND_WARNING
    if re.search(r"(مزایا|معایب|نقاط\s+قوت|نقاط\s+ضعف|pros\b|cons\b)", low):
        return KIND_REVIEW
    if re.search(r"(نتیجه[\s‌]*گیری|جمع[\s‌]*بندی|conclusion|verdict|نتیجهٔ)", low):
        return KIND_SUMMARY
    has_table = bool(re.search(r"(?m)^\s*\|.*\|\s*$", s)) and bool(
        re.search(r"(?m)^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$", s))
    if has_table:
        if re.search(r"(?m)^\s*\|?\s*(?:[0-9۰-۹]{1,3}|رتبه|rank|#)\s*\|", s, re.I):
            return KIND_STATS
        return KIND_COMPARISON
    if re.search(r"(گزارش|report|آمار|stats|وضعیت\s+سیستم|system\s+status)", low):
        return KIND_REPORT
    if re.search(r"(تحلیل|analysis|بررسی|review|جمع[\s‌]بندی)", low):
        return KIND_ANALYSIS
    return KIND_NEWS


def _fingerprint_source(text):
    s = str(text or "")
    s = re.sub(r"<[^>]+>", "", s)
    s = re.sub(r"(?m)^\s*\|?\s*:?-{2,}:?\s*(?:\|\s*:?-{2,}:?\s*)*\|?\s*$", "", s)
    s = s.replace("|", " ")
    s = re.sub(r"\[([^\]\n]{1,300})\]\(((?:https?://|tg://)[^\s)]{1,600})\)", r"\1", s)
    s = re.sub(r"(?m)^\s*(?:-{3,}|_{3,}|\*{3,}|—{3,}|─{3,})\s*$", "", s)
    s = re.sub(r"(?m)^\s*#{1,6}\s+", "", s)
    s = re.sub(r"(?m)^\s*>\s?", "", s)
    s = re.sub(r"(?m)^\s*(?:[-*+•◦▸◆‣⁃▪]|[0-9۰-۹٠-٩]{1,3}[.)])\s+", "", s)
    s = re.sub(r"\*\*|__|~~|`|\|\||\*", "", s)
    s = s.replace("•", " ")
    return re.sub(r"\s+", "", html.unescape(s))


def _doc_text_parts(doc):
    parts = []
    for b in doc.blocks:
        if isinstance(b, (Heading, Para, Quote)):
            parts.append(strip_tags(b.text))
        elif isinstance(b, ListBlock):
            parts.extend(strip_tags(i) for i in b.items)
        elif isinstance(b, TableBlock):
            parts.extend(strip_tags(h) for h in b.headers)
            for row in b.rows:
                parts.extend(strip_tags(c) for c in row)
        elif isinstance(b, Details):
            parts.append(strip_tags(b.summary))
            parts.extend(_doc_text_parts(Doc(b.body)))
        elif isinstance(b, Pre):
            parts.append(b.text)
    return parts


def _fingerprint_doc(doc):
    s = "".join(_doc_text_parts(doc))
    return re.sub(r"\s+", "", html.unescape(s))


def parse_content(text, kind=None):
    """متن خام → ``Doc``.

    ``kind`` اختیاری است؛ اگر داده نشود از روی محتوا حدس زده می‌شود.
    ``Doc.faithful`` نشان می‌دهد همهٔ متنِ ورودی در بلوک‌ها آمده است یا نه.
    """
    raw = "" if text is None else str(text)
    blocks = _parse_body(raw)
    doc = Doc(blocks, kind=kind or detect_kind(raw))
    src = _fingerprint_source(raw)
    got = _fingerprint_doc(doc)
    doc.faithful = (not src) or (src in got)
    return doc


def _legacy_table_lines(block):
    lines = []
    if block.headers and any(h.strip() for h in block.headers):
        lines.append("<b>" + " · ".join(h for h in block.headers if h.strip()) + "</b>")
    for row in block.rows:
        cells = [c for c in row if str(c).strip()]
        if not cells:
            continue
        if len(cells) == 1:
            lines.append("• " + cells[0])
        else:
            lines.append("• <b>%s</b> — %s" % (cells[0], " · ".join(cells[1:])))
    return lines


def _blocks_of(doc):
    return doc.blocks if isinstance(doc, Doc) else list(doc or [])


def render_rich(doc, kind=None):
    """رندر HTML پیام بومی تلگرام (تیتر/جدول/فهرست/بخشِ بازشوندهٔ واقعی)."""
    out = []
    for b in _blocks_of(doc):
        if isinstance(b, Heading):
            lvl = b.level
            out.append("<h%d>%s</h%d>" % (lvl, b.text, lvl))
        elif isinstance(b, Para):
            out.append("<p>%s</p>" % b.text)
        elif isinstance(b, ListBlock):
            tag = "ol" if b.ordered else "ul"
            out.append("<%s>%s</%s>" % (tag, "".join("<li>%s</li>" % i for i in b.items), tag))
        elif isinstance(b, Quote):
            out.append("<blockquote%s>%s</blockquote>" % (" expandable" if b.expandable else "", b.text))
        elif isinstance(b, Divider):
            out.append("<hr/>")
        elif isinstance(b, TableBlock):
            head = ""
            heads = [h for h in b.headers if str(h).strip()]
            if heads:
                head = "<tr>%s</tr>" % "".join("<th>%s</th>" % h for h in heads)
            body = "".join("<tr>%s</tr>" % "".join("<td>%s</td>" % c for c in row) for row in b.rows)
            out.append("<table>%s%s</table>" % (head, body))
        elif isinstance(b, Details):
            out.append("<details><summary>%s</summary>%s</details>" % (b.summary, render_rich(b.body)))
        elif isinstance(b, Pre):
            lang = ' class="language-%s"' % b.lang if b.lang else ""
            out.append("<pre><code%s>%s</code></pre>" % (lang, b.text))
    return "\n".join(out)


def render_legacy(doc, kind=None):
    """رندر HTML کلاسیک تلگرام؛ قالبِ فعلیِ پروژه را بازتولید می‌کند."""
    out = []
    for b in _blocks_of(doc):
        if isinstance(b, Heading):
            out.append("<b>%s</b>" % b.text)
        elif isinstance(b, Para):
            out.append(b.text)
        elif isinstance(b, ListBlock):
            if b.ordered:
                out.append("\n".join("%d. %s" % (n, it) for n, it in enumerate(b.items, 1)))
            else:
                out.append("\n".join("• %s" % it for it in b.items))
        elif isinstance(b, Quote):
            out.append("<blockquote%s>%s</blockquote>" % (" expandable" if b.expandable else "", b.text))
        elif isinstance(b, Divider):
            out.append("────────")
        elif isinstance(b, TableBlock):
            out.extend(_legacy_table_lines(b))
        elif isinstance(b, Details):
            out.append("<b>%s</b>" % b.summary)
            out.append(render_legacy(b.body))
        elif isinstance(b, Pre):
            out.append("<pre>%s</pre>" % b.text)
    return tidy_html("\n\n".join(x for x in out if x is not None))


def render_plain(doc, kind=None):
    """متنِ ساده؛ برای fallbackِ نهایی و برای مقایسهٔ وفاداری."""
    return strip_tags(render_legacy(doc))


def _split_inline(text, limit):
    parts = split_inline_html(text or "", limit)
    return parts or [text or ""]


def split_doc(doc, limit=SAFE_RICH_CHARS, render=render_rich):
    """تقسیمِ سند به چند سندِ کوچک‌تر بدون از دست رفتن محتوا."""
    if limit <= 0:
        limit = SAFE_RICH_CHARS
    parts, cur, cur_len = [], [], 0
    for b in doc.blocks:
        one = len(render([b]))
        if cur and cur_len + one > limit:
            parts.append(cur)
            cur, cur_len = [], 0
        if one > limit and isinstance(b, (Para, Quote, Heading)):
            for piece in _split_inline(b.text, max(64, limit - 32)):
                if isinstance(b, Heading):
                    nb = Heading(piece, b.level)
                elif isinstance(b, Quote):
                    nb = Quote(piece, b.expandable)
                else:
                    nb = Para(piece)
                cur.append(nb)
                cur_len += len(render([nb]))
                if cur_len >= limit:
                    parts.append(cur)
                    cur, cur_len = [], 0
            continue
        cur.append(b)
        cur_len += one
    if cur:
        parts.append(cur)
    out = []
    for part in parts:
        d = Doc(part, kind=doc.kind, faithful=doc.faithful)
        out.append(d)
    return out or [Doc([], kind=doc.kind, faithful=doc.faithful)]


def rich_html_for(text, kind=None):
    """میان‌بر: متن → HTML پیام بومی (یا رشتهٔ خالی اگر تجزیه وفادار نبود)."""
    doc = parse_content(text, kind=kind)
    if not doc.faithful or not doc.blocks:
        return ""
    return render_rich(doc)


def legacy_html_for(text, kind=None):
    """میان‌بر: متن → HTML کلاسیک."""
    return render_legacy(parse_content(text, kind=kind))
