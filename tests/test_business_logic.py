# -*- coding: utf-8 -*-
"""فاز ۷ — منطق کسب‌وکار، قرارداد ایجنت و ایمنی AI.

سه حوزهٔ پول/اعتماد این‌جا قفل می‌شوند:
1. لایهٔ AI: خروجی نامعتبر یا آلوده هرگز به‌عنوان پست معتبر پذیرفته نمی‌شود.
2. قرارداد ایجنت: پرامپت و پاکت JSON دست‌نخورده و قطعی می‌مانند.
3. منطق کسب‌وکار: سهمیه، تخفیف و ارجاع در برابر سوءاستفاده مقاوم‌اند.
"""
import pytest

from newsbot.ai_models import parse_json, _validate_generation, AIResponseError
from newsbot.text_format import salvage_post
from newsbot.core_discounts import disc_create, disc_use, disc_valid, disc_refund
from newsbot.core_referrals import record_referral
from newsbot.db import q


# ── لایهٔ AI: پذیرش/رد خروجی ──

VALID = '{"category":"c","title":"t","scores":{"a":1},"post":"<b>x</b>","full":null}'


def test_validate_accepts_wellformed_output():
    """قرارداد: `_validate_generation` یک *اعتبارسنج* است — موفق ⇒ None، نامعتبر ⇒ استثنا."""
    assert _validate_generation(VALID) is None


def test_validate_accepts_ad_marked_output():
    """خروجیِ «تبلیغ است» هم معتبر است (سیگنال تمیز، نه خطا)."""
    out = '{"category":"c","title":"t","scores":{"a":1},"post":"<b>x</b>","is_ad":true}'
    assert _validate_generation(out) is None


@pytest.mark.parametrize("bad", [
    "no json here",
    '{"post":"","scores":{"a":1}}',                        # post خالی
    '{"post":"ok"}',                                        # scores ندارد
    '{"post":"ok","scores":{}}',                            # scores خالی
    '{"post":"ok","scores":{"a":1},"x":"sk-abcdef1234567890"}',   # باقی‌ماندهٔ تزریق
    '{"post":"ok","scores":{"a":1},"d":"</article_data>"}',      # جداکنندهٔ جعبه
])
def test_validate_rejects_bad_output(bad):
    with pytest.raises(AIResponseError):
        _validate_generation(bad)


# ── parse_json: مقاومت در برابر خروجی‌های کج ──

@pytest.mark.parametrize("text", [
    '{"a":1}',
    '```json\n{"a":1}\n```',
    '{"a":1,}',
    'junk {"a":1} junk',
    '{"a":"line1\\nline2"}',
])
def test_parse_json_recovers_valid_payloads(text):
    assert parse_json(text) is not None


@pytest.mark.parametrize("text", ["", "null", "not json", '{"a":1', "[]"])
def test_parse_json_returns_none_for_invalid(text):
    assert parse_json(text) is None


def test_salvage_post_never_exposes_raw_json():
    raw = '{"category":"news","title":"t","scores":{"a":1},"post":"<b>x</b>","full":null}'
    out = salvage_post(raw)
    assert '"scores"' not in out and '"category"' not in out
    assert "x" in out


# ── تخفیف: اتمیک و بازگشت‌پذیر ──

def test_discount_cannot_be_reused_beyond_max():
    c = disc_create("SEC7A", 10, "2099-01-01T00:00:00", max_uses=1)
    assert disc_use(c) is True
    assert disc_use(c) is False, "تخفیف تک‌مصرف نباید دوباره قابل استفاده باشد"


def test_discount_refund_restores_usability():
    c = disc_create("REF7A", 15, "2099-01-01T00:00:00", max_uses=1)
    assert disc_use(c) is True
    assert disc_refund(c) is True
    assert disc_valid(c)[1] == "ok"
    assert disc_use(c) is True, "پس از بازگشت باید دوباره قابل استفاده باشد"


def test_expired_discount_is_invalid():
    c = disc_create("EXP7A", 20, "2000-01-01T00:00:00", max_uses=5)
    ok, why = disc_valid(c)
    assert ok is not True or why != "ok"


# ── ارجاع: سقف و fail-closed ──

def test_referral_cap_config_exists():
    """PH-01/FIN-023: سقفِ «تعدادِ» ارجاع حذف شد — فقط سقفِ مبلغی (به تفکیکِ واحد) وجود دارد.

    قبلاً ``REFERRAL_MAX_PER_USER`` در config بود ولی هیچ مسیری از آن استفاده نمی‌کرد؛ فقط
    یک تصورِ نادرست برای اپراتور می‌ساخت. حالا نباید وجود داشته باشد."""
    import newsbot.config as cfg
    assert not hasattr(cfg, "REFERRAL_MAX_PER_USER"), \
        "سقفِ «تعداد» نباید برگردد (Q-3a: پاداش روی هر خرید است و سقف فقط مبلغی/واحدی)"


def test_referral_self_and_missing_referrer_rejected():
    # خودارجاعی ممنوع
    q("INSERT OR IGNORE INTO users(id,username,name,role,created_at,last_seen) VALUES(?,?,?,?,?,?)",
      (777001, "u1", "U1", "user", "2026-01-01", "2026-01-01"), commit=True)
    assert record_referral(777001, 777001) is False
    # معرفِ ناموجود ⇒ رد (fail-closed)
    assert record_referral(777002, 999999) is False


def test_referral_valid_pair_recorded():
    q("INSERT OR IGNORE INTO users(id,username,name,role,created_at,last_seen) VALUES(?,?,?,?,?,?)",
      (778001, "r", "R", "user", "2026-01-01", "2026-01-01"), commit=True)
    assert record_referral(778001, 778002) is True
