# -*- coding: utf-8 -*-
"""فاز ۹ — تست‌های UI/UX و امنیت نمایش:
PLB-11 (نشانهٔ قطع روی واژه‌های ZWNJ)، GAP-11 (XSS ذخیره‌شده)،
UX-05/UISEC-05 (تأیید دوسطحی اقدامات مخرب)، GAP-04 (دسترس‌پذیری عملی)."""
import asyncio
import re
import pytest


# ── دوبل‌های تست: Update / Context سبک (مستقل از سایر فایل‌های تست) ──

class FakeUser:
    id = 1
    username = "admin"
    full_name = "Admin"
    first_name = "Admin"
    last_name = ""
    is_premium = False
    language_code = "fa"


class FakeMessage:
    message_id = 100
    chat_id = 1
    chat = None
    def __init__(self): self.edits = []
    async def edit_text(self, text, **kw): self.edits.append(text); return self
    async def reply_text(self, text, **kw): self.edits.append(text); return self
    async def delete(self): return True
    async def edit_reply_markup(self, **kw): return self


class FakeQuery:
    def __init__(self):
        self.data = "noop"; self.answers = []; self.message = FakeMessage()
        self.from_user = FakeUser(); self.markups = []
    async def answer(self, *a, **kw): self.answers.append((a, kw)); return True
    async def edit_message_text(self, text, **kw):
        if "reply_markup" in kw: self.markups.append(kw["reply_markup"])
        return await self.message.edit_text(text, **kw)
    async def edit_message_reply_markup(self, **kw): return self.message
    async def delete_message(self): return True


class FakeChat:
    id = 1
    type = "private"
    title = "chat"
    first_name = "Admin"


class FakeUpdate:
    def __init__(self):
        self.effective_user = FakeUser()
        self.effective_message = FakeMessage()
        self.callback_query = FakeQuery()
        self.message = FakeMessage()
        self.effective_chat = FakeChat()


class FakeBot:
    def __init__(self): self.sent = []
    async def send_message(self, *a, **kw): self.sent.append((a, kw)); return FakeMessage()
    async def edit_message_text(self, *a, **kw): return FakeMessage()
    async def get_chat_member(self, *a, **kw): return type("M", (), {"status": "administrator"})()
    async def get_me(self): return type("B", (), {"id": 123, "username": "testbot"})()


class FakeContext:
    def __init__(self):
        self.bot = FakeBot(); self.user_data = {}; self.chat_data = {}
        self._callback_answered = False


async def _call(fn, *args):
    return await fn(*args)


def render_text(up, ctx):
    """همهٔ متن‌های رندرشده در این فراخوان (پیام‌های موقت + ویرایش پیام)."""
    parts = list(up.callback_query.message.edits)
    parts += [kw.get("text") or (a[1] if len(a) > 1 else "") for a, kw in ctx.bot.sent]
    return "\n".join(str(p or "") for p in parts)


@pytest.fixture(autouse=True)
def _acting_admin_ready():
    """کاربر کنشگر (سوپرادمین ۱) باید وجود و زبان داشته باشد وگرنه dispatch صفحهٔ زبان می‌فرستد."""
    from newsbot.core_users import ensure_user
    from newsbot.db import q
    ensure_user(1, "admin", "Admin")
    q("UPDATE users SET lang='fa', role='admin' WHERE id=1", commit=True)
    yield


# ── PLB-11: نشانهٔ قطع (« …») روی واژه‌های ZWNJ‌دار ──

def test_finish_ok_marks_text_cut_after_zwnj():
    from newsbot.text_format import finish_ok
    t = "این متن دقیقاً وسط واژه\u200c"
    r = finish_ok(t)
    assert r.endswith(" …"), f"نشانهٔ قطع نیامد: {r!r}"
    assert r.startswith("این متن دقیقاً وسط واژه")


def test_finish_ok_marks_text_cut_after_zwj():
    from newsbot.text_format import finish_ok
    assert finish_ok("برش نیمه\u200d").endswith(" …")


def test_finish_ok_keeps_complete_text_untouched():
    from newsbot.text_format import finish_ok
    for t in ("جملهٔ کامل است.", "پایان سؤال؟", "", "متن با نیم‌فاصله می‌رود."):
        assert finish_ok(t) == t


def test_finish_ok_ignores_trailing_tags_but_still_marks():
    from newsbot.text_format import finish_ok
    t = "متن نیمه\u200c<b>پررنگ</b>"
    r = finish_ok(t)
    assert r.endswith(" …") and r.startswith("متن نیمه")


# ── GAP-11: هیچ دادهٔ خام در HTML تزریق نمی‌شود (XSS ذخیره‌شده) ──

def test_step_text_escapes_source_name():
    from newsbot.ui_widgets import _step_text
    st = {"category": "src_check", "key": "<img src=x onerror=alert(1)>", "status": "run",
          "variant": 0, "fmt": {}}
    out = _step_text(st, "fa")
    assert "<img" not in out and "&lt;img" in out


def test_step_text_escapes_ok_variant_payload():
    from newsbot.ui_widgets import _step_text
    st = {"category": "src_found", "key": "<b>evil</b>", "status": "ok", "variant": 1, "fmt": {}}
    out = _step_text(st, "fa")
    assert "<b>" not in out and "&lt;b&gt;" in out


def test_sup_header_escapes_username():
    from newsbot.ui_support import _sup_header
    h = _sup_header({"username": "<b>evil</b>", "id": 5, "name": "<i>n</i>"})
    assert "<b>evil" not in h and "&lt;b&gt;evil" in h


def test_channel_view_escapes_username_payload():
    from newsbot.core_channels import add_channel, channel_by_chat
    from newsbot.ui_dispatch import dispatch
    add_channel(1, 9701, "ch-esc", "<b>evil</b>", 1)
    cid = channel_by_chat(9701)["id"]
    up, ctx = FakeUpdate(), FakeContext()
    asyncio.run(_call(dispatch, up, ctx, f"a:ch:{cid}"))
    text = render_text(up, ctx)
    assert "<b>evil" not in text and "&lt;b&gt;evil" in text


def test_user_view_escapes_role_and_lang_fields():
    from newsbot.core_users import ensure_user
    from newsbot.db import q
    from newsbot.ui_dispatch import dispatch
    ensure_user(77, "u77", "U77")
    q("UPDATE users SET role=?, lang=? WHERE id=?", ("<b>role</b>", "<i>fa</i>", 77), commit=True)
    up, ctx = FakeUpdate(), FakeContext()
    asyncio.run(_call(dispatch, up, ctx, "s:user:77"))
    text = render_text(up, ctx)
    assert "<b>role" not in text and "<i>fa" not in text


# ── UX-05/UISEC-05: تأیید دوسطحی اقدامات مخرب ──

def _buttons(up):
    mk = up.callback_query.markups[-1]
    return [(btn.text, btn.callback_data) for row in mk.inline_keyboard for btn in row]


def _ch(chat_id, title="ch"):
    from newsbot.core_channels import add_channel, channel_by_chat
    add_channel(1, chat_id, title, f"u{chat_id}", 1)
    return channel_by_chat(chat_id)["id"]


def test_confirm_article_delete_requires_yes():
    from newsbot.core_articles import article_insert, get_article
    from newsbot.ui_dispatch import dispatch
    cid = _ch(9601)
    aid = article_insert(1, cid, "<p>x</p>", "https://e.com/1", "t1", 0, "2026-01-01T00:00:00")
    up, ctx = FakeUpdate(), FakeContext()
    asyncio.run(_call(dispatch, up, ctx, f"a:artd:{aid}"))
    assert get_article(aid), "پیش از تأیید نباید حذف شود"
    assert any(d == f"a:artd2:{aid}" for _, d in _buttons(up))
    assert any(d == f"a:art:{aid}" for _, d in _buttons(up))
    up2, ctx2 = FakeUpdate(), FakeContext()
    asyncio.run(_call(dispatch, up2, ctx2, f"a:artd2:{aid}"))
    assert not get_article(aid), "پس از تأیید باید حذف شود"


def test_confirm_source_delete_and_api_clear():
    from newsbot.core_sources import add_source, get_source, set_source_api
    from newsbot.ui_dispatch import dispatch
    cid = _ch(9602)
    sid = add_source(1, cid, "https://example.com/feed", "منبع-۹", api_url="https://api.example.com", api_key="k9")
    assert sid
    up, ctx = FakeUpdate(), FakeContext()
    asyncio.run(_call(dispatch, up, ctx, f"a:srcd:{sid}"))
    assert get_source(sid), "پیش از تأیید نباید حذف شود"
    assert any(d == f"a:srcd2:{sid}" for _, d in _buttons(up))
    up2, ctx2 = FakeUpdate(), FakeContext()
    asyncio.run(_call(dispatch, up2, ctx2, f"a:srcapix:{sid}"))
    assert get_source(sid)["api_url"], "پیش از تأیید نباید کلید پاک شود"
    assert any(d == f"a:srcapix2:{sid}" for _, d in _buttons(up2))
    up3, ctx3 = FakeUpdate(), FakeContext()
    asyncio.run(_call(dispatch, up3, ctx3, f"a:srcapix2:{sid}"))
    assert not get_source(sid)["api_url"], "پس از تأیید باید کلید پاک شود"
    up4, ctx4 = FakeUpdate(), FakeContext()
    asyncio.run(_call(dispatch, up4, ctx4, f"a:srcd2:{sid}"))
    assert not get_source(sid)


def test_confirm_model_delete_requires_yes():
    from newsbot.ai_models import add_model, get_model
    from newsbot.ui_dispatch import dispatch
    mid = add_model("https://api.openai.com/v1", "sk-test9", "gpt-4o-mini", name="مدل ۹")
    assert mid
    up, ctx = FakeUpdate(), FakeContext()
    asyncio.run(_call(dispatch, up, ctx, f"s:model_d:{mid}"))
    assert get_model(mid), "پیش از تأیید نباید حذف شود"
    assert any(d == f"s:model_d2:{mid}" for _, d in _buttons(up))
    up2, ctx2 = FakeUpdate(), FakeContext()
    asyncio.run(_call(dispatch, up2, ctx2, f"s:model_d2:{mid}"))
    assert not get_model(mid)


def test_confirm_ban_toggle_requires_yes():
    from newsbot.core_users import ensure_user, get_user
    from newsbot.ui_dispatch import dispatch
    ensure_user(9, None, "U9")
    up, ctx = FakeUpdate(), FakeContext()
    asyncio.run(_call(dispatch, up, ctx, "s:uban:9"))
    assert not get_user(9)["banned"], "پیش از تأیید نباید مسدود شود"
    assert any(d == "s:uban2:9" for _, d in _buttons(up))
    up2, ctx2 = FakeUpdate(), FakeContext()
    asyncio.run(_call(dispatch, up2, ctx2, "s:uban2:9"))
    assert get_user(9)["banned"]
    up3, ctx3 = FakeUpdate(), FakeContext()
    asyncio.run(_call(dispatch, up3, ctx3, "s:uban:9"))
    assert any("مسدودیت" in t for t, _ in _buttons(up3)) or any("برداشته" in render_text(up3, ctx3) for _ in [0])
    assert any(d == "s:uban2:9" for _, d in _buttons(up3))
    up4, ctx4 = FakeUpdate(), FakeContext()
    asyncio.run(_call(dispatch, up4, ctx4, "s:uban2:9"))
    assert not get_user(9)["banned"]


def test_confirm_plan_delete_requires_yes():
    from newsbot.core_plans import create_plan, get_plan
    from newsbot.db import q
    from newsbot.ui_dispatch import dispatch
    create_plan(name="پلن ۹", days=30, price=99000)
    pid = q("SELECT id FROM plans ORDER BY id DESC LIMIT 1", one=True)["id"]
    up, ctx = FakeUpdate(), FakeContext()
    asyncio.run(_call(dispatch, up, ctx, f"s:plan_d:{pid}"))
    assert get_plan(pid), "پیش از تأیید نباید حذف شود"
    assert any(d == f"s:plan_d2:{pid}" for _, d in _buttons(up))
    up2, ctx2 = FakeUpdate(), FakeContext()
    asyncio.run(_call(dispatch, up2, ctx2, f"s:plan_d2:{pid}"))
    assert not get_plan(pid)


def test_pay_no_renders_confirm_and_no_goes_back():
    from newsbot.ui_dispatch import dispatch
    up, ctx = FakeUpdate(), FakeContext()
    asyncio.run(_call(dispatch, up, ctx, "s:pay_no:999:l"))
    assert "رد شود" in render_text(up, ctx)
    assert any(d == "s:pay_no2:999:l" for _, d in _buttons(up))
    assert any(d == "s:pays" for _, d in _buttons(up))


def test_txtr_renders_confirm_without_reset():
    from newsbot.db import gget
    from newsbot.ui_dispatch import dispatch
    up, ctx = FakeUpdate(), FakeContext()
    asyncio.run(_call(dispatch, up, ctx, "s:txtr:welcome:fa"))
    assert "پیش‌فرض" in render_text(up, ctx)
    assert any(d == "s:txtr2:welcome:fa" for _, d in _buttons(up))
    assert gget("text_welcome_fa") is None, "پیش از تأیید نباید بازنشانی/ویرایش شود"


def test_urevoke_renders_confirm_only():
    from newsbot.core_users import ensure_user
    from newsbot.ui_dispatch import dispatch
    ensure_user(10, None, "U10")
    up, ctx = FakeUpdate(), FakeContext()
    asyncio.run(_call(dispatch, up, ctx, "s:urevoke:10"))
    assert "لغو شود" in render_text(up, ctx)
    assert any(d == "s:urevoke2:10" for _, d in _buttons(up))
    assert any(d == "s:user:10" for _, d in _buttons(up))
