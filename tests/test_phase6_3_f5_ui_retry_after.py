# -*- coding: utf-8 -*-
"""فاز ۶.۳ — F5: سازگاری RetryAfter (timedelta) در مسیر ارسال همگانی UI.

این تست‌ها مستقل و آفلاین‌اند: هیچ تماس Telegram/شبکه‌ای ندارند و مسیر واقعی
`ui_dispatch.dispatch(..., "s:bc_go")` را با دوبل‌های سبک اجرا می‌کنند.
"""
import asyncio
from datetime import timedelta
from types import SimpleNamespace

import pytest
from telegram.error import RetryAfter, TelegramError

from newsbot import config, ui_dispatch


class _FakeBot:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.copies = []

    async def copy_message(self, chat_id, from_chat_id, message_id):
        self.copies.append((chat_id, from_chat_id, message_id))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class _FakeContext:
    def __init__(self, bot):
        self.bot = bot
        self.user_data = {"bc_src": (100, 200)}
        self._callback_answered = False


class _FakeAsyncIO:
    def __init__(self):
        self.sleeps = []

    async def sleep(self, delay):
        self.sleeps.append(delay)


class _ValueRetryAfter(RetryAfter):
    """RetryAfter با مقدار دلخواه برای پوشش عدد/float/timedelta/نامعتبر."""
    __slots__ = ("_value",)

    def __init__(self, value):
        TelegramError.__init__(self, "Flood control exceeded")
        self._value = value

    @property
    def retry_after(self): return self._value


def _setup(monkeypatch, user_count, outcomes):
    update = SimpleNamespace(effective_user=SimpleNamespace(id=1))
    bot = _FakeBot(outcomes)
    context = _FakeContext(bot)
    fake_asyncio = _FakeAsyncIO()
    calls = {"status": [], "popup": [], "home": 0}

    monkeypatch.setattr(ui_dispatch, "L", lambda _update: "fa")
    monkeypatch.setattr(ui_dispatch, "user_lang", lambda _uid: "fa")
    monkeypatch.setattr(ui_dispatch, "is_super", lambda _uid: True)
    monkeypatch.setattr(ui_dispatch, "list_users",
                        lambda: [{"id": 1000 + i} for i in range(user_count)])
    monkeypatch.setattr(ui_dispatch, "asyncio", fake_asyncio)

    async def status(_ctx, text, **_kw): calls["status"].append(text)
    async def popup(_upd, _ctx, text): calls["popup"].append(text)
    async def home(_upd, _ctx): calls["home"] += 1

    monkeypatch.setattr(ui_dispatch, "operation_status", status)
    monkeypatch.setattr(ui_dispatch, "popup", popup)
    monkeypatch.setattr(ui_dispatch, "view_super_home", home)
    return update, context, calls, fake_asyncio


def _run(monkeypatch, outcomes):
    update, context, calls, fake_asyncio = _setup(monkeypatch, len(outcomes), outcomes)
    asyncio.run(ui_dispatch.dispatch(update, context, "s:bc_go"))
    return context, calls, fake_asyncio


# ── تبدیل امن مقدار RetryAfter ──

@pytest.mark.parametrize("value, expected", [
    (0, 0.0),
    (5, 5.0),
    (2.5, 2.5),
    (timedelta(seconds=7, microseconds=500000), 7.5),
])
def test_safe_retry_after_seconds_accepts_supported_values(value, expected):
    assert ui_dispatch._safe_retry_after_seconds(value) == expected


@pytest.mark.parametrize("value", [
    None,
    "bad",
    pytest.param(object(), id="unsupported-object"),
    float("nan"),
    float("inf"),
    float("-inf"),
    -1,
    timedelta(seconds=-1),
])
def test_safe_retry_after_seconds_rejects_invalid_values(value):
    assert ui_dispatch._safe_retry_after_seconds(value) is None


# ── مسیر واقعی s:bc_go ──

@pytest.mark.parametrize("retry_value, expected_wait", [
    (5, 6.0),
    (2.5, 3.5),
    (timedelta(seconds=7, microseconds=500000), 8.5),
])
def test_ui_broadcast_honors_numeric_and_timedelta_retry_after(
        monkeypatch, retry_value, expected_wait):
    context, calls, fake_asyncio = _run(monkeypatch, [_ValueRetryAfter(retry_value), None])
    assert fake_asyncio.sleeps == [expected_wait, 0.06, 0.06]
    assert len(context.bot.copies) == 2
    assert calls["popup"] == ["📣 موفق 1 · ناموفق 1"]
    assert calls["home"] == 1


def test_ui_broadcast_real_ptb_timedelta_retry_after(monkeypatch):
    """environ واقعی PTB: مقدار retry_after یک timedelta است، نه عدد."""
    monkeypatch.setenv("PTB_TIMEDELTA", "1")
    error = RetryAfter(retry_after=3)
    assert isinstance(error.retry_after, timedelta), "شبیه‌سازی محیط PTB نامعتبر است"
    context, calls, fake_asyncio = _run(monkeypatch, [error, None])
    assert fake_asyncio.sleeps == [4.0, 0.06, 0.06]
    assert len(context.bot.copies) == 2
    assert calls["popup"] == ["📣 موفق 1 · ناموفق 1"]
    assert calls["home"] == 1


@pytest.mark.parametrize("retry_value", [
    config.RETRY_AFTER_MAX + 500,
    timedelta(seconds=config.RETRY_AFTER_MAX + 500),
])
def test_ui_broadcast_caps_retry_after_wait(monkeypatch, retry_value):
    _context, _calls, fake_asyncio = _run(
        monkeypatch, [_ValueRetryAfter(retry_value), None])
    assert fake_asyncio.sleeps == [float(config.RETRY_AFTER_MAX), 0.06, 0.06]


@pytest.mark.parametrize("bad_value", [
    None,
    "bad",
    pytest.param(object(), id="unsupported-object"),
])
def test_ui_broadcast_invalid_retry_after_continues_without_retry_sleep(
        monkeypatch, bad_value):
    context, calls, fake_asyncio = _run(
        monkeypatch, [_ValueRetryAfter(bad_value), None])
    assert fake_asyncio.sleeps == [0.06, 0.06], "مقدار نامعتبر نباید sleep تأخیری بسازد"
    assert len(context.bot.copies) == 2, "حلقه باید ادامه یابد و به کاربر بعدی برسد"
    assert calls["popup"] == ["📣 موفق 1 · ناموفق 1"]
    assert calls["home"] == 1


def test_ui_broadcast_generic_failure_still_completes(monkeypatch):
    context, calls, fake_asyncio = _run(monkeypatch, [RuntimeError("boom"), None])
    assert fake_asyncio.sleeps == [0.06, 0.06]
    assert len(context.bot.copies) == 2
    assert calls["popup"] == ["📣 موفق 1 · ناموفق 1"]
    assert calls["home"] == 1


def test_ui_broadcast_success_path_preserves_completion_ui(monkeypatch):
    context, calls, fake_asyncio = _run(monkeypatch, [None, None])
    assert fake_asyncio.sleeps == [0.06, 0.06]
    assert len(context.bot.copies) == 2
    assert calls["popup"] == ["📣 موفق 2 · ناموفق 0"]
    assert calls["home"] == 1
