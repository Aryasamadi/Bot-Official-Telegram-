# -*- coding: utf-8 -*-
"""فاز ۶ — چرخهٔ نشانگرِ «در حال تایپ» (typing lifecycle).

تست‌های پیکربندی: سه مقدارِ env-backed و مستندسازیِ آنها در `.env.example` و preflight.
تست‌های Task 2: ضربان‌گرِ per-chat با refcount، سکوتِ ۳۰ثانیه‌ای پس از سه شکست و پاک‌سازیِ ایمن از لغو.
"""
import asyncio
import time
from pathlib import Path

import pytest
from telegram.constants import ChatAction

from newsbot import chat_action as ca
from newsbot import config

ROOT = Path(__file__).resolve().parents[1]


def test_typing_config_defaults_are_the_designed_values():
    assert config.TYPING_ENABLED is True
    assert (config.TYPING_START_DELAY_S, config.TYPING_REFRESH_S) == (1.0, 4.0)


def test_env_example_and_preflight_carry_the_three_names():
    env = (ROOT / ".env.example").read_text(encoding="utf-8", errors="replace")
    app = (ROOT / "newsbot" / "app.py").read_text(encoding="utf-8", errors="replace")
    assert all(n in env for n in ("TYPING_ENABLED", "TYPING_START_DELAY_S", "TYPING_REFRESH_S"))
    assert "TYPING_START_DELAY_S" in app and "TYPING_REFRESH_S" in app


# ── Task 2: ضربان‌گرِ «در حال تایپ» (chat_action.typing_while) ────────────────

class _Bot:
    """رباتِ جعلی: هر send_chat_action را ثبت می‌کند و می‌تواند n بار شکست بخورد."""

    def __init__(self, fail=0):
        self.calls, self.fail = [], fail

    async def send_chat_action(self, chat_id, action):
        self.calls.append((chat_id, action))
        if self.fail:
            self.fail -= 1
            raise RuntimeError("429")


@pytest.fixture
def fast_clock(monkeypatch):
    """ساعتِ سریع + رجیستریِ تمیز.

    عمداً autouse نیست: تست‌های پیکربندیِ Task 1 مقادیرِ پیش‌فرضِ config را می‌سنجند و
    نباید با monkeypatch آلوده شوند. هر تستِ Task 2 این fixture را صریح می‌خواهد.
    """
    monkeypatch.setattr(config, "TYPING_START_DELAY_S", 0.01)
    monkeypatch.setattr(config, "TYPING_REFRESH_S", 0.01)
    monkeypatch.setattr(config, "TYPING_ENABLED", True)
    ca._active.clear()
    ca._muted_until.clear()
    ca.reset_shutdown()
    yield
    ca._active.clear()
    ca._muted_until.clear()
    ca.reset_shutdown()


def _typing_tasks():
    return [t for t in asyncio.all_tasks() if t.get_name().startswith("typing:")]


def test_disabled_emits_nothing(fast_clock, monkeypatch):
    async def scenario():
        monkeypatch.setattr(config, "TYPING_ENABLED", False)
        bot = _Bot()
        async with ca.typing_while(bot, -555):
            await asyncio.sleep(0.08)
        assert bot.calls == []
        assert ca._active == {}

    asyncio.run(scenario())


def test_no_action_for_an_operation_shorter_than_the_delay(fast_clock):
    async def scenario():
        bot = _Bot()
        async with ca.typing_while(bot, -555):
            await asyncio.sleep(0)
        assert bot.calls == []

    asyncio.run(scenario())


def test_a_long_wait_sends_typing_repeatedly(fast_clock):
    async def scenario():
        bot = _Bot()
        async with ca.typing_while(bot, -555, kind="qa"):
            await asyncio.sleep(0.08)
        assert len(bot.calls) >= 2
        assert all(cid == -555 and action == ChatAction.TYPING for cid, action in bot.calls)

    asyncio.run(scenario())


def test_normal_exit_reaps_the_heartbeat(fast_clock):
    async def scenario():
        bot = _Bot()
        async with ca.typing_while(bot, -555):
            await asyncio.sleep(0.08)
        assert -555 not in ca._active
        assert not _typing_tasks()

    asyncio.run(scenario())


def test_exception_stops_and_reaps_the_heartbeat(fast_clock):
    async def scenario():
        bot = _Bot()
        with pytest.raises(ValueError):
            async with ca.typing_while(bot, -555):
                await asyncio.sleep(0.08)
                raise ValueError("work failed")
        assert -555 not in ca._active
        assert not _typing_tasks()

    asyncio.run(scenario())


def test_cancellation_stops_and_reaps_the_heartbeat(fast_clock):
    async def scenario():
        bot = _Bot()

        async def work():
            async with ca.typing_while(bot, -555):
                await asyncio.sleep(30)

        t = asyncio.create_task(work())
        await asyncio.sleep(0.03)
        t.cancel()
        with pytest.raises(asyncio.CancelledError):
            await t
        assert -555 not in ca._active
        assert not _typing_tasks()

    asyncio.run(scenario())


def test_three_failures_mute_the_chat_for_thirty_seconds(fast_clock):
    async def scenario():
        bot = _Bot(fail=10)
        async with ca.typing_while(bot, -555):
            await asyncio.sleep(0.08)
        assert len(bot.calls) <= 4
        assert -555 not in ca._active
        assert ca._muted_until[-555] > time.monotonic()
        assert ca._muted_until[-555] - time.monotonic() <= 31

        before = len(bot.calls)
        async with ca.typing_while(bot, -555):      # داخلِ پنجرهٔ سکوت: صفر فراخوانیِ تازه
            await asyncio.sleep(0.08)
        assert len(bot.calls) == before
        assert -555 not in ca._active
        assert not _typing_tasks()

        bot.fail = 0                                  # پس از پایانِ پنجره، ضربانِ تازه مجاز است
        ca._muted_until[-555] = time.monotonic() - 1
        async with ca.typing_while(bot, -555):
            await asyncio.sleep(0.05)
        assert len(bot.calls) > before
        assert -555 not in ca._active

    asyncio.run(scenario())


def test_two_entrants_in_one_chat_share_one_heartbeat(fast_clock):
    async def scenario():
        bot = _Bot()

        async def entrant():
            async with ca.typing_while(bot, -555):
                await asyncio.sleep(0.08)

        await asyncio.gather(entrant(), entrant())
        assert len(bot.calls) >= 2 and len({cid for cid, _ in bot.calls}) == 1
        assert -555 not in ca._active
        assert not _typing_tasks()

    asyncio.run(scenario())


def test_two_chats_have_independent_heartbeats(fast_clock):
    async def scenario():
        bot = _Bot()

        async def one(cid):
            async with ca.typing_while(bot, cid):
                await asyncio.sleep(0.08)

        await asyncio.gather(one(-1), one(-2))
        assert {cid for cid, _ in bot.calls} == {-1, -2}
        assert ca._active == {}

    asyncio.run(scenario())


def test_stop_all_typing_reaps_every_heartbeat_and_is_idempotent(fast_clock):
    async def scenario():
        bot = _Bot()

        async def entrant(cid):
            async with ca.typing_while(bot, cid):
                await asyncio.sleep(30)

        tasks = [asyncio.create_task(entrant(-1)), asyncio.create_task(entrant(-2))]
        await asyncio.sleep(0.03)
        assert set(ca._active) == {-1, -2}
        await ca.stop_all_typing()
        assert ca._active == {}
        assert not _typing_tasks()
        await ca.stop_all_typing()                    # فراخوانیِ دوم باید بی‌خطر باشد
        assert ca._active == {}
        for t in tasks:
            t.cancel()
        for t in tasks:
            with pytest.raises(asyncio.CancelledError):
                await t
        assert ca._active == {}

    asyncio.run(scenario())


def test_mark_shutdown_overrides_the_stop_reason(fast_clock, caplog):
    async def scenario():
        ca.mark_shutdown()
        with caplog.at_level("DEBUG", logger="core"):
            async with ca.typing_while(_Bot(), -9):
                await asyncio.sleep(0.02)

    asyncio.run(scenario())
    assert any("typing: stop chat=-9 reason=shutdown" in r.getMessage() for r in caplog.records)
    assert ca._active == {}


def test_a_bot_without_send_chat_action_is_a_silent_noop(fast_clock):
    class _Dumb:
        pass

    async def scenario():
        async with ca.typing_while(_Dumb(), -5):
            await asyncio.sleep(0.03)
        assert ca._active == {}
        assert not _typing_tasks()

    asyncio.run(scenario())


def test_start_and_stop_lines_are_debug_and_carry_only_ids(fast_clock, caplog):
    async def scenario():
        with caplog.at_level("DEBUG", logger="core"):
            async with ca.typing_while(_Bot(), -42, kind="qa"):
                await asyncio.sleep(0.02)

    asyncio.run(scenario())
    msgs = [r.getMessage() for r in caplog.records]
    assert "typing: start chat=-42 kind=qa" in msgs
    assert "typing: stop chat=-42 reason=normal" in msgs


def test_stop_reason_is_exception_when_the_body_raises(fast_clock, caplog):
    async def scenario():
        with caplog.at_level("DEBUG", logger="core"):
            with pytest.raises(ValueError):
                async with ca.typing_while(_Bot(), -7):
                    await asyncio.sleep(0.02)
                    raise ValueError("boom")

    asyncio.run(scenario())
    assert any("typing: stop chat=-7 reason=exception" in r.getMessage() for r in caplog.records)
