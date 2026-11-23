# -*- coding: utf-8 -*-
"""فاز ۴ — اسکیمای v12: qa_answers، ai_plan_grants، plans.ai_quota.

قراردادها (design: docs/superpowers/specs/2026-11-16-phase4-reply-qa-design.md):
  • qa_answers کلیدِ یکتای (chat_id, msg_id) دارد ⇒ at-most-once.
  • channel_id شناسهٔ چتِ تلگرام است، نه شناسهٔ داخلی (تصمیمِ مالک ۵).
  • ai_plan_grants به ازای هر دورهٔ اشتراک یک ردیف است: UNIQUE(user_id, expires_at).
"""
from newsbot import SCHEMA_VERSION
from newsbot.db import q


def test_schema_version_is_13(isolated_db):
    assert SCHEMA_VERSION == 13, "فاز ۷: users.onboarding_state ⇒ نسخه ۱۳"


def test_the_qa_answers_table_has_the_expected_columns(isolated_db):
    cols = {r["name"] for r in q("PRAGMA table_info(qa_answers)")}
    assert cols == {"chat_id", "msg_id", "user_id", "channel_id", "post_id",
                    "status", "answer", "error", "created_at", "updated_at"}


def test_qa_answers_is_keyed_by_chat_and_message(isolated_db):
    pk = [r["name"] for r in q("PRAGMA table_info(qa_answers)") if r["pk"]]
    assert pk == ["chat_id", "msg_id"], "کلیدِ یکتا همان چیزی است که at-most-once را تضمین می‌کند"


def test_the_ai_plan_grants_table_has_a_unique_user_period(isolated_db):
    cols = {r["name"] for r in q("PRAGMA table_info(ai_plan_grants)")}
    assert cols == {"id", "user_id", "plan_id", "quota", "used", "granted_at", "expires_at"}
    idx = [r["name"] for r in q("PRAGMA index_list(ai_plan_grants)")]
    assert any("user" in (n or "") for n in idx), "UNIQUE(user_id, expires_at) باید ایندکس داشته باشد"


def test_plans_has_an_ai_quota_column(isolated_db):
    assert "ai_quota" in {r["name"] for r in q("PRAGMA table_info(plans)")}


def test_an_existing_plan_row_defaults_to_unlimited(isolated_db):
    from newsbot.core_plans import create_plan, get_plan
    pid = create_plan(name="بی‌سقف", days=30)
    assert int(get_plan(pid)["ai_quota"] or 0) == 0


def test_the_d1_migration_carries_the_phase4_tables():
    from tools.migrate_sqlite_to_d1 import TABLES
    assert {"qa_answers", "ai_plan_grants"} <= set(TABLES)
