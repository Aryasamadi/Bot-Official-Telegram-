# -*- coding: utf-8 -*-
"""فاز ۸ — دکمهٔ «بازگشت به کانال» در دیپ‌لینک."""
from types import SimpleNamespace

from newsbot.core_channels import msg_link, post_url


def test_post_url_public_and_private_forms():
    assert post_url(-1001234567890, 42, "mychan") == "https://t.me/mychan/42"
    assert post_url(-1001234567890, 42, "") == "https://t.me/c/1234567890/42"
    assert post_url(-1001234567890, 0, "mychan") == ""
    assert post_url(-1001234567890, None, "mychan") == ""
    assert post_url(-1001234567890, "x", "mychan") == ""


def test_post_url_rejects_a_chat_id_that_cannot_form_a_c_link():
    assert post_url(-123456, 7, "") == ""
    assert post_url(None, 7, "") == ""


def test_msg_link_delegates_to_post_url():
    ch = {"chat_id": -1001234567890, "username": "mychan"}
    assert msg_link(ch, SimpleNamespace(message_id=42)) == post_url(-1001234567890, 42, "mychan")
    private = {"chat_id": -100999, "username": ""}
    assert msg_link(private, SimpleNamespace(message_id=7)) == "https://t.me/c/999/7"


import asyncio
import re

from newsbot import publish as pub_mod
from newsbot.core_articles import article_insert, article_update
from newsbot.core_channels import add_channel, channel_by_chat
from newsbot.net_http import load_deeplink


def _chan(chat_id, username=""):
    add_channel(1, chat_id, f"ch{chat_id}", username, 1)
    return channel_by_chat(chat_id)["id"]


def _ready(cid, h, post, full):
    aid = article_insert(1, cid, h, f"https://x.test/{h}", "t", 0, None, status="ready")
    article_update(aid, post_html=post, full_html=full)
    return aid


class _PubBot:
    id = 901
    def __init__(self, username=""):
        self.username = username
        self.sent = []
        self.attempted = []
    async def get_chat_member(self, chat_id, user_id):
        return type("M", (), {"status": "administrator", "can_post_messages": True})()
    async def send_message(self, chat_id, text, **kw):
        self.attempted.append(text)
        if getattr(self, "fail", False):
            raise RuntimeError("send failed")
        self.sent.append((chat_id, text))
        msg = type("Msg", (), {"message_id": len(self.sent)})()
        if self.username:
            msg.chat = type("Chat", (), {"username": self.username})()
        return msg


def _payload_of(bot):
    key = re.search(r"r_([A-Za-z0-9_\-]+)", bot.sent[0][1]).group(1)
    return asyncio.run(load_deeplink(key))


def test_publish_records_the_source_post():
    cid = _chan(820, "pubbot")
    post = "<b>" + "x" * 100 + "</b>"
    aid = _ready(cid, "h-src-1", post, post + " " + "y" * 250)
    bot = _PubBot(username="pubbot")
    ok, _ = asyncio.run(pub_mod.publish_article(bot, aid))
    assert ok is True
    src = _payload_of(bot)["src"]
    assert src == {"chat_id": 820, "message_id": 1, "username": "pubbot"}


def test_failed_publish_records_nothing():
    cid = _chan(821)
    post = "<b>" + "x" * 100 + "</b>"
    aid = _ready(cid, "h-src-2", post, post + " " + "y" * 250)
    bot = _PubBot()
    bot.fail = True
    ok, _ = asyncio.run(pub_mod.publish_article(bot, aid))
    assert ok is False
    from newsbot.db import q
    key = re.search(r"r_([A-Za-z0-9_\-]+)", bot.attempted[0]).group(1)
    assert q("SELECT 1 FROM deeplinks WHERE key=?", (key,), one=True) is None


def test_a_renamed_handle_is_refreshed_fail_safely(monkeypatch):
    _chan(822, "oldhandle")
    ch = channel_by_chat(822)
    msg = type("Msg", (), {"message_id": 5, "chat": type("C", (), {"username": "newhandle"})()})()
    pub_mod._note_channel_handle(ch, msg)
    assert channel_by_chat(822)["username"] == "newhandle"

    def boom(*a, **kw):
        raise RuntimeError("db down")
    monkeypatch.setattr(pub_mod, "update_channel_meta", boom)
    pub_mod._note_channel_handle(channel_by_chat(822), msg)   # must not raise


from telegram import InlineKeyboardMarkup

from newsbot import rich_send
from newsbot import ui_commands as uc
from newsbot.ui_i18n import tr
from newsbot.ui_widgets import U


class _Reply:
    def __init__(self): self.calls = []
    async def reply_text(self, text, **kw): self.calls.append((text, kw))


class _Upd:
    def __init__(self, uid=77):
        self.effective_chat = type("C", (), {"id": uid})()
        self.effective_user = type("U", (), {"id": uid})()
        self.message = _Reply()


class _Ctx:
    bot = type("B", (), {})()


def _render(monkeypatch, payload, lang="fa"):
    sent = []
    async def fake_send(bot, chat_id, text, **kw): sent.append((text, kw))
    async def fake_load(key): return payload
    monkeypatch.setattr(uc.rich_send, "send_content", fake_send)
    monkeypatch.setattr(uc, "load_deeplink", fake_load)
    upd = _Upd()
    asyncio.run(uc.send_deeplink(upd, _Ctx(), "k1"))
    return sent, upd


def test_public_channel_gets_one_url_button(monkeypatch):
    payload = {"short": "s", "full": "full body", "url": "https://x.test/a", "show_source": True,
               "src": {"chat_id": -1001234567890, "message_id": 42, "username": "mychan"}}
    sent, _ = _render(monkeypatch, payload)
    text, kw = sent[0]
    btn = kw["reply_markup"].inline_keyboard[0][0]
    assert btn.url == "https://t.me/mychan/42"
    assert btn.text == tr("fa", "dl_back")
    assert tr("fa", "dl_private_note") not in text and tr("fa", "dl_unknown_note") not in text
    assert kw["markup_last_only"] is True


def test_old_payload_has_no_button_and_no_note(monkeypatch):
    payload = {"short": "s", "full": "full body", "url": "https://x.test/a", "show_source": True}
    sent, _ = _render(monkeypatch, payload)
    text, kw = sent[0]
    assert kw["reply_markup"] is None
    assert tr("fa", "dl_private_note") not in text and tr("fa", "dl_unknown_note") not in text
    assert "t.me/c/" not in text


def test_private_channel_gets_member_note(monkeypatch):
    _chan(-100000830)                            # row exists, username empty ⇒ private
    payload = {"short": "s", "full": "full body",
               "src": {"chat_id": -100000830, "message_id": 9, "username": ""}}
    sent, _ = _render(monkeypatch, payload)
    text, kw = sent[0]
    assert kw["reply_markup"].inline_keyboard[0][0].url == "https://t.me/c/830/9"
    assert text.count(tr("fa", "dl_private_note")) == 1


def test_row_deleted_uses_snapshot_then_unknown_note(monkeypatch):
    payload = {"short": "s", "full": "full body",
               "src": {"chat_id": -100777, "message_id": 3, "username": "ghost"}}
    sent, _ = _render(monkeypatch, payload)      # no row for -100777
    text, kw = sent[0]
    assert kw["reply_markup"].inline_keyboard[0][0].url == "https://t.me/ghost/3"
    assert tr("fa", "dl_unknown_note") not in text

    payload["src"]["username"] = ""
    sent, _ = _render(monkeypatch, payload)
    text, kw = sent[0]
    assert kw["reply_markup"].inline_keyboard[0][0].url == "https://t.me/c/777/3"
    assert text.count(tr("fa", "dl_unknown_note")) == 1


def test_corrupt_payload_never_crashes(monkeypatch):
    for bad in ({"src": {"chat_id": None, "message_id": "x"}},
                {"src": {"chat_id": -1001, "message_id": 0}},
                {"src": "oops"}):
        sent, _ = _render(monkeypatch, bad)
        assert sent[0][1]["reply_markup"] is None
    sent, upd = _render(monkeypatch, ["not", "a", "dict"])
    assert sent == [] and upd.message.calls[0][0] == tr("fa", "dl_notfound")


def test_split_legacy_body_has_one_button(monkeypatch):
    calls = []
    class Bot:
        async def send_message(self, chat_id, text, **kw):
            calls.append((text, kw))
            return type("M", (), {"message_id": len(calls)})()
    monkeypatch.setattr(rich_send.text_format, "split_html", lambda t: ["a", "b", "c"])
    kb = InlineKeyboardMarkup([[U("back", "https://t.me/mychan/42")]])
    asyncio.run(rich_send.send_legacy(Bot(), 1, "abc", reply_markup=kb,
                                      markup_last_only=True, delay=0))
    assert "reply_markup" not in calls[0][1] and "reply_markup" not in calls[1][1]
    assert calls[2][1]["reply_markup"] is kb


def test_split_default_still_marks_every_chunk(monkeypatch):
    calls = []
    class Bot:
        async def send_message(self, chat_id, text, **kw):
            calls.append((text, kw))
            return type("M", (), {"message_id": len(calls)})()
    monkeypatch.setattr(rich_send.text_format, "split_html", lambda t: ["a", "b"])
    kb = InlineKeyboardMarkup([[U("back", "https://t.me/mychan/42")]])
    asyncio.run(rich_send.send_legacy(Bot(), 1, "ab", reply_markup=kb, delay=0))
    assert all("reply_markup" in kw for _, kw in calls)


def test_url_and_ids_never_reach_the_logs(monkeypatch, caplog):
    payload = {"short": "s", "full": "full body",
               "src": {"chat_id": -100424242, "message_id": 7, "username": "quietchan"}}
    with caplog.at_level("DEBUG"):
        _render(monkeypatch, payload)
    assert "quietchan" not in caplog.text and "424242" not in caplog.text
