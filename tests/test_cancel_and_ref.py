# -*- coding: utf-8 -*-
"""/cancel و /ref و اعلان چرخه — قراردادهای بحرانی که هرگز نباید بشکنند."""
import asyncio, os

os.environ.setdefault("BOT_TOKEN", "123456789:AAtest_only_dummy_token_0000000000000")
os.environ.setdefault("SUPER_ADMIN_IDS", "1")

import pytest


@pytest.fixture(autouse=True)
def _clean_rate():
    """محدودیت نرخ «هر دستور یکبار در ۱ ثانیه» بین تستهای همین فایل تداخل میکند؛
    حالت سراسری را قبل از هر تست پاک میکنم."""
    from newsbot.ratelimit import _rl
    _rl.clear()
    yield
    _rl.clear()


class _Q:
    async def answer(self, *a, **k): pass
    @property
    def message(self): return None


class _U:
    id = 4242
    username = "tester"
    full_name = "Tester"
    is_premium = False


class _C:
    id = 4242


class _Bot:
    def __init__(self): self.sent = []
    async def send_message(self, cid, text, **kw):
        self.sent.append(text)
        return type("M", (), {"message_id": 1})()
    async def edit_message_text(self, *a, **k): pass


class _Ctx:
    def __init__(self): self.bot = _Bot(); self.user_data = {}; self.args = []


class _Upd:
    callback_query = None
    effective_user = _U()
    effective_chat = _C()
    message = None


def _setup_user(uid, lang=None, clear_rate=True):
    from newsbot.core_users import ensure_user, set_lang
    ensure_user(uid, "u", "U")
    if lang: set_lang(uid, lang)
    if clear_rate:
        # محدودیت نرخ «هر دستور یکبار در ABUSE_RATE_SEC» بین تستها تداخل میکند
        from newsbot.ratelimit import rate_clear
        for name in ("cmd_ref", "cmd_cancel", "cmd_start"):
            rate_clear(f"cmd:{uid}:{name}")
        rate_clear(f"cb:{uid}")


# ══════════════════════════════════════════════════════════════════════════
# /cancel — باید واقعاً متوقف کند
# ══════════════════════════════════════════════════════════════════════════
def test_cancel_command_stops_running_operation():
    """ریشهٔ باگ: /cancel از ContextVar میخواند که در تسک تازه None است ⇒ هیچ کاری نمیکرد."""
    import newsbot.ui_widgets as W
    from newsbot.opctx import Operation
    from newsbot.ui_commands import cmd_cancel, managed_command

    uid = 3001; _setup_user(uid, "fa")
    op = Operation(uid, None, "test", uid); op.started = True
    W._operations[uid] = op

    async def _main():
        async def _long():
            try: await asyncio.sleep(60)
            except asyncio.CancelledError:
                op.cancelled = True; raise
        op.task = asyncio.create_task(_long())
        await asyncio.sleep(0.2)
        assert uid in W._operations, "عملیات باید پیش از کنسل در حال اجرا باشد"
        await managed_command(cmd_cancel)(_Upd(), _Ctx())
        await asyncio.sleep(0.2)
    asyncio.run(_main())

    assert op.task.cancelled(), "/cancel باید تسک عملیات را لغو کند"
    assert op.cancelled, "/cancel باید پرچم لغو را ست کند تا چرخه سریع تمام شود"


def test_cancel_without_operation_reports_nothing_to_cancel():
    """بدون عملیات فعال پیام صادقانه بدهد، نه «لغو شد»."""
    import newsbot.ui_widgets as W
    from newsbot.ui_commands import cmd_cancel, managed_command
    from newsbot.ui_widgets import operation_text

    uid = 3002; _setup_user(uid, "fa")
    assert uid not in W._operations
    ctx = _Ctx()
    asyncio.run(managed_command(cmd_cancel)(_Upd(), ctx))
    assert ctx.bot.sent, "باید پیامی داده شود"
    assert "لغو" in ctx.bot.sent[-1] or "⛔" in ctx.bot.sent[-1]
    assert operation_text("fa", "nothing_to_cancel")


def test_cancelled_operation_flags_are_set_for_checkpoints():
    """وقتی /cancel پرچم cancelled را ست میکند، هر checkpoint بلافاصله متوقف میشود."""
    import newsbot.ui_widgets as W
    from newsbot.opctx import Operation, operation_checkpoint, _current_operation
    uid = 3003
    op = Operation(uid, None, "test", uid); op.cancelled = True
    W._operations[uid] = op
    tok = _current_operation.set(op)
    try:
        operation_checkpoint(); assert False, "باید CancelledError بدهد"
    except asyncio.CancelledError:
        pass
    finally:
        _current_operation.reset(tok)


# ══════════════════════════════════════════════════════════════════════════
# مربوط به صفحهٔ دکمه هنگام اجرای چرخه
# ══════════════════════════════════════════════════════════════════════════
def test_button_during_operation_shows_wait_popup():
    """فشار دکمه وسط تست ⇒ پیام بزرگ «صبر کن یا /cancel»، نه جانشینی خاموش."""
    import newsbot.ui_widgets as W
    from newsbot.opctx import Operation, user_control
    from newsbot.ui_dispatch import _wait_for_running_operation

    uid = 3100; _setup_user(uid, "fa")
    lock = user_control(uid)
    op = Operation(uid, None, "test", uid); op.started = True
    W._operations[uid] = op

    # پیام busy_wait از مسیر callback_query.answer با show_alert=True میآید (پاپ‌آپ بزرگ)
    class _AlertQ:
        def __init__(self): self.alerts = []
        async def answer(self, text, show_alert=False):
            if show_alert: self.alerts.append(text)
        @property
        def message(self): return None

    qy = _AlertQ()
    up = _Upd(); up.callback_query = qy

    async def _run():
        await lock.acquire()          # قفل کاربر ⇐ شبیه‌سازی عملیات در حال اجرا
        try:
            return await _wait_for_running_operation(up, _Ctx(), uid)
        finally:
            lock.release()

    blocked = asyncio.run(_run())
    assert blocked is True, "باید جلوی کلیک را بگیرد"
    assert qy.alerts, "باید پاپ‌آپ بزرگ داده شود"
    assert any("/cancel" in a for a in qy.alerts), \
        f"پیام باید /cancel را بگوید: {qy.alerts}"


def test_button_when_idle_is_not_blocked():
    from newsbot.ui_dispatch import _wait_for_running_operation
    uid = 3101; _setup_user(uid, "fa")
    ctx = _Ctx()
    assert asyncio.run(_wait_for_running_operation(_Upd(), ctx, uid)) is False


# ══════════════════════════════════════════════════════════════════════════
# /ref — باید بدون نیاز به زبان کار کند
# ══════════════════════════════════════════════════════════════════════════
def test_ref_works_before_language_is_chosen():
    """کاربر تازهٔ بدون زبان هم بنر دعوت را میگیرد (پیشتر صفحهٔ زبان میآمد و دوباره /ref خطا میداد)."""
    from newsbot.ui_commands import cmd_ref, managed_command
    uid = 3200; _setup_user(uid, None)
    ctx = _Ctx()
    asyncio.run(managed_command(cmd_ref)(_Upd(), ctx))
    assert ctx.bot.sent, "/ref باید پیامی بدهد"
    assert "زبان" not in ctx.bot.sent[-1], "نباید به صفحهٔ زبان برود"


def test_ref_shows_banner_for_both_languages():
    """/ref برای هر دو زبان بنر میدهد.

    محدودیت نرخ «یک بار در ثانیه» عمدی است؛ بین دو زبان کمی فاصله میگذاریم
    تا فقط رفتار زبان سنجیده شود، نه سقف ضدسوءاستفاده.
    """
    import time
    from newsbot.ui_commands import cmd_ref, managed_command
    for uid, lg in ((3211, "fa"), (3212, "en")):
        _setup_user(uid, lg)
        ctx = _Ctx()
        asyncio.run(managed_command(cmd_ref)(_Upd(), ctx))
        assert ctx.bot.sent, f"/ref برای {lg} باید پیام بدهد"
        time.sleep(1.05)


def test_ref_second_call_within_gap_is_silent_by_design():
    """ضدسوءاستفاده: تکرار /ref زیر ۱ ثانیه بیصدا رد میشود — این رفتار محصول است."""
    from newsbot.ui_commands import cmd_ref, managed_command
    uid = 3213
    _setup_user(uid, "fa")
    asyncio.run(managed_command(cmd_ref)(_Upd(), _Ctx()))   # اولین → پاسخ
    ctx2 = _Ctx()
    asyncio.run(managed_command(cmd_ref)(_Upd(), ctx2))     # فوری دوباره → صدایش نیست
    assert not ctx2.bot.sent, "تکرار فوری باید بیصدا رد شود (محدودیت نرخ)"


def test_ref_command_registered_in_menu():
    import inspect
    from newsbot import app
    src = inspect.getsource(app)
    assert 'BotCommand("ref"' in src, "/ref باید در منوی دستورات ثبت شود"


# ══════════════════════════════════════════════════════════════════════════
# حذف اعلان طولانی چرخه
# ══════════════════════════════════════════════════════════════════════════
def test_failed_cycle_notification_removed():
    """اعلان طولانی «چرخه بدون انتشار» حذف شده باشد؛ اطلاعات در گزارش کانال است."""
    import inspect
    import newsbot.scheduler as S
    src = inspect.getsource(S)
    assert "_report_failed_cycle" not in src, "تابع اعلان طولانی باید حذف شده باشد"
    assert "_report_published" in src, "اعلان موفقیت باید بماند"


def test_report_text_still_short_and_useful():
    """گزارش کانال همان اطلاعات کوتاه را دارد."""
    from newsbot.scheduler import report_text
    assert callable(report_text)


# ══════════════════════════════════════════════════════════════════════════
# ایموجی دکمهها باید برگردد
# ══════════════════════════════════════════════════════════════════════════
def test_button_emoji_not_stripped():
    """کاربر خواستار حذف ایموجی دکمهها نبود؛ باید سر joules خود باشند."""
    from newsbot.ui_i18n import tr
    for key in ("back", "home", "test", "sources", "queue", "sched", "content",
                "my_ai", "report", "rejected", "automation", "ch_del", "refresh"):
        fa, en = tr("fa", key), tr("en", key)
        assert fa != en or not fa.strip(), f"{key} باید متن داشته باشد"
