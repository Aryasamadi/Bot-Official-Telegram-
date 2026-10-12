# -*- coding: utf-8 -*-
"""فاز ۲ — رگرسیون ماسک راز (H3) و نرمال‌سازی متن (H4).

این تست‌ها روی «قرارداد ریشه‌ای» قفل می‌گذارند، نه روی جزئیات پیاده‌سازی:
- هیچ شکلِ راز (توکن ربات، Bearer، کلید API، توکن URL) نباید از log عبور کند.
- نرمال‌سازی متن باید برای همهٔ زبان‌ها و همهٔ خانواده‌های رقم یکسان کار کند (H4).

علتِ وجود H3: regex کلیدهای عمومی پیش از regex توکن ربات اجرا می‌شد و شناسهٔ عددی
توکن را می‌خورد؛ نتیجه: `BOT_TOKEN=123456789:AAabcdef` دست‌نخورده می‌ماند.
علتِ وجود H4: دو شاخهٔ alternation در regex دیکشنری امتیاز *یکسان* بودند و
الگو عملاً هیچ‌گاه مچ نمی‌شد — این باگ همهٔ زبان‌ها و ارقام را می‌شکست، نه فقط فارسی.
"""
import pytest

from newsbot.logutils import redact
from newsbot.text_format import clean_ai_text


# ── H3: ماسک راز ──

# هر مورد: (ورودی، رشته‌هایی که نباید در خروجی بمانند)
SECRET_CASES = [
    ("BOT_TOKEN=123456789:AAabcdef", ["123456789:AAabcdef"]),
    ("token 9876543210:AAF-short_form_ok", ["9876543210:AAF"]),
    ("Authorization: Bearer abcdef1234567890xyz", ["abcdef1234567890xyz"]),
    ("Authorization: Basic dXNlcjpwYXNz", ["dXNlcjpwYXNz"]),
    ('api_key="sk-proj-abcdef1234567890"', ["sk-proj-abcdef1234567890"]),
    ("apikey=AKIAIOSFODNN7EXAMPLE", ["AKIAIOSFODNN7EXAMPLE"]),
    ("AIzaSyA1234567890abcdefghijklmnopqrst", ["AIzaSyA1234567890abcdefghijklmnopqrst"]),
    ("https://x.com/data?key=SECRETVAL123&token=OTHERSECRET", ["SECRETVAL123", "OTHERSECRET"]),
]


@pytest.mark.parametrize("raw,leaks", SECRET_CASES)
def test_redact_never_leaks_secrets(raw, leaks):
    out = redact(raw)
    for secret in leaks:
        assert secret not in out, f"راز در لاگ باقی ماند: {secret!r} → {out!r}"


def test_redact_masks_telegram_token_even_after_key_word():
    """سناریوی دقیق H3: کلمهٔ کلید + توکن ربات در یک رشته."""
    out = redact("BOT_TOKEN=123456789:AAabcdef")
    assert "123456789" not in out
    assert "AAabcdef" not in out


def test_redact_keeps_non_secret_text_intact():
    """ماسک نباید متن عادی را ببلعد (حفاظت در برابر over-redaction)."""
    out = redact("publication failed for channel 42 at 12:30")
    assert "publication failed for channel 42" in out
    assert "12:30" in out


# ── H4: نرمال‌سازی دیکشنری امتیاز ──

# همهٔ این‌ها باید حذف شوند: ترکیب زبان × خانوادهٔ رقم × جداکننده
SCORE_DICT_CASES = [
    "متن خبر\n\nارزش محتوایی: ۹",
    "متن خبر\n\nارزش محتوایی :  8",
    "متن خبر\n\nContent value: 9",
    "متن خبر\n\nContent value : 7",
    'متن خبر\n\n"Content value": 5',
    "متن خبر\n\n«ارزش محتوایی»: 6",
    "متن خبر\n\nNon-promotional: ٤",
]


@pytest.mark.parametrize("raw", SCORE_DICT_CASES)
def test_clean_ai_text_strips_score_dict_all_forms(raw):
    out = clean_ai_text(raw)
    assert "ارزش محتوایی" not in out
    assert "Content value" not in out
    assert "Non-promotional" not in out
    assert out.strip()


def test_clean_ai_text_preserves_body_content():
    """بدنهٔ خبر باید دست‌نخورده بماند؛ فقط دیکشنری امتیاز حذف می‌شود.

    توجه: `clean_ai_text` عمداً پاک‌سازیِ نشانه‌گذاری انتهایی را انجام می‌دهد
    (نقطهٔ پایانی حذف می‌شود)؛ این رفتار از قبل وجود داشته و بخشی از قرارداد است.
    """
    raw = "تیتر خبر\n\nبدنهٔ کامل خبر که باید بماند.\n\nارزش محتوایی: ۹"
    out = clean_ai_text(raw)
    assert "بدنهٔ کامل خبر که باید بماند" in out
    assert "ارزش محتوایی" not in out


def test_clean_ai_text_is_idempotent():
    """اجرای دوباره نباید چیزی را تغییر دهد (قرارداد ایمنی خط سرخ بند ۴)."""
    for raw in SCORE_DICT_CASES:
        once = clean_ai_text(raw)
        assert clean_ai_text(once) == once
