# -*- coding: utf-8 -*-
"""HTML امن تلگرام، فرمت‌بندی، پاک‌سازی خروجی مدل و الگوهای ضد prompt-injection.
این ماژول هیچ وابستگی به لایه‌ی AI/UI ندارد؛ salvage_post برای تجزیه‌ی JSON
از import درون‌تابعی ai_models استفاده می‌کند تا چرخه‌ی import ساخته نشود.
"""
import html, re
from newsbot import config

ALLOWED = {"b", "strong", "i", "em", "u", "s", "del", "code", "pre", "a", "blockquote", "tg-spoiler", "span"}

# ── لایه‌ی دوم دفاع ضد prompt-injection (لایه‌ی اول قاعده‌ی UNTRUSTED DATA در پرامپت است) ──
INJ_RE = re.compile(r"(?im)(ignore|disregard|forget)[ \t]+(all[ \t]+|the[ \t]+|any[ \t]+)?(previous|above|prior|earlier)[ \t]+(instruction|instructions|prompt|prompts|rule|rules)|^[ \t]*(system|assistant|user|human|developer)[ \t]*:|<\|?[ \t]*(im_start|im_end|system|assistant|endoftext)[ \t]*\|?>|you[ \t]+are[ \t]+now[ \t]+|new[ \t]+instruction(s)?[ \t]*:")
INJ_RESIDUE = re.compile(r"(?i)(ignore|disregard)[ \t]+(all[ \t]+|the[ \t]+|any[ \t]+)?(previous|above|prior)[ \t]+(instruction|instructions|prompt|prompts)|</?article_data>|<\|?[ \t]*(im_start|im_end|endoftext)[ \t]*\|?>|\bsk-[A-Za-z0-9_-]{12,}")

def strip_injection(t):
    """متن منبع هرگز نباید شبیه دستور دیده شود؛ الگوها با […] جایگزین می‌شوند."""
    return INJ_RE.sub("[…]", t if isinstance(t, str) else str(t))

def sanitize_html(text):
    """Markdown/HTML آزاد → HTML مجاز تلگرام."""
    if not text: return ""
    text = str(text)
    text = re.sub(r"(?m)^[ \t][-–—_=*~•⸻]{3,}[ \t]*$", "", text)   # جداکننده‌های رباتیک (--- و امثال آن) حذف می‌شوند
    text = re.sub(r"```[a-zA-Z]*\n?", "", text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"__(.+?)__", r"<u>\1</u>", text)
    text = re.sub(r"~~(.+?)~~", r"<s>\1</s>", text)
    text = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<i>\1</i>", text)
    text = re.sub(r"\|\|(.+?)\|\|", r"<tg-spoiler>\1</tg-spoiler>", text)
    text = re.sub(r"^#{1,6}\s*(.+)$", r"<b>\1</b>", text, flags=re.M)
    text = re.sub(r"^\s*>\s?(.+)$", r"<blockquote>\1</blockquote>", text, flags=re.M)
    text = re.sub(r"^\s*[-*]\s+", "• ", text, flags=re.M)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I).replace("</p>", "\n\n").replace("<p>", "")
    text = re.sub(r"</?(ul|ol|div|h[1-6]|section|article)[^>]*>", "\n", text)
    text = re.sub(r"<li[^>]*>", "• ", text).replace("</li>", "\n")
    out, stack = [], []
    for part in re.split(r"(<[^>]+>)", text):
        m = re.match(r"<(/?)([a-zA-Z-]+)([^>]*)>", part)
        if not m:
            out.append(html.escape(html.unescape(part), quote=False)); continue
        closing, tag, attrs = m.group(1), m.group(2).lower(), m.group(3)
        tag = {"strong": "b", "em": "i", "del": "s"}.get(tag, tag)
        if tag not in ALLOWED: continue
        if tag == "span":
            if "tg-spoiler" not in attrs: continue
            tag = "tg-spoiler"
        if closing:
            if tag in stack:
                while stack:
                    t = stack.pop(); out.append(f"</{t}>")
                    if t == tag: break
        else:
            if tag == "a":
                h = re.search(r'href=["\']([^"\']+)["\']', attrs)
                if not h or not h.group(1).strip().lower().startswith(("http://", "https://", "tg://")): continue
                out.append(f'<a href="{html.escape(html.unescape(h.group(1).strip()), quote=True)}">')
            elif tag == "blockquote" and "expandable" in attrs:
                out.append("<blockquote expandable>")
            else:
                out.append(f"<{tag}>")
            stack.append(tag)
    while stack: out.append(f"</{stack.pop()}>")
    return tidy_html("".join(out))

def tidy_html(t):
    t = t.replace("\r", ""); t = re.sub(r"[ \t]+\n", "\n", t); t = re.sub(r"(?<!\n)\n?•", "\n•", t)
    t = re.sub(r"(</b>|</blockquote>)\n(?!\n)(?!•)", r"\1\n\n", t); t = re.sub(r"([.!?؟۔])\n(?!\n)(?!•)(?=\S)", r"\1\n\n", t)
    t = re.sub(r"\n{3,}", "\n\n", t); return t.strip()

def downgrade_html(t):
    """حذف قابلیت‌های خاص با حفظ متن و فرمت‌های معمولی (برای تلاش دوباره پس از خطای پارس تلگرام)."""
    t = re.sub(r"</?tg-emoji[^>]*>", "", t or ""); t = re.sub(r"</?tg-spoiler>", "", t); t = t.replace("<blockquote expandable>", "<blockquote>")
    return sanitize_html(t)

def strip_tags(t): return html.unescape(re.sub(r"<[^>]+>", "", t or ""))

def salvage_post(raw):
    """نگهبان نشت JSON — اگر پاسخ مدل قابل تجزیه نباشد، پاکت JSON حذف و فقط متن واقعی پست بازیابی می‌شود؛ هرگز JSON خام منتشر نمی‌شود."""
    from newsbot.ai_models import _scan_json, parse_json   # import درون‌تابعی: جلوگیری از چرخه‌ی text_format <-> ai_models
    if not raw: return ""
    t = re.sub(r"```[a-zA-Z]*\n?", "", str(raw)).strip()
    if not t.startswith("{"): return t
    _, end = _scan_json(t)
    j = parse_json(t[:end + 1] if end >= 0 else t)
    if isinstance(j, dict):
        p = str(j.get("post") or "").strip()
        if p: return p
        f = str(j.get("full") or "").strip()
        if f: return f
    if end >= 0:
        rest = t[end + 1:].strip()
        if len(strip_tags(rest)) >= 40: return rest
    if end < 0:   # شیء ناکامل (پاسخ قطع‌شده): متنِ بازِ آخرین کلید بازیابی می‌شود
        m = None
        for mm in re.finditer(r'":\s*"', t): m = mm
        if m:
            rest = t[m.end():].strip()
            if len(strip_tags(rest)) >= 40: return rest
    lines = [ln for ln in t.splitlines() if not re.match(r'^\s*"?[A-Za-z_][A-Za-z_0-9]{2,24}"?\s*:', ln) and not re.fullmatch(r'\s*[{}\[\],]\s*', ln)]
    return "\n".join(lines).strip() or t

def _cut_pos(text, limit):
    """نقطه‌ی برش امن: پایان پاراگراف، بعد پایان جمله، بعد پایان کلمه — هرگز وسط جمله رها نمی‌شود."""
    cut = text[:limit]; nl = cut.rfind("\n")
    if nl > limit * 0.5: return nl
    pos = -1
    for m in re.finditer(r'[.!?؟…]+["»”’)\]]?(?=</[a-zA-Z]|\s|$)', cut): pos = m.end()
    if pos > limit * 0.4: return pos
    sp = cut.rfind(" ")
    return sp if sp > limit * 0.4 else len(cut)

def split_post_html(text, limit):
    """پست را در مرز امن به دو بخش «کانال + ادامه» می‌شکند؛ بخش دوم برای «ادامه در ربات» است و متنِ بخش اول را تکرار نمی‌کند."""
    if len(text) <= limit: return text, ""
    pos = _cut_pos(text, limit); head_raw, rest_raw = text[:pos], text[pos:]
    if rest_raw[:1] not in (" ", "\n", "") and ">" in rest_raw[:80]:
        gt = rest_raw.find(">")
        if "<" not in rest_raw[:gt]: rest_raw = rest_raw[gt + 1:]   # تکه‌ی نیمه‌تمامِ یک تگ از ابتدای ادامه حذف می‌شود
    head = sanitize_html(re.sub(r"<[^>]*$", "", head_raw)).rstrip() + " …"
    # اگر برشِ فعلی (اگر وسطِ Quote واقع شده باشد، sanitize تگِ ناقص را حذف می‌کند) Quote را از بخشِ «کانال» برداشته باشد
    # و Quote در ادامهٔ «ادامه در ربات» مانده باشد، مرزِ برش را به پایانِ نخستین Quote می‌رانیم تا Quote با کانال بماند؛
    # Quote‌های بعدی و متنِ اضافی به بخشِ ربات می‌روند.
    if "<blockquote" not in head.lower() and "<blockquote" in text.lower():
        bq = text.lower().find("</blockquote>")
        if 0 <= bq and _cut_pos(text, limit) < bq:
            pos = bq + len("</blockquote>")
            head_raw, rest_raw = text[:pos], text[pos:]
            head = sanitize_html(re.sub(r"<[^>]*$", "", head_raw)).rstrip() + " …"
    rest = sanitize_html(rest_raw).strip()
    return head, rest

def fit_html(text, limit):
    if len(text) <= limit: return text, False
    return split_post_html(text, limit)[0], True

def clean_ai_text(t, s=None):
    """زباله‌های مدل حذف می‌شود: دیکشنری امتیازها («معیار»: ۹، ...) و تکه‌های JSON چسبیده به متن پست — هرگز به کانال یا ربات نمی‌رسد."""
    if not t: return t
    t = str(t).replace("\r\n", "\n").replace("\r", "\n").replace("\t", " ")
    t = re.sub(r"[^\S\n]{2,}", " ", t)   # اجرای طولانی فاصله/نویسهٔ نامرئی یک‌بار جمع می‌شود؛ وگرنه الگوی امتیازها عقب‌نشینی فاجعه‌بار می‌کند
    names = [str(c.get("name", "")).strip() for c in (s or {}).get("criteria", []) if c.get("name")]
    names += ["ارزش محتوایی", "غیرتبلیغاتی بودن", "کیفیت و کامل بودن", "ارتباط با موضوع کانال", "تازگی", "Content value", "Non-promotional", "Quality & completeness", "Relevance to channel topic", "Relevance", "Freshness"]
    num = r'(?:10|[0-9۰-۹])(?:[.,][0-9۰-۹])?'
    pat = re.compile(r'["“«\']?([^"”»:\n]{2,40}?)[\t ]*:[\t ]*["”»\']?[\t ]*' + num + r'[\t ]*[,،.؛;]?')
    def rep(m):
        label = m.group(1).strip()
        return "" if any(label and (label in n or n in label) for n in names) else m.group(0)
    prev, guard = None, 0
    while prev != t and guard < 6: prev = t; t = pat.sub(rep, t); guard += 1   # سقف تکرار: جلوگیری از حلقه‌ی طولانی روی متن‌های عجیب
    t = re.sub(r'(?m)^\s*["”»،,{}\[\].…؛:!؟]+\s*$\n?', "", t)   # خط‌های زباله‌ی JSON (مثل «"،» یا «10.» تنها)
    t = re.sub(r"\[([^\]\n]{1,160})\]((https?://[^)\s\"']{4,}))", lambda m: m.group(2) if m.group(1).strip() in m.group(2) else f'<a href="{m.group(2)}">{m.group(1).replace(chr(34), "")}</a>', t)   # لینک مارک‌داونی → لینک واقعی
    t = re.sub(r"\[([^\]\n]{0,160})\](\s*)", r"\1", t)
    t = re.sub(r"(?<![\w])['‘’]([\w؀-ۿ][\w؀-ۿ -]{0,38}[\w؀-ۿ]|[\w؀-ۿ])['‘’](?![\w])", r"\1", t)   # کوتیشن دور واژه‌ها حذف می‌شود
    EM = r"[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF]\ufe0f?"
    t = re.sub(rf"({EM})\1{{3,}}", r"\1\1", t)                    # فقط ایموجی‌های پشت‌سرهمِ افراطی جمع می‌شوند
    t = re.sub(rf"(?m)^[ \t]*{EM}(?:[ \t]|{EM})+$", "", t)          # خطی که تنها ایموجی است حذف می‌شود
    t = re.sub(r"(?m)^[ \t]+|[ \t]+$", "", t); t = re.sub(r"[ \t]{2,}", " ", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    parts = re.split(r"(<pre>.+?</pre>|<code>.+?</code>)", t, flags=re.S)
    for i in range(0, len(parts), 2): parts[i] = re.sub(r"(?<!\n)\n(?!\n)", "\n\n", parts[i])   # پاراگراف‌ها همیشه با خط خالی از هم جدا می‌شوند
    t = "".join(parts)
    blocks, seen, keep = t.split("\n\n"), set(), []
    for b in blocks:
        k = re.sub(r"<[^>]+>", "", b); k = re.sub(r"[\s‌]+", " ", k).strip().lower()
        if k and k in seen: continue   # پاراگراف تکراری (متن یکسان) حذف می‌شود
        if k: seen.add(k)
        keep.append(b)
    t = "\n\n".join(keep).strip()
    t = re.sub(r"""[\s"'.,;:!?،؛؟\[\]{}]+$""", "", t)
    return t

def strip_source_url(t, url):
    """قانون ثابت: لینک منبع هرگز نباید داخل متن پست دیده شود — هر اشاره‌ای به دامنه‌ی منبع (لینک خام یا <a>) حذف می‌شود؛ بقیه‌ی لینک‌ها دست‌نخورده می‌مانند."""
    if not t or not url: return t
    host = re.sub(r"^[a-zA-Z]+://(www\.)?", "", str(url)).split("/")[0].strip().lower()
    if not host or "." not in host: return t
    h = re.escape(host)
    t = re.sub(r'<a href="[^"]*' + h + r'[^"]*"[^>]*>.+?</a>', "", t, flags=re.I | re.S)
    t = re.sub(r"https?://[^\s<>\"']*" + h + r"[^\s<>\"']*", "", t, flags=re.I)
    t = re.sub(r"(?m)^[ \t]+$", "", t)
    return t

def finish_ok(t):
    """اگر متن ناگهان وسط جمله قطع شده باشد، نشانه‌ی ادامه (« …») می‌گذارد — جمله‌ی تمام‌شده دست نمی‌خورد."""
    if not t: return t
    e = re.sub(r"(</[a-zA-Z]+>|\s)+$", "", t)
    if e and re.search(r"[\w؀-ۿ،,؛:]$", e): return t.rstrip() + " …"
    return t

def split_html(text, limit=None):
    limit = limit or config.MSG_LIMIT
    if len(text) <= limit: return [text]
    chunks, cur = [], ""
    for para in text.split("\n"):
        if len(cur) + len(para) + 1 > limit: chunks.append(sanitize_html(cur)); cur = para
        else: cur = f"{cur}\n{para}" if cur else para
    if cur: chunks.append(sanitize_html(cur))
    return chunks

def preview_text(html_text, n=100):
    t = re.sub(r"\s+", " ", strip_tags(html_text).replace("\n", " ")).strip()
    if len(t) <= n: return html.escape(t)
    cut = t[:n]; sp = cut.rfind(" "); return html.escape(cut[:sp] if sp > n * 0.6 else cut) + " …"
