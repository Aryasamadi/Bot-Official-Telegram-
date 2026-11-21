# -*- coding: utf-8 -*-
"""فاز ۶ — نشانگرِ بومیِ «در حال تایپ» برای هر چت (typing lifecycle).

یک ضربان‌گرِ per-chat با شمارشِ ارجاع: اولین ورود یک تسکِ نام‌دار (`typing:<chat>:<kind>`)
می‌سازد که پس از تأخیرِ کوتاه، هر چند ثانیه `send_chat_action` می‌فرستد؛ ورودهای بعدی همان
تسک را شریک می‌شوند و تا آخرین خروجِ آن‌ها زنده می‌ماند.

قواعدِ ایمنی:
- شکستِ `send_chat_action` هرگز به فراخواننده نمی‌رسد؛ پس از سه شکستِ پیاپی، چت ۳۰ ثانیه
  ساکت می‌شود تا سرورِ تلگرام را بیشتر اذیت نکنیم.
- توقف از الگویِ shield در `publish.py` پیروی می‌کند: حتی اگر خودِ تسکِ فراخواننده لغو شود،
  ضربان‌گر جمع می‌شود و هیچ تسکِ یتیمی نمی‌ماند.
- لاگ فقط شناسه‌ها را دارد: هیچ متنِ سؤال/پست/توکنی نوشته نمی‌شود.
"""
import asyncio
import contextlib
import time

from telegram.constants import ChatAction

from newsbot import config
from newsbot.logutils import log

#: پس از سه شکستِ پیاپی، چت این‌قدر ثانیه ساکت می‌ماند.
MUTE_S = 30.0
#: آستانهٔ شکست‌هایِ پیاپی پیش از سکوت.
MAX_FAILURES = 3

#: قلاب‌هایِ تست: ضربان‌گرهایِ فعال به‌ازایِ هر چت و پایانِ سکوتِ هر چت.
_active: dict[int, "_Heartbeat"] = {}
_muted_until: dict[int, float] = {}

_shutdown = False


class _Heartbeat:
    """ثبتِ یک ضربان‌گر: تسک، تعدادِ ورودهایِ هم‌زمان و نوعِ عملیات."""

    __slots__ = ("task", "count", "kind")

    def __init__(self, task, kind):
        self.task = task
        self.count = 1
        self.kind = kind


def mark_shutdown() -> None:
    """پرچمِ خاموشی را می‌نشاند تا دلیلِ توقف در لاگ `shutdown` شود."""
    global _shutdown
    _shutdown = True


def reset_shutdown() -> None:
    """پرچمِ خاموشی را پاک می‌کند (کمکیِ تست؛ تا تستی تستِ بعدی را آلوده نکند)."""
    global _shutdown
    _shutdown = False


def _discard_own(chat_id: int) -> None:
    """ثبتِ همین چت را فقط اگر متعلق به تسکِ جاری است حذف می‌کند."""
    rec = _active.get(chat_id)
    if rec is not None and rec.task is asyncio.current_task():
        _active.pop(chat_id, None)


async def _heartbeat(bot, chat_id: int, kind: str) -> None:
    """بدنهٔ ضربان‌گر: تأخیرِ شروع، سپس ارسالِ دوره‌ایِ TYPING."""
    fails = 0
    try:
        await asyncio.sleep(config.TYPING_START_DELAY_S)
        while True:
            try:
                await bot.send_chat_action(chat_id, ChatAction.TYPING)
                fails = 0
            except Exception:
                fails += 1
                log.debug("typing: send failed chat=%s kind=%s fails=%s", chat_id, kind, fails)
                if fails >= MAX_FAILURES:
                    _muted_until[chat_id] = time.monotonic() + MUTE_S
                    log.debug("typing: mute chat=%s seconds=%s", chat_id, int(MUTE_S))
                    _discard_own(chat_id)
                    return
            await asyncio.sleep(config.TYPING_REFRESH_S)
    except asyncio.CancelledError:
        raise


async def _reap(chat_id: int, rec: _Heartbeat, reason: str) -> None:
    """تسکِ ضربان‌گر را لغو و تا تمام‌شدنش جمع می‌کند؛ ثبت را فقط اگر مالِ ماست برمی‌دارد."""
    task = rec.task
    if not task.done():
        task.cancel()
    # الگویِ `publish.py:447-458`: لغوِ خودِ فراخواننده نباید توقف را نصفه رها کند.
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            continue
        except Exception:
            break
    if _active.get(chat_id) is rec:
        _active.pop(chat_id, None)
    log.debug("typing: stop chat=%s reason=%s", chat_id, "shutdown" if _shutdown else reason)


@contextlib.asynccontextmanager
async def typing_while(bot, chat_id: int, kind: str = "op"):
    """در طولِ بلوک، نشانگرِ «در حال تایپ» را برای `chat_id` روشن نگه می‌دارد.

    اگر ربات/قابلیت/چت در دسترس نباشد (یا چت در سکوتِ پس از شکست باشد) هیچ کاری نمی‌کند.
    """
    send = getattr(bot, "send_chat_action", None)
    if bot is None or chat_id is None or not callable(send) or not config.TYPING_ENABLED:
        yield
        return

    muted_until = _muted_until.get(chat_id)
    if muted_until is not None:
        if muted_until > time.monotonic():
            yield
            return
        _muted_until.pop(chat_id, None)      # پنجرهٔ سکوت گذشته است

    rec = _active.get(chat_id)
    if rec is None:
        task = asyncio.create_task(_heartbeat(bot, chat_id, kind), name=f"typing:{chat_id}:{kind}")
        rec = _Heartbeat(task, kind)
        _active[chat_id] = rec
        log.debug("typing: start chat=%s kind=%s", chat_id, kind)
    else:
        rec.count += 1

    reason = "normal"
    try:
        yield
    except asyncio.CancelledError:
        reason = "cancel"
        raise
    except BaseException:
        reason = "exception"
        raise
    finally:
        if _active.get(chat_id) is rec:       # اگر ضربان‌گر خودش ساکت شده یا shutdown جمعش کرده: بی‌صدا
            rec.count -= 1
            if rec.count <= 0:
                await _reap(chat_id, rec, reason)


async def stop_all_typing() -> None:
    """همهٔ ضربان‌گرها را لغو و جمع می‌کند و رجیستری را خالی می‌گذارد (تکرارپذیر)."""
    # حلقه به‌جای یک گذر: اگر در حین await یک ورودِ تازه ضربان‌گر بسازد، آن هم جمع می‌شود.
    while _active:
        chat_id, rec = next(iter(_active.items()))
        await _reap(chat_id, rec, "shutdown")
    _active.clear()
