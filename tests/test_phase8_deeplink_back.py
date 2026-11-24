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
