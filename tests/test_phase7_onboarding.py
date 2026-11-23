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


# ══════════════════════════════════════════════════════════════════════════════════
# تسک ۳ — منوی ساده، تکمیلِ «افزودن کانال» و محافظانِ مسیریابی
# ══════════════════════════════════════════════════════════════════════════════════
import time as _time
from telegram.constants import ChatType as _ChatType
from newsbot.core_users import set_lang as _set_lang, set_onboarding_state as _set_state
from newsbot.core_channels import add_channel as _add_channel
from newsbot.ui_inputs import handle_input as _handle_input
from newsbot.ui_support import on_message as _on_message


class _TextMsg(_Msg):
    """پیامِ متنیِ ورودی — handle_input به text/delete/chat نیاز دارد."""
    def __init__(self, sink, text, chat_id, chat_type):
        super().__init__(sink)
        self.text, self.caption = text, None
        self.text_html, self.caption_html = None, None
        self.forward_origin, self.reply_to_message, self.date = None, None, None
        self.chat = NS(id=chat_id, type=chat_type, title="TestCh")
        self.deleted = False
    async def delete(self): self.deleted = True; return True
    async def set_reaction(self, *a, **kw): return True


class _AdminsBot(_Bot):
    """باتِ ساختگی با فهرستِ ادمینانِ قابلِ تنظیم (گاردِ امنیتیِ ch_add)."""
    def __init__(self, sink, chat_id, admins=(), bot_admin=True):
        super().__init__(sink)
        self.id, self.chat_id, self.admins, self.bot_admin = 123, chat_id, admins, bot_admin
    async def get_chat(self, _identifier):
        return NS(id=self.chat_id, type=_ChatType.CHANNEL, title="TestCh", username="testch_xyz")
    async def get_chat_member(self, _chat_id, user_id):
        if user_id == self.id and not self.bot_admin:
            return NS(status="member", can_post_messages=False)
        return NS(status="administrator", can_post_messages=True)
    async def get_chat_administrators(self, _chat_id):
        return [NS(user=NS(id=uid)) for uid in self.admins]


def _text_env(uid, text, admins=(), chat_id=None):
    """Update/Context با ورودیِ متنی و فرمِ ch_addِ فعال."""
    from newsbot.core_users import ensure_user
    ensure_user(uid, "in%d" % uid, "In%d" % uid)
    ctx = _Ctx(uid)
    chat_id = chat_id or (-100780000 - uid)
    msg = _TextMsg(ctx._record, text, uid, _ChatType.PRIVATE)
    ctx.bot = _AdminsBot(ctx._record, chat_id, admins=admins)
    upd = NS(effective_user=NS(id=uid, username="in%d" % uid, full_name="In",
                               first_name="In", is_premium=False),
             effective_chat=NS(id=uid, type=_ChatType.PRIVATE),
             effective_message=msg, message=msg, callback_query=_Query(uid, ctx._record))
    ctx.user_data["await"] = {"kind": "ch_add", "back": "a:home", "ts": _time.time()}
    return upd, ctx


def _ready_home_user(uid):
    """کاربرِ دارای کانال با state=done — خانهٔ معمولی (نه onboarding)."""
    from newsbot.core_users import ensure_user
    ensure_user(uid, "home%d" % uid, "Home%d" % uid)
    _set_lang(uid, "fa")
    _add_channel(uid, -100780100 - uid, "HomeCh", "homech_xyz", uid)
    _set_state(uid, "done")


def test_home_has_no_shortcut_buttons_but_commands_still_work(fresh_user):
    uid = fresh_user
    _ready_home_user(uid)
    upd, ctx = _env(uid)
    asyncio.run(dispatch(upd, ctx, "home"))
    shortcuts = [b for b in ctx.buttons
                 if b.startswith(("u:plans", "u:man", "u:ref", "u:help"))]
    assert shortcuts == [], "چهار میانبر نباید در خانه باشند"
    assert "u:lang" in ctx.buttons, "ردیفِ زبان باید بماند"

    import newsbot.ui_commands as uc
    for cmd, expected in ((uc.cmd_help, "home"), (uc.cmd_man, "u:man_close"),
                          (uc.cmd_ref, "home")):
        u2, c2 = _env(uid)
        asyncio.run(cmd(u2, c2))
        assert expected in c2.buttons, f"فرمانِ {cmd.__name__} به صفحهٔ خود نرسید"
    u2, c2 = _env(uid)
    asyncio.run(uc.cmd_create(u2, c2))
    assert any(b.startswith("u:plan:") for b in c2.buttons), "/create ⇒ پلنها"


def test_first_channel_sets_done_and_welcome_is_not_repeated(fresh_user):
    uid = fresh_user
    upd, ctx = _env(uid)
    _first_run(upd, ctx)
    asyncio.run(dispatch(upd, ctx, "onb:start"))
    assert get_onboarding_state(uid) == "adding_channel"

    in_upd, in_ctx = _text_env(uid, "-100780001", admins=(uid,))
    asyncio.run(_handle_input(in_upd, in_ctx, in_ctx.user_data["await"]))
    assert get_onboarding_state(uid) == "done", "اولین کانال باید onboarding را ببندد"
    assert len(list(q("SELECT id FROM channels WHERE admin_id=?", (uid,)))) == 1

    u2, c2 = _env(uid)
    asyncio.run(cmd_start(u2, c2))
    assert "onb:start" not in c2.buttons, "خوشامد دوباره نمایش داده شد"
    assert "a:chadd" in c2.buttons, "کاربرِ دارای کانال باید به پنلِ خود برسد"


def test_ch_add_security_guards_survive_onboarding(fresh_user):
    uid = fresh_user
    upd, ctx = _env(uid)
    _first_run(upd, ctx)
    asyncio.run(dispatch(upd, ctx, "onb:start"))

    # همان گاردِ ادمینِ کانال: حتی با پلنِ رایگانِ onboarding، کاربرِ غیرِ ادمین رد میشود
    in_upd, in_ctx = _text_env(uid, "-100780002", admins=())
    asyncio.run(_handle_input(in_upd, in_ctx, in_ctx.user_data["await"]))
    from newsbot.ui_i18n import tr
    notices = [a[0][0] for a in in_upd.callback_query.answers]
    assert tr("fa", "ch_user_not_admin") in notices, notices
    assert get_onboarding_state(uid) == "adding_channel"
    assert not q("SELECT id FROM channels WHERE admin_id=?", (uid,)), "کانال ثبت نشده بماند"


def test_stale_onb_start_after_done_goes_home_without_grant(fresh_user):
    uid = fresh_user
    _set_lang(uid, "fa")
    _set_state(uid, "done")
    upd, ctx = _env(uid)
    asyncio.run(dispatch(upd, ctx, "onb:start"))
    assert get_user(uid)["free_used"] == 0
    assert get_onboarding_state(uid) == "done"
    assert "u:lang" in ctx.buttons and "onb:start" not in ctx.buttons


def test_stale_onb_start_with_existing_channel_does_not_grant(fresh_user):
    uid = fresh_user
    _set_lang(uid, "fa")
    _add_channel(uid, -100780003, "Existing", "existing_xyz", uid)
    _set_state(uid, "adding_channel")
    upd, ctx = _env(uid)
    asyncio.run(dispatch(upd, ctx, "onb:start"))
    assert get_user(uid)["free_used"] == 0
    assert get_onboarding_state(uid) == "adding_channel"
    assert "u:lang" in ctx.buttons


def test_admin_and_super_with_welcome_state_bypass_the_intercept(fresh_user, monkeypatch):
    from newsbot import config
    from newsbot.core_plans import assign_plan
    from newsbot.core_users import ensure_user
    uid = fresh_user
    _set_lang(uid, "fa")
    pid = next(p["id"] for p in list_plans() if not p["is_free"])
    assign_plan(uid, pid)                    # نقشِ admin
    _set_state(uid, "welcome")
    upd, ctx = _env(uid)
    asyncio.run(dispatch(upd, ctx, "home"))
    assert "onb:start" not in ctx.buttons, "مدیر در خوشامد گیر افتاد"
    assert "a:chadd" in ctx.buttons

    monkeypatch.setattr(config, "SUPER_ADMIN_IDS", (uid,))
    ensure_user(uid, "fresh", "Fresh")       # role → super
    upd, ctx = _env(uid)
    asyncio.run(dispatch(upd, ctx, "home"))
    assert "onb:start" not in ctx.buttons
    assert "s:report" in ctx.buttons


def test_plain_private_message_goes_through_the_welcome_intercept(fresh_user):
    uid = fresh_user
    _set_lang(uid, "fa")
    _set_state(uid, "welcome")
    upd, ctx = _text_env(uid, "سلام", admins=())
    ctx.user_data.pop("await", None)         # پیامِ عادی، نه ورودیِ فرم
    asyncio.run(_on_message(upd, ctx))
    assert "onb:start" in ctx.buttons


def test_ref_and_deeplink_args_take_priority_for_a_user_with_language(fresh_user, monkeypatch):
    """نسخهٔ زباندارِ رگرسیونِ Task 2: کاربرِ بیِزبان پیش از شاخهٔ جدید به picker میرفت."""
    import newsbot.ui_commands as uc
    import newsbot.core_referrals as refs
    import newsbot.notify as notify
    uid = fresh_user
    _set_lang(uid, "fa")
    calls = []
    def _ref(ref, invitee): calls.append(("ref", ref, invitee)); return True
    async def _notice(*a, **kw): return True
    async def _dl(update, context, key): calls.append(("dl", key)); return True
    monkeypatch.setattr(refs, "record_referral", _ref)
    monkeypatch.setattr(notify, "notify_user", _notice)
    monkeypatch.setattr(uc, "send_deeplink", _dl)

    upd, ctx = _env(uid); ctx.args = ["ref_900"]
    asyncio.run(cmd_start(upd, ctx))
    assert ("ref", 900, uid) in calls, "پیشوندِ ref_ باید پیش از onboarding پردازش شود"
    assert "onb:start" in ctx.buttons        # خوشامد پس از ثبتِ ارجاع همچنان نمایش داده میشود

    from newsbot.ratelimit import rate_clear
    rate_clear(f"dl:{uid}")   # تستِ Task 2 همان کلیدِ نرخ را مصرف کرده است
    upd, ctx = _env(uid); ctx.args = ["r_key"]
    asyncio.run(cmd_start(upd, ctx))
    assert ("dl", "key") in calls
    assert "onb:start" not in ctx.buttons


def test_failing_assign_plan_releases_the_free_claim(fresh_user, monkeypatch):
    import newsbot.ui_dispatch as ud
    uid = fresh_user
    _set_lang(uid, "fa")
    def _boom(*a, **kw): raise RuntimeError("assign failed")
    monkeypatch.setattr(ud, "assign_plan", _boom)
    upd, ctx = _env(uid)
    asyncio.run(dispatch(upd, ctx, "onb:start"))
    assert get_user(uid)["free_used"] == 0, "پلنِ رایگان نسوخته بماند"
    assert get_user(uid)["plan_id"] is None
    assert get_onboarding_state(uid) == "adding_channel"


def test_reply_still_routes_to_qa_after_the_home_simplification(isolated_db, monkeypatch):
    """همان قراردادِ فاز ۴: reply در گروهِ متصل فقط یک پاسخ میگیرد."""
    from newsbot import ai_models as amod, qa
    from newsbot.core_channels import add_channel as add_ch
    add_ch(900001, -100111, "کانال", "", 900001)
    async def _stub(system, user, **kw): return "پاسخِ مدل", "m", None
    monkeypatch.setattr(amod, "ai_chat", _stub)

    class _QABot:
        def __init__(self): self.sent = []
        async def get_chat(self, _gid): return NS(linked_chat_id=-100111)
        async def send_message(self, chat_id, text, **kw):
            self.sent.append({"chat_id": chat_id, "text": text, **kw})
            return NS(message_id=1)
    bot = _QABot()
    post = NS(sender_chat=NS(id=-100111, type="channel"), forward_from_chat=None,
              forward_from_message_id=77, text="متن پست", caption=None,
              is_automatic_forward=True, message_id=5)
    msg = NS(chat_id=-555, message_id=10, text="این پست دربارهٔ چیست؟", caption=None,
             chat=NS(type="supergroup"), reply_to_message=post, date=None)
    upd = NS(effective_message=msg,
             effective_user=NS(id=900001, username="u", full_name="U"))
    asyncio.run(qa.on_group_reply(upd, NS(bot=bot)))
    assert len(bot.sent) == 1
    assert bot.sent[0]["reply_to_message_id"] == 10
