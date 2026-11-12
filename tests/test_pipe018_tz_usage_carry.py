# -*- coding: utf-8 -*-
"""PIPE-018: تغییر منطقهٔ زمانی نباید هیچ‌کدام از شمارنده‌های سهمیهٔ امروز (به‌ویژه ai) را صفر کند."""
import inspect

from newsbot.core_users import carry_usage_day
from newsbot.db import q


def _row(uid, day):
    return q("SELECT posts,tests,ai FROM usage WHERE admin_id=? AND day=?", (uid, day), one=True)


def _seed(uid, day, posts, tests, ai):
    q("INSERT OR REPLACE INTO usage(admin_id,day,posts,tests,ai) VALUES(?,?,?,?,?)", (uid, day, posts, tests, ai), commit=True)


def test_ai_counter_is_carried_across_a_day_boundary_change():
    uid = 910001
    _seed(uid, "2026-01-01", 3, 2, 7)
    assert carry_usage_day(uid, "2026-01-01", "2026-01-02") is True
    r = _row(uid, "2026-01-02")
    assert (r["posts"], r["tests"], r["ai"]) == (3, 2, 7)


def test_carry_never_lowers_existing_target_counters():
    uid = 910002
    _seed(uid, "2026-02-01", 1, 1, 2)
    _seed(uid, "2026-02-02", 5, 4, 9)
    carry_usage_day(uid, "2026-02-01", "2026-02-02")
    r = _row(uid, "2026-02-02")
    assert (r["posts"], r["tests"], r["ai"]) == (5, 4, 9)


def test_carry_raises_target_to_the_larger_ai_value():
    uid = 910003
    _seed(uid, "2026-03-01", 0, 0, 8)
    _seed(uid, "2026-03-02", 0, 0, 1)
    carry_usage_day(uid, "2026-03-01", "2026-03-02")
    assert _row(uid, "2026-03-02")["ai"] == 8


def test_noop_when_days_equal_or_no_source_row():
    uid = 910004
    assert carry_usage_day(uid, "2026-04-01", "2026-04-01") is False
    assert carry_usage_day(uid, "2026-04-01", "2026-04-02") is False
    assert _row(uid, "2026-04-02") is None


def test_dispatch_uses_the_helper_instead_of_inline_sql():
    import newsbot.ui_dispatch as ud
    src = inspect.getsource(ud)
    assert "carry_usage_day" in src
    assert "SELECT posts,tests FROM usage" not in src
