# -*- coding: utf-8 -*-
"""فاز ۵ — پنلِ تنظیماتِ سهمیهٔ Q&A و اعتبارسنجیِ ورودی."""
import asyncio
from types import SimpleNamespace as NS

import pytest

from newsbot import ui_dispatch as udisp
from newsbot import ui_inputs, ui_views_admin as vadmin
from newsbot.core_channels import add_channel, channel_by_chat, default_settings
from newsbot.db import q
from newsbot.ui_i18n import QA_FIELDS, tr


@pytest.fixture
def chan(isolated_db):
    add_channel(900001, -100111, "کانال", "", 900001)
    return channel_by_chat(-100111)


def _update(uid=900001):
    return NS(effective_user=NS(id=uid), effective_chat=NS(id=-555),
              callback_query=None)


def _context():
    return NS(user_data={})


def test_default_settings_have_three_none_keys():
    s = default_settings()
    assert all(s[k] is None for k in QA_FIELDS)


def test_view_qa_renders_three_buttons_and_inherited_values(chan, monkeypatch):
    seen = {}

    async def fake_render(update, context, text, kb=None, force_new=False):
        seen["text"] = text
        seen["buttons"] = [b.callback_data for row in (kb or []) for b in row]
        seen["labels"] = [b.text for row in (kb or []) for b in row]

    monkeypatch.setattr(vadmin, "render", fake_render)
    monkeypatch.setattr(vadmin, "L", lambda update: "fa")
    monkeypatch.setattr(vadmin, "channel_owned", lambda cid, uid: True)
    monkeypatch.setattr(vadmin, "get_settings", lambda cid: default_settings())
    asyncio.run(vadmin.view_qa(_update(), _context(), chan["id"]))
    assert all(f"a:set:{chan['id']}:{k}" in seen["buttons"] for k in QA_FIELDS)
    assert all(k not in label for k in QA_FIELDS for label in seen["labels"])
    assert "30" in seen["text"] and "50" in seen["text"]


def test_a_set_callback_returns_to_view_qa(chan, monkeypatch):
    seen = {}

    async def fake_ask(update, context, prompt, kind, back, **extra):
        seen["back"] = back

    monkeypatch.setattr(udisp, "ask", fake_ask)
    monkeypatch.setattr(udisp, "user_lang", lambda uid: "fa")
    monkeypatch.setattr(udisp, "can_admin", lambda uid: True)
    monkeypatch.setattr(udisp, "channel_owned", lambda cid, uid: True)
    monkeypatch.setattr(udisp, "get_settings", lambda cid: {})
    asyncio.run(udisp._dispatch(_update(), _context(),
                                f"a:set:{chan['id']}:qa_cooldown_s"))
    assert seen["back"] == f"a:qa:{chan['id']}"


def test_blank_channel_input_means_inherit():
    assert ui_inputs._input_value("qa_cooldown_s", "  ", "fa") is None


def test_out_of_range_channel_input_is_rejected():
    with pytest.raises(ValueError):
        ui_inputs._input_value("qa_user_daily_max", "999999", "fa")


def test_i18n_keys_exist_in_both_languages():
    from newsbot.ui_i18n import FIELD_LABEL
    for key in ("qa_panel_title", "qa_settings", "qa_inherit", "qa_cap_user", "qa_cap_post"):
        assert tr("fa", key) != key and tr("en", key) != key
    for key in QA_FIELDS:
        assert key in FIELD_LABEL and all(FIELD_LABEL[key])


def test_the_three_overrides_are_written_to_channel_settings(chan):
    from newsbot.core_channels import update_settings
    update_settings(chan["id"], qa_cooldown_s=60, qa_user_daily_max=7, qa_post_daily_max=9)
    s = q("SELECT settings FROM channels WHERE id=?", (chan["id"],), one=True)
    assert '"qa_cooldown_s": 60' in s["settings"]
