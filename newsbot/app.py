# -*- coding: utf-8 -*-
"""راه‌اندازی: preflight، init_core، post_init/post_shutdown، ثبت handlerها و main.
FIX v4:
  • _rate_load_persisted() در init_core ⇒ محدودیت‌های نرخ حساس پس از restart باقی می‌مانند
  • preflight بک‌اند D1 و MAX_QUEUE_SIZE و کانال اجباری را هم اعتبارسنجی می‌کند
  • post_init با set_bot() بطریِ تلگرام را به ui_widgets می‌دهد (بدون APP سراسری در لایه‌ی UI)
"""
import asyncio, html, os, re
from telegram import BotCommand
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, ContextTypes, filters
from telegram.request import HTTPXRequest
from newsbot import config
from newsbot import runtime
from newsbot import watchdog
from newsbot.db import db, gget, gset, arun, close_db_pool, readonly_check
from newsbot.logutils import log, log_event
from newsbot.ratelimit import _rate_load_persisted
from newsbot.core_plans import seed_plans
from newsbot.core_articles import recover_publications
from newsbot.ai_models import list_models
from newsbot.scheduler import scheduler_tick
from newsbot.net_http import close_http
from newsbot import ui_widgets
from newsbot.ui_widgets import notify_supers, notify_user_fn, set_bot
from newsbot.ui_commands import (managed_command, cmd_start, cmd_create, cmd_admin, cmd_man,
                                 cmd_help, cmd_about, cmd_lang, cmd_cancel, cmd_ref, cmd_unknown)
from newsbot.ui_support import on_message
from newsbot.ui_dispatch import on_callback
from newsbot.ui_views_super import cmd_health

APP: Application = None
_db_lock = None      # قفل فایلِ دیتابیس: دو نسخه‌ی هم‌زمان ربات ⇒ دو چرخه روی یک کانال و پست تکراری
_SHUTDOWN_CANCEL_TIMEOUT = 8.0   # مهلت settle پس از لغو اولِ تسک‌های در حال اجرا
_SHUTDOWN_GRACE = 2.0            # فرصت دوم برای تسک‌هایی که لغو اول را قورت دادند


def init_core():
    db(); seed_plans(); recover_publications(); _rate_load_persisted()   # FIX v4: بقای rate-limit حساس
    from newsbot.secret_store import migrate_ai_keys   # SEC-09 (فاز ۱۲): رمزنگاری کلیدهای AI در حالت سکون
    migrate_ai_keys()
    if gget("automation_enabled") is None: gset("automation_enabled", True)
    log.info("core v4 ready | backend=%s | CF KV: %s | super_admins=%d | AI×%s FETCH×%s CYCLE×%s",
             config.DB_BACKEND, "on" if config.CF_ENABLED else "local", len(config.SUPER_ADMIN_IDS),
             config.AI_CONCURRENCY, config.FETCH_CONCURRENCY, config.CYCLE_CONCURRENCY)


async def job_tick(context: ContextTypes.DEFAULT_TYPE):
    try:
        await scheduler_tick(context.bot)
    except Exception as e:
        log_event("ERROR", f"scheduler tick failed: {type(e).__name__}: {e}",
                  exc_info=True, error_class=type(e).__name__)


async def post_init(app: Application):
    me = await app.bot.get_me(); runtime.BOT_USERNAME = me.username
    runtime.NOTIFY_SUPER = notify_supers; runtime.NOTIFY_USER = notify_user_fn
    set_bot(app.bot)   # FIX v4: لایه‌ی UI بدون APP سراسری، بطری را از این‌جا می‌گیرد
    from newsbot.notify import ensure_super_worker, notify_super   # ARCH-09/PH-06: outbox پایدار اعلان‌ها
    ensure_super_worker()
    await app.bot.set_my_commands([BotCommand("start", "منوی اصلی"), BotCommand("create", "پنل مدیریت"), BotCommand("ref", "دعوت دوستان"), BotCommand("man", "پشتیبانی"), BotCommand("lang", "زبان"), BotCommand("about", "درباره"), BotCommand("help", "راهنما"), BotCommand("cancel", "لغو عملیات")], language_code="fa")
    await app.bot.set_my_commands([BotCommand("start", "Main menu"), BotCommand("create", "Admin panel"), BotCommand("ref", "Invite friends"), BotCommand("man", "Support"), BotCommand("lang", "Language"), BotCommand("about", "About"), BotCommand("help", "Help"), BotCommand("cancel", "Cancel")])
    app.job_queue.run_repeating(job_tick, interval=config.TICK_SECONDS, first=15, name="tick")
    _dup = await arun(gget, "db_dup_warn")
    if _dup:
        await notify_super("⚠️ <b>ایندکس یکتای دیتابیس ساخته نشد</b>\n"
                            f"ردیف تکراری در: <code>{html.escape(', '.join(str(x) for x in _dup))}</code>\n"
                            "محافظت از ثبت کانال/منبع تکراری روی این نصب خاموش است. بررسی دستی:\n"
                            "<code>SELECT chat_id, COUNT(*) FROM channels GROUP BY chat_id HAVING COUNT(*) >1;</code>\n"
                            "<code>SELECT channel_id, url, COUNT(*) FROM sources GROUP BY channel_id, url HAVING COUNT(*) >1;</code>")
    log_event("INFO", f"ربات @{me.username} راه‌اندازی شد")
    extra = []
    if config.REFERRAL_ENABLED:
        from newsbot.core_referrals import referral_cfg   # REF-3: درصد زندهٔ پنل (نه فقط پیش‌فرض env)
        extra.append(f"referral {referral_cfg()[0]:g}%")
    if config.MANDATORY_CHANNEL_ID or config.MANDATORY_CHANNEL_USERNAME: extra.append("mandatory-channel ON")
    await notify_super(f"🚀 @{me.username} راه‌اندازی شد · 🤖 مدل‌های فعال {len(await arun(list_models, True))} · backend {config.DB_BACKEND} · CF KV {'✅' if config.CF_ENABLED else '❌'} · تیک هر {config.TICK_SECONDS}s" + (f" · {' · '.join(extra)}" if extra else ""))
    app.bot_data["_watchdog_task"] = watchdog.start()
    watchdog.notify_ready()


async def post_shutdown(app: Application):
    watchdog.notify_stopping()
    _wd = app.bot_data.pop("_watchdog_task", None) if app is not None else None
    if _wd is not None:
        _wd.cancel()
        try: await _wd
        except BaseException: pass
    _me = asyncio.current_task()
    # ARCH-06: همه‌ی تسک‌های نام‌دارِ طولانی (ارسال، عملیات کاربر، تفکر/چرخه) تا وسط کار رها نشوند
    pend = [t for t in asyncio.all_tasks() if t is not _me and t.get_name().split(":")[0] in ("publication", "user", "thinking")]
    if pend:
        for t in pend: t.cancel()
        _done, left = await asyncio.wait(pend, timeout=_SHUTDOWN_CANCEL_TIMEOUT)
        if left:   # تسک‌هایی که لغو را قورت دادند: فرصت دوم کوتاه؛ بعد رها می‌شوند (پروسه در حال خروج است)
            for t in left: t.cancel()
            _done2, left2 = await asyncio.wait(left, timeout=_SHUTDOWN_GRACE)
            if left2: log.warning("خاموشی: %d تسک پس از مهلت دوم هنوز تمام نشده و رها می‌شود.", len(left2))
        log.info("خاموشی: %d تسک در حال اجرا جمع شد.", len(pend))
    sw = ui_widgets._temp_sweeper
    if sw is not None and not sw.done():       # تسک پاک‌کننده‌ی پیام‌های موقت را تمیز جمع می‌کنیم
        sw.cancel()
        try: await sw
        except BaseException: pass
    from newsbot.notify import stop_super_worker   # ARCH-09: صف اعلان‌های سوپر تمیز جمع می‌شود
    await stop_super_worker()
    await close_http()
    close_db_pool()   # استخر تردهای دیتابیس آخر از همه بسته می‌شود تا نوشتن‌های settle قطع نشوند
    try:
        log_event("INFO", "ربات خاموش شد"); log.info("خاموشی تمیز انجام شد.")
    except Exception: pass


async def on_error(update, context):
    log.exception("خطا: %s", context.error)
    try:
        log_event("ERROR", f"unhandled: {type(context.error).__name__}: {context.error}",
                  exc_info=True, error_class=type(context.error).__name__)
    except Exception:
        log.error("[ON_ERROR_LOG_FAILED]")


def preflight(check=False):
    """اعتبارسنجی پیکربندی پیش از راه‌اندازی. همه‌ی ایرادها یک‌جا گزارش می‌شوند تا کارِ اپراتور یک‌بار باشد.
    check=True (خودآزمایی `run.py --check`): فقط‌خواندنی — پوشه ساخته نمی‌شود و دیتابیس فقط بازرسی می‌شود (ARCH-04/C7، تصمیم ۱۳)."""
    bad, warn = [], []
    if not config.BOT_TOKEN:
        bad.append("BOT_TOKEN تنظیم نشده است → توکن BotFather را بگذارید (BOT_TOKEN=123456789:AA...)")
    elif not re.fullmatch(r"\d{6,}:[A-Za-z0-9_-]{30,}", config.BOT_TOKEN):
        warn.append("BOT_TOKEN قالب استاندارد ندارد (باید شبیه 123456789:AA... باشد)؛ برای سرور Bot API محلی نادیده بگیرید.")
    if not config.SUPER_ADMIN_IDS:
        bad.append("SUPER_ADMIN_IDS تنظیم نشده است → شناسه‌ی عددی تلگرام خودتان (SUPER_ADMIN_IDS=123456789)")
    if config.CONFIG_ERRORS:   # CFG-01/02/03 (فاز ۱۲): env نامعتبر با پیام روشن، پیش از هر crash
        bad.extend(config.CONFIG_ERRORS)
    folder = os.path.dirname(os.path.abspath(config.DB_FILE)) or "."
    if config.DB_BACKEND != "d1":
        if not os.path.isdir(folder):
            if check:
                warn.append(f"پوشه‌ی دیتابیس وجود ندارد ({folder})؛ خودآزمایی فقط‌خواندنی چیزی نمی‌سازد — اولین اجرای ربات می‌سازد.")
            else:
                try: os.makedirs(folder, exist_ok=True)
                except Exception as e: bad.append(f"پوشه‌ی دیتابیس ساخته نشد: {folder} ({e})")
        if os.path.isdir(folder) and not os.access(folder, os.W_OK):
            bad.append(f"پوشه‌ی دیتابیس قابل نوشتن نیست: {folder}")
        if os.path.exists(config.DB_FILE) and not os.access(config.DB_FILE, os.W_OK):
            bad.append(f"فایل دیتابیس قابل نوشتن نیست: {config.DB_FILE}")
        if check:
            bad.extend(readonly_check())
    else:
        # FIX v4: بک‌اند D1 بدون هر سه مقدار، در runtime با SystemExit دیرتر می‌ترکد؛ این‌جا زود و یک‌جا
        if not (config.CF_ACCOUNT_ID and config.CF_D1_DATABASE_ID and config.CF_D1_TOKEN):
            bad.append("DB_BACKEND=d1 به CF_ACCOUNT_ID و CF_D1_DATABASE_ID و CF_D1_TOKEN (یا CF_API_TOKEN) نیاز دارد")
    if not -12 <= config.DEFAULT_UTC_OFFSET <= 14:
        bad.append(f"DEFAULT_UTC_OFFSET بیرون از بازه‌ی ‎-12..14‎ است: {config.DEFAULT_UTC_OFFSET}")
    for nm, val, lo, hi in (("TICK_SECONDS", config.TICK_SECONDS, 10, 3600), ("AI_CONCURRENCY", config.AI_CONCURRENCY, 1, 32),
                            ("FETCH_CONCURRENCY", config.FETCH_CONCURRENCY, 1, 64), ("CYCLE_CONCURRENCY", config.CYCLE_CONCURRENCY, 1, 16),
                            ("MAX_CYCLES_PER_TICK", config.MAX_CYCLES_PER_TICK, 1, 64), ("AI_INPUT_TEXT_CHARS", config.AI_INPUT_TEXT_CHARS, 500, 200000),
                            ("MAX_ARTICLE_CHARS", config.MAX_ARTICLE_CHARS, 1000, 2000000), ("MAX_ARTICLE_PAGES", config.MAX_ARTICLE_PAGES, 1, 50), ("MIN_INTERVAL", config.MIN_INTERVAL, 1, 1440),
                            ("DATA_TTL_HOURS", config.DATA_TTL_HOURS, 1, 8760), ("MAX_DAILY_POSTS_CAP", config.MAX_DAILY_POSTS_CAP, 1, 10000),
                            ("DEEPLINK_TTL_DAYS", config.DEEPLINK_TTL_DAYS, 0, 3650), ("AI_MAX_CALLS_PER_ARTICLE", config.AI_MAX_CALLS_PER_ARTICLE, 1, 100),
                            ("AI_ARTICLE_TIME_BUDGET", config.AI_ARTICLE_TIME_BUDGET, 30, 1800), ("AI_MAX_MODEL_ATTEMPTS", config.AI_MAX_MODEL_ATTEMPTS, 1, 10),
                            ("AI_CONTENT_RETRY_MODELS", config.AI_CONTENT_RETRY_MODELS, 0, 5), ("AI_CACHE_TTL_DAYS", config.AI_CACHE_TTL_DAYS, 0, 365),
                            ("AI_DAILY_FREE", config.AI_DAILY_FREE, 1, 100000), ("AI_DAILY_PAID", config.AI_DAILY_PAID, 1, 1000000),
                            ("RETRY_AFTER_MAX", config.RETRY_AFTER_MAX, 5, 600), ("ABUSE_RATE_SEC", config.ABUSE_RATE_SEC, 0.0, 60.0),
                            ("RECOVER_EVERY_MIN", config.RECOVER_EVERY_MIN, 1, 1440), ("STALE_UPDATE_MAX_MIN", config.STALE_UPDATE_MAX_MIN, 5, 1440),
                            ("MAX_QUEUE_SIZE", config.MAX_QUEUE_SIZE, 1, 100000), ("PAY_STUCK_MIN", config.PAY_STUCK_MIN, 1, 1440),
                            ("AWAIT_TTL_SEC", config.AWAIT_TTL_SEC, 60, 86400)):
        if not lo <= val <= hi: bad.append(f"{nm} بیرون از بازه‌ی مجاز {lo}..{hi} است: {val}")
    if config.POST_LIMIT_MAX > config.CAPTION_LIMIT - 100:   # قاعده‌ی سخت: کپشن هرگز نباید پست را دو پیام کند
        bad.append(f"POST_LIMIT_MAX ({config.POST_LIMIT_MAX}) باید حداقل ۱۰۰ کاراکتر زیر سقف تلگرام ({config.CAPTION_LIMIT}) بماند")
    if config.MANDATORY_CHANNEL_ID and not config.MANDATORY_CHANNEL_ID.lstrip("-").isdigit():
        warn.append("MANDATORY_CHANNEL_ID عددی نیست؛ اگر یوزرنیم است آن را در MANDATORY_CHANNEL_USERNAME بگذارید.")
    filled_cf = sum(bool(x) for x in (config.CF_ACCOUNT_ID, config.CF_KV_NAMESPACE_ID, config.CF_API_TOKEN))
    if filled_cf and filled_cf != 3:
        warn.append("Cloudflare KV ناقص تنظیم شده (هر سه مقدار لازم است) → آینه‌ی «بیشتر» محلی می‌مانَد.")
    for w in warn: log.warning("پیکربندی: %s", w)
    if bad:
        raise SystemExit("⛔ راه‌اندازی متوقف شد — ایرادهای پیکربندی:\n" + "\n".join(f"  • {b}" for b in bad))
    log.info("پیکربندی سالم | backend=%s | دیتابیس=%s | منطقه=UTC+%s | ادمین کلان=%d", config.DB_BACKEND, config.DB_FILE, config.DEFAULT_UTC_OFFSET, len(config.SUPER_ADMIN_IDS))


def _acquire_single_instance_lock():
    """قفل تک‌نسخهٔ پروسه روی فایل دیتابیس — این ربات تک‌پروسه اجرا می‌شود.
    DB-20: قبلاً فقط fcntl (لینوکس) بود و روی ویندوز import fcntl خطای ImportError
    می‌داد که با except OSError گرفته نمی‌شد ⇒ run.py روی ویندوز در استارت crash می‌کرد.
    حالا: لینوکس flock، ویندوز msvcrt.locking؛ هر شکست قفل ⇒ پیام روشن و خروج."""
    global _db_lock
    if config.DB_BACKEND == "d1": return None
    f = open(config.DB_FILE + ".lock", "a+")
    try:
        try:
            import fcntl
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except ImportError:              # ویندوز
            import msvcrt
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        f.close()
        raise SystemExit(f"⛔ نسخه‌ی دیگری از ربات روی همین دیتابیس در حال اجراست ({config.DB_FILE}) — ابتدا آن را ببندید.")
    _db_lock = f
    return f


def build_telegram_request():
    """PH-07/NET-005: size the Bot API pool explicitly for concurrent uploads/UI traffic."""
    return HTTPXRequest(connection_pool_size=config.TELEGRAM_POOL_SIZE,
                        read_timeout=60, write_timeout=600, connect_timeout=30,
                        pool_timeout=30, media_write_timeout=600)


def main():
    global APP
    preflight()
    _acquire_single_instance_lock()
    init_core(); _bld = Application.builder().token(config.BOT_TOKEN)
    if config.API_BASE: _bld = _bld.base_url(config.API_BASE)          # سرور محلی Bot API (اختیاری) → پشتیبانی از فایل‌های تا ۲ گیگ
    _bld = _bld.request(build_telegram_request())
    APP = _bld.post_init(post_init).post_shutdown(post_shutdown).concurrent_updates(True).build()
    for cmd, fn in (("start", cmd_start), ("create", cmd_create), ("admin", cmd_admin), ("man", cmd_man), ("help", cmd_help), ("about", cmd_about), ("lang", cmd_lang), ("ref", cmd_ref), ("cancel", cmd_cancel), ("health", cmd_health)): APP.add_handler(CommandHandler(cmd, managed_command(fn)))
    APP.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.COMMAND, managed_command(cmd_unknown)))
    APP.add_handler(CallbackQueryHandler(on_callback)); APP.add_handler(MessageHandler(filters.ChatType.PRIVATE & ~filters.COMMAND, on_message)); APP.add_error_handler(on_error)
    # drop_pending_updates=False: پیام‌هایی که کاربر در زمان خاموشی فرستاده از دست نمی‌روند؛ کهنه‌ها با stale() رد می‌شوند.
    log.info("در حال اجرا…"); APP.run_polling(drop_pending_updates=False, allowed_updates=["message", "callback_query"])
