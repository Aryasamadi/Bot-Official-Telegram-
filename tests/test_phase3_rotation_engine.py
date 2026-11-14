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


# ── Task 5: خواندنِ audit از داخلِ ربات (/rotation) ──────────────────────────────
class _User:
    def __init__(self, uid):
        self.id = uid


class _Update:
    def __init__(self, uid):
        self.effective_user = _User(uid)


class _Context:
    def __init__(self, args=None):
        self.args = list(args or [])


def _run_cmd(monkeypatch, uid, args=None, super_ids=(1,)):
    """اجرای /rotation با ثبتِ متنِ خروجی به‌جای تماس با تلگرام."""
    from newsbot import config as cfg
    from newsbot import ui_views_super as V

    shown = {}

    async def fake_render(update, context, text, kb=None):
        shown["text"] = text

    async def fake_popup(update, context, text, kb=None):
        shown["text"] = text

    monkeypatch.setattr(cfg, "SUPER_ADMIN_IDS", set(super_ids))
    monkeypatch.setattr(V, "render", fake_render)
    monkeypatch.setattr(V, "popup", fake_popup)
    asyncio.run(V.cmd_rotation(_Update(uid), _Context(args)))
    return shown.get("text", "")


def test_rotation_command_shows_the_cursor_and_the_last_requests(transport, monkeypatch):
    s = _settings()
    _add("rot-a")
    _add("rot-b")
    asyncio.run(gen_mod.generate(s, _article("r-cmd")))

    text = _run_cmd(monkeypatch, 1)
    assert "public" in text
    assert "rot-a" in text or "rot-b" in text


def test_rotation_command_is_super_admin_only(transport, monkeypatch):
    _settings()
    _add("rot-a")
    text = _run_cmd(monkeypatch, 2)
    assert text == "", "غیرِ مدیر کلان نباید چیزی ببیند"


# ── بازبینیِ فاز ۳: سه نقصِ واقعی که این فایل قفل می‌کند ─────────────────────────
def test_the_cas_predicate_is_actually_checked(transport):
    """UPDATE با ``WHERE version=?`` اگر ردیفی عوض نکند، advance **باید** تلاشِ دوباره بدهد.

    نسخه‌های اولِ کد rowcount را نادیده می‌گرفتند: در باختِ مسابقه، مکان‌نما جلو نمی‌رفت ولی
    تابع «موفقیت» و version+۱ برمی‌گرداند — یک advance گم‌شده که کسی هم خبردار نمی‌شد.
    """
    from newsbot import rotation

    class _Cur:
        def __init__(self, rows, rowcount):
            self._rows, self.rowcount = rows, rowcount

        def fetchone(self):
            return self._rows[0]

    class _Conn:
        def __init__(self, rowcount):
            self.rowcount, self.sqls = rowcount, []

        def execute(self, sql, params=()):
            self.sqls.append(sql.split()[0].upper())
            if sql.lstrip().upper().startswith("SELECT"):
                return _Cur([{"cursor": 4, "version": 9}], 1)
            return _Cur([], self.rowcount)

    pool = [{"id": 1, "priority": 10}, {"id": 2, "priority": 10}]

    conn = _Conn(0)                                   # مسابقه باخت: هیچ ردیفی عوض نشد
    try:
        rotation._advance_rmw(conn, "public", pool)
        raised = False
    except rotation.RotationCASMiss:
        raised = True
    assert raised, "rowcount=0 یعنی CAS باخت؛ نباید «موفق» گزارش شود"

    conn = _Conn(1)                                   # CAS برد
    m, idx, cursor, version = rotation._advance_rmw(conn, "public", pool)
    assert (cursor, version) == (5, 10) and m["id"] in (1, 2) and idx in (0, 1)


def test_a_lost_race_is_retried_not_reported_as_success(transport, monkeypatch):
    """باختِ CAS در ``advance`` باید به تلاشِ دوباره (یا fail-open) برسد، نه به یک عددِ جعلی."""
    from newsbot import rotation

    rotation.rotation_ensure("public")
    real = rotation._advance_rmw
    calls = {"n": 0}

    def flaky(conn, scope, models):
        calls["n"] += 1
        if calls["n"] == 1:
            raise rotation.RotationCASMiss()
        return real(conn, scope, models)

    monkeypatch.setattr(rotation, "_advance_rmw", flaky)
    m, _idx, version = rotation.advance("public", [{"id": 7, "priority": 10}])
    row = rotation.rotation_get("public")
    assert m is not None and calls["n"] >= 2, "باید بعد از باختِ مسابقه دوباره تلاش کند"
    assert version == int(row["version"]), "نسخه باید همان چیزی باشد که در دیتابیس است"
    assert int(row["cursor"]) == 1, "فقط یک advance باید ثبت شود"
    q("DELETE FROM ai_rotation WHERE scope='public'", commit=True)


def test_a_permanently_stuck_cursor_fails_open_without_lying(transport, monkeypatch):
    """اگر مکان‌نما هرگز advance نشود، انتخاب انجام می‌شود ولی version از خودش ساخته نمی‌شود."""
    from newsbot import rotation

    rotation.rotation_ensure("public")
    monkeypatch.setattr(rotation, "_advance_rmw",
                        lambda conn, scope, models: (_ for _ in ()).throw(rotation.RotationCASMiss()))
    m, idx, version = rotation.advance("public", [{"id": 3, "priority": 10}, {"id": 4, "priority": 10}])
    row = rotation.rotation_get("public")
    assert m is not None and idx in (0, 1), "fail-open باید یک مدلِ قابلِ استفاده برگرداند"
    assert version == int(row["version"]), "نباید نسخه‌ای که در دیتابیس نیست گزارش شود"
    assert int(row["cursor"]) == 0, "مکان‌نما نباید جلو رفته باشد"
    q("DELETE FROM ai_rotation WHERE scope='public'", commit=True)


def test_a_broken_rotation_never_stops_publishing(transport, monkeypatch):
    """ADR-۳: چرخش هرگز نباید نشر را متوقف کند — خطای advance فقط به failoverِ عادی برمی‌گردد."""
    from newsbot import rotation

    def boom(scope, models):                 # هم‌گام، چون arun آن را در تردِ DB اجرا می‌کند
        raise RuntimeError("rotation backend exploded")

    s = _settings()
    _add("rot-a")
    _add("rot-b")
    monkeypatch.setattr(rotation, "advance", boom)
    _j, model, err = asyncio.run(gen_mod.generate(s, _article("r-hard")))
    assert err is None and model in ("rot-a", "rot-b")


def test_the_audit_row_only_names_a_model_that_was_really_called(transport, monkeypatch):
    """اگر مدلِ نوبتی busy/در دسترس‌نبودنی باشد، آن درخواست آن را «برنداشت» ⇒ نباید ردیف بگیرد.

    ردیفِ audit یعنی «این درخواست مدلِ X را از چرخش برداشت». اگر X صدا زده نشد و failover
    مدلِ دیگری را برداشت، ردیفِ به‌نامِ X یک دروغ است؛ پس هیچ ردیفی نوشته نمی‌شود.
    """
    s = _settings()
    _add("rot-a")
    _add("rot-b")
    monkeypatch.setattr(amod, "model_busy", lambda mid: amod.get_model(mid)["name"] == "rot-a")

    _j, model, err = asyncio.run(gen_mod.generate(s, _article("r-busy")))
    assert err is None and model == "rot-b", "failover باید مدلِ آزاد را بردارد"
    assert rotation.rotation_log_recent("public", limit=5) == [], "مدلی که صدا زده نشد نباید audit بگیرد"
    assert rotation.rotation_get("public")["version"] == 1, "advance یک بار انجام شده است"
