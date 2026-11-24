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
    cid = _chan(822, "oldhandle")
    ch = channel_by_chat(822)
    msg = type("Msg", (), {"message_id": 5, "chat": type("C", (), {"username": "newhandle"})()})()
    pub_mod._note_channel_handle(ch, msg)
    assert channel_by_chat(822)["username"] == "newhandle"

    def boom(*a, **kw):
        raise RuntimeError("db down")
    monkeypatch.setattr(pub_mod, "update_channel_meta", boom)
    pub_mod._note_channel_handle(channel_by_chat(822), msg)   # must not raise
