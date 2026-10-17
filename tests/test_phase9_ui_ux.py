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
    def __init__(self, uid=1): self.id = uid


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
    def __init__(self, uid=1):
        self.effective_user = FakeUser(uid)
        self.effective_message = FakeMessage()
        self.callback_query = FakeQuery()
        self.message = FakeMessage()
        self.effective_chat = FakeChat()


class FakeBot:
    def __init__(self): self.sent = []; self.edits = []
    async def send_message(self, *a, **kw): self.sent.append((a, kw)); return FakeMessage()
    async def copy_message(self, *a, **kw): return FakeMessage()
    async def edit_message_text(self, text=None, **kw): self.edits.append(text); return FakeMessage()
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
    from newsbot.core_sources import add_source, get_source
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


# ── UISEC-07: توصیف سادهٔ عملیات در حال اجرا ──

def test_running_op_text_describes_operation():
    import time as _t
    from newsbot.ui_widgets import running_op_text
    op = type("Op", (), {"operation_type": "callback:a:test:9601", "t0": _t.time() - 7})()
    fa = running_op_text("fa", op)
    assert "تست" in fa and "(7s)" in fa
    en = running_op_text("en", op)
    assert "test" in en
    op2 = type("Op", (), {"operation_type": "command:start", "t0": 0})()
    assert "در حال اجرا" in running_op_text("fa", op2)
    op3 = type("Op", (), {"operation_type": "callback:a:pub:9601", "t0": 0})()
    assert "انتشار" in running_op_text("fa", op3)


def test_wait_for_running_operation_names_current_operation():
    from newsbot.opctx import Operation, user_control
    from newsbot.ui_dispatch import _wait_for_running_operation
    from newsbot.ui_widgets import _operations
    up, ctx = FakeUpdate(), FakeContext()
    op = Operation(1, 9601, "callback:a:test:9601", 1)
    _operations[1] = op
    try:
        lock = user_control(1)
        async def scenario():
            async with lock:
                return await _wait_for_running_operation(up, ctx, 1)
        assert asyncio.run(scenario()) is True
        alert = up.callback_query.answers[-1][0][0]
        assert "تست" in alert and "/cancel" in alert
    finally:
        _operations.pop(1, None)


# ── UX-02: حالت‌های خالی قابل‌فهم ──

def test_empty_states_sources_queue_rejected():
    from newsbot.ui_dispatch import dispatch
    cid = _ch(9603)
    up, ctx = FakeUpdate(), FakeContext()
    asyncio.run(_call(dispatch, up, ctx, f"a:src:{cid}"))
    assert "هنوز منبعی" in render_text(up, ctx)
    up2, ctx2 = FakeUpdate(), FakeContext()
    asyncio.run(_call(dispatch, up2, ctx2, f"a:que:{cid}"))
    assert "صف خالی" in render_text(up2, ctx2)
    up3, ctx3 = FakeUpdate(), FakeContext()
    asyncio.run(_call(dispatch, up3, ctx3, f"a:rej:{cid}"))
    assert "موردی رد نشده" in render_text(up3, ctx3)


def test_empty_state_models():
    from newsbot.ai_models import delete_model, list_models
    from newsbot.ui_dispatch import dispatch
    for m in list_models(): delete_model(m["id"])
    up, ctx = FakeUpdate(), FakeContext()
    asyncio.run(_call(dispatch, up, ctx, "s:models"))
    assert "هنوز مدلی" in render_text(up, ctx)
    _buttons(up)


# ── UX-06: مرتب‌سازی صف بر اساس امتیاز ──

def test_queue_sort_by_score_toggle():
    from newsbot.core_articles import article_insert, article_update
    from newsbot.ui_dispatch import dispatch
    cid = _ch(9604)
    a1 = article_insert(1, cid, "<p>1</p>", "https://e.com/s1", "اول", 0, "2026-01-01T00:00:00", status="ready")
    a2 = article_insert(1, cid, "<p>2</p>", "https://e.com/s2", "دوم", 0, "2026-01-02T00:00:00", status="ready")
    article_update(a1, score=5); article_update(a2, score=9)
    up, ctx = FakeUpdate(), FakeContext()
    asyncio.run(_call(dispatch, up, ctx, f"a:que:{cid}"))
    assert _buttons(up)[0][0].startswith("⭐5"), "پیش‌فرض باید قدیمی‌ترین (امتیاز ۵) اول باشد"
    assert any("امتیاز" in t for t, _ in _buttons(up)), "دکمهٔ مرتب‌سازی باید برچسب متنی داشته باشد"
    up2, ctx2 = FakeUpdate(), FakeContext()
    asyncio.run(_call(dispatch, up2, ctx2, f"a:que:{cid}:s"))
    assert _buttons(up2)[0][0].startswith("⭐9"), "با سورت امتیازی باید مطلب ۹ اول باشد"
    assert any("زمان" in t for t, _ in _buttons(up2))


# ── UX-07: دکمه‌های بازنشانی متن، برچسب‌دار ──

def test_texts_view_reset_buttons_have_labels():
    from newsbot.ui_dispatch import dispatch
    up, ctx = FakeUpdate(), FakeContext()
    asyncio.run(_call(dispatch, up, ctx, "s:texts"))
    texts = [t for t, _ in _buttons(up)]
    assert any("پیش‌فرض" in t for t in texts)
    assert all(re.search(r"[A-Za-z0-9\u0600-\u06FF]", t) for t in texts), f"دکمهٔ بدون برچسب متنی: {texts}"


# ── UX-01/08: میان‌بر کانال یکتا در خانه ──

def test_admin_home_shortcut_with_single_channel():
    from newsbot.core_users import ensure_user
    from newsbot.db import q
    from newsbot.core_channels import add_channel, channel_by_chat
    from newsbot.ui_dispatch import dispatch
    ensure_user(2, "u2", "U2")
    q("UPDATE users SET role='admin', lang='fa' WHERE id=2", commit=True)
    add_channel(2, 9606, "ch-u2", "u2chan", 1)
    cid = channel_by_chat(9606)["id"]
    up, ctx = FakeUpdate(uid=2), FakeContext()
    asyncio.run(_call(dispatch, up, ctx, "a:home"))
    row0 = [d for _, d in _buttons(up)][:2]
    assert row0 == [f"a:que:{cid}", f"a:test:{cid}"], "با یک کانال باید میان‌بر صف/تست بالای پنل باشد"


# ── UX-04: پیشرفت عملیات‌های طولانی ──

def test_models_test_reports_progress(monkeypatch):
    from newsbot.ai_models import add_model
    from newsbot.ui_dispatch import dispatch
    async def fake_test(mid):
        return True, "ok", 0.05
    monkeypatch.setattr("newsbot.ui_dispatch.test_model", fake_test)
    m1 = add_model("https://api.example.com/v1", "sk-a", "m-a", name="مدل الف")
    m2 = add_model("https://api.example.com/v1", "sk-b", "m-b", name="مدل ب")
    up, ctx = FakeUpdate(), FakeContext()
    from newsbot.opctx import Operation, _current_operation
    op = Operation(1, None, "callback:s:models_test", 1)
    tok = _current_operation.set(op)
    try: asyncio.run(_call(dispatch, up, ctx, "s:models_test"))
    finally: _current_operation.reset(tok)
    joined = "\n".join(str(x or "") for x in ctx.bot.edits) + "\n" + "\n".join(str(kw.get("text") or "") for _, kw in ctx.bot.sent)
    assert "1/" in joined and "2/" in joined, "پیشرفت هر مدل باید گزارش شود"
    from newsbot.ai_models import delete_model
    for mid in (m1, m2): delete_model(mid)


def test_broadcast_reports_progress():
    from newsbot.core_users import ensure_user
    from newsbot.opctx import Operation, _current_operation
    from newsbot.ui_dispatch import dispatch
    ensure_user(11, None, "U11")
    up, ctx = FakeUpdate(), FakeContext()
    ctx.user_data["bc_src"] = (1, 10)
    op = Operation(1, None, "callback:s:bc_go", 1)
    tok = _current_operation.set(op)
    try: asyncio.run(_call(dispatch, up, ctx, "s:bc_go"))
    finally: _current_operation.reset(tok)
    joined = "\n".join(str(x or "") for x in ctx.bot.edits) + "\n" + "\n".join(str(kw.get("text") or "") for _, kw in ctx.bot.sent)
    assert "ارسال همگانی" in joined, "پیشرفت ارسال همگانی باید گزارش شود"


# ── GAP-04: دسترس‌پذیری عملی (برچسب متنی دکمه‌ها، توازن HTML، راهنما) ──

_LABEL_RE = re.compile(r"[A-Za-z0-9\u0600-\u06FF]")


def test_all_main_buttons_have_textual_labels():
    from newsbot.ui_dispatch import dispatch
    cid = _ch(9607)
    targets = [
        "a:home", "a:plan", f"a:ch:{cid}", f"a:src:{cid}", f"a:que:{cid}", f"a:rej:{cid}",
        f"a:sch:{cid}", f"a:quiet:{cid}", f"a:con:{cid}", f"a:cat:{cid}", f"a:cri:{cid}",
        f"a:myai:{cid}", f"a:rep:{cid}", "s:home", "s:health", "s:plans", "s:discs", "s:models",
        "s:pays", "s:users", "s:texts", "s:mand", "u:plans", "s:user:9",
    ]
    for data in targets:
        up, ctx = FakeUpdate(), FakeContext()
        asyncio.run(_call(dispatch, up, ctx, data))
        for t, _d in _buttons(up):
            assert _LABEL_RE.search(t), f"دکمهٔ بدون برچسب متنی در {data}: «{t}»"


def _html_balanced(t):
    stack = []
    for m in re.finditer(r"<(/?)([a-zA-Z]+)([^>]*)>", t):
        close, tag = m.group(1), m.group(2).lower()
        if tag in ("br",): continue
        if close:
            if not stack or stack.pop() != tag: return False
        else: stack.append(tag)
    return not stack


def test_rendered_html_tags_are_balanced():
    from newsbot.ui_i18n import DEFAULT_TEXTS
    from newsbot.ui_dispatch import dispatch
    texts = []
    texts.append(DEFAULT_TEXTS["help"]["fa"]); texts.append(DEFAULT_TEXTS["help"]["en"])
    texts.append(DEFAULT_TEXTS["about"]["fa"]); texts.append(DEFAULT_TEXTS["about"]["en"])
    texts.append(DEFAULT_TEXTS["pay"]["fa"].format(plan="پلن", price="۱۰۰"))
    cid = _ch(9608)
    for data in ("a:home", f"a:ch:{cid}", f"a:que:{cid}", f"a:rej:{cid}", f"a:cat:{cid}", f"a:cri:{cid}", "s:home", "s:health", "s:models", "s:users", "s:texts"):
        up, ctx = FakeUpdate(), FakeContext()
        asyncio.run(_call(dispatch, up, ctx, data))
        texts.append(render_text(up, ctx))
    for t in texts:
        clean = re.sub(r"<code>.*?</code>", "", t, flags=re.S)
        assert _html_balanced(clean), f"HTML نامتوازن:\n{t[:400]}"


def test_help_lists_core_commands():
    from newsbot.ui_i18n import DEFAULT_TEXTS
    cmds = ["/create", "/ref", "/man", "/lang", "/about", "/cancel"]
    for lang in ("fa", "en"):
        h = DEFAULT_TEXTS["help"][lang]
        for c in cmds:
            assert c in h, f"دستور {c} در راهنمای {lang} نیست"
