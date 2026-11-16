# -*- coding: utf-8 -*-
"""فاز ۴ — توابعِ خالصِ Q&A و هندلرِ گروهِ بحث.

تصمیم‌های مالک (design: docs/superpowers/specs/2026-11-16-phase4-reply-qa-design.md):
  ۱ fail-closed: فقط گروهِ بحثِ رسمیِ همان کانالِ ثبت‌شده پاسخ می‌گیرد.
  ۲ at-most-once: هر پیام حداکثر یک پاسخ؛ پاسخِ گم‌شده دوباره فرستاده نمی‌شود.
  ۳ سهمیهٔ AI از خودِ پرسشگر کم می‌شود.
"""
import asyncio
from types import SimpleNamespace as NS

import pytest

from newsbot import ai_models as amod
from newsbot import config, qa
from newsbot.core_channels import add_channel, channel_by_chat, update_settings


def _channel_post(text="متن پست", mid=77, auto=True):
    return NS(sender_chat=NS(id=-100111, type="channel"), forward_from_chat=None,
              forward_from_message_id=mid, text=text, caption=None,
              is_automatic_forward=auto, message_id=5)


def test_a_reply_to_a_channel_post_is_detected():
    assert qa.detect_post(_channel_post()) == {"channel_chat_id": -100111, "post_id": 77}


def test_a_manual_forward_of_a_channel_post_is_detected():
    r = NS(sender_chat=None, forward_from_chat=NS(id=-100111, type="channel"),
           forward_from_message_id=77, text="x", caption=None,
           is_automatic_forward=False, message_id=5)
    assert qa.detect_post(r) == {"channel_chat_id": -100111, "post_id": 77}


def test_a_reply_to_a_normal_user_message_is_not_a_post():
    r = NS(sender_chat=None, forward_from_chat=None, text="سلام", caption=None,
           is_automatic_forward=False, message_id=5)
    assert qa.detect_post(r) is None


def test_the_post_text_is_stripped_and_capped():
    r = _channel_post(text="<b>" + "ا" * 50 + "</b>")
    out = qa.post_text(r, 20)
    assert "<b>" not in out and len(out) <= 21 and out.endswith("…")


def test_the_caption_is_used_when_the_post_has_no_text():
    r = _channel_post(text=None)
    r.caption = "کپشن"
    assert qa.post_text(r, 100) == "کپشن"


def test_the_question_text_is_capped():
    m = NS(text="س" * 50, caption=None)
    assert len(qa.question_text(m, 10)) == 11      # ۱۰ کاراکتر + …


def test_the_prompt_contains_only_the_post_and_the_question():
    system, user = qa.build_prompt("متنِ پست", "پرسش؟")
    assert "متنِ پست" in user and "پرسش؟" in user
    assert "فقط" in system and "پست" in system


def test_the_answer_is_stripped_and_capped():
    assert len(qa.cap_answer("<b>" + "پ" * 40 + "</b>", 15)) == 16
    assert "<b>" not in qa.cap_answer("<b>x</b>", 100)


def test_the_config_defaults_are_the_designed_values():
    assert config.QA_ENABLED is True
    assert (config.QA_CONTEXT_MAX_CHARS, config.QA_QUESTION_MAX_CHARS,
            config.QA_ANSWER_MAX_CHARS) == (4000, 1000, 900)


# ── هندلرِ گروهِ بحث ────────────────────────────────────────────────────────────────
class _Bot:
    """باتِ ساختگی: فقط get_chat و send_message لازم است."""

    def __init__(self, linked=-100111):
        self.linked, self.sent, self.fail = linked, [], False

    async def get_chat(self, gid):
        return NS(linked_chat_id=self.linked)

    async def send_message(self, chat_id, text, **kw):
        if self.fail:
            raise RuntimeError("network")
        self.sent.append({"chat_id": chat_id, "text": text, **kw})
        return NS(message_id=1)


def _group_msg(gid=-555, mid=10, text="این پست دربارهٔ چیست؟", reply=None):
    return NS(chat_id=gid, message_id=mid, text=text, caption=None,
              chat=NS(type="supergroup"), reply_to_message=reply, date=None)


def _update(msg, uid=900001):
    return NS(effective_message=msg,
              effective_user=NS(id=uid, username="u", full_name="U"))


@pytest.fixture
def chan(isolated_db):
    """کانالِ ثبت‌شدهٔ -100111 که مالکش 900001 است."""
    add_channel(900001, -100111, "کانال", "", 900001)
    return channel_by_chat(-100111)


def _stub_ai(monkeypatch, out="پاسخِ مدل"):
    async def fake(system, user, **kw):
        return out, "m", None
    monkeypatch.setattr(amod, "ai_chat", fake)


def test_a_reply_in_the_linked_group_gets_one_answer(chan, monkeypatch):
    _stub_ai(monkeypatch)
    bot = _Bot()
    asyncio.run(qa.on_group_reply(_update(_group_msg(reply=_channel_post())), NS(bot=bot)))
    assert len(bot.sent) == 1
    assert bot.sent[0]["reply_to_message_id"] == 10
    assert bot.sent[0]["chat_id"] == -555


def test_the_prompt_carries_the_post_text_and_the_question(chan, monkeypatch):
    seen = {}

    async def fake(system, user, **kw):
        seen["user"] = user
        return "ok", "m", None
    monkeypatch.setattr(amod, "ai_chat", fake)
    asyncio.run(qa.on_group_reply(
        _update(_group_msg(reply=_channel_post(text="متنِ خاصِ پست"))), NS(bot=_Bot())))
    assert "متنِ خاصِ پست" in seen["user"]
    assert "این پست دربارهٔ چیست؟" in seen["user"]


def test_the_ai_call_is_charged_to_the_asker(chan, monkeypatch):
    seen = {}

    async def fake(system, user, **kw):
        seen.update(kw)
        return "ok", "m", None
    monkeypatch.setattr(amod, "ai_chat", fake)
    asyncio.run(qa.on_group_reply(_update(_group_msg(reply=_channel_post()), uid=900002),
                                  NS(bot=_Bot())))
    assert seen.get("owner_id") == 900002


def test_a_group_linked_to_another_channel_is_silent(chan, monkeypatch):
    _stub_ai(monkeypatch)
    bot = _Bot(linked=-999999)
    asyncio.run(qa.on_group_reply(_update(_group_msg(reply=_channel_post())), NS(bot=bot)))
    assert bot.sent == []


def test_an_unknown_group_link_is_silent(chan, monkeypatch):
    _stub_ai(monkeypatch)
    bot = _Bot(linked=None)
    asyncio.run(qa.on_group_reply(_update(_group_msg(reply=_channel_post(auto=False))),
                                  NS(bot=bot)))
    assert bot.sent == []


def test_a_reply_to_an_unregistered_channel_is_silent(isolated_db, monkeypatch):
    """کانالی که در ربات ثبت نشده ⇒ پستِ فورواردشده هم پاسخ نمی‌گیرد (fail-closed)."""
    _stub_ai(monkeypatch)
    bot = _Bot()
    asyncio.run(qa.on_group_reply(_update(_group_msg(reply=_channel_post())), NS(bot=bot)))
    assert bot.sent == []


def test_a_message_without_a_reply_is_silent(chan, monkeypatch):
    _stub_ai(monkeypatch)
    bot = _Bot()
    asyncio.run(qa.on_group_reply(_update(_group_msg(reply=None)), NS(bot=bot)))
    assert bot.sent == []


def test_the_same_message_is_answered_at_most_once(chan, monkeypatch):
    _stub_ai(monkeypatch)
    bot = _Bot()
    u = _update(_group_msg(reply=_channel_post()))
    asyncio.run(qa.on_group_reply(u, NS(bot=bot)))
    asyncio.run(qa.on_group_reply(u, NS(bot=bot)))
    assert len(bot.sent) == 1


def test_a_failed_delivery_is_not_retried(chan, monkeypatch):
    _stub_ai(monkeypatch)
    bot = _Bot()
    bot.fail = True
    u = _update(_group_msg(reply=_channel_post()))
    with pytest.raises(RuntimeError):
        asyncio.run(qa.on_group_reply(u, NS(bot=bot)))
    bot.fail = False
    asyncio.run(qa.on_group_reply(u, NS(bot=bot)))
    assert bot.sent == []


def test_a_media_only_post_gets_an_honest_message_without_ai(chan, monkeypatch):
    called = {"n": 0}

    async def fake(*a, **k):
        called["n"] += 1
        return "x", "m", None
    monkeypatch.setattr(amod, "ai_chat", fake)
    bot = _Bot()
    asyncio.run(qa.on_group_reply(_update(_group_msg(reply=_channel_post(text=None))),
                                  NS(bot=bot)))
    assert called["n"] == 0 and len(bot.sent) == 1


def test_an_empty_question_gets_an_honest_message_without_ai(chan, monkeypatch):
    called = {"n": 0}

    async def fake(*a, **k):
        called["n"] += 1
        return "x", "m", None
    monkeypatch.setattr(amod, "ai_chat", fake)
    bot = _Bot()
    asyncio.run(qa.on_group_reply(
        _update(_group_msg(reply=_channel_post(), text="")), NS(bot=bot)))
    assert called["n"] == 0 and len(bot.sent) == 1


def test_an_ai_failure_delivers_one_honest_error(chan, monkeypatch):
    async def fake(*a, **k):
        return None, None, "ai_error"
    monkeypatch.setattr(amod, "ai_chat", fake)
    bot = _Bot()
    asyncio.run(qa.on_group_reply(_update(_group_msg(reply=_channel_post())), NS(bot=bot)))
    assert len(bot.sent) == 1 and bot.sent[0]["text"] == qa.AI_FAIL


def test_an_exhausted_quota_delivers_the_quota_message(chan, monkeypatch):
    async def fake(*a, **k):
        return None, None, "ai_limit"
    monkeypatch.setattr(amod, "ai_chat", fake)
    bot = _Bot()
    asyncio.run(qa.on_group_reply(_update(_group_msg(reply=_channel_post())), NS(bot=bot)))
    assert len(bot.sent) == 1 and "سهمیه" in bot.sent[0]["text"]


def test_the_feature_toggle_off_means_silence(chan, monkeypatch):
    update_settings(chan["id"], qa_enabled=False)
    _stub_ai(monkeypatch)
    bot = _Bot()
    asyncio.run(qa.on_group_reply(_update(_group_msg(reply=_channel_post())), NS(bot=bot)))
    assert bot.sent == []


def test_the_global_kill_switch_means_silence(chan, monkeypatch):
    monkeypatch.setattr(config, "QA_ENABLED", False)
    _stub_ai(monkeypatch)
    bot = _Bot()
    asyncio.run(qa.on_group_reply(_update(_group_msg(reply=_channel_post())), NS(bot=bot)))
    assert bot.sent == []


def test_the_log_does_not_leak_the_question(chan, monkeypatch, caplog):
    _stub_ai(monkeypatch)
    asyncio.run(qa.on_group_reply(
        _update(_group_msg(reply=_channel_post(), text="رازِ سؤال")), NS(bot=_Bot())))
    assert "رازِ سؤال" not in caplog.text


def test_two_concurrent_handlers_send_one_answer(chan, monkeypatch):
    _stub_ai(monkeypatch)
    bot = _Bot()
    u = _update(_group_msg(reply=_channel_post()))

    async def both():
        await asyncio.gather(qa.on_group_reply(u, NS(bot=bot)),
                             qa.on_group_reply(u, NS(bot=bot)))
    asyncio.run(both())
    assert len(bot.sent) == 1


# ── ثبتِ هندلر در برنامه ───────────────────────────────────────────────────────────
def test_the_group_reply_handler_is_registered():
    import inspect

    from newsbot import app
    src = inspect.getsource(app.main)
    assert "on_group_reply" in src, "هندلرِ گروه باید در main ثبت شود، وگرنه هرگز اجرا نمی‌شود"
    assert "filters.ChatType.GROUPS" in src, "ثبت باید فقط روی گروهِ بحث باشد، نه چتِ خصوصی"


# ── کلیدِ per-channel در پنلِ مدیر (تصمیمِ مالک ۴) ──────────────────────────────────
def test_qa_enabled_defaults_on_for_a_channel(isolated_db):
    from newsbot.core_channels import get_settings
    add_channel(900001, -100111, "کانال", "", 900001)
    assert get_settings(channel_by_chat(-100111)["id"])["qa_enabled"] is True


def test_the_toggle_is_a_known_toggleable_setting():
    from newsbot.ui_i18n import TOGGLABLE, tr
    assert "qa_enabled" in TOGGLABLE
    assert tr("fa", "tog_qa_enabled") and tr("en", "tog_qa_enabled")
    assert tr("fa", "togd_qa_enabled") and tr("en", "togd_qa_enabled")
    assert tr("fa", "qa_btn", i="🟢") and tr("en", "qa_btn", i="🟢")


def test_the_channel_panel_has_the_qa_toggle_button():
    import inspect

    from newsbot import ui_views_admin
    src = inspect.getsource(ui_views_admin.view_channel)
    assert "qa_enabled" in src and "qa_btn" in src


def test_the_toggle_callback_returns_to_the_channel_panel():
    import inspect

    from newsbot import ui_dispatch
    src = inspect.getsource(ui_dispatch)
    assert "qa_enabled" in src, "کال‌بکِ a:tog:<cid>:qa_enabled باید به پنلِ کانال برگردد"
