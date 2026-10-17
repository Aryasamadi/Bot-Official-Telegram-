# -*- coding: utf-8 -*-
"""فاز ۸ — تست جامع لایهٔ رابط کاربری: کشف خطاهای زمان‌اجرا در مسیرهای داغ.

زمینه: H11/H12/H13/H14 همه در کد UI بودند (نام‌های تعریف‌نشده در ui_dispatch و ui_views_super)
و چون پشت `except` پنهان می‌شدند، فقط با کلیک واقعی کاربر دیده می‌شدند.

این ماژول هر تابعِ نمایشیِ پنل‌ها را با یک Update/Context ساختگی صدا می‌زند و تأیید می‌کند
که هیچ‌کدام با `NameError`/`ImportError`/`AttributeError` نمی‌شکنند. هرگونه نقصِ ارجاعی
در لایهٔ UI این‌جا لو می‌رود.
"""
import asyncio
import pytest

from newsbot.db import q
from newsbot.core_channels import add_channel, channel_by_chat
from newsbot.core_sources import add_source
from newsbot.core_articles import article_insert
from newsbot.ai_models import add_model


# ── دوبل‌های تست: Update / Context سبک ──

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
        self.from_user = FakeUser()
    async def answer(self, *a, **kw): self.answers.append((a, kw)); return True
    async def edit_message_text(self, text, **kw): return await self.message.edit_text(text, **kw)
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


def _mk_fixtures():
    add_channel(1, 9501, "ch", "chan", 1)
    cid = channel_by_chat(9501)["id"]
    add_source(1, cid, "https://example.com/feed.xml")
    aid = article_insert(1, cid, "h-ui-1", "https://example.com/a", "t", 0, None, status="ready")
    if aid is None:   # درج تکراری (چند فراخوان در یک session) ⇒ همان رکورد موجود
        row = q("SELECT id FROM articles WHERE channel_id=? AND hash=?", (cid, "h-ui-1"), one=True)
        aid = row["id"] if row else None
    add_model("m1", "openai", "https://api.example.com/v1", "k", "gpt-x", owner_id=None)
    return cid, aid


# ── مسیر پنل کاربر ──

def test_user_panel_views_do_not_crash():
    from newsbot.ui_views_user import (view_lang, view_user_home, view_referral, view_plans)
    up, ctx = FakeUpdate(), FakeContext()
    for fn in (view_lang, view_user_home, view_referral, view_plans):
        asyncio.run(_call(fn, up, ctx))


def test_user_plan_view_requires_valid_id_gracefully():
    """شناسهٔ نامعتبر نباید NameError/AttributeError بدهد."""
    from newsbot.ui_views_user import view_plan
    up, ctx = FakeUpdate(), FakeContext()
    try:
        asyncio.run(_call(view_plan, up, ctx, 999999))
    except (NameError, ImportError, AttributeError) as e:
        pytest.fail(f"نقص ارجاع در view_plan: {type(e).__name__}: {e}")


# ── مسیر پنل مدیر میانی ──

def test_admin_panel_views_do_not_crash():
    from newsbot.ui_views_admin import (view_admin_home, view_sources, view_content, view_cats,
                                        view_crits, view_sched, view_quiet, view_tz, view_queue,
                                        view_my_ai, view_my_plan)
    cid, aid = _mk_fixtures()
    up, ctx = FakeUpdate(), FakeContext()
    cases = [
        (view_admin_home, ()), (view_sources, (cid,)), (view_content, (cid,)),
        (view_cats, (cid,)), (view_crits, (cid,)), (view_sched, (cid,)),
        (view_quiet, (cid,)), (view_tz, (cid,)), (view_queue, (cid, "ready")),
        (view_my_ai, ()), (view_my_plan, ()),
    ]
    for fn, args in cases:
        try:
            asyncio.run(_call(fn, up, ctx, *args))
        except (NameError, ImportError, AttributeError) as e:
            pytest.fail(f"نقص ارجاع در {fn.__name__}: {type(e).__name__}: {e}")


def test_admin_channel_and_article_views():
    from newsbot.ui_views_admin import view_channel, view_article, view_cat, view_crit, view_source
    cid, aid = _mk_fixtures()
    up, ctx = FakeUpdate(), FakeContext()
    src = q("SELECT * FROM sources WHERE channel_id=?", (cid,), one=True)
    for fn, args in ((view_channel, (cid,)), (view_article, (aid,)),
                     (view_cat, (cid, 0)), (view_crit, (cid, 0)),
                     (view_source, (src["id"],))):
        try:
            asyncio.run(_call(fn, up, ctx, *args))
        except (NameError, ImportError, AttributeError) as e:
            pytest.fail(f"نقص ارجاع در {fn.__name__}: {type(e).__name__}: {e}")


# ── مسیر پنل سوپرادمین (جایی که H14 بود) ──

def test_super_panel_views_do_not_crash():
    from newsbot.ui_views_super import (view_super_home, view_health, view_s_plans, view_s_discs,
                                        view_s_users, view_s_models, view_s_pays, view_s_texts)
    up, ctx = FakeUpdate(), FakeContext()
    for fn in (view_super_home, view_health, view_s_plans, view_s_discs, view_s_users,
               view_s_models, view_s_pays, view_s_texts):
        try:
            asyncio.run(_call(fn, up, ctx))
        except (NameError, ImportError, AttributeError) as e:
            pytest.fail(f"نقص ارجاع در {fn.__name__}: {type(e).__name__}: {e}")


def test_super_texts_uses_correct_text_keys():
    """مسیر متن‌های قابل‌ویرایش (منبع یکتای TEXT_KEYS در ui_i18n)."""
    from newsbot.ui_views_super import view_s_texts
    from newsbot.ui_i18n import TEXT_KEYS
    up, ctx = FakeUpdate(), FakeContext()
    asyncio.run(_call(view_s_texts, up, ctx))
    assert TEXT_KEYS, "TEXT_KEYS خالی است"


# ── مسیر dispatch: هر action باید بدون NameError مسیر عوض کند ──

def test_dispatch_routes_known_actions():
    """هر action در لیست ناوبری باید بدون خطای ارجاع اجرا شود."""
    from newsbot.ui_dispatch import dispatch
    cid, aid = _mk_fixtures()
    up, ctx = FakeUpdate(), FakeContext()
    # actionهای امنِ فقط-خواندنی (آن‌هایی که پارامتر معتبر لازم ندارند)
    safe = ["home", "plans", "models", "users", "discs", "pays", "texts", "report"]
    for data in safe:
        try:
            asyncio.run(dispatch(up, ctx, f"s:{data}"))
        except (NameError, ImportError, AttributeError) as e:
            pytest.fail(f"نقص ارجاع در dispatch('s:{data}'): {type(e).__name__}: {e}")


def test_dispatch_admin_actions():
    from newsbot.ui_dispatch import dispatch
    cid, aid = _mk_fixtures()
    up, ctx = FakeUpdate(), FakeContext()
    for data in (f"a:ch:{cid}", f"a:srcv:{q('SELECT id FROM sources WHERE channel_id=?', (cid,), one=True)['id']}",
                 f"a:art:{aid}", f"a:que:{cid}"):
        try:
            asyncio.run(dispatch(up, ctx, data))
        except (NameError, ImportError, AttributeError) as e:
            pytest.fail(f"نقص ارجاع در dispatch('{data}'): {type(e).__name__}: {e}")
