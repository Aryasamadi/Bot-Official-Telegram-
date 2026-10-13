# -*- coding: utf-8 -*-
"""مقاومت لایهٔ نمایش و رندر منوها.

این تست‌ها خطاهایی را پوشش می‌دهند که در محیط واقعی تلگرام رخ می‌دهند ولی در
شبیه‌سازی ساده دیده نمی‌شوند: پیام پنلِ کهنه، خطای Forbidden، HTML ردشده.
"""
import asyncio, os

os.environ.setdefault("BOT_TOKEN", "123456789:AAtest_only_dummy_token_0000000000000")
os.environ.setdefault("SUPER_ADMIN_IDS", "1")

import pytest


@pytest.fixture(autouse=True)
def _clean_rate():
    from newsbot.ratelimit import _rl
    _rl.clear()
    yield
    _rl.clear()


class _Q:
    async def answer(self, *a, **k): pass
    @property
    def message(self): return None


class _U:
    id = 7700
    username = "tester"
    full_name = "Tester"
    is_premium = False


class _C:
    id = 7700


class _Upd:
    callback_query = None
    effective_user = _U()
    effective_chat = _C()
    message = None


class _Ctx:
    def __init__(self):
        self.bot = None
        self.user_data = {}
        self.args = []


class _Bot:
    """بات جعلی با حالتهای تزریقي برای خطا."""
    def __init__(self, edit_error=None, html_error=False):
        self.sent = []
        self.edits = []
        self._edit_error = edit_error
        self._html_error = html_error

    async def send_message(self, chat_id, text, parse_mode=None, reply_markup=None, **kw):
        if self._html_error and parse_mode == "HTML":
            from telegram.error import BadRequest
            raise BadRequest("can't parse entities")
        self.sent.append(text)
        return type("M", (), {"message_id": 99})()

    async def edit_message_text(self, *a, **kw):
        self.edits.append(a)
        if self._edit_error:
            raise self._edit_error


def _mkctx(bot):
    c = _Ctx(); c.bot = bot
    return c


# ══════════════════════════════════════════════════════════════════════════
# render باید در برابر همهٔ خطاهای تلگرام عقبنشینی کند
# ══════════════════════════════════════════════════════════════════════════
def test_render_falls_back_when_edit_raises_forbidden():
    """Forbidden زیرکلاس BadRequest نیست؛ قبلاً کل عملیات را زمین میزد."""
    from telegram.error import Forbidden
    from newsbot.ui_widgets import render

    bot = _Bot(edit_error=Forbidden("bot was blocked"))
    ctx = _mkctx(bot)
    ctx.user_data["panel"] = 4242          # پیام پنلِ کهنه

    asyncio.run(render(_Upd(), ctx, "<b>سلام</b>"))
    assert bot.sent, "باید پیام تازه بفرستد نه اینکه خطا بدهد"
    assert "سلام" in bot.sent[-1]


def test_render_falls_back_on_any_telegram_error():
    from telegram.error import NetworkError
    from newsbot.ui_widgets import render

    bot = _Bot(edit_error=NetworkError("connection reset"))
    ctx = _mkctx(bot); ctx.user_data["panel"] = 7
    asyncio.run(render(_Upd(), ctx, "متن"))
    assert bot.sent, "هر خطای شبکه نباید عملیات را بشکند"


def test_render_sends_plain_text_when_html_rejected():
    """اگر HTML تلگرام رد شود، متن ساده فرستاده شود تا کاربر هیچ پاسخی نگیرد."""
    from newsbot.ui_widgets import render
    bot = _Bot(html_error=True)
    ctx = _mkctx(bot)
    asyncio.run(render(_Upd(), ctx, "<b>سلام</b>"))
    assert bot.sent, "باید پیام برسد"
    assert bot.sent[-1] == "سلام", f"متن باید بدون تگ باشد: {bot.sent[-1]!r}"


def test_render_does_not_crash_on_bad_request_not_modified():
    from telegram.error import BadRequest
    from newsbot.ui_widgets import render
    bot = _Bot(edit_error=BadRequest("Message is not modified"))
    ctx = _mkctx(bot); ctx.user_data["panel"] = 5
    asyncio.run(render(_Upd(), ctx, "متن"))     # نباید خطا بدهد


# ══════════════════════════════════════════════════════════════════════════
# /ref باید همیشه لینک دعوت را برساند
# ══════════════════════════════════════════════════════════════════════════
def test_ref_still_delivers_link_when_render_fails():
    """اگر رندر کامل شکست خورد، دست‌کم لینک ساده برود؛ کاربر هیچ نکند."""
    from newsbot.ui_views_user import view_referral
    from newsbot.core_users import ensure_user
    uid = 7800
    ensure_user(uid, "u", "U")

    bot = _Bot(html_error=True)
    ctx = _mkctx(bot)

    class _Uu(_U):
        id = uid
    class _Cu(_C):
        id = uid
    up = _Upd(); up.effective_user = _Uu(); up.effective_chat = _Cu()

    asyncio.run(view_referral(up, ctx))
    assert bot.sent, "باید پیامی برود"
    assert any("ref_" in s or "ت.me" in s for s in bot.sent), \
        f"لینک دعوت باید در پیام باشد: {bot.sent}"


def test_ref_does_not_raise_on_db_error():
    """خطای دیتابیس در آمار نباید کل دستور را بشکند."""
    from newsbot.ui_views_user import view_referral
    from newsbot.core_users import ensure_user
    uid = 7801
    ensure_user(uid, "u", "U")

    bot = _Bot()
    ctx = _mkctx(bot)

    class _Uu(_U):
        id = uid
    class _Cu(_C):
        id = uid
    up = _Upd(); up.effective_user = _Uu(); up.effective_chat = _Cu()

    # referral_stats را میشکنیم تا مسیر fallback اجرا شود
    import newsbot.ui_views_user as V
    orig = V.referral_stats
    V.referral_stats = lambda u: (_ for _ in ()).throw(RuntimeError("db down"))
    try:
        asyncio.run(view_referral(up, ctx))
    finally:
        V.referral_stats = orig
    assert bot.sent, "حتی با خطای دیتابیس باید لینک برود"


# ══════════════════════════════════════════════════════════════════════════
# نسخهٔ کد — تشخیص اینکه کدام deploy اجرا میشود
# ══════════════════════════════════════════════════════════════════════════
def test_code_version_reports_running_commit():
    """کاربر باید ببیند کدام کامیت روی سرور اجرا میشود؛ دیگر حدس نمیزنیم."""
    from newsbot.ui_views_super import _code_version
    v = _code_version()
    assert v and v != "unknown", f"نسخهٔ کد باید خوانده شود، گرفتم: {v!r}"
    assert len(v) >= 6


# ══════════════════════════════════════════════════════════════════════════
# چیدمان مینیمال تایتلها — یک سنجه در هر خط
# ══════════════════════════════════════════════════════════════════════════
def test_super_home_title_is_one_item_per_line():
    from newsbot.ui_i18n import tr
    t = tr("fa", "s_title")
    assert "\n\n" in t, "باید فاصله بین تیتر و سنجهها باشد"
    lines = [l for l in t.split("\n") if l.strip() and not l.startswith("👑")]
    for l in lines:
        assert "·" not in l, f"هر خط یک سنجه باشد، نه چند: {l!r}"


def test_health_and_report_titles_are_one_item_per_line():
    from newsbot.scheduler import report_text
    from newsbot.core_channels import add_channel
    from newsbot.core_users import ensure_user
    uid = 7900
    ensure_user(uid, "a", "A")
    cid = add_channel(uid, -100555, "T", "", 1)
    txt = report_text(uid, cid, "fa")
    body = txt.split("\n\n", 1)[1] if "\n\n" in txt else txt
    for line in body.split("\n"):
        if not line.strip():
            continue
        assert line.count("·") <= 1, f"خط شلوغ است: {line!r}"
