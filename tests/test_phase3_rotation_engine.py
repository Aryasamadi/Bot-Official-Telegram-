# -*- coding: utf-8 -*-
"""عبورِ چرخش از مسیرِ تولید (فاز ۳ · ADR-۳/ADR-۴).

این فایل **صدرصد مسیرِ چرخش** را می‌سنجد، نه لایهٔ HTTP (آن لایه در
tests/test_ai_end_to_end.py پوشش داده شده و دوباره‌سازی‌اش این‌جا فقط شکننده است).
seam این فایل: «generate() → انتخاب مدل → advance → audit»، با یک transport جعلی.
"""
import asyncio

import httpx
import pytest

from newsbot import ai_models as amod
from newsbot import generate as gen_mod
from newsbot import rotation
from newsbot.core_channels import add_channel, channel_by_chat, get_settings
from newsbot.db import q

PAYLOAD = ('{"category": "خبری", "title": "تیتر معتبر", '
           '"post": "<b>تیتر معتبر</b>\\n\\n" + "%s", '
           '"scores": {"x": 9}, "full": null}')


def _payload_text(settings):
    """یک پاسخِ معتبرِ مسیرِ مقاله (JSON با post و scores) — با json.dumps تا همیشه معتبر باشد."""
    import json

    return json.dumps({
        "category": settings["categories"][0]["name"],
        "title": "تیتر معتبر",
        "post": "<b>تیتر معتبر</b>\n\n" + "متن خبری معتبر برای انتشار. " * 10,
        "scores": {c["name"]: 9 for c in settings["criteria"]},
        "full": None,
    }, ensure_ascii=False)


@pytest.fixture()
def transport(isolated_db, monkeypatch):
    """ جایگزینی _call_model_impl: بدونِ شبکه، بدونِ HTTP،行为 قابلِ پیش‌بینی."""
    state = {"fail_first": False, "calls": []}

    async def fake(m, system, user):
        state["calls"].append(m["id"])
        if state["fail_first"]:
            state["fail_first"] = False
            raise httpx.ConnectError("stub transport failure")
        if "health-check" in (system or ""):        # مسیرِ probe: همان قراردادِ READY
            return "READY", {}
        return _payload_text(_settings()), {}

    monkeypatch.setattr(amod, "_call_model_impl", fake)
    q("DELETE FROM ai_models WHERE name LIKE 'rot-%'", commit=True)
    q("DELETE FROM ai_rotation WHERE scope='public'", commit=True)
    q("DELETE FROM ai_rotation_log WHERE scope='public'", commit=True)
    yield state
    q("DELETE FROM ai_models WHERE name LIKE 'rot-%'", commit=True)


def _settings():
    add_channel(1, -100970111, "کانال چرخش", "", 1)
    return get_settings(channel_by_chat(-100970111)["id"])


def _add(name, priority=10):
    q("DELETE FROM ai_models WHERE name=?", (name,), commit=True)
    return amod.add_model("https://api.example.com/v1", "dummy-key", "m-" + name,
                          name=name, priority=priority)


def _article(tag):
    from newsbot.timeutils import now_iso

    return {"url": "https://news.test/%s" % tag, "title": "تیتر %s" % tag,
            "text": "متن خبری برای آزمون چرخش. " * 12, "media": None, "published": now_iso()}


def test_each_request_takes_the_next_model_in_order(transport):
    s = _settings()
    _add("rot-a")
    _add("rot-b")
    _add("rot-c")

    got = [asyncio.run(gen_mod.generate(s, _article("r%d" % i)))[1] for i in range(3)]
    assert got == ["rot-a", "rot-b", "rot-c"]
    assert rotation.rotation_get("public")["version"] == 3
    assert rotation.rotation_stats("public")["log_rows"] == 3


def test_a_failed_provider_does_not_burn_the_cursor(transport):
    """ADR-۴: یک advance در هر درخواست — نه به‌ازای هر تلاش."""
    s = _settings()
    _add("rot-a")            # اولویتِ مساوی ⇒ تقدم با id؛ rot-a اول
    _add("rot-b")
    transport["fail_first"] = True

    _j, model, err = asyncio.run(gen_mod.generate(s, _article("r-fail")))
    assert err is None and model in ("rot-a", "rot-b")
    assert len(transport["calls"]) >= 2, "باید بعد از خطا مدلِ بعدی را امتحان کند"
    assert rotation.rotation_get("public")["version"] == 1, "advance فقط یک بار"
    assert rotation.rotation_stats("public")["log_rows"] == 1


def test_the_audit_row_names_the_chosen_model(transport):
    s = _settings()
    _add("rot-a")
    _add("rot-b")
    asyncio.run(gen_mod.generate(s, _article("r-audit")))

    rows = rotation.rotation_log_recent("public", limit=5)
    assert len(rows) == 1
    assert rows[0]["pool_size"] == 2 and rows[0]["idx"] in (0, 1)
    model = amod.get_model(rows[0]["model_id"])
    assert model and model["name"] in ("rot-a", "rot-b")


def test_the_probe_path_does_not_advance_the_public_cursor(transport):
    """تستِ مدل (probe) یک advance نیست: هدفش همان یک مدل است."""
    _settings()
    mid = _add("rot-probe")
    before = rotation.rotation_get("public")
    ok, _out, _t = asyncio.run(amod.test_model(mid))
    assert ok
    after = rotation.rotation_get("public")
    assert (after["version"] if after else 0) == (before["version"] if before else 0)