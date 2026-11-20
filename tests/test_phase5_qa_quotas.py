# -*- coding: utf-8 -*-
"""فاز ۵ — سهمیهٔ پرسش و پاسخ: cooldown، سقفِ روزانهٔ کاربر و سقفِ روزانهٔ پست."""
import asyncio
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from newsbot import ai_models as amod
from newsbot import config, qa, qa_quota, ratelimit
from newsbot.core_channels import add_channel, channel_by_chat
from newsbot.db import q

ROOT = Path(__file__).resolve().parents[1]
now = datetime(2026, 11, 20, 12, 0, tzinfo=config.UTC)
settings = {"qa_cooldown_s": 30, "qa_user_daily_max": 1, "qa_post_daily_max": 50}


def test_the_config_defaults_and_ranges_are_the_designed_values():
    assert (config.QA_COOLDOWN_S, config.QA_USER_DAILY_MAX,
            config.QA_POST_DAILY_MAX) == (30, 30, 50)


def test_the_env_example_and_self_check_include_all_three_names():
    env = (ROOT / ".env.example").read_text(encoding="utf-8", errors="replace")
    app = (ROOT / "newsbot" / "app.py").read_text(encoding="utf-8", errors="replace")
    for name in ("QA_COOLDOWN_S", "QA_USER_DAILY_MAX", "QA_POST_DAILY_MAX"):
        assert name in env and name in app


def test_an_accepted_claim_returns_empty_and_creates_one_row(isolated_db):
    assert qa.claim(1, 1, 7, -100, 55, settings=settings, now=now) == ""
    assert q("SELECT COUNT(*) AS n FROM qa_answers", one=True)["n"] == 1


def test_the_same_message_is_a_duplicate_and_consumes_nothing(isolated_db):
    qa.claim(1, 1, 7, -100, 55, settings=settings, now=now)
    assert qa.claim(1, 1, 7, -100, 55, settings=settings, now=now) == "duplicate"
    assert q("SELECT COUNT(*) AS n FROM qa_answers", one=True)["n"] == 1


def test_a_second_ask_inside_cooldown_is_rejected_without_a_row(isolated_db):
    qa.claim(1, 1, 7, -100, 55, settings=settings, now=now)
    later = now + timedelta(seconds=10)
    assert qa.claim(1, 2, 7, -100, 55, settings=settings, now=later) == "cooldown"
    assert q("SELECT COUNT(*) AS n FROM qa_answers", one=True)["n"] == 1


def test_an_ask_after_cooldown_is_accepted(isolated_db):
    cap = {"qa_cooldown_s": 30, "qa_user_daily_max": 30, "qa_post_daily_max": 50}
    qa.claim(1, 1, 7, -100, 55, settings=cap, now=now)
    assert qa.claim(1, 2, 7, -100, 55, settings=cap,
                    now=now + timedelta(seconds=31)) == ""


def test_the_user_daily_cap_rejects_the_next_ask(isolated_db):
    qa.claim(1, 1, 7, -100, 55, settings=settings, now=now)
    nxt = dict(settings, qa_cooldown_s=0)
    assert qa.claim(1, 2, 7, -200, 99, settings=nxt, now=now) == "user_cap"


def test_the_post_daily_cap_is_per_post(isolated_db):
    cap = {"qa_cooldown_s": 0, "qa_user_daily_max": 30, "qa_post_daily_max": 1}
    qa.claim(1, 1, 7, -100, 55, settings=cap, now=now)
    assert qa.claim(1, 2, 8, -100, 55, settings=cap, now=now) == "post_cap"
    assert qa.claim(1, 3, 8, -100, 56, settings=cap, now=now) == ""


def test_the_same_post_id_in_two_channels_does_not_share_the_cap(isolated_db):
    cap = {"qa_cooldown_s": 0, "qa_user_daily_max": 30, "qa_post_daily_max": 1}
    qa.claim(1, 1, 7, -100, 55, settings=cap, now=now)
    assert qa.claim(1, 2, 8, -200, 55, settings=cap, now=now) == ""


def test_a_super_admin_bypasses_all_three_guards(isolated_db, monkeypatch):
    monkeypatch.setattr(config, "SUPER_ADMIN_IDS", (7,))
    tight = {"qa_cooldown_s": 3600, "qa_user_daily_max": 1, "qa_post_daily_max": 1}
    assert qa.claim(1, 1, 7, -100, 55, settings=tight, now=now) == ""
    assert qa.claim(1, 2, 7, -100, 55, settings=tight, now=now) == ""
    assert q("SELECT COUNT(*) AS n FROM qa_answers", one=True)["n"] == 2


def test_concurrent_claims_at_cap_one_produce_exactly_one_row(isolated_db):
    from concurrent.futures import ThreadPoolExecutor
    cap = {"qa_cooldown_s": 0, "qa_user_daily_max": 1, "qa_post_daily_max": 100}
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(
            lambda i: qa.claim(1, i, 7, -100, 55, settings=cap, now=now), range(8)))
    assert results.count("") == 1 and results.count("user_cap") == 7
    assert q("SELECT COUNT(*) AS n FROM qa_answers", one=True)["n"] == 1


def test_a_lost_insert_race_is_reported_as_duplicate(isolated_db):
    """دو فراخوانیِ D1 میتوانند هر دو پیشبررسی را بگذرانند؛ برنده یکی است."""
    from newsbot.db import db
    from newsbot.timeutils import iso_fixed
    real = db()

    class Race:
        def execute(self, sql, params=()):
            cur = real.execute(sql, params)
            if sql.strip().startswith("INSERT INTO qa_answers"):
                return NS(rowcount=0, fetchone=lambda: None)
            return cur

    reason = qa_quota.claim(
        Race(), 1, 1, 7, -100, 55,
        "0001-01-01T00:00:00.000000+00:00", "0001-01-01T00:00:00.000000+00:00",
        "2026-11-20T12:00:00.000000+00:00", 1, 100, at=iso_fixed(now))
    assert reason == "duplicate"


def test_a_yesterday_ask_is_outside_today_window(isolated_db):
    q("INSERT INTO qa_answers(chat_id,msg_id,user_id,channel_id,post_id,status,answer,error,created_at,updated_at)"
      " VALUES(1,1,7,-100,55,'done','','','2026-11-19T12:00:00.000000+00:00','')")
    cap = {"qa_cooldown_s": 0, "qa_user_daily_max": 1, "qa_post_daily_max": 1}
    assert qa.claim(1, 2, 7, -100, 55, settings=cap, now=now) == ""


def test_a_failed_answer_still_counts_as_an_ask(isolated_db):
    q("INSERT INTO qa_answers(chat_id,msg_id,user_id,channel_id,post_id,status,answer,error,created_at,updated_at)"
      " VALUES(1,1,7,-100,55,'failed','','ai_fail','2026-11-20T11:00:00.000000+00:00','')")
    cap = {"qa_cooldown_s": 0, "qa_user_daily_max": 1, "qa_post_daily_max": 50}
    assert qa.claim(1, 2, 7, -100, 55, settings=cap, now=now) == "user_cap"
    assert qa.claim(1, 3, 8, -100, 56, settings=cap, now=now) == ""


def test_the_two_daily_indexes_exist_after_init(isolated_db):
    names = {r["name"] for r in q("PRAGMA index_list(qa_answers)")}
    assert {"idx_qa_answers_user_day", "idx_qa_answers_post_day"} <= names


def test_effective_limits_inherit_env_when_settings_have_none():
    assert qa_quota.effective_limits({}) == (
        config.QA_COOLDOWN_S, config.QA_USER_DAILY_MAX, config.QA_POST_DAILY_MAX)


def test_effective_limits_fall_back_on_invalid_override():
    bad = {"qa_cooldown_s": -5, "qa_user_daily_max": "x", "qa_post_daily_max": 0}
    assert qa_quota.effective_limits(bad) == (
        config.QA_COOLDOWN_S, config.QA_USER_DAILY_MAX, config.QA_POST_DAILY_MAX)


def test_day_start_uses_the_given_offset():
    assert qa_quota.day_start("2026-11-20", 3.5) == "2026-11-19T20:30:00.000000+00:00"


# ── هندلر: رفتار در برابرِ ردِ سهمیه ────────────────────────────────────────────────
class _Bot:
    def __init__(self, linked=-100111):
        self.linked, self.sent = linked, []

    async def get_chat(self, gid):
        return NS(linked_chat_id=self.linked)

    async def send_message(self, chat_id, text, **kw):
        self.sent.append({"chat_id": chat_id, "text": text, **kw})
        return NS(message_id=1)


def _channel_post(text="متن پست", mid=77):
    return NS(sender_chat=NS(id=-100111, type="channel"), forward_from_chat=None,
              forward_from_message_id=mid, text=text, caption=None,
              is_automatic_forward=True, message_id=5)


def _group_msg(gid=-555, mid=10, text="این پست دربارهٔ چیست؟", reply=None):
    return NS(chat_id=gid, message_id=mid, text=text, caption=None,
              chat=NS(type="supergroup"), reply_to_message=reply, date=None)


def _update(msg, uid=900001):
    return NS(effective_message=msg,
              effective_user=NS(id=uid, username="u", full_name="U"))


@pytest.fixture
def chan(isolated_db):
    add_channel(900001, -100111, "کانال", "", 900001)
    return channel_by_chat(-100111)


def _stub_ai(monkeypatch, out="پاسخِ مدل"):
    async def fake(system, user, **kw):
        return out, "m", None
    monkeypatch.setattr(amod, "ai_chat", fake)


def test_a_cap_refusal_sends_one_short_notice(chan, monkeypatch):
    monkeypatch.setattr(qa, "claim", lambda *a, **k: "user_cap")
    called = []

    async def fake_ai(*a, **k):
        called.append(1)
        return "x", "m", None
    monkeypatch.setattr(amod, "ai_chat", fake_ai)
    ratelimit._rl.clear()
    bot = _Bot()
    asyncio.run(qa.on_group_reply(
        _update(_group_msg(reply=_channel_post())), NS(bot=bot)))
    assert len(bot.sent) == 1 and called == []


def test_the_cap_notice_is_throttled(chan, monkeypatch):
    monkeypatch.setattr(qa, "claim", lambda *a, **k: "post_cap")
    ratelimit._rl.clear()
    bot = _Bot()
    for mid in (11, 12):
        asyncio.run(qa.on_group_reply(
            _update(_group_msg(mid=mid, reply=_channel_post())), NS(bot=bot)))
    assert len(bot.sent) == 1


def test_a_cooldown_refusal_is_silent_and_never_calls_ai(chan, monkeypatch):
    monkeypatch.setattr(qa, "claim", lambda *a, **k: "cooldown")
    called = []

    async def fake_ai(*a, **k):
        called.append(1)
        return "x", "m", None
    monkeypatch.setattr(amod, "ai_chat", fake_ai)
    bot = _Bot()
    asyncio.run(qa.on_group_reply(
        _update(_group_msg(reply=_channel_post())), NS(bot=bot)))
    assert bot.sent == [] and called == []


def test_an_empty_reply_in_cooldown_is_silent(chan, monkeypatch):
    monkeypatch.setattr(qa, "claim", lambda *a, **k: "cooldown")
    bot = _Bot()
    asyncio.run(qa.on_group_reply(
        _update(_group_msg(reply=_channel_post(text=None))), NS(bot=bot)))
    assert bot.sent == []


def test_the_normal_phase4_path_still_answers_once(chan, monkeypatch):
    _stub_ai(monkeypatch)
    bot = _Bot()
    u = _update(_group_msg(reply=_channel_post()))
    asyncio.run(qa.on_group_reply(u, NS(bot=bot)))
    assert len(bot.sent) == 1
