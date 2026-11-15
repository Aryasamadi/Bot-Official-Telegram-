# -*- coding: utf-8 -*-
"""فاز ۴ — توابعِ خالصِ Q&A: تشخیصِ پستِ کانال، سقفِ زمینه و ساختِ prompt."""
from types import SimpleNamespace as NS

from newsbot import config, qa


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
