# -*- coding: utf-8 -*-
"""فاز ۶ — چرخهٔ نشانگرِ «در حال تایپ» (typing lifecycle).

تست‌های پیکربندی: سه مقدارِ env-backed و مستندسازیِ آنها در `.env.example` و preflight.
تست‌های Task 2: ضربان‌گرِ per-chat با refcount، سکوتِ ۳۰ثانیه‌ای پس از سه شکست و پاک‌سازیِ ایمن از لغو.
"""
import asyncio
import time
from pathlib import Path

import pytest
from types import SimpleNamespace as NS
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
        remaining = ca._muted_until[-555] - time.monotonic()
        assert 29.5 <= remaining <= 31, f"پنجرهٔ سکوت باید ~۳۰ ثانیه باشد، نه {remaining:.1f}s"

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


def test_a_crashing_heartbeat_never_breaks_the_caller_and_is_drained(fast_clock, monkeypatch, caplog):
    async def boom(*args, **kwargs):
        raise RuntimeError("heartbeat exploded")

    monkeypatch.setattr(ca, "_heartbeat", boom)

    async def scenario():
        with caplog.at_level("DEBUG", logger="core"):
            async with ca.typing_while(_Bot(), -99):
                await asyncio.sleep(0.05)
        assert -99 not in ca._active
        assert not _typing_tasks()

    asyncio.run(scenario())
    assert any("heartbeat ended with error chat=-99" in r.getMessage() for r in caplog.records)


# ── Task 3: خاموشی، ضربان‌گرها را هم جمع می‌کند ──────────────────────────────

def test_shutdown_clears_typing_registry_and_keeps_unrelated_tasks(fast_clock, monkeypatch):
    """post_shutdown تسکِ typing را لغو و رجیستری را خالی می‌کند؛ تسک نامرتبط دست‌نخورده می‌ماند."""
    import newsbot.app as APP

    monkeypatch.setattr(APP, "_SHUTDOWN_CANCEL_TIMEOUT", 0.3)
    monkeypatch.setattr(APP, "_SHUTDOWN_GRACE", 0.2)
    ca.reset_shutdown()
    try:
        async def scenario():
            async def long_typing():
                async with ca.typing_while(_Bot(), -777, kind="qa"):
                    await asyncio.sleep(30)

            t1 = asyncio.create_task(long_typing(), name="typing:-777:qa")
            t2 = asyncio.create_task(asyncio.sleep(30), name="other:keep")
            await asyncio.sleep(0.05)
            assert -777 in ca._active                      # ضربان‌گر واقعاً زنده است

            await APP.post_shutdown(None)

            typing_done = t1.done()
            registry_empty = ca._active == {}
            stray_typing = [t for t in asyncio.all_tasks()
                            if t.get_name().startswith("typing:") and not t.done()]
            unrelated_pending = not t2.done()
            t2.cancel()                                      # نظافت داخلی حلقه (مثل تست فاز ۸)
            try:
                await t2
            except asyncio.CancelledError:
                pass
            await asyncio.sleep(0)                           # فرصتِ unwind به context
            return t1, typing_done, registry_empty, stray_typing, unrelated_pending

        t1, typing_done, registry_empty, stray_typing, unrelated_pending = asyncio.run(scenario())
        assert typing_done and t1.done(), "تسکِ typing پس از خاموشی تمام نشد"
        assert t1.cancelled(), "تسکِ typing باید لغو شده باشد"
        assert registry_empty and ca._active == {}, "رجیستریِ ضربان‌گرها خالی نشد"
        assert not stray_typing, "هیچ تسکِ typing نباید زنده بماند"
        assert unrelated_pending, "تسک نامرتبط نباید لغو شود"
        # unwind بدونِ KeyError: اگر finally رجیستری را دوباره حذف می‌کرد، همین‌جا می‌ترکید.
    finally:
        ca.reset_shutdown()


# ── Task 4: نصب در مرزِ عملیات و در Q&A گروهِ بحث ────────────────────────────

class _QABot:
    """رباتِ Q&A: علاوه بر send_message / get_chat، chat action هم ثبت میکند."""

    def __init__(self, linked=-100111):
        self.linked, self.calls, self.sent = linked, [], []

    async def send_chat_action(self, chat_id, action):
        self.calls.append((chat_id, action))

    async def get_chat(self, gid):
        return NS(linked_chat_id=self.linked)

    async def send_message(self, chat_id, text, **kw):
        self.sent.append({"chat_id": chat_id, "text": text, **kw})
        return NS(message_id=1)


@pytest.fixture
def qa_chan(isolated_db):
    from newsbot.core_channels import add_channel, channel_by_chat
    add_channel(900001, -100111, "کانال", "", 900001)
    return channel_by_chat(-100111)


def _qa_update():
    reply = NS(sender_chat=NS(id=-100111, type="channel"), forward_from_chat=None,
               forward_from_message_id=77, text="متن پست", caption=None,
               is_automatic_forward=True, message_id=5)
    msg = NS(chat_id=-555, message_id=10, text="سؤال؟", caption=None,
             chat=NS(type="supergroup"), reply_to_message=reply, date=None)
    return NS(effective_message=msg,
              effective_user=NS(id=900001, username="u", full_name="U"))


def test_run_user_operation_sends_typing_when_a_bot_is_present(fast_clock, monkeypatch):
    from newsbot import ui_widgets as uw
    monkeypatch.setattr(uw, "user_lang", lambda uid: "fa")
    bot = _Bot()

    async def work():
        await asyncio.sleep(0.08)
        return "ok"

    update = NS(effective_user=NS(id=910001), effective_chat=NS(id=-555))
    ctx = NS(bot=bot, user_data={})
    result = asyncio.run(uw.run_user_operation(update, ctx, "test:phase6", None, work))
    assert result == "ok"
    assert any(cid == -555 and action == ChatAction.TYPING for cid, action in bot.calls)


def test_run_user_operation_without_a_bot_is_safe(fast_clock, monkeypatch):
    from newsbot import ui_widgets as uw
    monkeypatch.setattr(uw, "user_lang", lambda uid: "fa")

    async def work():
        await asyncio.sleep(0.02)
        return "ok"

    update = NS(effective_user=NS(id=910002), effective_chat=NS(id=-556))
    ctx = NS(user_data={})

    async def scenario():
        assert await uw.run_user_operation(update, ctx, "test:phase6:nobot", None, work) == "ok"
        assert not _typing_tasks()

    asyncio.run(scenario())


def test_group_qa_uses_typing_during_the_ai_wait(fast_clock, qa_chan, monkeypatch):
    from newsbot import ai_models as amod, qa

    async def slow(system, user, **kw):
        await asyncio.sleep(0.08)
        return "پاسخِ مدل", "m", None

    monkeypatch.setattr(amod, "ai_chat", slow)
    bot = _QABot()
    asyncio.run(qa.on_group_reply(_qa_update(), NS(bot=bot)))
    assert len(bot.sent) == 1
    assert any(cid == -555 and action == ChatAction.TYPING for cid, action in bot.calls)


def test_a_quota_refusal_sends_no_chat_action(fast_clock, qa_chan, monkeypatch):
    from newsbot import qa
    monkeypatch.setattr(qa, "claim", lambda *a, **k: "cooldown")
    bot = _QABot()
    asyncio.run(qa.on_group_reply(_qa_update(), NS(bot=bot)))
    assert bot.sent == []
    assert bot.calls == []
