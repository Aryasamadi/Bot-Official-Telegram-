# -*- coding: utf-8 -*-
"""تست‌های تکمیلیِ فاز ۲:
  • PIPE-013: ادبِ کاوش (per-host pacing)، بودجهٔ کاوش و احترام به robots.txt
  • TEST-007: backoff نماییِ شکستِ منبع، سقف ۱۲ ساعت، ریست با source_ok، غیرفعال‌سازیِ خودکار و شاخهٔ paused در چرخه
  • TEST-008: سقفِ روزانهٔ تستِ فوری (daily_tests) تا مرزِ کامل + بازنشانی با usage_reset
  • TEST-009: هشدارِ صریح روی callbackهای ناشناخته + بررسیِ دکمه‌های تولیدشده توسط نماها
  • TEST-013: فیکسچرِ دیتابیسِ ایزوله و پاک‌سازیِ پوشهٔ موقتِ تست
  • TEST-014: تطابقِ ادعای README با کد و نبودِ except: pass بی‌صدا در ماژول‌های بحرانی مالی/انتشار
"""
import ast
import asyncio
import pathlib
import time
from datetime import timedelta
from types import SimpleNamespace

from newsbot import config, discover
from newsbot.core_articles import reserve_publication
from newsbot.core_channels import add_channel
from newsbot.core_plans import assign_plan, create_plan
from newsbot.core_sources import add_source, get_source, source_fail, source_ok
from newsbot.core_users import ensure_user, remaining, usage_reset
from newsbot.discover import probe_source
from newsbot.cycle import run_channel_cycle
from newsbot.db import q
from newsbot.timeutils import now_utc, parse_dt


# ── PIPE-013: robots.txt + politeness + probe budget ─────────────────────────
def test_robots_allows_blocks_disallowed_paths_and_allows_others():
    robots = (
        "User-agent: *\n"
        "Disallow: /feed\n"
        "Disallow: /private/\n"
        "Allow: /public/\n"
    )
    assert discover.robots_allows(robots, "https://example.com/feed") is False
    assert discover.robots_allows(robots, "https://example.com/feed/rss") is False
    assert discover.robots_allows(robots, "https://example.com/private/rss.xml") is False
    assert discover.robots_allows(robots, "https://example.com/rss.xml") is True
    assert discover.robots_allows(robots, "https://example.com/public/feed.xml") is True
    assert discover.robots_allows("", "https://example.com/feed") is True


def test_host_pace_enforces_spacing_per_host_and_allows_parallel_hosts():
    discover._HOST_LAST_REQ.clear()
    discover._HOST_LOCKS.clear()
    stamps_a = []
    stamps_b = []

    async def hit(url, bucket):
        await discover._host_pace(url, min_interval=0.06)
        bucket.append(time.monotonic())

    async def run():
        await asyncio.gather(
            hit("https://a.example.com/1", stamps_a),
            hit("https://a.example.com/2", stamps_a),
            hit("https://a.example.com/3", stamps_a),
            hit("https://b.example.com/1", stamps_b),
        )

    asyncio.run(run())
    assert len(stamps_a) == 3 and len(stamps_b) == 1
    stamps_a.sort()
    assert (stamps_a[1] - stamps_a[0]) >= 0.045
    assert (stamps_a[2] - stamps_a[1]) >= 0.045


def test_discover_source_honours_robots_and_probe_budget(monkeypatch):
    uid = 771001
    ensure_user(uid, "u771001", "U", "fa")
    cid = add_channel(uid, -100771001, "Ch771001", "ch771001", uid)
    sid = add_source(uid, cid, "https://polite.example.com", "Polite")
    src = get_source(sid)

    fetched_urls = []

    async def fake_fetch(url, headers=None, timeout=20):
        fetched_urls.append(url)
        if url == "https://polite.example.com":
            return SimpleNamespace(status_code=200, text="<html><body>No feed links</body></html>",
                                   content=b"<html><body>No feed links</body></html>", headers={})
        if url == "https://polite.example.com/robots.txt":
            return SimpleNamespace(
                status_code=200,
                text="User-agent: *\nDisallow: /feed\nDisallow: /rss\nSitemap: https://polite.example.com/sitemap.xml\n",
                content=b"",
                headers={},
            )
        return SimpleNamespace(status_code=404, text="", content=b"", headers={})

    monkeypatch.setattr(discover, "fetch", fake_fetch)
    monkeypatch.setattr(discover, "HOST_DELAY_SEC", 0.0)
    monkeypatch.setattr(discover, "DISCOVER_PROBE_BUDGET", 4)

    items, changed, method = asyncio.run(discover.discover_source(src, use_cache=False))
    assert method == "html"
    # مسیرهای Disallow شده (/feed و /rss) هرگز نباید درخواست شوند
    probed = [u for u in fetched_urls if u not in ("https://polite.example.com", "https://polite.example.com/robots.txt")]
    assert all(not u.endswith(("/feed", "/feed/", "/rss", "/rss/")) for u in probed)
    assert len(probed) <= 4


# ── TEST-007: Source failure backoff, cap, reset, auto-disable & cycle pause ─
def test_source_fail_exponential_backoff_cap_and_source_ok_reset():
    uid = 771002
    ensure_user(uid, "u771002", "U", "fa")
    cid = add_channel(uid, -100771002, "Ch771002", "ch771002", uid)
    sid = add_source(uid, cid, "https://backoff.example.com", "Backoff")

    expected_waits = []
    for i in range(8):
        t0 = now_utc()
        source_fail(sid, f"boom-{i}")
        s = get_source(sid)
        assert s["fail_count"] == i + 1
        nta = parse_dt(s["next_try_after"])
        wait_min = round((nta - t0).total_seconds() / 60.0)
        expected_waits.append(wait_min)

    # دنبالهٔ نمایی با پایهٔ SOURCE_PAUSE_MIN (15) و سقف 720 دقیقه (۱۲ ساعت)
    base = config.SOURCE_PAUSE_MIN
    for idx, w in enumerate(expected_waits):
        target = min(base * (2 ** idx), 720)
        assert abs(w - target) <= 1, (idx, w, target)

    # source_ok شمارندهٔ خطا و آخرین خطا را پاک می‌کند
    source_ok(sid, "https://backoff.example.com/rss.xml", "etag-1", "lm-1")
    s_ok = get_source(sid)
    assert s_ok["fail_count"] == 0
    assert s_ok["last_error"] is None
    assert s_ok["feed_url"] == "https://backoff.example.com/rss.xml"


def test_probe_source_auto_disables_at_source_fail_limit(monkeypatch):
    uid = 771003
    ensure_user(uid, "u771003", "U", "fa")
    cid = add_channel(uid, -100771003, "Ch771003", "ch771003", uid)
    sid = add_source(uid, cid, "https://dead.example.com", "Dead")

    async def fail_discover(src, use_cache=False, tz=None):
        source_fail(src["id"], "timeout")
        raise RuntimeError("timeout")

    monkeypatch.setattr(discover, "discover_source", fail_discover)
    for step in range(config.SOURCE_FAIL_LIMIT):
        ok, _msg = asyncio.run(probe_source(get_source(sid), "fa"))
        assert ok is False
        s = get_source(sid)
        if step < config.SOURCE_FAIL_LIMIT - 1:
            assert s["active"] == 1
        else:
            assert s["active"] == 0


def test_cycle_skips_paused_and_cooldown_sources(monkeypatch):
    uid = 771004
    ensure_user(uid, "u771004", "U", "fa")
    pid = create_plan(name="P771004", name_en="P771004", days=30, daily_posts=10,
                      max_sources=5, max_channels=5, daily_tests=5, price="1000", price_en="10")
    assign_plan(uid, pid)
    cid = add_channel(uid, -100771004, "Ch771004", "ch771004", uid)
    sid1 = add_source(uid, cid, "https://paused.example.com", "Paused")
    sid2 = add_source(uid, cid, "https://cooldown.example.com", "Cooldown")

    future = (now_utc() + timedelta(minutes=60)).isoformat()
    recent = now_utc().isoformat()
    q("UPDATE sources SET fail_count=?, next_try_after=? WHERE id=?",
      (config.SOURCE_FAIL_LIMIT, future, sid1), commit=True)
    q("UPDATE sources SET last_fetch=?, fail_count=0 WHERE id=?",
      (recent, sid2), commit=True)

    called = []

    async def spy_discover(src, use_cache=True, tz=None):
        called.append(src["id"])
        return [], False, "feed"

    async def always_admin(bot, chat_id):
        return True

    import newsbot.cycle as cyc
    monkeypatch.setattr(cyc, "discover_source", spy_discover)
    monkeypatch.setattr(cyc, "bot_can_post", always_admin)

    res = asyncio.run(run_channel_cycle(SimpleNamespace(), uid, cid, test_mode=False))
    # هیچ‌کدام از دو منبعِ paused و cooldown نباید فراخوانیِ شبکه داشته باشند
    assert called == []
    cooldown_hosts = [kw.get("name") for k, kw in res["diag"].items if k == "src_cooldown"]
    assert "paused.example.com" in cooldown_hosts and "cooldown.example.com" in cooldown_hosts


# ── TEST-008: Daily test-run quota driven to cap + reset ─────────────────────
def test_daily_test_quota_driven_to_cap_blocks_next_test_and_resets(monkeypatch):
    uid = 771005
    ensure_user(uid, "u771005", "U", "fa")
    pid = create_plan(name="P771005", name_en="P771005", days=30, daily_posts=10,
                      max_sources=5, max_channels=5, daily_tests=3, price="1000", price_en="10")
    assign_plan(uid, pid)
    cid = add_channel(uid, -100771005, "Ch771005", "ch771005", uid)
    add_source(uid, cid, "https://quota.example.com", "QuotaSrc")

    assert remaining(uid, "tests") == 3
    for i, expected_rem in enumerate((2, 1, 0), 1):
        jr = reserve_publication(950000 + i, uid, False, uid, None)
        assert jr is not None and jr["test_day"] is not None
        assert remaining(uid, "tests") == expected_rem

    # تلاشِ چهارم برای رزروِ تست رد می‌شود (None)
    assert reserve_publication(950004, uid, False, uid, None) is None

    # وقتی سهمیهٔ تست صفر شد، چرخهٔ تست فوری باید با خطای quota_tests متوقف شود
    res = asyncio.run(run_channel_cycle(SimpleNamespace(), uid, cid, test_mode=True))
    codes = [k for k, _ in res["diag"].items]
    assert "quota_tests" in codes

    # بازنشانیِ سهمیهٔ تست توسط سوپرادمین دوباره ظرفیت را باز می‌کند
    usage_reset(uid, "tests")
    assert remaining(uid, "tests") == 3
    assert reserve_publication(950005, uid, False, uid, None) is not None


# ── TEST-009: Unrouted callback warning + view button verification ───────────
def test_unrouted_callbacks_emit_warning_and_redirect_safely(caplog):
    from newsbot.ui_dispatch import dispatch
    from tests.test_ui_routing import FakeContext, FakeUpdate, _mk_fixtures

    cid, _aid = _mk_fixtures()
    up, ctx = FakeUpdate(), FakeContext()
    for bad in ("u:nonexistent_btn", f"a:nonexistent_btn:{cid}", "s:nonexistent_btn", "x:unknown"):
        caplog.clear()
        with caplog.at_level("WARNING", logger="core"):
            asyncio.run(dispatch(up, ctx, bad))
        assert any("unrouted callback" in r.message for r in caplog.records), bad


def test_all_emitted_view_callbacks_route_without_unrouted_warning(caplog, monkeypatch):
    from newsbot import ui_widgets
    from newsbot.ui_dispatch import dispatch
    from newsbot.ui_views_admin import (
        view_admin_home, view_article, view_cat, view_cats, view_channel,
        view_content, view_crit, view_crits, view_my_ai, view_my_plan,
        view_queue, view_quiet, view_sched, view_source, view_sources, view_tz,
    )
    from newsbot.ui_views_super import (
        view_health, view_s_discs, view_s_mand, view_s_models, view_s_pays,
        view_s_plans, view_s_ref, view_s_texts, view_s_users, view_super_home,
    )
    from newsbot.ui_views_user import view_plans, view_referral
    from tests.test_ui_routing import FakeContext, FakeUpdate, _call, _mk_fixtures

    cid, aid = _mk_fixtures()
    sid = q("SELECT id FROM sources WHERE channel_id=?", (cid,), one=True)["id"]
    up, ctx = FakeUpdate(), FakeContext()

    emitted = set()
    real_render = ui_widgets.render

    async def spy_render(update, context, text, kb=None, **kw):
        for row in (kb or []):
            for btn in row:
                cb = getattr(btn, "callback_data", None)
                if cb:
                    emitted.add(cb)
        return await real_render(update, context, text, kb, **kw)

    for mod_name in ("newsbot.ui_widgets", "newsbot.ui_views_user", "newsbot.ui_views_admin", "newsbot.ui_views_super"):
        mod = __import__(mod_name, fromlist=["render"])
        if hasattr(mod, "render"):
            monkeypatch.setattr(mod, "render", spy_render)

    views = [
        (view_plans, ()),
        (view_referral, ()),
        (view_admin_home, ()),
        (view_channel, (cid,)),
        (view_sources, (cid, 0)),
        (view_source, (sid,)),
        (view_content, (cid,)),
        (view_cats, (cid, 0)),
        (view_cat, (cid, 0)),
        (view_crits, (cid, 0)),
        (view_crit, (cid, 0)),
        (view_sched, (cid,)),
        (view_quiet, (cid,)),
        (view_tz, (cid,)),
        (view_queue, (cid,)),
        (view_article, (aid,)),
        (view_my_ai, ()),
        (view_my_plan, ()),
        (view_super_home, ()),
        (view_health, ()),
        (view_s_plans, ()),
        (view_s_discs, ()),
        (view_s_users, ()),
        (view_s_models, ()),
        (view_s_pays, ()),
        (view_s_texts, ()),
        (view_s_mand, ()),
        (view_s_ref, ()),
    ]

    for fn, args in views:
        asyncio.run(_call(fn, up, ctx, *args))

    assert len(emitted) >= 35
    skip_heavy = {f"a:test:{cid}", f"a:pub:{aid}", f"a:srcr:{sid}", "s:models_test"}
    for cb in sorted(emitted - skip_heavy):
        caplog.clear()
        with caplog.at_level("WARNING"):
            asyncio.run(dispatch(up, ctx, cb))
        unrouted = [r.message for r in caplog.records if "unrouted callback" in r.message]
        assert not unrouted, f"Dead/unrouted button emitted by UI view: {cb}"


# ── TEST-013: Isolated DB fixture ────────────────────────────────────────────
def test_isolated_db_fixture_provides_clean_database(isolated_db):
    # در دیتابیس ایزوله، کاربرانی که در تست‌های قبلی ساخته شده‌اند وجود ندارند
    assert q("SELECT COUNT(*) c FROM users", one=True)["c"] == 0
    assert q("SELECT COUNT(*) c FROM plans", one=True)["c"] >= 1


# ── TEST-014: Critical modules have no silent `except: pass` + README match ──
def test_critical_modules_have_no_silent_except_pass_and_readme_is_accurate():
    root = pathlib.Path(__file__).resolve().parent.parent
    readme = (root / "README.md").read_text(encoding="utf-8")
    assert "هیچ `except` خالی‌ای خطا را پنهان نمی‌کند" not in readme
    assert "مسیرهای بحرانی" in readme

    critical_files = [
        root / "newsbot" / "core_payments.py",
        root / "newsbot" / "core_wallet.py",
    ]
    for path in critical_files:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler):
                assert not (len(node.body) == 1 and isinstance(node.body[0], ast.Pass)), (
                    f"Silent 'except: pass' found in critical module {path.name}:{node.lineno}"
                )
