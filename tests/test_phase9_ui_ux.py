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


def render_text(ctx):
    """همهٔ متن‌های رندرشده در این فراخوان (پیام‌های موقت + ویرایش پیام)."""
    parts = list(ctx.callback_query.message.edits)
    parts += [kw.get("text") or (a[1] if len(a) > 1 else "") for a, kw in ctx.bot.sent]
    return "\n".join(str(p or "") for p in parts)


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
    text = render_text(ctx)
    assert "<b>evil" not in text and "&lt;b&gt;evil" in text


def test_user_view_escapes_role_and_lang_fields():
    from newsbot.core_users import ensure_user
    from newsbot.db import q
    from newsbot.ui_dispatch import dispatch
    ensure_user(77, "u77", "U77")
    q("UPDATE users SET role=?, lang=? WHERE id=?", ("<b>role</b>", "<i>fa</i>", 77), commit=True)
    up, ctx = FakeUpdate(), FakeContext()
    asyncio.run(_call(dispatch, up, ctx, "s:user:77"))
    text = render_text(ctx)
    assert "<b>role" not in text and "<i>fa" not in text
