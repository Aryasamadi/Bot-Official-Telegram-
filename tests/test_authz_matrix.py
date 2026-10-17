# -*- coding: utf-8 -*-
"""فاز ۶ — تست جامع «مرز دسترسی» (authz): ماتریس نقش×منبع×اقدام و گاردهای سرور.

پوشش هدف‌های فاز ۶ طبق ROADMAP:
  • GAP-02 / QA-06 — نقض ماتریس نقش در dispatch (کاربر عادی در بخش «a:»، مدیر در «s:»،
    کانال/منبع/مقالهٔ غیرمالک) و در _input_access
  • C8 — بازیابی مالکیت (a:chrec) فقط برای ثبت‌کنندهٔ نخست و فقط وقتی مالک فعلی
    دیگر ادمین کانال نیست
  • UISEC-01/02 — اعتبارسنجی سمت سرور (مقدار فیلدها و دسترسی state ورودی)
  • UISEC-03 — ماسک کلید API در view_s_model و غیبت آن در a:myai_v و a:srcv
  • UISEC-04 — سقف نرخ disc/mtest + ماندگاری کلیدها در DB (RATE_PERSIST_PREFIXES)
  • UISEC-06 — انقضای نشست فرم (await_expired در handle_input و on_message)
  • UISEC-08 — escape داده‌ی کاربر در ویوها (عنوان کانال/مقاله، نام کاربر)
  • UISEC-09 — سقف اندازهٔ فایل رسید پرداخت

هیچ تماس شبکه‌ای انجام نمی‌شود: ai_chat با دوبل جایگزین می‌شود و Update/Context ساختگی‌اند.
"""
import asyncio
import time
from datetime import timedelta
from types import SimpleNamespace

import pytest

from newsbot import config
from newsbot.db import q
from newsbot.timeutils import now_iso, now_utc
from newsbot.core_channels import add_channel, get_channel, get_settings
from newsbot.core_sources import add_source, set_source_api
from newsbot.core_articles import article_insert
from newsbot.core_plans import create_plan, assign_plan
from newsbot.core_discounts import disc_create
from newsbot.ai_models import add_model
from newsbot.ui_i18n import tr


# ── دوبل‌های تست: Update / Context سبک (الگو: tests/test_ui_routing.py) ──

class FakeUser:
    def __init__(self, uid=1, username=None, name=None):
        self.id = uid
        self.username = username if username is not None else f"u{uid}"
        self.full_name = name if name is not None else f"User {uid}"
        self.first_name = self.full_name
        self.last_name = ""
        self.is_premium = False
        self.language_code = "fa"


class FakeChat:
    def __init__(self, chat_id=1, kind="private"):
        self.id = chat_id
        self.type = kind
        self.title = "chat"
        self.first_name = "Admin"


class FakeMessage:
    def __init__(self, message_id=100, chat_id=1):
        self.message_id = message_id
        self.chat_id = chat_id
        self.chat = FakeChat(chat_id)
        self.edits = []
        self.text = None
        self.caption = None
        self.text_html = None
        self.caption_html = None
        self.photo = None
        self.document = None
        self.reply_to_message = None
        self.forward_origin = None
        self.date = None
    async def edit_text(self, text, **kw): self.edits.append(text); return self
    async def reply_text(self, text, **kw): self.edits.append(text); return self
    async def delete(self): return True
    async def edit_reply_markup(self, **kw): return self
    async def set_reaction(self, *a, **kw): return True


class FakeQuery:
    def __init__(self, user=None):
        self.data = "noop"
        self.answers = []
        self.message = FakeMessage()
        self.from_user = user or FakeUser()
    async def answer(self, *a, **kw): self.answers.append((a, kw)); return True
    async def edit_message_text(self, text, **kw): return await self.message.edit_text(text, **kw)
    async def edit_message_reply_markup(self, **kw): return self.message
    async def delete_message(self): return True


class FakeBot:
    def __init__(self):
        self.sent = []
        self.member_status = "administrator"
    async def send_message(self, *a, **kw): self.sent.append((a, kw)); return FakeMessage()
    async def edit_message_text(self, *a, **kw): return FakeMessage()
    async def get_chat_member(self, *a, **kw): return SimpleNamespace(status=self.member_status)
    async def get_chat(self, *a, **kw): return SimpleNamespace(id=1, username="ch", title="ch", type="channel")
    async def get_me(self): return SimpleNamespace(id=123, username="testbot")


class FakeContext:
    def __init__(self):
        self.bot = FakeBot()
        self.user_data = {}
        self.chat_data = {}
        self._callback_answered = False


# ── کمکی‌ها ──

def _mk_user(uid, role="user", lang="fa", banned=0, name=None, username=None):
    q("INSERT OR REPLACE INTO users(id,username,name,role,banned,lang,created_at,last_seen) VALUES(?,?,?,?,?,?,?,?)",
      (uid, username if username is not None else f"u{uid}", name if name is not None else f"User {uid}",
       role, banned, lang, now_iso(), now_iso()), commit=True)
    return uid


def _mk_update(uid, text=None, callback=True):
    up = SimpleNamespace()
    up.effective_user = FakeUser(uid)
    up.effective_message = FakeMessage()
    up.message = FakeMessage()
    if text is not None:
        up.message.text = text
    up.callback_query = FakeQuery(up.effective_user) if callback else None
    up.effective_chat = FakeChat()
    return up, FakeContext()


def _mk_super_update():
    _mk_user(1, role="super", lang="fa")
    return _mk_update(1)


def _texts(up, ctx):
    """همهٔ متن‌هایی که در این چرخه به کاربر نمایش داده شده: answers + sent + edits."""
    parts = []
    qy = up.callback_query
    if qy is not None:
        for a, kw in qy.answers:
            if a and isinstance(a[0], str):
                parts.append(a[0])
        parts += list(qy.message.edits)
    for a, kw in ctx.bot.sent:
        if len(a) > 1 and isinstance(a[1], str):
            parts.append(a[1])
    return "\n".join(parts)


def _mk_plan(uid, **kw):
    fields = dict(name=f"plan-{uid}", name_en="plan", days=30, daily_posts=5,
                  max_sources=10, max_channels=5, daily_tests=5)
    fields.update(kw)
    pid = create_plan(**fields)
    assign_plan(uid, pid)
    return pid


# ── GAP-02 / QA-06: ماتریس نقش در dispatch ──

def test_role_matrix_user_cannot_enter_admin_area():
    from newsbot.ui_dispatch import dispatch
    uid = _mk_user(430001, role="user", lang="fa")
    up, ctx = _mk_update(uid)
    asyncio.run(dispatch(up, ctx, "a:home"))
    texts = _texts(up, ctx)
    assert tr("fa", "no_admin") in texts
    assert tr("fa", "panel") not in texts          # پنل مدیر هرگز برای کاربر عادی رندر نمی‌شود


def test_role_matrix_admin_cannot_enter_super_area():
    from newsbot.ui_dispatch import dispatch
    uid = _mk_user(430002, role="admin", lang="fa")
    up, ctx = _mk_update(uid)
    asyncio.run(dispatch(up, ctx, "s:home"))
    assert ctx.bot.sent == []
    assert up.callback_query.message.edits == []
    assert up.callback_query.answers == []


def test_admin_cannot_touch_foreign_channel_source_article():
    from newsbot.ui_dispatch import dispatch
    uid_a = _mk_user(430010, role="admin", lang="fa")
    uid_b = _mk_user(430011, role="admin", lang="fa")
    cid_b = add_channel(uid_b, 97111, "Secret Channel B", "secretb", uid_b)
    sid_b = add_source(uid_b, cid_b, "https://example.com/foreign-feed.xml")
    aid_b = article_insert(uid_b, cid_b, "h-authz-f", "https://example.com/f", "Foreign article", 0, None, status="ready")
    up, ctx = _mk_update(uid_a)
    asyncio.run(dispatch(up, ctx, f"a:ch:{cid_b}"))
    asyncio.run(dispatch(up, ctx, f"a:srcv:{sid_b}"))
    asyncio.run(dispatch(up, ctx, f"a:art:{aid_b}"))
    texts = _texts(up, ctx)
    assert tr("fa", "notfound") in texts
    assert "Secret Channel B" not in texts
    assert "Foreign article" not in texts


def test_plan_gate_blocks_content_actions():
    from newsbot.ui_dispatch import dispatch
    uid = _mk_user(430020, role="admin", lang="fa")     # بدون پلن فعال
    cid = add_channel(uid, 97121, "NoPlan Ch", "noplanch", uid)
    up, ctx = _mk_update(uid)
    asyncio.run(dispatch(up, ctx, f"a:test:{cid}"))
    assert tr("fa", "plan_inactive") in _texts(up, ctx)


# ── UISEC-01/02: _input_access (نقش + مالکیت + سهمیه) و _input_value ──

def test_input_access_matrix_rejects_by_role_and_ownership():
    from newsbot.ui_inputs import _input_access
    banned = _mk_user(430030, role="user", lang="fa", banned=1)
    plain = _mk_user(430031, role="user", lang="fa")
    admin_noplan = _mk_user(430032, role="admin", lang="fa")
    admin_plan = _mk_user(430033, role="user", lang="fa")
    _mk_plan(admin_plan, max_sources=1, max_channels=1)
    cid_plan = add_channel(admin_plan, 97131, "Plan Ch", "planch", admin_plan)
    add_source(admin_plan, cid_plan, "https://example.com/plan-feed.xml")
    cid_noplan = add_channel(admin_noplan, 97132, "NoPlan2 Ch", "noplan2", admin_noplan)
    foreign = _mk_user(430034, role="admin", lang="fa")
    cid_foreign = add_channel(foreign, 97133, "Foreign Ch", "foreignch", foreign)
    aid_foreign = article_insert(foreign, cid_foreign, "h-authz-ia", "https://example.com/ia", "IA", 0, None, status="ready")
    mid_foreign = add_model("https://api.example.com/v1", "k", "gpt-ia", owner_id=foreign)
    mid_global = add_model("https://api.example.com/v1", "k", "gpt-ia2", owner_id=None)

    assert _input_access({"kind": "field", "cid": cid_plan, "field": "topic"}, banned, "fa") == tr("fa", "banned")
    assert _input_access({"kind": "field", "cid": cid_plan, "field": "topic"}, plain, "fa") == tr("fa", "no_admin")
    assert _input_access({"kind": "src_add", "cid": cid_noplan}, admin_noplan, "fa") == tr("fa", "plan_inactive")
    assert _input_access({"kind": "field", "cid": cid_plan, "field": "topic"}, admin_noplan, "fa") == tr("fa", "notfound")
    assert _input_access({"kind": "plan_field", "pid": 1, "field": "name"}, admin_plan, "fa") == tr("fa", "no_admin")
    assert _input_access({"kind": "art_edit", "aid": aid_foreign}, admin_plan, "fa") == tr("fa", "notfound")
    assert _input_access({"kind": "model_field", "mid": mid_global, "field": "name"}, admin_plan, "fa") == tr("fa", "no_admin")
    assert _input_access({"kind": "model_field", "mid": mid_foreign, "field": "name", "own": 1}, admin_plan, "fa") == tr("fa", "notfound")
    assert _input_access({"kind": "src_add", "cid": cid_plan}, admin_plan, "fa") == tr("fa", "limit_sources", n=1)
    assert _input_access({"kind": "field", "cid": cid_plan, "field": "topic"}, admin_plan, "fa") is None


def test_input_value_validates_server_side(monkeypatch):
    from newsbot import ui_inputs
    monkeypatch.setattr(ui_inputs, "_ALLOW_PRIVATE_MODELS", False)
    bad = [("channel", "no spaces allowed here!"), ("channel", "@x"),
           ("base_url", "http://127.0.0.1:9999"), ("source_url", "http://10.0.0.5/rss"),
           ("api_url", "ftp://example.com/x"), ("model", "x" * 257),
           ("api_key", "with space"), ("temperature", "3"), ("daily_posts_cap", "0")]
    for field, value in bad:
        with pytest.raises(ValueError):
            ui_inputs._input_value(field, value, "fa")
    assert ui_inputs._input_value("api_key", "-", "fa") == ""
    assert ui_inputs._input_value("channel", "@validchannel", "fa") == "@validchannel"
    assert ui_inputs._input_value("temperature", "0.7", "fa") == 0.7


# ── C8: بازیابی مالکیت فقط برای ثبت‌کنندهٔ نخست ──

def test_chrec_only_registrant_can_restore():
    from newsbot.ui_dispatch import dispatch
    owner = _mk_user(430040, role="admin", lang="fa")
    rival = _mk_user(430041, role="admin", lang="fa")
    cid = add_channel(owner, 97141, "Claimed Ch", "claimedch", owner)
    q("UPDATE channels SET admin_id=?, verified_by=? WHERE id=?", (rival, rival, cid), commit=True)

    up_r, ctx_r = _mk_update(rival)                     # غیرثبت‌کننده
    asyncio.run(dispatch(up_r, ctx_r, f"a:chrec:{cid}"))
    assert tr("fa", "notfound") in _texts(up_r, ctx_r)
    assert get_channel(cid)["admin_id"] == rival

    up_o, ctx_o = _mk_update(owner)                     # ثبت‌کنندهٔ نخست
    asyncio.run(dispatch(up_o, ctx_o, f"a:chrec:{cid}"))   # مالک فعلی هنوز ادمین کانال است
    assert get_channel(cid)["admin_id"] == rival

    ctx_left = FakeContext()
    ctx_left.bot.member_status = "left"                 # مالک فعلی دیگر ادمین کانال نیست
    asyncio.run(dispatch(up_o, ctx_left, f"a:chrec:{cid}"))
    assert get_channel(cid)["admin_id"] == owner


# ── UISEC-06: انقضای نشست فرم ──

def test_await_ttl_and_config_value():
    from newsbot.ui_widgets import await_expired
    assert config.AWAIT_TTL_SEC == 1800
    assert await_expired({"ts": time.time()}) is False
    assert await_expired({"ts": time.time() - config.AWAIT_TTL_SEC - 1}) is True
    assert await_expired({}) is True
    assert await_expired(None) is True


def test_handle_input_expired_state_is_discarded():
    from newsbot.ui_inputs import handle_input
    uid = _mk_user(430050, role="admin", lang="fa")
    cid = add_channel(uid, 97151, "Exp Ch", "expch", uid)
    up, ctx = _mk_update(uid, text="value-that-must-not-apply")
    st = {"kind": "field", "field": "topic", "cid": cid, "back": f"a:con:{cid}",
          "ts": time.time() - config.AWAIT_TTL_SEC - 5}
    ctx.user_data["await"] = st
    asyncio.run(handle_input(up, ctx, st))
    assert tr("fa", "await_expired") in _texts(up, ctx)
    assert ctx.user_data.get("await") is None
    assert get_settings(cid)["topic"] == ""


def test_on_message_expired_state_falls_through():
    from newsbot.ui_support import on_message
    uid = _mk_user(430060, role="user", lang="fa")
    up, ctx = _mk_update(uid, text="سلام", callback=False)
    st = {"kind": "field", "cid": None, "field": "topic", "back": "home",
          "ts": time.time() - config.AWAIT_TTL_SEC - 5}
    ctx.user_data["await"] = st
    asyncio.run(on_message(up, ctx))
    assert tr("fa", "await_expired") in _texts(up, ctx)
    assert ctx.user_data.get("await") is None
    assert len(ctx.bot.sent) >= 2          # بعد از اعلام انقضا، پیام به‌عنوان پیام تازه (خانه) ادامه یافته


def test_on_message_fresh_state_is_consumed():
    from newsbot.ui_support import on_message
    uid = _mk_user(430061, role="admin", lang="fa")
    cid = add_channel(uid, 97161, "Fresh Ch", "freshch", uid)
    up, ctx = _mk_update(uid, text="موضوع تازه", callback=False)
    st = {"kind": "field", "field": "topic", "cid": cid, "back": f"a:con:{cid}", "ts": time.time()}
    ctx.user_data["await"] = st
    asyncio.run(on_message(up, ctx))
    assert ctx.user_data.get("await") is None
    assert tr("fa", "await_expired") not in _texts(up, ctx)
    assert get_settings(cid)["topic"] == "موضوع تازه"


# ── UISEC-04: سقف نرخ کد تخفیف و تست مدل شخصی ──

def test_disc_rate_limit_and_persistence():
    from newsbot.ui_inputs import handle_input
    code = disc_create("AUTHZ10", 10, (now_utc() + timedelta(days=2)).isoformat(), 0)
    assert code == "AUTHZ10"
    uid = _mk_user(430070, role="user", lang="fa")
    pid = create_plan(name="disc-plan", name_en="dp", days=30, daily_posts=1,
                      max_sources=1, max_channels=1, daily_tests=1)

    up, ctx = _mk_update(uid, text="AUTHZ10")
    st = {"kind": "disc", "pid": pid, "back": f"u:plan:{pid}", "ts": time.time()}
    ctx.user_data["await"] = st
    asyncio.run(handle_input(up, ctx, st))
    assert ctx.user_data.get("disc", {}).get(str(pid)) == "AUTHZ10"
    assert q("SELECT ts FROM rate_limits WHERE key=?", (f"disc:{uid}",), one=True) is not None

    up2, ctx2 = _mk_update(uid, text="AUTHZ10")         # تلاش دوم بلافاصله ⇒ سقف نرخ
    st2 = {"kind": "disc", "pid": pid, "back": f"u:plan:{pid}", "ts": time.time()}
    ctx2.user_data["await"] = st2
    asyncio.run(handle_input(up2, ctx2, st2))
    assert "تست بعدی" in _texts(up2, ctx2)
    assert ctx2.user_data.get("await") is st2           # state حفظ می‌شود تا بعداً امتحان کند
    assert ctx2.user_data.get("disc", {}) == {}


def test_mtest_rate_limit_personal_and_super_exempt(monkeypatch):
    from newsbot import ui_widgets
    calls = {"n": 0}
    async def fake_ai_chat(*a, **kw):
        calls["n"] += 1
        return ("READY", "fake-model", None)
    monkeypatch.setattr(ui_widgets, "ai_chat", fake_ai_chat)

    uid = _mk_user(430080, role="admin", lang="fa")
    mid = add_model("https://api.example.com/v1", "k-test", "gpt-mtest", owner_id=uid)
    up, ctx = _mk_update(uid)
    asyncio.run(ui_widgets.run_model_test(up, ctx, mid, personal=True))
    assert calls["n"] == 1

    up2, ctx2 = _mk_update(uid)
    asyncio.run(ui_widgets.run_model_test(up2, ctx2, mid, personal=True))
    assert calls["n"] == 1                              # هزینهٔ AI دوباره خرج نشد
    assert "تست بعدی" in _texts(up2, ctx2)

    super_mid = add_model("https://api.example.com/v1", "k-super", "gpt-super", owner_id=1)
    up3, ctx3 = _mk_super_update()
    asyncio.run(ui_widgets.run_model_test(up3, ctx3, super_mid, personal=True))
    asyncio.run(ui_widgets.run_model_test(up3, ctx3, super_mid, personal=True))
    assert calls["n"] == 3                              # super از سقف نرخ معاف است


# ── UISEC-03: ماسک/غیبت کلید API ──

def test_api_key_never_leaks_into_super_and_user_views():
    from newsbot.ui_dispatch import dispatch
    full_key = "sk-abcdefghijklmnop"
    mid_g = add_model("https://api.example.com/v1", full_key, "gpt-mask", name="Masked", owner_id=None)
    up, ctx = _mk_super_update()
    asyncio.run(dispatch(up, ctx, f"s:model:{mid_g}"))
    texts = _texts(up, ctx)
    assert full_key not in texts
    assert "sk-abc" not in texts            # حتی پیشوند واقعی کلید هم لو نمی‌رود

    uid = _mk_user(430090, role="admin", lang="fa")
    personal_key = "sk-personalsecret99"
    mid_p = add_model("https://api.example.com/v1", personal_key, "gpt-personal", owner_id=uid)
    up2, ctx2 = _mk_update(uid)
    asyncio.run(dispatch(up2, ctx2, f"a:myai_v:{mid_p}"))
    assert personal_key not in _texts(up2, ctx2)

    cid = add_channel(uid, 97191, "Key Ch", "keych", uid)
    sid = add_source(uid, cid, "https://example.com/key-feed.xml")
    set_source_api(sid, "https://api.example.com/feed", "SECRETKEY-9876543210", "")
    asyncio.run(dispatch(up2, ctx2, f"a:srcv:{sid}"))
    texts2 = _texts(up2, ctx2)
    assert "SECRETKEY-9876543210" not in texts2
    assert "api.example.com" in texts2          # ویو واقعاً رندر شده (نه فقط رد شدن)


# ── UISEC-08: escape داده‌ی کاربر در ویوها ──

def test_views_escape_untrusted_titles_and_names():
    from newsbot.ui_dispatch import dispatch
    uid = _mk_user(430100, role="admin", lang="fa")
    cid = add_channel(uid, 97201, "<b>x</b><script>alert(1)</script>", "evilch", uid)
    up, ctx = _mk_update(uid)
    asyncio.run(dispatch(up, ctx, f"a:ch:{cid}"))
    texts = _texts(up, ctx)
    assert "<script>" not in texts and "&lt;script&gt;" in texts

    _mk_user(430101, role="user", lang="fa", name="<img src=x onerror=alert(1)>", username="evil<img>")
    up2, ctx2 = _mk_super_update()
    asyncio.run(dispatch(up2, ctx2, "s:users"))
    texts2 = _texts(up2, ctx2)
    assert "<img" not in texts2 and "&lt;img" in texts2

    aid = article_insert(uid, cid, "h-authz-xss", "https://example.com/x",
                         "<script>alert('art')</script>", 0, None, status="ready")
    up3, ctx3 = _mk_update(uid)
    asyncio.run(dispatch(up3, ctx3, f"a:art:{aid}"))
    texts3 = _texts(up3, ctx3)
    assert "<script>" not in texts3 and "&lt;script&gt;" in texts3


# ── UISEC-09: سقف اندازهٔ فایل رسید ──

def test_receipt_size_cap_blocks_oversize():
    from newsbot.ui_inputs import handle_input
    uid = _mk_user(430110, role="user", lang="fa")
    pid = create_plan(name="rcpt-plan", name_en="rp", days=30, daily_posts=1,
                      max_sources=1, max_channels=1, daily_tests=1)

    up, ctx = _mk_update(uid)
    up.message.document = SimpleNamespace(file_size=config.MAX_MEDIA_BYTES + 1)
    st = {"kind": "receipt", "pid": pid, "back": "home", "ts": time.time(), "final": "5", "display": "5"}
    ctx.user_data["await"] = st
    asyncio.run(handle_input(up, ctx, st))
    assert tr("fa", "receipt_too_big", n=config.MAX_MEDIA_MB) in _texts(up, ctx)
    assert ctx.user_data.get("await") is st             # state می‌ماند تا فایل کوچک‌تر بیاید
    assert q("SELECT COUNT(*) c FROM pay_requests WHERE user_id=?", (uid,), one=True)["c"] == 0

    uid_ok = _mk_user(430111, role="user", lang="fa")   # دقیقاً روی مرز سقف ⇒ مجاز
    up2, ctx2 = _mk_update(uid_ok)
    up2.message.document = SimpleNamespace(file_size=config.MAX_MEDIA_BYTES)
    st2 = {"kind": "receipt", "pid": pid, "back": "home", "ts": time.time(), "final": "5", "display": "5"}
    ctx2.user_data["await"] = st2
    asyncio.run(handle_input(up2, ctx2, st2))
    assert q("SELECT COUNT(*) c FROM pay_requests WHERE user_id=?", (uid_ok,), one=True)["c"] == 1
