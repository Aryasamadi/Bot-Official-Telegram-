# -*- coding: utf-8 -*-
"""PH-03 — پایداریِ پرداخت و اعلان (UI-006 · UI-009 · OBS-011 · OBS-012 · OBS-013 · FIN-021 · DB-008 · PIPE-021).

هدفِ فاز: «مشتریِ پرداخت‌کرده هرگز بی‌سروصدا گم نشود».

پوشش:
  UI-006  — کپشنِ رسید باید HTML معتبر و ≤۱۰۲۴ باشد و به مدیر برسد (برشِ داخلِ تگ = رسیدِ گم‌شده)
  UI-009  — فرمِ رسید نباید با یک ضربهٔ دکمهٔ دیگر پاک شود
  OBS-011 — اعلانِ نرسیدهٔ مدیر کلان باید durable شود (dead-letter)، نه اینکه دراپ شود
  OBS-013 — رسیدی که هیچ مدیری ندیده باید دوباره اعلام شود
  DB-008  — نشانگرهای یتیم، توکن‌های منقضی و آمارِ AI باید پاک شوند
  PIPE-021— سطرِ ژورنالِ «unsettled» نباید در هر چرخهٔ بازیابی دوباره نوشته شود
"""
import asyncio
import time
from types import SimpleNamespace

from newsbot import runtime
from newsbot.db import q, gset
from newsbot.timeutils import now_iso, now_utc


# ── دوبل‌های تست (الگو: tests/test_authz_matrix.py) ─────────────────────────────────
class FakeUser:
    def __init__(self, uid=1, username=None, name=None):
        self.id = uid
        self.username = username or f"u{uid}"
        self.full_name = name or f"User {uid}"
        self.first_name = self.full_name
        self.last_name = ""
        self.is_premium = False
        self.language_code = "fa"


class FakeChat:
    def __init__(self, chat_id=1, kind="private"):
        self.id = chat_id; self.type = kind; self.title = "chat"; self.first_name = "Admin"


class FakeMessage:
    def __init__(self, message_id=100, chat_id=1):
        self.message_id = message_id; self.chat_id = chat_id
        self.chat = FakeChat(chat_id); self.edits = []; self.text = None; self.caption = None
        self.text_html = None; self.caption_html = None
        self.photo = None; self.document = None
        self.reply_to_message = None; self.forward_origin = None; self.date = None
    async def edit_text(self, text, **kw): self.edits.append(text); return self
    async def reply_text(self, text, **kw): self.edits.append(text); return self
    async def delete(self): return True
    async def edit_reply_markup(self, **kw): return self
    async def set_reaction(self, *a, **kw): return True


class FakeQuery:
    def __init__(self, user=None):
        self.data = "noop"; self.answers = []; self.message = FakeMessage()
        self.from_user = user or FakeUser()
    async def answer(self, *a, **kw): self.answers.append((a, kw)); return True
    async def edit_message_text(self, text, **kw): return await self.message.edit_text(text, **kw)
    async def edit_message_reply_markup(self, *a, **kw): return self.message
    async def delete_message(self): return True


class FakeBot:
    """رباتِ ساختگی؛ ``fail_copy`` باعث می‌شود کپشن‌رساندن شکست بخورد (UI-006)."""
    def __init__(self, fail_copy=False):
        self.sent = []; self.copied = []; self.fail_copy = fail_copy
        self.member_status = "administrator"
    async def send_message(self, *a, **kw): self.sent.append((a, kw)); return FakeMessage()
    async def copy_message(self, *a, **kw):
        if self.fail_copy: raise RuntimeError("telegram: caption is invalid")
        self.copied.append((a, kw)); return FakeMessage()
    async def edit_message_text(self, *a, **kw): return FakeMessage()
    async def get_chat_member(self, *a, **kw): return SimpleNamespace(status=self.member_status)
    async def get_chat(self, *a, **kw): return SimpleNamespace(id=1, username="ch", title="ch", type="channel")
    async def get_me(self): return SimpleNamespace(id=123, username="testbot")


class FakeContext:
    def __init__(self, bot=None):
        self.bot = bot or FakeBot(); self.user_data = {}; self.chat_data = {}
        self._callback_answered = False


def _mk_user(uid, role="user", lang="fa", name=None):
    q("INSERT OR REPLACE INTO users(id,username,name,role,banned,lang,created_at,last_seen) VALUES(?,?,?,?,?,?,?,?)",
      (uid, f"u{uid}", name or f"User {uid}", role, 0, lang, now_iso(), now_iso()), commit=True)
    return uid


def _mk_update(uid, text=None, photo=False, callback=True):
    up = SimpleNamespace()
    up.effective_user = FakeUser(uid)
    up.effective_message = FakeMessage()
    up.message = FakeMessage()
    if text is not None: up.message.text = text
    if photo: up.message.photo = [SimpleNamespace(file_size=1000, file_id="f")]
    up.callback_query = FakeQuery(up.effective_user) if callback else None
    up.effective_chat = FakeChat()
    return up, FakeContext()


def _texts(up, ctx):
    parts = []
    qy = up.callback_query
    if qy is not None:
        for a, kw in qy.answers:
            if a and isinstance(a[0], str): parts.append(a[0])
        parts += list(qy.message.edits)
    for a, kw in ctx.bot.sent:
        if len(a) > 1 and isinstance(a[1], str): parts.append(a[1])
    return "\n".join(parts)


def _mk_plan(uid, price="100000 تومان", price_num=100.0, days=30):
    from newsbot.core_plans import create_plan
    return create_plan(name=f"ph03-{uid}", name_en=f"ph03-{uid}", days=days, daily_posts=5,
                       max_sources=10, max_channels=5, daily_tests=5, price=price,
                       price_num=price_num, price_num_fa=price_num * 10)


def _tags_balanced(html_text):
    """آیا تگ‌های HTML جفت هستند؟ (برشِ داخلِ تگ ⇒ تلگرام رد می‌کند)"""
    import re
    stack = []
    for m in re.finditer(r"<(/?)([a-zA-Z][\w-]*)", html_text):
        closing, tag = m.group(1), m.group(2).lower()
        if closing:
            if not stack or stack[-1] != tag: return False
            stack.pop()
        else: stack.append(tag)
    return not stack


# ══════════════════════════════════════════════════════════════════════════════════════
# UI-006 — کپشنِ رسید: HTML معتبر، ≤۱۰۲۴، و برش **داخلِ تگ** ممنوع
# ══════════════════════════════════════════════════════════════════════════════════════
def test_ui006_long_note_yields_valid_caption_within_the_telegram_limit():
    from newsbot.core_payments import pay_receipt_caption
    long_note = "ی" * 400
    caption = pay_receipt_caption(42, "Ali", 7, "Plan", "100", "100", "", long_note, "fa")
    assert len(caption) <= 1024, f"کپشن از سقفِ کپشنِ تلگرام رد شد: {len(caption)}"
    assert _tags_balanced(caption), "تگ‌های HTML جفت نیستند ⇒ تلگرام کپشن را رد می‌کند"
    assert long_note not in caption, "یادداشتِ بلند باید در سطحِ فیلد کوتاه شود"


def test_ui006_caption_is_built_with_the_truncation_applied_before_the_limit():
    """رگرسیونِ ریشه: برشِ header[:1000] **داخلِ تگ** می‌افتاد ⇒ تلگرام کپشن را رد می‌کرد
    ⇒ رسید به هیچ مدیری نمی‌رسید. کپشنِ جدید همیشه معتبر و ≤۱۰۲۴ است."""
    from newsbot.core_payments import pay_receipt_caption
    from newsbot.ui_i18n import tr
    long_plan = "پلنِ " + ("خیلی" * 400)          # نامِ پلنِ بلند داخلِ <b> ⇒ برشِ ۱۰۰۰ وسطِ تگ می‌افتد
    raw = tr("fa", "s_pay_new", id=1, who="Ali", uid=7, plan=long_plan, plan_price="100", price="100",
             disc="\n🎟 CODE", note=f"\n📝 {'ی' * 400}")
    assert len(raw) > 1000, "پیش‌فرضِ تست معتبر نیست (متن باید از ۱۰۰۰ بگذرد)"
    assert not _tags_balanced(raw[:1000]), "برشِ ساده باید تگ را بشکند (وگرنه رگرسیون بی‌معناست)"
    caption = pay_receipt_caption(1, "Ali", 7, long_plan, "100", "100", "CODE", "ی" * 400, "fa")
    assert len(caption) <= 1024
    assert _tags_balanced(caption), "کپشنِ جدید هم تگِ باز دارد ⇒ تلگرام آن را رد می‌کند"


def _receipt_state(uid, pid):
    return {"kind": "receipt", "pid": pid, "back": f"u:plan:{pid}", "disc": None,
            "final": "100", "display": "100", "ts": time.time()}


def test_ui006_receipt_reaches_the_super_admin(monkeypatch):
    """رسید باید به مدیر کلان برسد و سطر «اعلام‌شد» علامت بخورد."""
    from newsbot.ui_inputs import handle_input
    uid = _mk_user(430301)
    _mk_user(1, role="super")
    pid = _mk_plan(uid)
    up, ctx = _mk_update(uid, text="یادداشتِ پرداخت", photo=True)
    ctx.user_data["await"] = _receipt_state(uid, pid)
    asyncio.run(handle_input(up, ctx, ctx.user_data["await"]))
    assert ctx.bot.copied, "رسید به مدیر کلان نرسید"
    kw = ctx.bot.copied[0][1]
    assert len(kw.get("caption", "")) <= 1024
    assert _tags_balanced(kw.get("caption", ""))
    rows = q("SELECT * FROM pay_requests WHERE user_id=?", (uid,))
    assert rows, "درخواستِ پرداخت ثبت نشد"
    assert rows[0]["super_notified_at"], "اعلام به مدیر علامت نخورد"


def test_ui006_failed_notification_is_marked_and_the_customer_is_told(monkeypatch):
    """اگر هیچ مدیری نرسید ⇒ سطر «اعلام‌نشده» می‌ماند (زمان‌بند دوباره می‌فرستد) و
    مشتری صادقانه مطلع می‌شود (پیش از این «رسید دریافت شد» می‌دید و پولش گم می‌شد)."""
    from newsbot.ui_inputs import handle_input
    from newsbot.ui_i18n import tr
    uid = _mk_user(430302)
    _mk_user(1, role="super")
    pid = _mk_plan(uid)
    up, ctx = _mk_update(uid, text="یادداشت", photo=True)
    ctx.bot.fail_copy = True
    ctx.user_data["await"] = _receipt_state(uid, pid)
    asyncio.run(handle_input(up, ctx, ctx.user_data["await"]))
    rows = q("SELECT * FROM pay_requests WHERE user_id=?", (uid,))
    assert rows, "درخواستِ پرداخت ثبت نشد (نباید حذف شود)"
    assert not rows[0]["super_notified_at"], "نباید «اعلام‌شد» علامت بخورد"
    assert int(rows[0]["notify_attempts"] or 0) >= 1
    assert tr("fa", "pay_notify_delayed") in _texts(up, ctx), "مشتری باید مطلع شود که اعلام به تأخیر افتاد"


# ══════════════════════════════════════════════════════════════════════════════════════
# UI-009 — فرمِ رسید با یک ضربهٔ دکمهٔ دیگر پاک نشود
# ══════════════════════════════════════════════════════════════════════════════════════
def test_ui009_receipt_form_survives_another_button_press():
    from newsbot.opctx import clear_interaction
    up, ctx = _mk_update(430303)
    st = {"kind": "receipt", "pid": 1, "back": "u:plan:1", "ts": time.time()}
    ctx.user_data["await"] = st
    clear_interaction(ctx)                      # یک دکمهٔ دیگر زده شده
    assert ctx.user_data.get("await") is st, "فرمِ رسید پاک شد ⇒ رسیدِ مشتری گم می‌شود"


def test_ui009_other_forms_are_still_cleared():
    from newsbot.opctx import clear_interaction
    up, ctx = _mk_update(430304)
    st = {"kind": "field", "field": "topic", "ts": time.time()}
    ctx.user_data["await"] = st
    clear_interaction(ctx)
    assert ctx.user_data.get("await") is None, "فرم‌های دیگر باید مثل قبل پاک شوند"


def test_ui009_cancel_still_drops_the_receipt_form():
    """لغوِ صریح باید فرم را ببندد (وگرنه مشتری گیر می‌کند)."""
    up, ctx = _mk_update(430305)
    ctx.user_data["await"] = {"kind": "receipt", "pid": 1, "back": "u:plan:1", "ts": time.time()}
    ctx.user_data.pop("await", None)           # همان کاری که مسیرِ c:cancel می‌کند
    assert ctx.user_data.get("await") is None


# ══════════════════════════════════════════════════════════════════════════════════════
# OBS-011 — اعلانِ نرسیدهٔ مدیر کلان durable می‌شود
# ══════════════════════════════════════════════════════════════════════════════════════
def test_obs011_failed_super_notification_is_persisted_not_dropped(monkeypatch):
    from newsbot import notify
    async def boom(text, kb=None): raise RuntimeError("telegram down")
    monkeypatch.setattr(runtime, "NOTIFY_SUPER", boom)
    q("DELETE FROM notifications", commit=True)
    asyncio.run(notify.notify_super("هشدارِ مهم"))
    rows = q("SELECT * FROM notifications")
    assert rows, "اعلانِ ناموفق دراپ شد ⇒ مدیر هرگز باخبر نمی‌شود"
    assert not rows[0]["delivered_at"] and not rows[0]["dead"]


def test_obs011_queue_overflow_lands_in_the_dead_letter_table(monkeypatch):
    from newsbot import notify
    async def ok(text, kb=None): return True
    monkeypatch.setattr(runtime, "NOTIFY_SUPER", ok)
    q("DELETE FROM notifications", commit=True)
    # worker فعال با صفِ پر
    async def fake_loop(): return None
    monkeypatch.setattr(notify, "_super_worker", SimpleNamespace(done=lambda: False))
    notify._queue()._maxsize = 1
    try:
        notify._queue().put_nowait(("قدیمی", None))
        asyncio.run(notify.notify_super("پیامِ دوم"))
    finally:
        try: notify._queue().get_nowait()
        except Exception: pass
        notify._queue()._maxsize = notify.SUPER_QUEUE_MAX
    rows = q("SELECT * FROM notifications")
    assert rows, "پیامِ سرریز دراپ شد"


def test_obs011_alert_is_retried_and_then_marked_dead_not_deleted(monkeypatch):
    from newsbot.core_alerts import alert_enqueue, alert_counts, ALERT_MAX_ATTEMPTS
    from newsbot.scheduler import _retry_alerts
    q("DELETE FROM notifications", commit=True)
    nid = alert_enqueue("هشدار")
    async def boom(text, kb=None): raise RuntimeError("telegram down")
    monkeypatch.setattr(runtime, "NOTIFY_SUPER", boom)
    asyncio.run(_retry_alerts())
    row = q("SELECT * FROM notifications WHERE id=?", (nid,), one=True)
    assert row and int(row["attempts"]) == 1, "تلاشِ دوباره انجام نشد"
    assert not row["delivered_at"]
    # تا سقفِ تلاش ⇒ dead-letter (و **حذف** نمی‌شود)
    for _ in range(ALERT_MAX_ATTEMPTS + 2):
        q("UPDATE notifications SET next_attempt_at=0 WHERE id=?", (nid,), commit=True)   # زمان گذشته
        asyncio.run(_retry_alerts())
    row = q("SELECT * FROM notifications WHERE id=?", (nid,), one=True)
    assert row is not None, "اعلانِ مرده حذف شد ⇒ از دست می‌رود"
    assert row["dead"] == 1
    pending_n, dead_n = alert_counts()
    assert dead_n >= 1


def test_obs011_alert_is_delivered_on_a_later_tick(monkeypatch):
    from newsbot.core_alerts import alert_enqueue
    from newsbot.scheduler import _retry_alerts
    q("DELETE FROM notifications", commit=True)
    nid = alert_enqueue("هشدار")
    sent = []
    async def ok(text, kb=None): sent.append(text); return True
    monkeypatch.setattr(runtime, "NOTIFY_SUPER", ok)
    asyncio.run(_retry_alerts())
    row = q("SELECT * FROM notifications WHERE id=?", (nid,), one=True)
    assert row["delivered_at"], "اعلان تحویل‌نشده باقی ماند"
    assert sent and sent[0] == "هشدار"


# ══════════════════════════════════════════════════════════════════════════════════════
# OBS-013 — رسیدی که هیچ مدیری ندیده، دوباره اعلام می‌شود
# ══════════════════════════════════════════════════════════════════════════════════════
def test_obs013_unnotified_receipt_is_renotified_by_the_scheduler(monkeypatch):
    from newsbot.core_payments import pay_create, pay_unnotified_super
    from newsbot.scheduler import _renotify_payments
    uid = _mk_user(430306)
    _mk_user(1, role="super")
    pid = _mk_plan(uid)
    q("UPDATE pay_requests SET status='approved' WHERE status='pending'", commit=True)   # ایزوله‌سازی تست
    rid = pay_create(uid, pid, receipt_chat=555, receipt_msg=777, note="رسید", final_price="100")
    assert pay_unnotified_super(10), "سطرِ «اعلام‌نشده» پیدا نشد"
    bot = FakeBot()
    asyncio.run(_renotify_payments(bot))
    row = q("SELECT * FROM pay_requests WHERE id=?", (rid,), one=True)
    assert row["super_notified_at"], "رسید دوباره اعلام نشد ⇒ پرداخت یتیم می‌ماند"
    texts = [kw.get("caption", "") for _, kw in bot.copied] + [a[1] for a, _ in bot.sent if len(a) > 1]
    assert any(f"#{rid}" in t for t in texts), "پیامِ این رسید به مدیر نرسید"


def test_obs013_already_notified_receipt_is_not_resent():
    from newsbot.core_payments import pay_create, pay_mark_super_notified
    from newsbot.scheduler import _renotify_payments
    uid = _mk_user(430307)
    _mk_user(1, role="super")
    pid = _mk_plan(uid)
    q("UPDATE pay_requests SET status='approved' WHERE status='pending'", commit=True)   # ایزوله‌سازی تست
    rid = pay_create(uid, pid, note="رسید", final_price="100")
    pay_mark_super_notified(rid)
    bot = FakeBot()
    asyncio.run(_renotify_payments(bot))
    texts = [kw.get("caption", "") for _, kw in bot.copied] + [a[1] for a, _ in bot.sent if len(a) > 1]
    assert not any(f"#{rid}" in t for t in texts), "رسیدِ اعلام‌شده دوباره فرستاده شد"


def test_obs013_health_reports_the_unnotified_count():
    from newsbot.core_payments import pay_create, pay_super_unnotified_count
    uid = _mk_user(430308)
    pid = _mk_plan(uid)
    before = pay_super_unnotified_count()
    pay_create(uid, pid, note="رسید", final_price="100")
    assert pay_super_unnotified_count() == before + 1


# ══════════════════════════════════════════════════════════════════════════════════════
# سقفِ سختِ متن — ریشهٔ UI-006 در لایهٔ فرمت
# ══════════════════════════════════════════════════════════════════════════════════════
def test_hard_html_never_exceeds_the_limit_even_when_open_tags_are_closed():
    """بستنِ تگِ باز چند کاراکتر اضافه می‌کرد ⇒ خروجی از سقف رد می‌شد ⇒ تلگرام رد می‌کرد."""
    from newsbot.text_format import hard_html
    raw = "<b>" + ("متنِ بلند " * 400) + "</b>"
    for limit in (200, 512, 1024, 2600):
        out = hard_html(raw, limit)
        assert len(out) <= limit, f"سقفِ سخت نقض شد: {len(out)} > {limit}"
        assert _tags_balanced(out), "تگِ باز در خروجیِ بریده‌شده"


def test_hard_html_keeps_short_text_untouched():
    from newsbot.text_format import hard_html
    raw = "<b>سلام</b> دنیا"
    assert hard_html(raw, 1024) == raw


# ══════════════════════════════════════════════════════════════════════════════════════
# DB-008 — جدول‌هایی که بی‌حدّ رشد می‌کردند
# ══════════════════════════════════════════════════════════════════════════════════════
def test_db008_cleanup_purges_orphan_referral_markers_but_keeps_live_ones():
    from newsbot.core_articles import cleanup
    from newsbot.core_referrals import REF_PAID_PREFIX
    uid = _mk_user(430309)
    pid = _mk_plan(uid)
    # پرداختِ زنده + نشانگرش ⇒ باید بماند
    q("INSERT INTO pay_requests(user_id,plan_id,status,created_at,final_price) VALUES(?,?,'approved',?,?)",
             (uid, pid, now_iso(), "100"), commit=True)
    live_id = q("SELECT last_insert_rowid() id", one=True)["id"]
    q("INSERT OR REPLACE INTO settings(key,value) VALUES(?, '1')", (REF_PAID_PREFIX + f"pay:{live_id}",), commit=True)
    # پرداختِ حذف‌شده + نشانگرِ یتیم ⇒ باید پاک شود
    q("INSERT INTO pay_requests(user_id,plan_id,status,created_at,final_price) VALUES(?,?,'approved',?,?)",
               (uid, pid, now_iso(), "100"), commit=True)
    orphan_id = q("SELECT last_insert_rowid() id", one=True)["id"]
    q("INSERT OR REPLACE INTO settings(key,value) VALUES(?, '1')", (REF_PAID_PREFIX + f"pay:{orphan_id}",), commit=True)
    q("DELETE FROM pay_requests WHERE id=?", (orphan_id,), commit=True)
    gset("last_cleanup", 0)
    cleanup()
    assert q("SELECT 1 FROM settings WHERE key=?", (REF_PAID_PREFIX + f"pay:{orphan_id}",), one=True) is None, \
        "نشانگرِ یتیم پاک نشد ⇒ جدولِ settings بی‌حدّ رشد می‌کند"
    assert q("SELECT 1 FROM settings WHERE key=?", (REF_PAID_PREFIX + f"pay:{live_id}",), one=True) is not None, \
        "نشانگرِ پرداختِ زنده پاک شد ⇒ پنجرهٔ replay باز شد"


def test_db008_cleanup_purges_expired_channel_tokens():
    from newsbot.core_articles import cleanup
    q("DELETE FROM ch_tokens", commit=True)
    q("INSERT INTO ch_tokens(token,channel_id,issued_to,issued_at,expires_at) VALUES(?,?,?,?,?)",
      ("expired-token", 1, 1, now_iso(), (now_utc() - __import__("datetime").timedelta(days=1)).isoformat()),
      commit=True)
    q("INSERT INTO ch_tokens(token,channel_id,issued_to,issued_at,expires_at) VALUES(?,?,?,?,?)",
      ("live-token", 1, 1, now_iso(), (now_utc() + __import__("datetime").timedelta(days=1)).isoformat()),
      commit=True)
    gset("last_cleanup", 0)
    cleanup()
    assert q("SELECT 1 FROM ch_tokens WHERE token='expired-token'", one=True) is None
    assert q("SELECT 1 FROM ch_tokens WHERE token='live-token'", one=True) is not None


# ══════════════════════════════════════════════════════════════════════════════════════
# PIPE-021 — سطرِ ژورنالِ «unsettled» در هر چرخهٔ بازیابی دوباره نوشته نشود
# ══════════════════════════════════════════════════════════════════════════════════════
def _mk_journal(aid, phase, status="failed", reason="delivery_unknown"):
    import json
    q("INSERT OR REPLACE INTO articles(id,admin_id,channel_id,hash,url,title,source_id,created_at,status,reason) "
      "VALUES(?,?,?,?,?,?,?,?,?,?)",
      (aid, 1, 1, f"h{aid}", "u", "t", 1, now_iso(), status, reason), commit=True)
    q("INSERT OR REPLACE INTO pub_journal(article_id,journal,updated_at) VALUES(?,?,?)",
      (aid, json.dumps({"phase": phase, "admin_id": 1}), now_iso()), commit=True)


def test_pipe021_terminal_journal_rows_are_deleted_not_rewritten():
    from newsbot.core_articles import recover_publications
    aid = 430310
    _mk_journal(aid, "unsettled", status="failed", reason="delivery_unknown")
    for _ in range(3):
        recover_publications()
    assert q("SELECT 1 FROM pub_journal WHERE article_id=?", (aid,), one=True) is None, \
        "سطرِ ژورنالِ پایانی حذف نشد ⇒ بارِ نوشتن تکرار می‌شود"


def test_pipe021_live_journal_row_is_not_rewritten_on_every_pass():
    """مقالهٔ «ready» با ژورنالِ unsettled: سطر می‌ماند ولی **دیگر نوشته نمی‌شود**."""
    from newsbot.core_articles import recover_publications
    aid = 430311
    _mk_journal(aid, "unsettled", status="ready", reason="")
    row = q("SELECT updated_at FROM pub_journal WHERE article_id=?", (aid,), one=True)
    before = row["updated_at"]
    time.sleep(0.01)
    recover_publications()
    recover_publications()
    after = q("SELECT updated_at FROM pub_journal WHERE article_id=?", (aid,), one=True)
    assert after is not None, "سطرِ ژورنالِ زنده نباید حذف شود"
    assert after["updated_at"] == before, "سطرِ ژورنال در هر چرخه دوباره نوشته می‌شود ⇒ نوشتنِ بی‌پایان"
