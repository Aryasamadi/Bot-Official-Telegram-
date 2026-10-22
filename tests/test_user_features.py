# -*- coding: utf-8 -*-
"""قابلیت‌های کاربر/مدیر اضافه‌شده: کانال اجباری مستقل زبان، /ref،
دکمهٔ AI در منوی کانال، پیام خطای مدل و قاعدهٔ نمایش پیام.

این تست‌ها قرارداد رفتار را قفل می‌کنند تا بازگشت باگ ممکن نباشد.
"""
import os

os.environ.setdefault("BOT_TOKEN", "123456789:AAtest_only_dummy_token_0000000000000")
os.environ.setdefault("SUPER_ADMIN_IDS", "1")


# ══════════════════════════════════════════════════════════════════════════
# ۱) کانال اجباری — مستقل برای هر زبان
# ══════════════════════════════════════════════════════════════════════════
def _reset_mandatory():
    from newsbot import config
    from newsbot.core_channels import MANDATORY_DB_KEY
    from newsbot.db import q
    config.MANDATORY_CHANNEL_ID = ""
    config.MANDATORY_CHANNEL_USERNAME = ""
    try: q("DELETE FROM settings WHERE key=?", (MANDATORY_DB_KEY,), commit=True)
    except Exception: pass


def test_mandatory_channel_is_independent_per_language():
    """فعال‌سازی زبانِ انگلیسی نباید زبان فارسی را تغییر دهد."""
    from newsbot.core_channels import mandatory_set, mandatory_configured, mandatory_target
    _reset_mandatory()
    assert not mandatory_configured("fa") and not mandatory_configured("en")

    mandatory_set("en", on=True, user="en_channel")
    assert mandatory_configured("en")
    assert not mandatory_configured("fa"), "تنظیم انگلیسی نباید فارسی را فعال کند"
    assert mandatory_target("en") == "@en_channel"
    assert mandatory_target("fa") is None

    mandatory_set("fa", on=True, id="-1001234567890")
    assert mandatory_target("fa") == -1001234567890
    assert mandatory_target("en") == "@en_channel", "کانال هر زبان باید جدا بماند"

    mandatory_set("fa", on=False)
    assert not mandatory_configured("fa")
    assert mandatory_configured("en"), "خاموش‌کردن فارسی نباید انگلیسی را خاموش کند"


def test_mandatory_channel_reads_env_only_for_fa():
    """env جای پیکربندی دیتابیس را نمی‌گیرد و فقط پیش‌فرض زبان فارسی است."""
    from newsbot import config
    from newsbot.core_channels import mandatory_configured
    _reset_mandatory()
    config.MANDATORY_CHANNEL_ID = "-100999"
    try:
        assert mandatory_configured("fa")
        assert not mandatory_configured("en"), "env تک‌کاناله است؛ انگلیسی نباید فعال شود"
    finally:
        _reset_mandatory()


def test_mandatory_channel_invalid_id_falls_back_to_username():
    from newsbot.core_channels import mandatory_set, mandatory_target
    _reset_mandatory()
    mandatory_set("fa", on=True, id="not-a-number", user="fallback_ch")
    assert mandatory_target("fa") == "@fallback_ch"


# ══════════════════════════════════════════════════════════════════════════
# ۲) رفرال — دستور /ref بدون دکمهٔ جدید
# ══════════════════════════════════════════════════════════════════════════
def test_ref_command_registered_and_the_home_screen_offers_the_referral_route():
    """PH-05/UI-014: صفحهٔ خانه باید **دسترسی** به دعوت/پشتیبانی/راهنما/زبان بدهد.

    پیش از این، صفحهٔ خانه فقط یک دکمه (پلن‌ها) داشت و مسیرِ ``u:ref`` از هیچ صفحه‌ای
    قابل‌دسترس نبود ⇒ مشتریِ غیرفنی نمی‌توانست پشتیبانی یا دعوت را پیدا کند."""
    from newsbot.ui_views_user import view_user_home
    from newsbot.ui_commands import cmd_ref
    assert callable(cmd_ref), "دستور /ref باید وجود داشته باشد"

    import inspect
    src = inspect.getsource(view_user_home)
    assert "u:ref" in src, "دکمهٔ دعوت باید در صفحهٔ خانه باشد"
    assert "u:man" in src, "دکمهٔ پشتیبانی باید در صفحهٔ خانه باشد"
    assert "u:help" in src and "u:lang" in src, "دکمهٔ راهنما و زبان باید در صفحهٔ خانه باشد"
    assert "plans_btn" in src


def test_referral_reward_message_exists_for_both_langs():
    from newsbot.ui_i18n import tr
    assert tr("fa", "ref_reward", amount="100") == (
        "🎉 پاداش دعوت: 100 به کیف پول شما اضافه شد؛ در خرید یا ارتقای بعدی خودکار کسر می‌شود.")
    assert tr("en", "ref_reward", amount="100") == (
        "🎉 Referral reward: 100 added to your wallet; it's applied automatically to your next purchase or upgrade.")
    assert tr("fa", "ref_joined")
    assert tr("en", "ref_joined")


# ══════════════════════════════════════════════════════════════════════════
# ۳) دکمهٔ هوش مصنوعی در منوی کانال (نه منوی اصلی)
# ══════════════════════════════════════════════════════════════════════════
def test_ai_button_moved_from_home_to_channel():
    import inspect
    from newsbot.ui_views_admin import view_admin_home, view_channel
    home = inspect.getsource(view_admin_home)
    ch = inspect.getsource(view_channel)
    assert '"a:myai"' not in home, "دکمهٔ AI نباید در منوی اصلی باشد"
    assert 'f"a:myai:{cid}"' in ch, "دکمهٔ AI باید در منوی هر کانال باشد"


def test_ai_models_are_per_user_not_per_channel():
    """مدل انتخابی کاربر باید بین همهٔ کانال‌های او مشترک باشد."""
    from newsbot.ai_models import list_models
    import inspect
    sig = inspect.signature(list_models)
    assert "owner_id" in sig.parameters, "انتخاب مدل باید مالک‌محور باشد نه کانال"


# ══════════════════════════════════════════════════════════════════════════
# ۴) مدیریت خطای مدل‌های هوش مصنوعی
# ══════════════════════════════════════════════════════════════════════════
def test_all_models_failed_message_exists():
    from newsbot.ai_models import ai_err_text
    assert "هیچ" in ai_err_text("ai_all_failed", "fa")
    assert "None of the models" in ai_err_text("ai_all_failed", "en")


def test_model_attempts_do_not_repeat_same_model():
    """هر مدل حداکثر یک بار امتحان می‌شود (بدون تکرار بی‌فایده)."""
    import inspect
    from newsbot.ai_models import _try_models
    src = inspect.getsource(_try_models)
    assert "tried" in src, "نشانی تکرار مدل باید کنترل شود"
    assert "ai_all_failed" in src, "وقتی همه‌ی مدل‌ها تمام شدند باید پیام یک‌دست داده شود"


def test_model_cap_covers_user_and_admin_models():
    """سقف تلاش باید به مدل‌های کاربر و مدیر اجازهٔ پاسخ‌دادن بدهد."""
    from newsbot import config
    assert config.AI_MAX_MODEL_ATTEMPTS >= 5


# ══════════════════════════════════════════════════════════════════════════
# ۵) قاعدهٔ نمایش پیام بر اساس طول
# ══════════════════════════════════════════════════════════════════════════
def test_popup_threshold_constants():
    """PH-05/UI-026: تلگرام تا ۲۰۰ کاراکتر را به‌صورت پاپ‌آپ می‌پذیرد ⇒ آستانه ۲۰۰ است
    (پیش از این ۳۵ بود و حدود ۴۰ پیامِ مدیر به‌صورت پیامِ جداگانه می‌رسید)."""
    from newsbot.ui_widgets import POPUP_MAX_CHARS, POPUP_TEMP_SECONDS
    assert POPUP_MAX_CHARS == 200
    assert POPUP_TEMP_SECONDS == 10


def test_popup_no_longer_accepts_alert_flag():
    """قاعدهٔ طول باید مرجع تصمیم باشد؛ پرچم alert بی‌معنی می‌شود."""
    import inspect
    from newsbot.ui_widgets import popup
    assert "alert" not in inspect.signature(popup).parameters
    src = inspect.getsource(popup)
    assert "POPUP_MAX_CHARS" in src and "POPUP_TEMP_SECONDS" in src


class _FakeAns:
    def __init__(self): self.shown = []
    async def answer(self, text, show_alert=False): self.shown.append((text, show_alert))
    @property
    def message(self): return None


class _FakeBot:
    """بات جعلی: تنها send_message لازم است."""
    def __init__(self): self.sent = []
    async def send_message(self, chat_id, text, **kw):
        self.sent.append(text)
        return type("M", (), {"message_id": 7})()
    async def edit_message_text(self, *a, **k): return None


class _Upd:
    def __init__(self, qy):
        self.callback_query = qy
        self.effective_user = type("U", (), {"id": 5})()
        self.effective_chat = type("C", (), {"id": 5})()


class _Ctx:
    def __init__(self): self.bot = _FakeBot(); self.user_data = {}


def test_short_message_is_popup():
    """متن کوتاه (<35) باید پاپ‌آپ شود، نه پیام چت."""
    import asyncio
    from newsbot.ui_widgets import popup
    qy = _FakeAns(); up = _Upd(qy); ctx = _Ctx()
    asyncio.run(popup(up, ctx, "ذخیره شد"))
    assert qy.shown == [("ذخیره شد", False)], "پیام کوتاه باید پاپ‌آپ بالای صفحه باشد"
    assert not ctx.bot.sent, "پیام کوتاه نباید در چت فرستاده شود"


def test_long_message_is_temp_with_10s():
    """متنِ خیلی بلند (>۲۰۰) باید پیام خودحذف‌شوندهٔ ۱۰ثانیه‌ای باشد (PH-05/UI-026)."""
    import asyncio, time as _t
    from newsbot.ui_widgets import popup, _temp_messages
    qy = _FakeAns(); up = _Upd(qy); ctx = _Ctx()
    long_txt = "این یک پیام خیلی بلند است که باید در چت به مدت ده ثانیه دیده شود " + ("ا" * 200)
    asyncio.run(popup(up, ctx, long_txt))
    assert not qy.shown, "پیام بلند نباید پاپ‌آپ شود"
    assert ctx.bot.sent, "پیام بلند باید در چت فرستاده شود"
    key = (5, 7)
    assert key in _temp_messages
    remaining = _temp_messages[key][0] - _t.monotonic()
    assert 9.5 <= remaining <= 10.5, f"پیام بلند باید ۱۰ ثانیه بماند، نه {remaining:.1f}"
