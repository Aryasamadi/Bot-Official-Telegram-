# -*- coding: utf-8 -*-
"""فاز ۵ — سهمیهٔ پرسش و پاسخ: cooldown، سقفِ روزانهٔ کاربر و سقفِ روزانهٔ پست."""
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from newsbot import config, qa, qa_quota
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
