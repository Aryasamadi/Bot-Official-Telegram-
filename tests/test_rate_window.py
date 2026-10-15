# -*- coding: utf-8 -*-
"""SEC-03 (فاز ۳): محدودیت نرخ سراسری per-user و شمارندهٔ پنجره‌ای."""
from newsbot import config
from newsbot.ratelimit import rate_window, global_rate_ok, rate_cleanup, _win


def test_rate_window_allows_up_to_limit_then_blocks():
    k = "g:test-window-1"
    t0 = 1000.0
    assert all(rate_window(k, 3, 10, now=t0 + i * 0.1) for i in range(3))
    assert rate_window(k, 3, 10, now=t0 + 1) is False
    assert rate_window(k, 3, 10, now=t0 + 5) is False   # هنوز در پنجره


def test_rate_window_frees_after_window_passes():
    k = "g:test-window-2"
    t0 = 2000.0
    for i in range(3):
        assert rate_window(k, 3, 10, now=t0 + i)
    assert rate_window(k, 3, 10, now=t0 + 4) is False
    assert rate_window(k, 3, 10, now=t0 + 11) is True    # پنجره تمام شد


def test_rate_window_rejected_event_not_counted():
    """رخداد ردشده نباید پنجره را تمدید کند (وگرنه برای همیشه مسدود می‌شود)."""
    k = "g:test-window-3"
    t0 = 3000.0
    assert rate_window(k, 1, 5, now=t0) is True
    assert rate_window(k, 1, 5, now=t0 + 1) is False
    assert rate_window(k, 1, 5, now=t0 + 6) is True      # آخرین رخدادِ مجاز در t0 بود


def test_global_rate_ok_blocks_flood_for_normal_user():
    uid = 777001
    key = f"g:{uid}"
    limit = config.GLOBAL_RATE_MAX
    _win.pop(key, None)
    allowed = sum(1 for _ in range(limit + 5) if global_rate_ok(uid))
    assert allowed == limit
    assert global_rate_ok(uid) is False
    _win.pop(key, None)


def test_global_rate_ok_exempts_super_admin():
    uid = int(next(iter(config.SUPER_ADMIN_IDS)))
    key = f"g:{uid}"
    limit = config.GLOBAL_RATE_MAX
    _win.pop(key, None)
    assert all(global_rate_ok(uid) for _ in range(limit + 10))   # هیچ‌وقت نباید بسته شود
    _win.pop(key, None)


def test_rate_cleanup_purges_stale_window_keys():
    _win["g:stale"] = [1.0, 2.0]
    _win["g:fresh"] = [9e9]   # آینده/تازه — نباید حذف شود
    rate_cleanup(now_w=10_000_000.0)
    assert "g:stale" not in _win
    _win.pop("g:fresh", None)
