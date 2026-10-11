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
    pos = _cut_pos
