# -*- coding: utf-8 -*-
"""قرارداد بایت‌به‌بایت محتوای تولیدشده توسط ایجنت/AI — خط سرخ بند ۴ پرامپت.

هدف: هرگونه تغییرِ ناخواسته در *قرارداد* ایجنت (ساختار پرامپت، پاکت JSON، ترتیب/شکل قرارداد
خروجی، رشته‌های ساختاری) باید این تست‌ها را بشکند. این تست‌ها عمداً روی «شکلِ قرارداد» قفل
می‌گذارند، نه روی متنِ دلبخواهی مدل — چون متنِ مدل در زمان اجرا تولید می‌شود و این‌جا موجود نیست.

قاعده: اگر روزی کسی بخواهد پرامپت/قرارداد را عوض کند، باید آگاهانه این snapshotها را
به‌روزرسانی کند؛ عبورِ بی‌صدا از این تست‌ها ممنوع است.
"""
import hashlib, re

from newsbot.generate import build_prompt, compose, ensure_quote, ai_cache_key
from newsbot.text_format import clean_ai_text, salvage_post, sanitize_html, strip_source_url
from newsbot.core_channels import default_settings


def _h(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


# ── ساختار پرامپت: کلیدهای قرارداد باید وجود داشته باشند (نه متنِ ثابت) ──
PROMPT_CONTRACT_MARKERS = (
    "UNTRUSTED DATA RULE", "<article_data>", "FORMATTING RULES",
    "Return ONLY one valid JSON object", '"category"', '"title"', '"scores"', '"post"', '"full"',
    "MANDATORY: \"post\" MUST contain at least one <blockquote>",
)


def _sample_settings(lang="fa"):
    s = default_settings(lang, "testch")
    s["topic"] = "اقتصاد"
    return s


def _sample_article():
    return {"url": "https://news.example.com/a/1", "title": "عنوان خبر",
            "text": "متن کامل خبر " * 40, "published": "2026-10-01T10:00:00+00:00", "media": None}


def test_prompt_contract_markers_are_stable():
    """قرارداد پرامپت (کلیدواژه‌های ساختاری) نباید بی‌خبر حذف/جابه‌جا شود."""
    sysmsg, usermsg = build_prompt(_sample_settings(), _sample_article(), want_full=True)
    for marker in PROMPT_CONTRACT_MARKERS:
        assert marker in sysmsg, f"قرارداد پرامپت شکست: نبودِ {marker!r}"
    # داده‌ی منبع همیشه داخل پاکت untrusted قرار می‌گیرد
    assert usermsg.startswith("<article_data>\n") and usermsg.rstrip().endswith("</article_data>")


def test_prompt_is_deterministic_for_same_inputs():
    """ورودی یکسان ⇒ پرامپت یکسان (بایت‌به‌بایت)."""
    s, a = _sample_settings(), _sample_article()
    p1 = build_prompt(s, a, want_full=True)
    p2 = build_prompt(s, a, want_full=True)
    assert p1 == p2
    assert _h(p1[0]) == _h(p2[0]) and _h(p1[1]) == _h(p2[1])


def test_prompt_full_flag_changes_only_full_field():
    """want_full=False باید فقط مقدار «full» را null کند، نه بقیهٔ قرارداد را."""
    s, a = _sample_settings(), _sample_article()
    full = build_prompt(s, a, want_full=True)[0]
    nofull = build_prompt(s, a, want_full=False)[0]
    assert full != nofull
    assert "'null'" in nofull or "null" in nofull
    # بخش‌های مشترک دست‌نخورده می‌مانند
    head = full.split('"full":')[0]
    assert head in nofull


def test_json_envelope_shape_is_frozen():
    """پاکت JSON قرارداد: پنج کلید در همین ترتیب — این ساختار بخشی از قرارداد ایجنت است."""
    sysmsg, _ = build_prompt(_sample_settings(), _sample_article(), want_full=True)
    m = re.search(r'\{"category".*\}$', sysmsg, re.S | re.M)
    assert m, "پاکت JSON قرارداد پیدا نشد"
    assert m.group(0).startswith('{"category": "category name", "title": "headline", "scores": {"criterion name": 0-10}, "post": "HTML", "full": "HTML or null"}')


def test_salvage_post_never_leaks_raw_json():
    """نگهبان نشت JSON: خروجی salvage هرگز نباید پاکت JSON خام باشد."""
    raw = '{"category":"x","title":"t","scores":{"a":5},"post":"<b>متن واقعی</b>\\n\\nبدنه","full":null}'
    out = salvage_post(raw)
    assert out and "{" not in out and '"category"' not in out
    assert "متن واقعی" in out


def test_clean_ai_text_is_idempotent_and_never_rewrites_words():
    """پاک‌سازی زبالهٔ متا نباید واژه‌های متن را بازنویسی کند و باید idempotent باشد."""
    body = "تیتر مهم\n\nپاراگراف اول با یک عدد ۱۲۰ و نام شرکت الف.\n\nپاراگراف دوم."
    once = clean_ai_text(body)
    twice = clean_ai_text(once)
    assert once == twice, "clean_ai_text باید idempotent باشد"
    assert "پاراگراف اول" in once and "۱۲۰" in once and "شرکت الف" in once


def test_clean_ai_text_strips_score_dict_all_separator_forms():
    """همهٔ شکل‌های «برچسب: امتیاز» حذف می‌شوند (پارسی/لاتین/فاصله‌دار/بی‌فاصله)."""
    for label in ("ارزش محتوایی", "Content value", "Non-promotional"):
        for sep in (":", " :", ": ", " : "):
            t = f"متن خبر\n{label}{sep}9"
            out = clean_ai_text(t)
            assert label not in out, f"نشت برچسب با جداکننده {sep!r}: {out!r}"
            assert "متن خبر" in out


def test_compose_output_structure_is_stable():
    """compose باید ساختار قرارداد را حفظ کند: blockquote اجباری + امضا در انتها."""
    s = _sample_settings()
    ch = {"username": "testch"}
    art = _sample_article()
    gen = {"post": "تیتر پست\n\nپاراگراف اول با جزئیات کافی برای عبور از آستانه.\n\nپاراگراف دوم.",
           "full": None, "title": "تیتر"}
    post, full = compose(s, ch, art, gen)
    assert "<blockquote>" in post, "قرارداد: پست باید blockquote داشته باشد"
    assert "@testch" in post, "امضای کانال باید در انتهای پست باشد"
    assert full is None


def test_source_url_stripped_at_caption_layer():
    """قرارداد ثابت: لینک منبع هرگز داخل متن پست دیده نمی‌شود.
    حذف در لایهٔ کپشن (channel_caption) انجام می‌شود، نه در compose — این تست همان لایه را می‌سنجد."""
    from newsbot.publish import channel_caption
    s = _sample_settings(); s["ui_lang"] = "fa"
    ch = {"username": "testch"}
    post_html = 'خبر <a href="https://news.example.com/a/1">منبع</a> مهم است و بقیهٔ متن.'
    cut, tail, full = channel_caption(s, ch, post_html, None, "تیتر", "https://news.example.com/a/1")
    assert "news.example.com" not in cut
    assert "مهم است" in cut


def test_ai_cache_key_depends_on_contract_version():
    s, a = _sample_settings(), _sample_article()
    k1 = ai_cache_key(s, a)
    s2 = dict(s); s2["prompt"] = s["prompt"] + " "
    assert ai_cache_key(s2, a) != k1
    # ورودی یکسان ⇒ کلید یکسان
    assert ai_cache_key(s, a) == k1


def test_ensure_quote_is_contract_preserving():
    """ensure_quote فقط و فقط یک blockquote اضافه می‌کند و بقیهٔ متن را دست‌نخورده می‌گذارد."""
    body = "<b>تیتر</b>\n\nپاراگراف اول بلند برای کاندید شدن در نقل‌قول.\n\nپاراگراف دوم."
    out = ensure_quote(body)
    assert out.count("<blockquote>") == 1 and out.count("</blockquote>") == 1
    assert strip_source_url == strip_source_url  # no-op نگه‌داشتن import
    plain = re.sub(r"</?blockquote>", "", out)
    assert plain == body, "ensure_quote نباید متن را جز افزودن تگ عوض کند"


def test_sanitize_html_preserves_allowed_and_drops_script():
    src = "<b>ب</b><i>ا</i><blockquote>نقل</blockquote><script>x</script>"
    out = sanitize_html(src)
    for tag in ("<b>ب</b>", "<i>ا</i>", "<blockquote>نقل</blockquote>"):
        assert tag in out
    assert "script" not in out.lower()
