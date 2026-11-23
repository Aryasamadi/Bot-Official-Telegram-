# -*- coding: utf-8 -*-
"""فاز ۷ — ستونِ افزایشیِ onboarding_state و کمککنندههای حالتِ نخستین ورود."""
import asyncio
from types import SimpleNamespace as NS

import pytest

from newsbot.db import q
from newsbot.core_users import get_user, get_onboarding_state
from newsbot.core_plans import list_plans
from newsbot.ui_commands import cmd_start
from newsbot.ui_dispatch import dispatch


# ── دوبل های محلی (بدون شبکه): هر render/پاپآپ در context ثبت میشود ──────────────
class _Msg:
    def __init__(self, sink):
        self.sink, self.message_id = sink, 100
    async def edit_text(self, text, **kw):
        self.sink(text, kw); return self
    async def reply_text(self, text, **kw):
        self.sink(text, kw); return self
    async def edit_reply_markup(self, **kw): return self
    async def delete(self): return True


class _Query:
    def __init__(self, uid, sink):
        self.data = "noop"; self.answers = []; self.from_user = NS(id=uid)
        self.message = _Msg(sink)
    async def answer(self, *a, **kw): self.answers.append((a, kw)); return True
    async def edit_message_text(self, text, **kw): return await self.message.edit_text(text, **kw)
    async def edit_message_reply_markup(self, **kw): return self.message
    async def delete_message(self): return True


class _Bot:
    def __init__(self, sink): self.sink = sink
    async def send_message(self, chat_id, text, **kw):
        self.sink(text, kw); return _Msg(self.sink)
    async def edit_message_text(self, text, **kw):
        self.sink(text, kw); return _Msg(self.sink)
    async def get_chat_member(self, *a, **kw): return NS(status="administrator")
    async def get_me(self): return NS(id=123, username="testbot")


class _Ctx:
    def __init__(self, uid):
        self.bot = _Bot(self._record); self.user_data = {}; self.chat_data = {}
        self.args = None; self._callback_answered = False
        self.renders, self.snap, self.buttons = [], [], []
    def _record(self, text, kw):
        self.renders.append(text)
        row = []
        for r in (kw.get("reply_markup").inline_keyboard if kw.get("reply_markup") else []):
            for b in r:
                if getattr(b, "callback_data", None): row.append(b.callback_data)
        self.snap.append(row); self.buttons.extend(row)


def _env(uid):
    ctx = _Ctx(uid)
    user = NS(id=uid, username="u%d" % uid, full_name="User %d" % uid,
              first_name="User", is_premium=False)
    upd = NS(effective_user=user, effective_chat=NS(id=uid, type="private"),
             effective_message=_Msg(ctx._record), message=_Msg(ctx._record),
             callback_query=_Query(uid, ctx._record))
    return upd, ctx


@pytest.fixture
def fresh_user(isolated_db):
    from newsbot.core_users import ensure_user
    uid = 770701
    ensure_user(uid, "fresh", "Fresh")
    return uid


@pytest.fixture
def paid_user(isolated_db):
    from newsbot.core_users import ensure_user, set_lang
    from newsbot.core_plans import assign_plan
    uid = 770702
    ensure_user(uid, "paid", "Paid"); set_lang(uid, "fa")
    pid = next(p["id"] for p in list_plans() if not p["is_free"])
    assign_plan(uid, pid)
    return uid, pid


def _first_run(upd, ctx):
    """/start ⇒ انتخاب زبان ⇒ خوشآمدگویی."""
    asyncio.run(cmd_start(upd, ctx))
    asyncio.run(dispatch(upd, ctx, "lang:fa"))


def test_new_user_reaches_add_channel_without_a_plan_button(fresh_user):
    # /start ⇒ picker ⇒ welcome ⇒ onb:start
    uid = fresh_user; upd, ctx = _env(uid)
    _first_run(upd, ctx)
    welcome = [i for i, s in enumerate(ctx.snap) if s == ["onb:start"]]
    assert welcome, "صفحهٔ خوشآمدگویی با یک دکمهٔ onb:start نمایش داده نشد"
    assert "u:plans" not in ctx.snap[welcome[-1]]      # بدون میانبرِ پلن
    asyncio.run(dispatch(upd, ctx, "onb:start"))
    u = get_user(uid)
    assert u["free_used"] == 1 and u["role"] == "admin"
    assert get_onboarding_state(uid) == "adding_channel"
    # a:chadd was reached — همان فرمی که tr(lang, "ch_add_prompt") نمایش میدهد
    from newsbot.ui_i18n import tr
    assert ctx.user_data["await"]["kind"] == "ch_add"
    assert any(tr("fa", "ch_add_prompt")[:40] in t for t in ctx.renders)


def test_start_and_onb_start_are_idempotent(fresh_user):
    uid = fresh_user; upd, ctx = _env(uid)
    _first_run(upd, ctx)
    asyncio.run(dispatch(upd, ctx, "onb:start"))
    first = get_user(uid)["plan_expires"]
    # repeat /start then onb:start
    asyncio.run(cmd_start(upd, ctx))
    asyncio.run(dispatch(upd, ctx, "onb:start"))
    assert get_user(uid)["plan_expires"] == first
    assert get_user(uid)["free_used"] == 1
    assert get_onboarding_state(uid) in ("adding_channel", "done")


def test_paid_user_is_not_downgraded_by_onboarding(paid_user):
    uid, paid_plan_id = paid_user; upd, ctx = _env(uid)
    # onb:start with an active paid plan
    asyncio.run(dispatch(upd, ctx, "onb:start"))
    assert get_user(uid)["free_used"] == 0
    assert get_user(uid)["plan_id"] == paid_plan_id
    assert get_onboarding_state(uid) == "adding_channel"


def test_ref_and_deeplink_start_args_are_unchanged(fresh_user, monkeypatch):
    import newsbot.ui_commands as uc
    import newsbot.core_referrals as refs
    import newsbot.notify as notify
    uid = fresh_user; calls = []
    def _ref(ref, invitee): calls.append(("ref", ref, invitee)); return True
    async def _notify(*a, **kw): return True
    async def _dl(update, context, key): calls.append(("dl", key)); return True
    monkeypatch.setattr(refs, "record_referral", _ref)
    monkeypatch.setattr(notify, "notify_user", _notify)
    monkeypatch.setattr(uc, "send_deeplink", _dl)

    # cmd_start with context.args = ["ref_900"] ⇒ record_referral called
    upd, ctx = _env(uid); ctx.args = ["ref_900"]
    asyncio.run(cmd_start(upd, ctx))
    assert ("ref", 900, uid) in calls

    # cmd_start with ["r_key"] ⇒ send_deeplink called, no welcome view
    upd, ctx = _env(uid); ctx.args = ["r_key"]
    asyncio.run(cmd_start(upd, ctx))
    assert ("dl", "key") in calls
    assert "onb:start" not in ctx.buttons


def test_schema_is_thirteen_and_the_column_is_additive(isolated_db):
    import newsbot
    from newsbot.core_users import ensure_user
    assert newsbot.SCHEMA_VERSION == 13
    ensure_user(4242, "u", "U")   # جدولِ ایزوله خالی است؛ یک ردیف برای بررسیِ پیشفرضِ ستون لازم است
    row = q("SELECT onboarding_state FROM users LIMIT 1", one=True)
    assert row is not None and row["onboarding_state"] in (None, "")


def test_onboarding_state_roundtrip_and_validation(isolated_db):
    from newsbot.core_users import ensure_user, get_onboarding_state, set_onboarding_state
    ensure_user(4242, "u", "U")
    assert get_onboarding_state(4242) == ""
    set_onboarding_state(4242, "welcome")
    assert get_onboarding_state(4242) == "welcome"
    set_onboarding_state(4242, "bogus")
    assert get_onboarding_state(4242) == "welcome"


def test_v13_db_is_refused_by_a_v12_build(isolated_db, monkeypatch):
    import newsbot.db as dbmod
    from newsbot.app import init_core
    q("UPDATE meta SET value='13' WHERE key='schema_version'", commit=True)
    monkeypatch.setattr(dbmod, "SCHEMA_VERSION", 12)
    conn = getattr(dbmod._tls, "conn", None)
    if conn is not None:
        conn.close()
        dbmod._tls.conn = None
    dbmod._SCHEMA_READY = False
    try:
        with pytest.raises(SystemExit) as exc:
            init_core()
        assert "newer than this build" in str(exc.value)
    finally:
        dbmod._SCHEMA_READY = False
