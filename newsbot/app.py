# -*- coding: utf-8 -*-
"""راه‌اندازی: preflight، init_core، post_init/post_shutdown، ثبت handlerها و main.
FIX v4:
  • _rate_load_persisted() در init_core ⇒ محدودیت‌های نرخ حساس پس از restart باقی می‌مانند
  • preflight بک‌اند D1 و MAX_QUEUE_SIZE و کانال اجباری را هم اعتبارسنجی می‌کند
  • post_init با set_bot() بطریِ تلگرام را به ui_widgets می‌دهد (بدون APP سراسری در لایه‌ی UI)
"""
import asyncio, html, os, re, sys
from telegram import BotCommand
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, ContextTypes, filters
from newsbot import config
from newsbot.db import db, q, gget, gset
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
                                 cmd_help, cmd_about, cmd_lang, cmd_cancel, cmd_unknown)
from newsbot.ui_support import on_message
from newsbot.ui_dispatch import on_callback
from newsbot.ui_views_super import cmd_health

APP: Application = None
_db_lock = None      # قفل فایلِ دیتابیس: دو نسخه‌ی هم‌زمان ربات ⇒ دو چرخه روی یک کانال و پست تکراری


def init_core():
    db(); seed_plans(); recover_publications(); _rate_load_persisted()   # FIX v4: بقای rate-limit حساس
    if gget("automation_enabled") is None: gset("automation_enabled", True)
    log.info(f"core v4 ready | backend={config.DB_BACKEND} | CF KV: {'on' if config.CF_ENABLED else 'local'} | super: {config.SUPER_ADMIN_IDS} | AI×{config.AI_CONCURRENCY} FETCH×{config.FETCH_CONCURRENCY} CYCLE×{config.CYCLE_CONCURRENCY}")


async def job_tick(context: ContextTypes.DEFAULT_TYPE):
    try: await scheduler_tick(context.bot)
    except Exception as e: log_event("ERROR", f"scheduler: {e}")


async def post_init(app: Application):
    me = await app.bot.get_me(); config.BOT_USERNAME = me.username
    config.NOTIFY_SUPER = notify_supers; config.NOTIFY_USER = notify_user_fn
    set_bot(app.bot)   # FIX v4: لایه‌ی UI بدون APP سراسری، بطری را از این‌جا می‌گیرد
    await app.bot.set_my_commands([BotCommand("start", "منوی اصلی"), BotCommand("create", "پنل مدیریت / پلن"), BotCommand("man", "پشتیبانی"), BotCommand("about", "درباره"), BotCommand("lang", "زبان"), BotCommand("help", "راهنما"), BotCommand("cancel", "لغو عملیات")], language_code="fa")
    await app.bot.set_my_commands([BotCommand("start", "Main menu"), BotCommand("create", "Admin panel / plan"), BotCommand("man", "Support"), BotCommand("about", "About"), BotCommand("lang", "Language"), BotCommand("help", "Help"), BotCommand("cancel", "Cancel action")])
    app.job_queue.run_repeating(job_tick, interval=config.TICK_SECONDS, first=15, name="tick")
    _dup = gget("db_dup_warn")
    if _dup:
        await notify_supers("⚠️ <b>ایندکس یکتای دیتابیس ساخته نشد</b>\n"
                            f"ردیف تکراری در: <code>{html.escape(', '.join(str(x) for x in _dup))}</code>\n"
                            "محافظت از ثبت کانال/منبع تکراری روی این نصب خاموش است. بررسی دستی:\n"
                            "<code>SELECT chat_id, COUNT(*) FROM channels GROUP BY chat_id HAVING COUNT(*) >1;</code>\n"
                            "<code>SELECT channel_id, url, COUNT(*) FROM sources GROUP BY channel_id, url HAVING COUNT(*) >1;</code>")
    log_event("INFO", f"ربات @{me.username} راه‌اندازی شد")
    extra = []
    if config.REFERRAL_ENABLED: extra.append(f"referral {config.REFERRAL_PERCENT:g}%")
    if config.MANDATORY_CHANNEL_ID or config.MANDATORY_CHANNEL_USERNAME: extra.append("mandatory-channel ON")
    await notify_supers(f"🚀 @{me.username} راه‌اندازی شد · 🤖 مدل‌های فعال {len(list_models(True))} · backend {config.DB_BACKEND} · CF KV {'✅' if config.CF_ENABLED else '❌'} · تیک هر {config.TICK_SECONDS}s" + (f" · {' · '.join(extra)}" if extra else ""))


async def post_shutdown(app: Application):
    _me = asyncio.current_task()
    pend = [t for t in asyncio.all_tasks() if t is not _me and t.get_name().split(":")[0] in ("publication", "user")]
    if pend:                                   # ارسال/عملیات در حال اجرا: لغو با مهلت تا وسط کار رها نشود
        for t in pend: t.cancel()
        await asyncio.wait(pend, timeout=8)
        log.info("خاموشی: %d تسک در حال اجرا جمع شد.", len(pend))
    sw = ui_widgets._temp_sweeper
    if sw is not None and not sw.done():       # تسک پاک‌کننده‌ی پیام‌های موقت را تمیز جمع می‌کنیم
        sw.cancel()
        try: await sw
        except BaseException: pass
    await close_http()
    try:
        log_event("INFO", "ربات خاموش شد"); log.info("خاموشی تمیز انجام شد.")
    except Exception: pass


async def on_error(update, context):
    log.exception("خطا: %s", context.error)
    try: log_event("ERROR", f"unhandled: {context.error}")
    except Exception: pass


def preflight():
    """اعتبارسنجی پیکربندی پیش از راه‌اندازی. همه‌ی ایرادها یک‌جا گزارش می‌شوند تا کارِ اپراتور یک‌بار باشد."""
    bad, warn = [], []
    if not config.BOT_TOKEN:
        bad.append("BOT_TOKEN تنظیم نشده است → توکن BotFather را بگذارید (BOT_TOKEN=123456789:AA...)")
    elif not re.fullmatch(r"\d{6,}:[A-Za-z0-9_-]{30,}", config.BOT_TOKEN):
        warn.append("BOT_TOKEN قالب استاندارد ندارد (باید شبیه 123456789:AA... باشد)؛ برای سرور Bot API محلی نادیده بگیرید.")
    if not config.SUPER_ADMIN_IDS:
        bad.append("SUPER_ADMIN_IDS تنظیم نشده است → شناسه‌ی عددی تلگرام خودتان (SUPER_ADMIN_IDS=123456789)")
    folder = os.path.dirname(os.path.abspath(config.DB_FILE)) or "."
    if config.DB_BACKEND != "d1":
        if not os.path.isdir(folder):
            try: os.makedirs(folder, exist_ok=True)
            except Exception as e: bad.append(f"پوشه‌ی دیتابیس ساخته نشد: {folder} ({e})")
        if os.path.isdir(folder) and not os.access(folder, os.W_OK):
            bad.append(f"پوشه‌ی دیتابیس قابل نوشتن نیست: {folder}")
        if os.path.exists(config.DB_FILE) and not os.access(config.DB_FILE, os.W_OK):
            bad.append(f"فایل دیتابیس قابل نوشتن نیست: {config.DB_FILE}")
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
                            ("RETRY_AFTER_MAX", config.RETRY_AFTER_MAX, 5, 600), ("ABUSE_RATE_SEC", config.ABUSE_RATE_SEC, 0.0, 60.0),
                            ("RECOVER_EVERY_MIN", config.RECOVER_EVERY_MIN, 1, 1440), ("STALE_UPDATE_MAX_MIN", config.STALE_UPDATE_MAX_MIN, 5, 1440),
                            ("MAX_QUEUE_SIZE", config.MAX_QUEUE_SIZE, 1, 100000), ("PAY_STUCK_MIN", config.PAY_STUCK_MIN, 1, 1440)):
        if not lo <= val <= hi: bad.append(f"{nm} بیرون از بازه‌ی مجاز {lo}..{hi} است: {val}")
    if config.POST_LIMIT_MAX > config.CAPTION_LIMIT - 100:   # قاعده‌ی سخت: کپشن هرگز نباید پست را دو پیام کند
        bad.append(f"POST_LIMIT_MAX ({config.POST_LIMIT_MAX}) باید حداقل ۱۰۰ کاراکتر زیر سقف تلگرام ({config.CAPTION_LIMIT}) بماند")
    if not 0 <= config.REFERRAL_PERCENT <= 50:
        bad.append(f"REFERRAL_PERCENT باید بین 0 و 50 باشد: {config.REFERRAL_PERCENT}")
    if config.MANDATORY_CHANNEL_ID and not config.MANDATORY_CHANNEL_ID.lstrip("-").isdigit():
        warn.append("MANDATORY_CHANNEL_ID عددی نیست؛ اگر یوزرنیم است آن را در MANDATORY_CHANNEL_USERNAME بگذارید.")
    filled_cf = sum(bool(x) for x in (config.CF_ACCOUNT_ID, config.CF_KV_NAMESPACE_ID, config.CF_API_TOKEN))
    if filled_cf and filled_cf != 3:
        warn.append("Cloudflare KV ناقص تنظیم شده (هر سه مقدار لازم است) → آینه‌ی «بیشتر» محلی می‌مانَد.")
    for w in warn: log.warning("پیکربندی: %s", w)
    if bad:
        raise SystemExit("⛔ راه‌اندازی متوقف شد — ایرادهای پیکربندی:\n" + "\n".join(f"  • {b}" for b in bad))
    log.info("پیکربندی سالم | backend=%s | دیتابیس=%s | منطقه=UTC+%s | ادمین کلان=%d", config.DB_BACKEND, config.DB_FILE, config.DEFAULT_UTC_OFFSET, len(config.SUPER_ADMIN_IDS))


def main():
    global APP, _db_lock
    preflight()
    if config.DB_BACKEND != "d1":
        try:
            import fcntl
            _db_lock = open(config.DB_FILE + ".lock", "a+")
            fcntl.flock(_db_lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)   # این ربات تک‌پروسه اجرا می‌شود
        except OSError:
            raise SystemExit(f"⛔ نسخه‌ی دیگری از ربات روی همین دیتابیس در حال اجراست ({config.DB_FILE}) — ابتدا آن را ببندید.")
    init_core(); _bld = Application.builder().token(config.BOT_TOKEN)
    if config.API_BASE: _bld = _bld.base_url(config.API_BASE)          # سرور محلی Bot API (اختیاری) → پشتیبانی از فایل‌های تا ۲ گیگ
    APP = _bld.post_init(post_init).post_shutdown(post_shutdown).concurrent_updates(True).build()
    for cmd, fn in (("start", cmd_start), ("create", cmd_create), ("admin", cmd_admin), ("man", cmd_man), ("help", cmd_help), ("about", cmd_about), ("lang", cmd_lang), ("cancel", cmd_cancel), ("health", cmd_health)): APP.add_handler(CommandHandler(cmd, managed_command(fn)))
    APP.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.COMMAND, managed_command(cmd_unknown)))
    APP.add_handler(CallbackQueryHandler(on_callback)); APP.add_handler(MessageHandler(filters.ChatType.PRIVATE & ~filters.COMMAND, on_message)); APP.add_error_handler(on_error)
    # drop_pending_updates=False: پیام‌هایی که کاربر در زمان خاموشی فرستاده از دست نمی‌روند؛ کهنه‌ها با stale() رد می‌شوند.
    log.info("در حال اجرا…"); APP.run_polling(drop_pending_updates=False, allowed_updates=["message", "callback_query"])
