# -*- coding: utf-8 -*-
"""فاز ۴ — سهمیهٔ هوش مصنوعیِ سطحِ پلن (تصمیمِ مالک ۶).

سهمیه:
  • در تعریفِ پلن تعیین می‌شود (۰ = نامحدود).
  • به *کاربر* تعلق دارد، نه به کانال و نه به یک قابلیت.
  • سراسری اجرا می‌شود: تولیدِ مقاله و Q&A هر دو از یک دروازه رد می‌شوند.
  • پیش از خرید نشان داده می‌شود و در پنلِ کاربر (کل/مصرف/مانده) دیده می‌شود.
  • با تمام‌شدن، درخواستِ تازهٔ AI متوقف می‌شود.
"""
import asyncio

import pytest

from newsbot import ai_models as amod
from newsbot import ai_quota
from newsbot.core_plans import assign_plan, create_plan, get_plan
from newsbot.core_users import ai_claim, ensure_user
from newsbot.db import q

UID = 700001


@pytest.fixture
def quota_user(isolated_db):
    ensure_user(UID, "u", "U")
    pid = create_plan(name="پلنِ تست", days=30, ai_quota=3, price_num=1)
    assign_plan(UID, pid)
    return pid


def test_the_plan_carries_its_quota(quota_user):
    assert get_plan(quota_user)["ai_quota"] == 3


def test_the_grant_is_created_for_the_active_period(quota_user):
    g = ai_quota.plan_grant(UID)
    assert g["quota"] == 3 and g["used"] == 0


def test_each_claim_uses_one_unit(quota_user):
    assert ai_claim(UID) is True
    assert ai_claim(UID) is True
    assert ai_quota.plan_grant(UID)["used"] == 2


def test_the_quota_stops_further_requests(quota_user):
    for _ in range(3):
        assert ai_claim(UID) is True
    assert ai_claim(UID) is False
    assert ai_quota.plan_grant(UID)["used"] == 3, "واحدِ مازاد هرگز مصرف نمی‌شود"


def test_the_article_path_consumes_the_same_quota(quota_user, monkeypatch):
    """سهمیه سراسری است: مسیرِ تولیدِ مقاله همان دروازهٔ ai_claim را صدا می‌زند."""
    async def fake_try(*a, **k):
        return "ok", "m", None
    monkeypatch.setattr(amod, "_try_models", fake_try)
    amod.add_model("https://api.example.com/v1", "k", "m-1", name="quota-model", priority=10)
    out, _label, err = asyncio.run(amod.ai_chat("s", "u", owner_id=UID))
    assert (out, err) == ("ok", None)
    assert ai_quota.plan_grant(UID)["used"] == 1


def test_a_renewal_resets_the_period(quota_user):
    for _ in range(3):
        assert ai_claim(UID) is True
    assign_plan(UID, quota_user, days=30)          # دورهٔ تازه (تاریخِ پایان جلو می‌رود)
    g = ai_quota.plan_grant(UID)
    assert g["used"] == 0 and ai_claim(UID) is True


def test_an_unlimited_plan_never_blocks(isolated_db):
    ensure_user(700002, "u2", "U2")
    pid = create_plan(name="نامحدود", days=30, ai_quota=0)
    assign_plan(700002, pid)
    for _ in range(5):
        assert ai_claim(700002) is True
    assert ai_quota.plan_grant(700002) is None


def test_a_user_without_a_plan_is_not_capped_by_this_gate(isolated_db):
    """پلنِ فعال ندارد ⇒ گرانت ساخته نمی‌شود؛ سقفِ روزانهٔ رایگان همچنان کار می‌کند."""
    ensure_user(700004, "u4", "U4")
    assert ai_quota.plan_grant(700004) is None


def test_concurrent_claims_never_exceed_the_quota(isolated_db):
    ensure_user(700003, "u3", "U3")
    pid = create_plan(name="۲ واحد", days=30, ai_quota=2)
    assign_plan(700003, pid)
    ok = [ai_claim(700003) for _ in range(6)]
    assert ok.count(True) == 2
    assert ai_quota.plan_grant(700003)["used"] == 2


def test_a_model_test_consumes_only_the_plan_quota(quota_user, monkeypatch):
    amod.add_model("https://api.example.com/v1", "k", "m-2", name="test-model",
                   priority=10, owner_id=UID)
    mid = q("SELECT id FROM ai_models WHERE name='test-model'", one=True)["id"]

    async def fake_try(*a, **k):
        return "READY", "test-model", None
    monkeypatch.setattr(amod, "_try_models", fake_try)
    assert asyncio.run(amod.test_model(mid))[0] is True
    assert ai_quota.plan_grant(UID)["used"] == 1


def test_an_exhausted_plan_blocks_a_model_test(quota_user, monkeypatch):
    for _ in range(3):
        assert ai_claim(UID) is True
    amod.add_model("https://api.example.com/v1", "k", "m-3", name="test-model-2",
                   priority=10, owner_id=UID)
    mid = q("SELECT id FROM ai_models WHERE name='test-model-2'", one=True)["id"]

    async def fake_try(*a, **k):
        raise AssertionError("با سهمیهٔ تمام‌شده نباید فراخوانیِ مدل رخ دهد")
    monkeypatch.setattr(amod, "_try_models", fake_try)
    ok, msg, _secs = asyncio.run(amod.test_model(mid))
    assert ok is False and msg == "ai_limit"


def test_the_user_panel_shows_total_used_and_remaining(quota_user):
    assert ai_claim(UID) is True
    line = ai_quota.quota_lines(UID, "fa")
    assert "3" in line and "1" in line and "2" in line


def test_the_unlimited_word_is_used_for_zero(quota_user):
    assert "نامحدود" in ai_quota.units_text(0, "fa")
    assert ai_quota.units_text(0, "en").lower().startswith("unlimited")
    assert "7" in ai_quota.units_text(7, "fa")


# ── نمایش در پنل‌ها (تصمیمِ مالک ۶: پیش از خرید + در پنلِ کاربر) ────────────────────
def test_the_admin_can_edit_the_plan_quota():
    from newsbot.ui_views_admin import PLAN_FIELDS
    assert any(f[0] == "ai_quota" for f in PLAN_FIELDS)


def test_the_plan_quota_is_validated_as_a_non_negative_integer():
    from newsbot.ui_inputs import _input_value
    assert _input_value("ai_quota", "12", "fa") == 12


def test_the_purchase_card_shows_the_quota():
    import inspect

    from newsbot import ui_i18n, ui_views_user
    assert "{ai}" in ui_i18n.tr("fa", "plan_details")
    assert "ai=" in inspect.getsource(ui_views_user.view_plan)


def test_the_super_plan_view_shows_the_quota():
    import inspect

    from newsbot import ui_views_super
    assert "ai=" in inspect.getsource(ui_views_super.view_s_plan)


def test_the_user_panel_shows_the_live_quota_line():
    import inspect

    from newsbot import ui_views_user
    assert "quota_lines" in inspect.getsource(ui_views_user.view_plan)
