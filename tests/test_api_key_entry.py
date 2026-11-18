# -*- coding: utf-8 -*-
"""نگهبانِ «ورودِ کلیدِ API» — چرا یک مدلِ سالم با کلیدِ سالم 403 می‌گیرد؟

سه نقصِ واقعی که این فایل قفل می‌کند:

1. کلیدی که با فاصله/خطِ‌جدید/نویسهٔ نامرئی (ZWSP، LRM، BOM) وارد شود، **بی‌صدا**
   ذخیره می‌شد و پروایدر آن را رد می‌کرد؛ کاربر فقط «مدل از کار افتاد» می‌دید.
2. صفحهٔ فراهم‌کننده هیچ راهی برای دیدنِ **طولِ کلیدِ ذخیره‌شده** نداشت، پس
   «کلیدِ بریده/آلوده» از «کلیدِ بی‌اعتبار» قابلِ تشخیص نبود.
3. اگر کلیدِ ذخیره‌شده با ``SECRETS_KEY`` جاری رمزگشایی نمی‌شد،
   ``secret_store.decrypt`` رشتهٔ خالی می‌داد و ربات ``Bearer `` می‌فرستاد؛
   نتیجهٔ پروایدر («403 Authorization failed») به‌اشتباه به گردنِ مدل می‌افتاد.
"""
import pytest

from newsbot import ai_models, config, providers, secret_store, ui_views_super


# ── ۱) پاک‌سازیِ کلید ─────────────────────────────────────────────────────────────
def test_a_clean_key_is_returned_unchanged():
    assert secret_store.clean_api_key("nvapi-abc123") == "nvapi-abc123"


def test_an_empty_key_stays_empty():
    assert secret_store.clean_api_key("") == ""
    assert secret_store.clean_api_key(None) == ""


@pytest.mark.parametrize("bad", ["nvapi-abc 123", "nvapi-abc\n123", "nvapi-abc\t123", "nvapi-abc\u00a0123"])
def test_a_key_with_whitespace_is_rejected_with_a_clear_message(bad):
    with pytest.raises(ValueError) as e:
        secret_store.clean_api_key(bad)
    assert "فاصله" in str(e.value)


@pytest.mark.parametrize("invisible", ["\u200b", "\u200e", "\u200f", "\ufeff", "\u2060"])
def test_invisible_format_characters_are_removed(invisible):
    assert secret_store.clean_api_key("nvapi-" + invisible + "abc" + invisible) == "nvapi-abc"


def test_removing_invisible_characters_is_logged_with_a_count_and_never_the_key(caplog):
    with caplog.at_level("WARNING"):
        out = secret_store.clean_api_key("nvapi-\u200babc")
    assert out == "nvapi-abc"
    assert "nvapi" not in caplog.text, "کلید هرگز نباید در لاگ بیاید"


def test_surrounding_whitespace_is_trimmed_not_rejected():
    assert secret_store.clean_api_key("  nvapi-abc\n") == "nvapi-abc"


# ── ۲) همان پاک‌سازی در همهٔ مسیرهای نوشتن ───────────────────────────────────────
def test_create_provider_stores_the_cleaned_key(isolated_db):
    pid = providers.create_provider("p", "https://api.example.com/v1", "nvapi-\u200babc")
    row = providers.get_provider_row(pid)
    assert secret_store.decrypt(row["api_key"]) == "nvapi-abc"


def test_create_provider_refuses_a_key_with_whitespace(isolated_db):
    with pytest.raises(ValueError):
        providers.create_provider("p2", "https://api2.example.com/v1", "nvapi-abc 123")


def test_update_provider_stores_the_cleaned_key(isolated_db):
    pid = providers.create_provider("p", "https://api.example.com/v1", "old")
    providers.update_provider(pid, api_key="nvapi-\u200eabc")
    assert secret_store.decrypt(providers.get_provider_row(pid)["api_key"]) == "nvapi-abc"


def test_add_model_and_update_model_store_the_cleaned_key(isolated_db):
    mid = ai_models.add_model("https://api.example.com/v1", "nvapi-\u200babc", "m1")
    assert secret_store.decrypt(ai_models.get_model(mid)["api_key"]) == "nvapi-abc"
    ai_models.update_model(mid, api_key="nvapi-\ufeffdef")
    assert secret_store.decrypt(ai_models.get_model(mid)["api_key"]) == "nvapi-def"


# ── ۳) کلیدِ غیرقابل‌رمزگشایی هرگز به پروایدر نمی‌رود ────────────────────────────
def test_an_unreadable_key_is_reported_as_a_key_problem_not_as_the_model(isolated_db, monkeypatch):
    monkeypatch.setattr(config, "SECRETS_KEY", "first-key")
    pid = providers.create_provider("p", "https://api.example.com/v1", "nvapi-abc")
    model = {"id": 1, "provider_id": pid, "kind": "openai", "base_url": "https://api.example.com/v1", "api_key": ""}

    # همان ردیف با کلیدِ دیگر رمزگشایی نمی‌شود ⇒ باید خطای صریحِ کلید بدهد
    monkeypatch.setattr(config, "SECRETS_KEY", "another-key")
    with pytest.raises(ai_models.AIProviderError) as e:
        ai_models.resolve_endpoint(model)
    msg = str(e.value).lower()
    assert "decrypt" in msg or "secret" in msg, msg
    assert "nvapi" not in msg, "متنِ خطا نباید کلید را لو بدهد"


def test_a_readable_key_is_returned_normally(isolated_db, monkeypatch):
    monkeypatch.setattr(config, "SECRETS_KEY", "stable-key")
    pid = providers.create_provider("p", "https://api.example.com/v1", "nvapi-abc")
    model = {"id": 1, "provider_id": pid, "kind": "openai", "base_url": "https://api.example.com/v1", "api_key": ""}
    kind, base, key = ai_models.resolve_endpoint(model)
    assert (kind, base, key) == ("openai", "https://api.example.com/v1", "nvapi-abc")


# ── ۴) دیدنِ طولِ کلید در پنل (بدونِ افشای مقدار) ────────────────────────────────
def test_the_provider_line_shows_the_stored_key_length(isolated_db, monkeypatch):
    monkeypatch.setattr(config, "SECRETS_KEY", "stable-key")
    pid = providers.create_provider("p", "https://api.example.com/v1", "nvapi-abc")
    row = providers.get_provider_row(pid)
    line = ui_views_super._pline(row, "fa")
    assert "key=9" in line
    assert "nvapi" not in line


def test_the_provider_line_marks_an_unreadable_key(isolated_db, monkeypatch):
    monkeypatch.setattr(config, "SECRETS_KEY", "first-key")
    pid = providers.create_provider("p", "https://api.example.com/v1", "nvapi-abc")
    monkeypatch.setattr(config, "SECRETS_KEY", "another-key")
    row = providers.get_provider_row(pid)
    assert "key=UNREADABLE" in ui_views_super._pline(row, "fa")
    # مسیرِ واقعیِ پنل: ردیفِ عمومی هم همان را نشان می‌دهد، بی‌آنکه کلید را لو بدهد.
    public = providers.list_providers()[0]
    line = ui_views_super._pline(public, "fa")
    assert "key=UNREADABLE" in line
    assert "nvapi" not in line

# ── ۵) ویزاردِ پنل هم همان پاک‌سازی را دارد (مسیرِ دومِ ورودِ کلید) ─────────────
def test_the_wizard_strips_invisible_characters_before_storing():
    from newsbot.ui_inputs import _input_value
    assert _input_value("api_key", "nvapi-\u200babc\ufeff", "fa") == "nvapi-abc"


def test_the_wizard_refuses_a_key_with_whitespace():
    from newsbot.ui_inputs import _input_value
    with pytest.raises(ValueError):
        _input_value("api_key", "nvapi abc", "fa")


def test_the_wizard_keeps_the_empty_key_marker():
    from newsbot.ui_inputs import _input_value
    assert _input_value("api_key", "-", "fa") == ""
