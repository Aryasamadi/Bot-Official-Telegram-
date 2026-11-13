# -*- coding: utf-8 -*-
"""OPS-014: READY=1 نباید به در دسترس بودن Telegram API وابسته باشد."""
import asyncio
from types import SimpleNamespace

from newsbot import app as appmod, runtime, watchdog


class _Bot:
    """ربات جعلی: هر فراخوانی شبکه‌ای با خطا می‌شکند (قطعی Telegram)."""
    bot = SimpleNamespace(id=1, username="outagebot")

    async def get_me(self):
        raise ConnectionError("telegram unreachable")

    async def set_my_commands(self, *a, **k):
        raise ConnectionError("telegram unreachable")


def test_post_init_sends_ready_even_if_telegram_is_down(monkeypatch):
    events = []
    monkeypatch.setattr(watchdog, "start", lambda: events.append("wd_start") or None)
    monkeypatch.setattr(watchdog, "notify_ready", lambda: events.append("ready") or True)

    async def _noop(*a, **k):
        return None

    # بعد از بخش شبکه‌ای، نوتیف سوپرادمین و ... ممکن است شبکه بخواهند؛ مهم این است که READY زودتر رفته باشد.
    monkeypatch.setattr("newsbot.notify.notify_super", _noop, raising=False)
    monkeypatch.setattr("newsbot.notify.ensure_super_worker", lambda: None, raising=False)
    monkeypatch.setattr(runtime, "clear_stale_drain", lambda: None)

    app = SimpleNamespace(bot=_Bot(), bot_data={}, job_queue=None)
    try:
        asyncio.run(appmod.post_init(app))
    except Exception:
        pass  # بخش‌های بعدی (DB/اعلان) در تست جعلی می‌توانند بشکنند؛ ترتیب READY مهم است
    assert "ready" in events, "READY=1 باید بدون وابستگی به Telegram ارسال شود"
    assert runtime.BOT_USERNAME == "outagebot", "username از کاربر کش‌شدهٔ PTB می‌آید، نه get_me شبکه‌ای"


def test_run_polling_retries_bootstrap_forever():
    src = open(appmod.__file__, encoding="utf-8").read()
    assert "bootstrap_retries=-1" in src
