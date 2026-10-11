# -*- coding: utf-8 -*-
"""فیلتر تبلیغ — چندزبانه، منطبق با پرشمارترین زبان‌های کاربران تلگرام.
فیلتر نرم‌شده: یک کلمه‌ی معمولی به‌تنهایی خبر را رد نمی‌کند؛ فقط نشانه‌های قطعی وزن جدی دارند.
"""
import re
from urllib.parse import urlparse

AD_WORDS_BY_LANG = {
    "fa": ["رپورتاژ", "رپورتاژ آگهی", "تبلیغ", "آگهی", "اسپانسر", "کد تخفیف", "تخفیف ویژه", "همین حالا خرید", "خرید آنلاین", "تماس بگیرید", "شماره تماس", "سفارش دهید", "قیمت مناسب", "ارزان‌ترین", "بهترین قیمت", "فروش ویژه", "ثبت‌نام کنید", "ثبت نام کنید", "لینک خرید", "شرط‌بندی", "شرط بندی", "سایت شرط", "درآمد میلیونی", "وام فوری", "خرید فالوور", "مشاوره رایگان", "پیش‌فروش", "کلیک کنید"],
    "en": ["sponsored", "sponsored content", "advertorial", "promoted post", "affiliate", "buy now", "limited offer", "special offer", "discount code", "coupon code", "order now", "sign up now", "click here", "shop now", "best price", "giveaway", "casino", "betting"],
    "ru": ["реклама", "рекламный", "спонсор", "спонсируемый", "на правах рекламы", "скидка", "промокод", "купон", "казино", "ставки на спорт", "купить сейчас", "заказать сейчас", "акция"],
    "hi": ["विज्ञापन", "प्रायोजित", "छूट", "कूपन", "अभी खरीदें", "विशेष ऑफर"],
    "id": ["iklan", "bersponsor", "disponsori", "promo", "diskon", "kupon", "beli sekarang", "judi", "taruhan"],
    "pt": ["publicidade", "patrocinado", "patrocinada", "publieditorial", "desconto", "cupom", "compre agora", "oferta especial", "aposta", "cassino"],
    "es": ["publicidad", "patrocinado", "patrocinada", "publirreportaje", "descuento", "cupón", "compra ahora", "oferta especial", "apuestas", "casino"],
    "ar": ["إعلان", "ممول", "برعاية", "إعلان ممول", "خصم", "كوبون", "اشتر الآن", "عرض خاص", "كازينو", "مراهنات"],
    "tr": ["reklam", "sponsorlu", "sponsor", "indirim", "kupon", "hemen satın al", "kampanya", "bahis", "kumar"],
    "uk": ["реклама", "спонсор", "знижка", "промокод", "купон", "купити зараз", "ставки", "казино"],
    "uz": ["reklama", "homiy", "chegirma", "kupon", "hozir xarid qiling", "tikish"],
    "ms": ["iklan", "tajaan", "ditaja", "diskaun", "kupon", "beli sekarang", "pertaruhan"],
    "vi": ["quảng cáo", "tài trợ", "được tài trợ", "giảm giá", "mã giảm giá", "mua ngay", "cá cược"],
    "zh": ["广告", "赞助", "赞助内容", "推广", "优惠券", "折扣", "立即购买", "博彩", "赌场"],
    "de": ["werbung", "gesponsert", "anzeige", "rabatt", "gutschein", "jetzt kaufen", "wetten"],
    "fr": ["publicité", "sponsorisé", "parrainé", "réduction", "code promo", "achetez maintenant", "paris sportifs", "casino"],
    "it": ["pubblicità", "sponsorizzato", "sconto", "codice sconto", "acquista ora", "scommesse"],
    "bn": ["বিজ্ঞাপন", "প্রচারিত", "ছাড়", "কুপন", "এখনই কিনুন"],
    "th": ["โฆษณา", "ผู้สนับสนุน", "ส่วนลด", "คูปอน", "ซื้อเลย", "พนัน"],
}
AD_WORDS = sorted({w for ws in AD_WORDS_BY_LANG.values() for w in ws}, key=len, reverse=True)
AD_STRONG = ["رپورتاژ", "رپورتاژ آگهی", "sponsored", "sponsored content", "advertorial", "promoted post", "реклама", "на правах рекламы", "спонсируемый", "patrocinado", "patrocinada", "publirreportaje", "publieditorial", "bersponsor", "disponsori", "إعلان ممول", "برعاية", "sponsorlu", "gesponsert", "sponsorisé", "sponsorizzato", "प्रायोजित", "প্রচারিত", "ผู้สนับสนุน", "tajaan", "ditaja", "homiy", "赞助内容", "được tài trợ"]

def _wordset_re(words):
    """کلمات لاتین با مرز واژه (تا «ads» داخل کلمه‌ی دیگر شمرده نشود) و کلمات غیرلاتین به‌صورت زیررشته."""
    lat = [re.escape(w) for w in words if re.fullmatch(r"[\x20-\x7f]+", w)]; oth = [re.escape(w) for w in words if not re.fullmatch(r"[\x20-\x7f]+", w)]
    parts = ([r"\b(?:" + "|".join(lat) + r")\b"] if lat else []) + (["(?:" + "|".join(oth) + ")"] if oth else [])
    return re.compile("|".join(parts), re.I) if parts else None

AD_RE = _wordset_re(AD_WORDS); AD_STRONG_RE = _wordset_re(AD_STRONG)
AD_URL = re.compile(r"/(rpt|reportage|reportaj|sponsored|sponsor|ads?|advert|advertorial|promo|partner|pr-|press-release|reklama)[/-]", re.I)

def heuristic_ad_check(art, strict=False):
    """خروجی: (is_ad, reasons, score) — آستانه: ۵ در حالت سخت، ۸ در حالت عادی."""
    text, title, url = art["text"], art["title"] or "", art["url"]; low = (title + "\n" + text).lower(); n = max(1, len(text) / 1000); score = 0.0; reasons = []
    if AD_URL.search(url): score += 4; reasons.append("ad-url")
    strong = {m.lower() for m in (AD_STRONG_RE.findall(low) if AD_STRONG_RE else [])}
    if strong: score += min(5, 2.5 * len(strong)); reasons.append(f"ad-strong×{len(strong)}")
    hits = [m.lower() for m in (AD_RE.findall(low) if AD_RE else [])]; distinct = len(set(hits))
    if distinct >= 2: score += min(4, (distinct - 1) * 0.9 + len(hits) / n * 0.5); reasons.append(f"ad-words×{len(hits)}/{distinct}")
    if AD_STRONG_RE and AD_STRONG_RE.search(title.lower()): score += 3; reasons.append("ad-title")
    phones = len(re.findall(r"(?<!\d)(?:\+98|0)9\d{9}(?!\d)|(?<!\d)0\d{2,3}[-\s]?\d{7,8}(?!\d)|(?<!\d)\+\d{1,3}[-\s]?\d{3}[-\s]?\d{3,4}[-\s]?\d{3,4}(?!\d)", text))
    if phones: score += 2 + phones; reasons.append(f"phone×{phones}")
    money = len(re.findall(r"\d[\d,٬.]*\s*(تومان|ریال|دلار|درهم|\$|€|£|₽|₺|₹|﷼)", text))
    if money / n > 4: score += 2; reasons.append(f"prices×{money}")
    if money and re.search(r"قیمت اصلی|original price|list price|اکنون فقط|فقط \d+([.,]\d+)?\s*(دلار|تومان|ریال|یورو|€|\$)", low): return True, "price-discount", round(score + 6, 1)
    if len(re.findall(r"t\.me/|telegram\.me/|instagram\.com/|wa\.me/|whatsapp\.com/", text)) > 1: score += 2; reasons.append("social-link")
    if len(re.findall(r"https?://", text)) / n > 6: score += 2; reasons.append("many-links")
    if len(text) < 300: score += 1.5; reasons.append("short")
    promo = len(re.findall(r"کد ?تخفیف|promo ?code|coupon|ارسال رایگان|free shipping|ثبت\u200c?نام کنید|sign ?up now|همین حالا سفارش|order now|تماس بگیرید|call now|کاتالوگ|نمایندگی|limited offer|فرصت محدود", low))
    if promo >= 2: score += 3; reasons.append(f"promo×{promo}")
    risk = len(re.findall(r"کازینو|casino|شرط\u200c?بندی|پیش\u200c?بینی فوتبال|forex|فارکس|سیگنال ?(?:خرید|فروش)|signal group|وام فوری|سرمایه\u200c?گذاری تضمینی|guaranteed (?:profit|return)|سود تضمینی|بیت ?کوین رایگان", low))
    if risk: score += 3 + min(3, risk); reasons.append(f"risk×{risk}")
    if re.search(r"/(sponsored|advertorial|reklama|رپورتاژ)(?=/|$)", urlparse(url).path.lower()): return True, "advertorial-url", round(score + 8, 1)
    if AD_URL.search(url) and (phones or money): return True, "ad-url+contact", round(score + 5, 1)
    if AD_STRONG_RE and AD_STRONG_RE.search(title.lower()): return True, "advertorial-title", round(score + 9, 1)
    if promo >= 2 and (phones or money): return True, "promo+contact", round(score + 6, 1)
    if risk >= 2: return True, "betting/forex", round(score + 5, 1)
    threshold = 5 if strict else 8; return score >= threshold, ", ".join(reasons), round(score, 1)
