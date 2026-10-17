# -*- coding: utf-8 -*-
"""ابزارهای رابط کاربری، عملیات زنده (Thinking/Working)، پیام‌های موقت،
اجرای عملیات کاربر با قفل و لغو، تست مدل با گزارش زنده، و پیاده‌سازی تلگرامی اعلان‌ها.
FIX v4:
  • BOT به‌جای APP سراسری با set_bot() ست می‌شود (بدون وابستگی دایره‌ای به app.py)
  • sweep_temp_overflow() در CLEANUP_HOOKS ثبت می‌شود تا cleanup() هسته آن را صدا بزند
  • esc() این‌جا تعریف می‌شود (publish.py و همه‌ی viewها از همین‌جا می‌گیرند)
"""
import asyncio, html, re, secrets, time
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.error import BadRequest, TelegramError
from newsbot import config
from newsbot.logutils import log, log_event, redact
from newsbot.timeutils import now_utc
from newsbot.text_format import strip_tags
from newsbot.core_users import user_lang, is_super, can_admin
from newsbot.ratelimit import rate_ok, rate_left
from newsbot.ai_models import (ai_err_text, test_model, get_model, _model_label,
                               _ai_events, ai_chat, _validate_ready)
from newsbot.opctx import (Operation, _current_operation, _operations,
                           user_control, operation_checkpoint, clear_interaction, step_add, CLEANUP_HOOKS)
from newsbot.ui_i18n import tr, WIZ, DIAG

HTML = ParseMode.HTML
BOT = None          # در post_init با set_bot(app.bot) ست می‌شود (جایگزین APP سراسری)

def set_bot(bot):
    global BOT
    BOT = bot

# ══════════════════════════════════════════════════════════════════════════
# ابزارهای کوچک UI
# ══════════════════════════════════════════════════════════════════════════
def bar(used, cap, width=10):
    """نوار پیشرفت سهمیه؛ cap=None یعنی نامحدود (نوار خالی)."""
    if not cap or cap <= 0: return ""
    filled = min(width, max(0, int(round(width * min(used, cap) / cap))))
    return "▰" * filled + "▱" * (width - filled)

def quota_bars(rows, width=10):
    """خط نوارِ سهمیه‌ها: rows = [(emoji, used, cap), ...] — بدون متن، پس نیازی به i18n ندارد."""
    out = [f"{i} <code>{bar(u, c, width)}</code> <b>{u}</b>/{c}" for i, u, c in rows if c is not None]
    return ("\n" + " · ".join(out)) if out else ""

def B(t, d): return InlineKeyboardButton(t, callback_data=d[:64])
def U(t, url): return InlineKeyboardButton(t, url=url)
def esc(x): return html.escape(str(x if x is not None else ""))
def onoff(v): return "✅" if v else "❌"
def to_int(s):
    text = str(s).strip().translate(config._FA_DIGITS)
    if not re.fullmatch(r"[+-]?[0-9]+", text): raise ValueError("Expected an integer")
    return int(text)
def pairs(btns, n=2): return [btns[i:i + n] for i in range(0, len(btns), n)]
def uname(u): return ("@" + u["username"]) if u and u["username"] else (u["name"] if u and u["name"] else str(u["id"] if u else "?"))
def L(update): return user_lang(update.effective_user.id) or "fa"
def cap(v): return "∞" if v is None else v
def kbm(kb):
    """[[(label, data)]] → InlineKeyboardMarkup (برای notify از هسته)"""
    return InlineKeyboardMarkup([[B(t, d) if not str(d).startswith("http") else U(t, d) for t, d in row] for row in kb]) if kb else None

def stale(update, kind="update"):
    """پیام‌های کهنه (ارسال‌شده در زمان offline) پردازش نمی‌شوند؛ نه کل صف دور ریخته می‌شود و نه سیل کهنه می‌آید.
    فقط برای پیام‌ها استفاده می‌شود: کال‌بک روی یک پست قدیمی («بیشتر») کارِ درست است و باید اجرا شود."""
    try:
        d = update.effective_message.date
        if d is None: return False
        age = (now_utc() - d).total_seconds()
    except Exception: return False
    if age > config.STALE_UPDATE_MAX_MIN * 60:
        log_event("INFO", f"stale {kind} ignored (age {int(age)}s)"); return True
    return False

def await_expired(st):
    """UISEC-06 (فاز ۶): مهلت state فرم ورودی؛ state بدون ts یا قدیمی‌تر از AWAIT_TTL_SEC منقضی است.
    (پیش از این state تا پایان عمر پروسه باقی می‌ماند و پیامِ بعدیِ کاربر به فرمِ فراموش‌شده می‌خورد.)"""
    ts = st.get("ts") if isinstance(st, dict) else None
    if not isinstance(ts, (int, float)): return True
    return (time.time() - ts) > config.AWAIT_TTL_SEC

# ══════════════════════════════════════════════════════════════════════════
# پیام‌های موقت (یک sweeper رویدادمحور برای همه)
# ══════════════════════════════════════════════════════════════════════════
_temp_messages = {}      # (chat_id, message_id) → (مهلت پاک‌سازی monotonic | None اگر زمان‌بندی نشده، bot)
_temp_sweeper = None
_temp_wake = None

async def _temp_sweeper_loop():
    """یک تسکِ مشترک برای پاک‌سازی پیام‌های موقت — رویدادمحور: بیدارشدن بی‌دلیل ندارد.
    قبلاً هر ۰.۵ ثانیه بیدار می‌شد (~۱۷۳ هزار بار در روز) صرف‌نظر از اینکه پیامی در انتظار باشد یا نه."""
    global _temp_wake
    _temp_wake = asyncio.Event()
    while True:
        now = time.monotonic()
        for key, (due, bot) in list(_temp_messages.items()):
            if due is None or due > now: continue
            _temp_messages.pop(key, None)
            try: await bot.delete_message(*key)
            except Exception: log.debug("temporary message deletion failed", exc_info=True)
        nxt = min((v[0] for v in list(_temp_messages.values()) if v[0] is not None), default=None)
        try:
            if nxt is None: await _temp_wake.wait()                       # هیچ پیام موقتی نیست ⇒ بدون بیدارشدن منتظر می‌ماند
            else: await asyncio.wait_for(_temp_wake.wait(), timeout=max(0.25, nxt - time.monotonic()))
        except asyncio.TimeoutError: pass
        _temp_wake.clear()

def expire_temp(bot, chat_id, mid, seconds=5):
    """پیام موقت را برای پاک‌سازی خودکار علامت می‌زند؛ تسک جدیدی ساخته نمی‌شود."""
    global _temp_sweeper
    prev = _temp_messages.get((chat_id, mid), (None, None))[1]
    _temp_messages[(chat_id, mid)] = (time.monotonic() + max(0.0, float(seconds)), bot or prev)
    if _temp_wake is not None: _temp_wake.set()      # پاک‌کنندهٔ خواب‌رفته فوراً بیدار می‌شود (بدون نظرسنجی دوره‌ای)
    if _temp_sweeper is None or _temp_sweeper.done():
        try: _temp_sweeper = asyncio.create_task(_temp_sweeper_loop(), name="temp-sweeper")
        except RuntimeError: pass          # خارج از event loop؛ با اولین پیام بعدی راه می‌افتد

def sweep_temp_overflow():
    """FIX v4: هوک پاک‌سازی سرریز پیام‌های موقت — cleanup() هسته از طریق CLEANUP_HOOKS صدا می‌زند."""
    if len(_temp_messages) > 5000:
        for k in list(_temp_messages)[:len(_temp_messages) - 5000]: _temp_messages.pop(k, None)
CLEANUP_HOOKS.append(sweep_temp_overflow)

async def create_temp(bot, chat_id, text, temporary=True):
    task = asyncio.create_task(bot.send_message(chat_id, text[:4000], parse_mode=HTML, disable_web_page_preview=True))
    try: msg = await asyncio.shield(task)
    except asyncio.CancelledError:
        try:
            msg = await task
            if temporary:
                expire_temp(bot, chat_id, msg.message_id, 0)
            else:
                op = _current_operation.get()
                if op: op.status_message_id = msg.message_id
        except Exception:
            log.debug("cancelled status delivery failed", exc_info=True)
        raise
    if temporary: _temp_messages[(chat_id, msg.message_id)] = (None, bot)
    return msg.message_id

async def send_temp(context, chat_id, text, seconds=None):
    """پیام موقت: پیش‌فرض ۱۰ ثانیه (قاعدهٔ نمایش)؛ مقدار صریح فقط جایی که واقعاً لازم است."""
    seconds = 10 if seconds is None else max(1, int(seconds))
    try: mid = await create_temp(context.bot, chat_id, text)
    except Exception:
        log.debug("temporary message send failed", exc_info=True); return
    expire_temp(context.bot, chat_id, mid, seconds)

# ══════════════════════════════════════════════════════════════════════════
# پنل زنده‌ی Thinking/Working
# ══════════════════════════════════════════════════════════════════════════
DOTS_SEQ = (3, 2, 1, 0, 1, 2)          # ابتدا سه نقطه یکی‌یکی پاک می‌شود، سپس یکی‌یکی برمی‌گردد

def strip_thinking_prefix(text):
    """Remove only a leading Thinking header from a status body."""
    if not text: return ""
    return re.sub(r"^\s*(?:<b>)?Thinking(?:.{1,3}|…)?(?:</b>)?\s*\n", "", str(text), count=1)

SPHARSE = {
    # ── مدل‌ها (وضعیت Working) — همیشه با ضمیر اول‌شخص
    ("model_start", "run", 0): "⏳ دارم مدل {} را اجرا می‌کنم…", ("model_start", "run", 1): "🧠 دارم با مدل {} می‌نویسم…", ("model_start", "run", 2): "▶️ مدل {} را روشن می‌کنم…",
    ("model_start", "ok", 0): "✅ مدل {} جواب داد؛ دارم خروجی‌اش را بررسی می‌کنم", ("model_start", "ok", 1): "🎯 خروجی مدل {} سالم بود", ("model_start", "ok", 2): "✅ مدل {} کارش را درست انجام داد",
    ("model_start", "fail", 0): "⚠️ مدل {} جواب نداد", ("model_start", "fail", 1): "❌ مدل {} از کار افتاده بود؛ مدل بعدی را امتحان می‌کنم", ("model_start", "fail", 2): "🛑 مدل {} پاسخ نداد؛ سوییچ می‌کنم روی مدل بعدی",
    ("prep", "run", 0): "🧾 دارم متن مقاله را برای مدل {} بخش‌بندی و خلاصه می‌کنم…", ("prep", "run", 1): "📚 دارم کل متن را می‌خوانم تا به مدل {} بدهم…", ("prep", "run", 2): "🗂 دارم متن را آماده‌ی تحویل به {} می‌کنم…",
    ("post_gen", "run", 0): "⏳ دارم محتوا را می‌نویسم…", ("post_gen", "run", 1): "🧠 دارم مطابق سبک کانال می‌نویسم…", ("post_gen", "run", 2): "✍️ دارم متن را آماده می‌کنم…",
    ("post_ready", "ok", 0): "✅ محتوا آماده شد", ("post_ready", "ok", 1): "🎉 پست تولید شد", ("post_ready", "ok", 2): "📨 دارم به کانال می‌فرستم…",
    ("ai", "run", 0): "⏳ دارم با مدل تولید می‌کنم…", ("ai", "run", 1): "🧠 مدل دارد فکر می‌کند…", ("ai", "run", 2): "✍️ دارم می‌نویسم…",
    # ── منابع (وضعیت Thinking)
    ("src_check", "run", 0): "🔎 دارم {} را بررسی می‌کنم…", ("src_check", "run", 1): "⏳ خب، می‌روم سراغ {}…", ("src_check", "run", 2): "📖 دارم {} را می‌خوانم…",
    ("src_found", "ok", 0): "✅ در {} یک مورد مناسب پیدا کردم", ("src_found", "ok", 1): "📝 {} یک خبر به‌درد‌بخور داشت", ("src_found", "ok", 2): "🎯 در {} یک گزینه‌ی خوب دیدم",
    ("src_empty", "fail", 0): "📭 {} خبری نداشت", ("src_empty", "fail", 1): "⚠️ خروجی {} با معیارهای کانال نمی‌خواند", ("src_empty", "fail", 2): "📭 {} خبر تازه‌ای نداشت",
    ("src_cooldown", "run", 0): "⏳ {} در بازه‌ی استراحت است؛ فعلاً ردش می‌کنم", ("src_cooldown", "run", 1): "🔄 {} را تازه بررسی کرده‌ام؛ بعداً سراغش می‌روم", ("src_cooldown", "run", 2): "💤 {} دارد بازیابی می‌شود",
    ("src_fail", "fail", 0): "⚠️ {} پاسخ نداد", ("src_fail", "fail", 1): "❌ نتوانستم به {} وصل شوم", ("src_fail", "fail", 2): "🔌 {} قطع بود؛ می‌روم منبع بعدی",
    ("src_notmod", "run", 0): "ℹ️ {} از آخرین بررسی تازه‌تر نشده", ("src_notmod", "run", 1): "⏳ {} خبر جدیدی نداشت", ("src_notmod", "run", 2): "📄 محتوای {} همان قبلی است",
    ("src_extract_fail", "fail", 0): "📄 در {} متن خبر خوانده نشد؛ ردش می‌کنم", ("src_extract_fail", "fail", 1): "❌ {} محتوای قابل‌خواندن نداشت؛ ردش کردم", ("src_extract_fail", "fail", 2): "⚠️ بدنهٔ خبر در {} خالی بود؛ ردش کردم",
    ("src_skip_old", "fail", 0): "🕰 در {} این خبر قدیمی‌تر از بازهٔ تعیین‌شده بود", ("src_skip_old", "fail", 1): "⏳ {} قدیمی بود", ("src_skip_old", "fail", 2): "📅 خبر {} بیرون از بازهٔ زمانی بود",
    ("src_ad", "fail", 0): "🚫 محتوای {} تبلیغاتی بود؛ پردازش نمی‌کنم", ("src_ad", "fail", 1): "⚠️ محتوای {} را تبلیغ تشخیص دادم؛ کنارش می‌گذارم", ("src_ad", "fail", 2): "🛑 فیلتر تبلیغ، مورد {} را رد کرد",
    ("src_ai_fail", "fail", 0): "🧠 مدل {} خروجی قابل‌استفاده نداد؛ می‌روم سراغ بعدی", ("src_ai_fail", "fail", 1): "❌ تولید محتوا با {} ناموفق بود", ("src_ai_fail", "fail", 2): "⚠️ خروجی مدل {} معتبر نبود",
    ("src_dup", "fail", 0): "🔁 خبر {} را قبلاً پردازش کرده‌ام؛ دوباره نمی‌سازمش", ("src_dup", "fail", 1): "📌 خبر {} تکراری بود؛ ردش می‌کنم", ("src_dup", "fail", 2): "♻️ {} خبر تکراری داشت؛ می‌روم سراغ خبر بعدی",
    ("src_low_score", "fail", 0): "📏 امتیاز خبر {} به حداقل کانال نرسید", ("src_low_score", "fail", 1): "🧪 در {} امتیاز پایین بود؛ ردش می‌کنم", ("src_low_score", "fail", 2): "⚠️ خبر {} با معیارهای کانال هم‌خوان نبود",
    # ── مراحل میانی
    ("found", "ok", 0): "📥 یک مورد مناسب", ("found", "ok", 1): "✅ یک گزینه‌ی خوب پیدا کردم", ("found", "ok", 2): "🎯 آیتم مناسب را تشخیص دادم",
    ("extract", "run", 0): "🧾 دارم متن خبر را استخراج می‌کنم…", ("extract", "run", 1): "🔍 دارم بدنه‌ی خبر را می‌خوانم…", ("extract", "run", 2): "📄 دارم کل مقاله را می‌خوانم…",
    ("low", "run", 0): "⏳ دارم امتیاز می‌دهم…", ("low", "run", 1): "📏 دارم معیارها را می‌سنجم…", ("low", "run", 2): "🧪 دارم محتوا را ارزیابی می‌کنم…",
    ("ad", "run", 0): "🚫 دارم تبلیغاتی بودن را بررسی می‌کنم…", ("ad", "run", 1): "🛡 دارم نشانه‌های تبلیغ را می‌گردم…", ("ad", "run", 2): "⚠️ دارم محتوای تبلیغاتی را جدا می‌کنم…",
    ("queue", "run", 0): "⏳ دارم در صف انتشار می‌گذارم…", ("queue", "run", 1): "📥 دارم برای انتشار آماده می‌کنم…", ("queue", "run", 2): "🕐 دارم برای انتشار نگه می‌دارم…",
    ("published", "ok", 0): "✅ منتشر شد", ("published", "ok", 1): "📣 ارسال به کانال انجام شد", ("published", "ok", 2): "🎉 در کانال قرار گرفت",
    ("queued", "ok", 0): "📌 در صف قرار گرفت", ("queued", "ok", 1): "📥 برای انتشار بعدی ذخیره شد", ("queued", "ok", 2): "✅ آماده‌ی انتشار شد",
    # ── محدودیت‌ها و وضعیت‌های عمومی
    ("busy", "run", 0): "⏳ عملیات قبلی هنوز تمام نشده", ("busy", "run", 1): "⏳ دارم چرخه‌ی قبلی را تمام می‌کنم…", ("busy", "run", 2): "⏳ منتظر باز شدن کانال هستم…",
    ("plan_inactive", "fail", 0): "⛔ پلن فعال نیست", ("plan_inactive", "fail", 1): "🛑 پلن نیاز به تمدید دارد", ("plan_inactive", "fail", 2): "⚠️ مجوز انتشار فعلی منقضی شده",
    ("quota_posts", "fail", 0): "📉 سهمیه‌ی امروز تمام شد", ("quota_posts", "fail", 1): "⛔ سقف روزانه پر شده", ("quota_posts", "fail", 2): "⚠️ امروز دیگر ظرفیت انتشار ندارم",
    ("quota_tests", "fail", 0): "🧪 سهمیه‌ی تست امروز پر شده", ("quota_tests", "fail", 1): "⛔ ظرفیت تست تمام شده", ("quota_tests", "fail", 2): "🧪 به محدودیت تست رسیدم",
    ("no_sources", "fail", 0): "📭 هیچ منبع فعالی ندارم", ("no_sources", "fail", 1): "🚫 منبع فعالی پیدا نکردم", ("no_sources", "fail", 2): "📭 منبعی برای بررسی نیست",
}

def _step_text(st, lang):
    cat = st.get("category"); status = st.get("status", "run"); var = int(st.get("variant", 0))
    f = dict(st.get("fmt") or {})
    key_tpl = SPHARSE.get((cat, status, var)) or SPHARSE.get((cat, status, 0))
    model = st.get("model") or st.get("key") or ""
    if not key_tpl:                                  # وضعیت دیگر مهم نیست: هر سه وارینت را امتحان می‌کنم
        for alt in ("run", "ok", "fail"):
            key_tpl = SPHARSE.get((cat, alt, var)) or SPHARSE.get((cat, alt, 0))
            if key_tpl: break
    if not key_tpl:                                  # هر مرحله‌ای که متن اختصاصی ندارد، از جدول DIAG جمله می‌گیرد
        d = DIAG.get(cat)
        if not d: return cat
        key_tpl = d[1 if lang == "en" else 0]
    try: out = key_tpl.format(esc(model), **{k: esc(v) for k, v in f.items()})
    except Exception: out = key_tpl
    return re.sub(r"{[a-z_]+}", "", out).strip()   # هیچ جای‌نگهدار بی‌مقداری در متن نمی‌ماند

STATUS_STEPS = 6                       # چند مرحلهٔ آخر در پنل زنده دیده شود
STEP_ICON = {"run": "⏳", "ok": "✅", "fail": "❌", "skip": "⏭"}

def _elapsed(op): return max(0, int(time.time() - getattr(op, "t0", time.time()))) if op else 0

def make_status(lang, text, step=None):
    """پنل Thinking، شبیه چت‌بات‌های هوش مصنوعی: هر مرحله با آیکونِ وضعیتِ خودش، ترتیب زمانی
    (قدیمی‌ترین بالا، کارِ جاری پایین)، شمارندهٔ مراحلِ جمع‌شده و زمان سپری‌شده در هدر."""
    if step is None: return str(text or "")
    op = _current_operation.get()
    kind = getattr(op, "header_kind", "thinking")
    dots = "." * DOTS_SEQ[step % len(DOTS_SEQ)]
    header = f"Working{dots}" if kind == "working" else f"Thinking{dots}"
    el = _elapsed(op)
    head = f"<b>{header}</b>" + (f" <code>{el}s</code>" if el >= 3 else "")
    steps = sorted(getattr(op, "steps", None) or [], key=lambda x: x.get("entered", 0.0)) if op else []
    hidden, shown = max(0, len(steps) - STATUS_STEPS), steps[-STATUS_STEPS:]
    body = strip_thinking_prefix(text)
    lines = [tr(lang, "more_steps", n=hidden)] if hidden else []
    lines += [f"{STEP_ICON.get(st.get('status'), '⏳')} {_step_text(st, lang) or st.get('category') or ''}" for st in shown]
    if body: lines.append(body)
    return head + ("\n" + "\n".join(lines) if lines else "")

def operation_text(lang, key):
    texts = {
        "running": ("⚙️ در حال پردازش…", "⚙️ Processing…"),
        "completed": ("✅ عملیات انجام شد", "✅ Operation completed"),
        "cancelled": ("⛔ عملیات لغو شد", "⛔ Operation cancelled"),
        "nothing_to_cancel": ("⛔ عملیاتی برای لغو وجود ندارد", "⛔ Nothing to cancel"),
        "cancelled_after_publish": ("ادامهٔ عملیات لغو شد؛ {n} مطلب پیش از توقف منتشر شده و حذف نشده است.", "Further work cancelled; {n} post(s) were published before stopping and have not been removed."),
        "failed": ("⚠️ عملیات انجام نشد؛ دوباره تلاش کنید.", "⚠️ Operation failed; please try again."),
        "busy": ("عملیات قبلی در حال اجراست؛ برای توقف /cancel را بفرستید.", "An operation is running; use /cancel to stop it."),
        "busy_wait": ("⏳ یک عملیات در حال اجراست.\n\nصبر کنید تا تمام شود، یا برای توقف فوری /cancel را بفرستید.", "⏳ An operation is running.\n\nWait for it to finish, or send /cancel to stop it."),
        "fallback": ("⚠️ مدل پاسخ قابل‌استفاده نداد؛ در حال بررسی مدل بعدی…", "⚠️ Model unavailable; trying the next model…"),
    }
    return texts[key][1 if lang == "en" else 0]

# UISEC-07: به‌جای نمایش خامِ نوع عملیات در پیام «در حال اجرا»، عملیات را با زبان ساده می‌گوید
_RUNNING_WORDS = (
    ("a:test", ("🧪 ساخت پیش‌نمایش (تست) در حال اجراست…", "🧪 A test run is in progress…")),
    ("a:pub", ("🚀 انتشار مطلب در حال اجراست…", "🚀 Publishing is in progress…")),
    ("a:tog", ("⏳ روشن/خاموش‌کردن اتوماسیون در حال اجراست…", "⏳ Toggling automation…")),
    ("a:srcr", ("🔌 بررسی منبع در حال اجراست…", "🔌 Checking the source…")),
    ("a:qreset2", ("♻️ ریست صف در حال اجراست…", "♻️ Resetting the queue…")),
    ("s:bc_go", ("📣 ارسال همگانی در حال اجراست…", "📣 Broadcast is in progress…")),
    ("u:req", ("🎫 ثبت درخواست پلن در حال اجراست…", "🎫 Submitting your plan request…")),
    ("u:free", ("🎁 فعال‌سازی پلن رایگان در حال اجراست…", "🎁 Activating the free plan…")),
)

def running_op_text(lang, op):
    """عملیاتِ در حال اجرا را به زبان کاربر توصیف می‌کند (زمان سپری‌شده از ۳ ثانیه به بعد)."""
    kind = getattr(op, "operation_type", "") or ""
    k = kind.split(":", 1)[1] if kind.startswith(("callback:", "command:", "input:")) else kind
    words = next((w for pref, w in _RUNNING_WORDS if k.startswith(pref)), None)
    base = (words[1 if lang == "en" else 0] if words
            else ("⏳ درخواست قبلی شما هنوز در حال اجراست…" if lang != "en" else "⏳ Your previous request is still running…"))
    try:
        el = int(time.time() - float(getattr(op, "t0", 0) or 0))
        if el >= 3: base += f" ({el}s)"
    except Exception: pass
    return base

# ══════════════════════════════════════════════════════════════════════════
# نوشتن وضعیت روی پیام (زنده و پایانی)
# ══════════════════════════════════════════════════════════════════════════
async def _paint_status(op, context, display):
    """وضعیت را روی پیام موجود می‌نویسد و اگر پیام نبود یکی می‌سازد (منطق مشترکِ حالت زنده و حالت پایانی)."""
    candidate_id = op.status_message_id or context.user_data.get("status_message_id")
    if candidate_id is not None:
        try:
            await context.bot.edit_message_text(display, chat_id=op.chat_id, message_id=candidate_id, parse_mode=HTML, disable_web_page_preview=True)
            op.status_message_id = candidate_id
        except BadRequest as e:
            if "not modified" not in str(e).lower(): op.status_message_id = None
    if op.status_message_id is None:
        op.status_message_id = await create_temp(context.bot, op.chat_id, display, temporary=False)
    if op.status_message_id is not None:
        context.user_data["status_message_id"] = op.status_message_id
        return True
    return False

async def operation_status(context, text, failed=False, terminal=False):
    op = _current_operation.get()
    if not op: return False
    if not terminal:
        if op.cancelled or op.terminalized: return False
        operation_checkpoint()
    op.failed = op.failed or failed
    body = strip_thinking_prefix(str(text or ""))[:4000]
    lang = user_lang(op.user) or "fa"
    # Terminal is a hard barrier: invalidate animation before rendering the final state.
    if terminal:
        async with op.status_lock:
            op.terminalized = True
            task = op.thinking_task
            op.thinking_task = None
            if task and not task.done(): task.cancel()
            steps = sorted(getattr(op, "steps", None) or [], key=lambda x: x.get("entered", 0.0))
            hidden, shown = max(0, len(steps) - STATUS_STEPS), steps[-STATUS_STEPS:]
            step_lines = ([tr(lang, "more_steps", n=hidden)] if hidden else []) + [f"{STEP_ICON.get(st.get('status'), '⏳')} {_step_text(st, lang)}" for st in shown]
            base = body or strip_thinking_prefix(op.status_text)
            el = _elapsed(op)
            display = (("\n".join(step_lines) + "\n") if step_lines else "") + (base or "") + (f"\n\n<i>⏱ {el}s</i>" if el >= 3 and base else "")
            await _paint_status(op, context, display)
            if op.status_message_id is not None:
                # پیام نتیجه‌ی نهایی پس از نمایش ۵ ثانیه‌ای پاک می‌شود تا صفحه تمیز بماند
                _temp_messages[(op.chat_id, op.status_message_id)] = (None, context.bot)
                expire_temp(context.bot, op.chat_id, op.status_message_id, 5)
        return True
    async with op.status_lock:
        if op.terminalized or op.cancelled: return False
        now = time.time()
        if body == op.status_text and now - getattr(op, "_last_status_update", 0) < 0.5:
            return True
        op.status_text = body
        op._last_status_update = now
        display = make_status(lang, body, op.thinking_step)
        op.status_display = display
        await _paint_status(op, context, display)
        if op.thinking_task is None or op.thinking_task.done():
            async def animate():
                try:
                    while not op.terminalized and not op.cancelled:
                        await asyncio.sleep(1.0)      # هر ۱ ثانیه یک قدم انیمیشن
                        if op.terminalized or op.cancelled: break
                        async with op.status_lock:
                            if op.terminalized or op.cancelled or op.status_message_id is None: break
                            op.thinking_step += 1
                            display = make_status(lang, op.status_text, op.thinking_step)
                            if display == op.status_display: continue
                            try:
                                await context.bot.edit_message_text(display, chat_id=op.chat_id, message_id=op.status_message_id, parse_mode=HTML, disable_web_page_preview=True)
                                op.status_display = display
                            except BadRequest:
                                break
                except asyncio.CancelledError:
                    pass
                except Exception:
                    log.debug("thinking animation stopped", exc_info=True)
            op.thinking_task = asyncio.create_task(animate(), name=f"thinking:{op.user}:{op.operation_type}")
    return True

# ══════════════════════════════════════════════════════════════════════════
# اجرای عملیات کاربر (قفل per-user + لغو + وضعیت پایانی)
# ══════════════════════════════════════════════════════════════════════════
async def run_user_operation(update, context, kind, channel, work, show_status=False, replace=False, keep=()):
    uid = update.effective_user.id; lang = L(update)
    async with user_control(uid):
        replaced_status = False
        if replace:
            replaced_status = await cancel_user_operation(uid)
            clear_interaction(context, keep=keep)
        if uid in _operations:
            await popup(update, context, operation_text(lang, "busy"))
            return
        op = Operation(uid, channel, kind, update.effective_chat.id, replaced_status=replaced_status)
        op.oid = secrets.token_hex(2); 
        from newsbot.logutils import log_ctx
        log_ctx(op=op.oid)   # همهٔ لاگ‌های این عملیات با همین شناسه می‌آیند
        context.user_data.pop("status_message_id", None)  # clear stale status message from prior operation
        async def run():
            token = _current_operation.set(op)
            outcome = "completed"
            try:
                op.started = True
                log_event("INFO", f"operation start type={kind} channel={channel}", uid)
                operation_checkpoint()
                if show_status: await operation_status(context, operation_text(lang, "running"))
                result = await work()
                operation_checkpoint()
                if op.failed: outcome = "failed"
                return result
            except asyncio.CancelledError:
                op.cancelled = True; outcome = "cancelled"
                raise
            except Exception:
                op.failed = True; outcome = "failed"
                op.final_status = False
                log.exception("operation failed user=%s type=%s channel=%s", uid, kind, channel)
                clear_interaction(context)
            finally:
                try:
                    if outcome == "cancelled":
                        clear_interaction(context)
                        key = "cancelled_after_publish" if op.published_count else "cancelled"
                        await operation_status(context, operation_text(lang, key).format(n=op.published_count), terminal=True)
                    elif outcome == "failed" and not op.final_status:
                        await operation_status(context, operation_text(lang, outcome), failed=True, terminal=True)
                    elif op.status_message_id is not None and not op.final_status:
                        await operation_status(context, operation_text(lang, "completed"), terminal=True)
                    log_event("ERROR" if outcome == "failed" else "INFO", f"operation {outcome} type={kind} channel={channel}", uid)
                finally:
                    op.terminalized = True
                    if op.thinking_task and not op.thinking_task.done(): op.thinking_task.cancel()
                    op.thinking_task = None
                    context.user_data.pop("status_message_id", None)
                    if _operations.get(uid) is op: _operations.pop(uid, None)
                    _current_operation.reset(token)
        op.task = asyncio.create_task(run(), name=f"user:{uid}:{kind}")
        _operations[uid] = op
        try: return await asyncio.shield(op.task)
        except asyncio.CancelledError:
            if not op.cancelled:
                op.cancelled = True
                if op.started: op.task.cancel()
            if op.started:
                try: await asyncio.shield(op.task)
                except asyncio.CancelledError: pass
        finally:
            if op.task.done() and _operations.get(uid) is op: _operations.pop(uid, None)

async def cancel_user_operation(uid):
    op = _operations.get(uid)
    if not op: return False
    if not op.cancelled and not op.task.done():
        op.cancelled = True
        if op.started: op.task.cancel()
    try: await asyncio.shield(op.task)
    except asyncio.CancelledError:
        if not op.task.done(): raise
    if _operations.get(uid) is op: _operations.pop(uid, None)
    return op.status_message_id is not None

# ══════════════════════════════════════════════════════════════════════════
# گزارش زنده‌ی رویدادهای AI + تست مدل
# ══════════════════════════════════════════════════════════════════════════
def _ai_status_reporter(context, lang):
    async def report(event, label, code):
        name = esc(label)
        if name in ("مدل عمومی/پیش‌فرض", "Public/default model"): name = "مدل عمومی" if lang != "en" else "Public model"
        reason = ai_err_text(code, lang) if code else ""
        if lang == "en":
            texts = {
                "busy": f"Model {name} is busy; waiting for it to become available…",
                "start": f"Running {name} for this task…",
                "failure": f"{name} did not respond ({reason}); trying the next model…" if reason else f"{name} did not respond; trying the next model…",
                "unavailable": f"{name} is unavailable; checking the next model…",
                "no_test": "No dedicated test model is available; checking public models…",
                "success": f"Response received from {name}; checking the result…",
                "failed": "The available models did not return a usable response.",
            }
        else:
            texts = {
                "busy": f"مدل {name} مشغول است؛ دارم منتظر می‌مانم…",
                "start": f"دارم {name} رو برای این کار اجرا می‌کنم…",
                "failure": f"مدل {name} پاسخ نداد ({reason})؛ دارم مدل بعدی رو امتحان می‌کنم…" if reason else f"مدل {name} پاسخ نداد؛ دارم مدل بعدی رو امتحان می‌کنم…",
                "unavailable": f"مدل {name} در دسترس نبود؛ دارم گزینه بعدی رو بررسی می‌کنم…",
                "no_test": "مدل اختصاصی تست در دسترس نیست؛ دارم مدل‌های عمومی رو بررسی می‌کنم…",
                "success": f"پاسخ از {name} رسید؛ دارم نتیجه رو بررسی می‌کنم…",
                "failed": "مدل‌های در دسترس پاسخ قابل‌استفاده‌ای ندادند.",
            }
        op = _current_operation.get()
        body = texts.get(event, reason or "")
        if op is not None:
            # هر رویداد فقط یک سطر می‌سازد (نه سطر مرحله + سطر وضعیت؛ وگرنه دو جمله‌ی متناقض با هم دیده می‌شود)
            if getattr(op, "prep", False) and event in ("start", "busy"):
                step_add(op, "prep", key=name, status="run", model=name); body = ""     # پیش‌پردازش: هنوز Thinking
            elif event == "start":
                step_add(op, "model_start", key=name, status="run", model=name); op.header_kind = "working"; body = ""
            elif event == "busy":
                step_add(op, "model_start", key=name, status="run", model=name); body = ""
            elif event == "success":
                step_add(op, "model_start", key=name, status="ok", model=name); body = ""
            elif event == "failure":
                step_add(op, "model_start", key=name, status="fail", model=name); body = ""
            elif event == "unavailable":
                step_add(op, "model_start", key=name, status="fail", model=name); body = ""
        await operation_status(context, body)
    return report

async def run_model_test(update, context, mid, personal=False):
    uid = update.effective_user.id; lang = L(update); m = get_model(mid)
    op = _current_operation.get()
    if not m or (personal and m["owner_id"] != uid):
        return await popup(update, context, tr(lang, "notfound"))
    # UISEC-04 (فاز ۶): تست مدل شخصی هم هزینهٔ AI دارد؛ سقف نرخ مثل تست کانال (super معاف)
    if personal and not is_super(uid) and not rate_ok(f"mtest:{uid}", config.TEST_COOLDOWN_SEC):
        return await popup(update, context, tr(lang, "test_wait", n=rate_left(f"mtest:{uid}", config.TEST_COOLDOWN_SEC)))
    started = time.monotonic(); label = _model_label(m, uid if personal else None)
    token = _ai_events.set(_ai_status_reporter(context, lang))
    try:
        if personal:
            out, label, error = await ai_chat("You are a health-check. Reply with exactly one word.", "Say: READY",
                                              owner_id=uid, selected_id=mid, validate=_validate_ready)
            ok = out is not None
            elapsed = round(time.monotonic() - started, 1)   # فاز ۶ (یافتهٔ تست): مسیر موفقیت تست مدل شخصی بدون این خط با UnboundLocalError می‌شکست
        else:
            ok, out, elapsed = await test_model(mid)
            error = None if ok else out
            elapsed = round(time.monotonic() - started, 1)
        if ok:
            result = f"Test successful — model: {esc(label)} ({elapsed}s)" if lang == "en" else f"تست موفق — مدل: {esc(label)} ({elapsed}s)"
        else:
            result = ("Test failed — " if lang == "en" else "تست ناموفق — ") + ai_err_text(error, lang)
        await operation_status(context, result, failed=not ok, terminal=True)
        if op: op.final_status = True
        await popup(update, context, result)
        return ok
    finally:
        _ai_events.reset(token)

# ══════════════════════════════════════════════════════════════════════════
# popup / render / ask / wizard
# ══════════════════════════════════════════════════════════════════════════
POPUP_MAX_CHARS = 35     # کوتاه‌تر از این ⇒ پاپ‌آپ بالای صفحه
POPUP_TEMP_SECONDS = 10  # بلندتر ⇒ پیام معمولی که پس از این مدت حذف می‌شود

async def popup(update, context, text):
    """قاعدهٔ یکتای نمایش پیام: کوتاه‌تر از ۳۵ کاراکتر ⇒ پاپ‌آپ بالای صفحه؛
    بلندتر ⇒ پیام معمولی در چت که پس از ۱۰ ثانیه خودش حذف می‌شود."""
    plain = strip_tags(text).strip()
    qy = update.callback_query
    if qy and len(plain) < POPUP_MAX_CHARS:
        try:
            await qy.answer(plain, show_alert=False); context._callback_answered = True
            return
        except Exception: pass
    await send_temp(context, update.effective_chat.id, text, seconds=POPUP_TEMP_SECONDS)

async def render(update, context, text, kb=None, force_new=False):
    operation_checkpoint()
    notice = context.user_data.pop("notice", None)
    if notice: await popup(update, context, notice)
    text = text[:4000]; markup = InlineKeyboardMarkup(kb) if kb else None; qy = update.callback_query; chat_id = update.effective_chat.id
    # هر خطای تلگرام (نه فقط BadRequest) باید به پیام تازه برگردد؛ وگرنه یک پیام کهنه
    # کل عملیات را با «عملیات انجام نشد» زمین میزند. Forbidden زیرکلاس BadRequest نیست.
    if qy and qy.message and not force_new:
        try:
            await qy.edit_message_text(text, reply_markup=markup, parse_mode="HTML", disable_web_page_preview=True); context.user_data["panel"] = qy.message.message_id; return
        except TelegramError as e:
            if "not modified" in str(e).lower(): return
    mid = context.user_data.get("panel")
    if mid and not force_new:
        try:
            await context.bot.edit_message_text(text, chat_id=chat_id, message_id=mid, reply_markup=markup, parse_mode="HTML", disable_web_page_preview=True); return
        except TelegramError as e:
            if "not modified" in str(e).lower(): return
    try:
        m = await context.bot.send_message(chat_id, text, reply_markup=markup, parse_mode="HTML", disable_web_page_preview=True)
    except TelegramError:
        # آخرین حد: اگر HTML هم رد شد، متن ساده بفرست تا کاربر هیچ پاسخی نگیرد
        m = await context.bot.send_message(chat_id, strip_tags(text), reply_markup=markup, disable_web_page_preview=True)
    context.user_data["panel"] = m.message_id

async def ask(update, context, prompt, kind, back, **extra):
    lang = L(update); context.user_data["await"] = {"kind": kind, "back": back, "ts": time.time(), **extra}
    await render(update, context, f"✏️ {prompt}\n\n<i>{tr(lang, 'send_value')}</i>", [[B(tr(lang, "cancel"), "c:cancel")]])

async def wiz_start(update, context, wiz, back, **data):
    context.user_data["await"] = {"kind": "wiz", "wiz": wiz, "step": 0, "data": data, "back": back, "ts": time.time()}; await wiz_prompt(update, context)

async def wiz_prompt(update, context):
    lang = L(update); st = context.user_data.get("await")
    if not isinstance(st, dict) or st.get("kind") != "wiz": return
    steps = WIZ.get(st.get("wiz"), ()); i = st.get("step")
    if type(i) is not int or not 0 <= i < len(steps):
        if context.user_data.get("await") is st: context.user_data.pop("await", None)
        return
    await render(update, context, f"{tr(lang, 'step', i=i + 1, n=len(steps))} ✏️ {steps[i][1][1 if lang == 'en' else 0]}\n\n<i>{tr(lang, 'send_value')}</i>", [[B(tr(lang, "cancel"), "c:cancel")]])

# ══════════════════════════════════════════════════════════════════════════
# پیاده‌سازی تلگرامی اعلان‌های هسته + خانه
# ══════════════════════════════════════════════════════════════════════════
async def notify_supers(text, kb=None):
    for sid in config.SUPER_ADMIN_IDS:
        try:
            await BOT.send_message(sid, redact(text), parse_mode=HTML, reply_markup=kbm(kb) if kb and isinstance(kb[0][0], tuple) else (InlineKeyboardMarkup(kb) if kb else None), disable_web_page_preview=True)
        except Exception as e: log.warning(f"notify {sid}: {e}")

async def notify_user_fn(uid, text, kb=None):
    try:
        await BOT.send_message(uid, text, parse_mode=HTML, reply_markup=kbm(kb), disable_web_page_preview=True)
    except Exception as e:
        log.warning(f"notify user {uid}: {e}")
        if config.SCHED_NOTIF_FAILURES.get():
            raise   # scheduled-warning mode: propagate so _plan_notifications releases remind_key

async def go_home(update, context):
    uid = update.effective_user.id; context.user_data.pop("await", None)
    if not user_lang(uid):
        from newsbot.ui_views_user import view_lang
        return await view_lang(update, context)
    from newsbot.ui_views_user import view_user_home
    from newsbot.ui_views_admin import view_admin_home
    from newsbot.ui_views_super import view_super_home
    return await (view_super_home if is_super(uid) else view_admin_home if can_admin(uid) else view_user_home)(update, context)
