# -*- coding: utf-8 -*-
"""دستورات، دکوریتور مدیریت‌شده (ضد سوءاستفاده) و دیپ‌لینک «بیشتر».
NEW v4: پیشوند ref_ در /start انتساب ارجاع را ثبت می‌کند (قبل از r_؛ «ref_» هرگز با «r_» collide نمی‌کند).
"""
import html
from telegram import InlineKeyboardMarkup

from newsbot import config
from newsbot.core_channels import channel_by_chat, post_url
from newsbot.logutils import log
from newsbot.ratelimit import rate_ok, global_rate_ok
from newsbot.core_users import ensure_user, is_super, user_lang, can_admin
from newsbot.core_payments import support_open
from newsbot.net_http import load_deeplink
from newsbot.text_format import fit_html, preview_text
from newsbot import rich_send
from newsbot.ui_i18n import tr, gtext
from newsbot.ui_widgets import B, U, render, popup, operation_text, run_user_operation, go_home
from newsbot.ui_views_user import view_lang, view_plans, view_referral, view_onboarding_welcome
from newsbot.ui_views_admin import view_admin_home
from newsbot.ui_views_super import view_super_home


def managed_command(handler):
    async def command(update, context):
        u = update.effective_user
        try:
            if u and not is_super(u.id) and get_user_banned(u.id): return
        except Exception: pass
        # ضد سوءاستفاده: هر کاربر برای هر دستور یک‌بار در ABUSE_RATE_SEC (قبلاً فقط کلیک کال‌بک محدود بود)
        if u and not is_super(u.id) and not rate_ok(f"cmd:{u.id}:{handler.__name__}", config.ABUSE_RATE_SEC): return
        if u and not global_rate_ok(u.id): return   # SEC-03: محدودیت سراسری per-user (سیل دستور)
        # /cancel و /ref دستورهای سادهاند: نباید داخل «عملیات کاربر» اجرا شوند.
        # اگر ببندیم، همان عملیاتی که خودشان میدهد شاملِ خودشان میشود و آن‌ها را کنسل میکند.
        if handler.__name__ in ("cmd_cancel", "cmd_ref"):
            return await handler(update, context)
        # /start باید عملیات در حال اجرا (تست/چرخه) را متوقف کند و بعد خانه را نشان دهد
        if handler.__name__ == "cmd_start":
            from newsbot.ui_widgets import cancel_user_operation
            await cancel_user_operation(u.id)
        return await run_user_operation(update, context, f"command:{handler.__name__}", None, lambda: handler(update, context), replace=True)
    command.__name__ = handler.__name__
    return command

def get_user_banned(uid):
    from newsbot.core_users import get_user
    u = get_user(uid); return bool(u and u["banned"])

async def _prep(update, context, keep_await=False):
    u = update.effective_user; ensure_user(u.id, u.username, u.full_name, premium=getattr(u, "is_premium", None))
    if not keep_await: context.user_data.pop("await", None)     # PH-05/UI-024: /lang فرم را دور نمی‌ریزد
    context.user_data["panel"] = None
    return user_lang(u.id)

async def send_deeplink(update, context, key):
    data = await load_deeplink(key); chat_id = update.effective_chat.id; lang = user_lang(update.effective_user.id) or "fa"
    if not isinstance(data, dict): data = None
    if not data: return await update.message.reply_text(tr(lang, "dl_notfound"))
    short = data.get("short") or ""; full = data.get("full"); media = data.get("media") or {}
    body = full if full and str(full).lower() != "null" else short
    if len(body) > config.BOT_FULL_MAX: body, _ = fit_html(body, config.BOT_FULL_MAX)
    src = data.get("src") if isinstance(data.get("src"), dict) else None
    post_chat = src.get("chat_id") if src else None
    message_id = src.get("message_id") if src else None
    ch = channel_by_chat(post_chat) if post_chat else None
    name = (ch["username"] if ch else "") or (src.get("username") if src else "") or ""
    url = post_url(post_chat, message_id, name)
    kb = InlineKeyboardMarkup([[U(tr(lang, "dl_back"), url)]]) if url else None
    if data.get("url") and data.get("show_source", True): body += f'\n\n<a href="{html.escape(data["url"], quote=True)}">{tr(lang, "dl_source")}</a>'
    if url and not name:
        # تنها وقتی که نشانی عمومی نداریم، کاربر را از محدودیتِ تلگرام آگاه میکنیم
        body += "\n\n" + tr(lang, "dl_private_note" if ch else "dl_unknown_note")
    if media.get("kind") in ("photo", "video", "animation"):
        try: await getattr(context.bot, f"send_{media['kind']}")(chat_id, media["url"], caption=preview_text(short, 100), parse_mode="HTML")
        except Exception as e: log.warning(f"deeplink media: {e}")
    await rich_send.send_content(context.bot, chat_id, body, reply_markup=kb,
                                 markup_last_only=True)

async def cmd_start(update, context):
    lang = await _prep(update, context)
    if context.args:
        arg = context.args[0]
        if arg.startswith("ref_"):   # انتساب ارجاع — idempotent؛ معرف از دعوت موفق باخبر می‌شود
            from newsbot.core_referrals import record_referral
            from newsbot.notify import notify_user
            try: ref = int(arg[4:])
            except (TypeError, ValueError): ref = 0
            if ref and record_referral(ref, update.effective_user.id):
                await notify_user(ref, tr(user_lang(ref) or "fa", "ref_joined"))
                # REF-3/تصمیم ۸ (فاز ۱۲): دعوت‌شده هم تأیید می‌گیرد (پاداش با هر خرید به معرف می‌رسد)
                await notify_user(update.effective_user.id, tr(user_lang(update.effective_user.id) or "fa", "ref_join_ok"))
        elif arg.startswith("r_"):
            if not rate_ok(f"dl:{update.effective_user.id}", config.CB_RATE): return   # جلوی تقویتِ درخواست بیرونی با /start تکراری را می‌گیرد
            return await send_deeplink(update, context, arg[2:])
    uid = update.effective_user.id
    from newsbot.core_channels import list_channels
    from newsbot.core_users import get_onboarding_state, set_onboarding_state
    # استثنای نقش (همانند go_home در ui_widgets): مدیر/سوپر حتی با زبان و بدون کانال
    # مستقیم به پنلِ خود میرود و نباید پشت خوشامدگویی onboarding گیر کند.
    fresh = (not is_super(uid) and not can_admin(uid)
             and not list_channels(uid) and get_onboarding_state(uid) != "done")
    if not lang:
        # فاز ۷: حالت قبل از صفحهٔ زبان نوشته میشود تا set_language → go_home آن را ببیند
        if fresh and get_onboarding_state(uid) != "welcome": set_onboarding_state(uid, "welcome")
        return await view_lang(update, context)
    if fresh and get_onboarding_state(uid) == "":
        set_onboarding_state(uid, "welcome")
    if fresh and get_onboarding_state(uid) == "welcome":
        return await view_onboarding_welcome(update, context)
    await go_home(update, context)

async def cmd_create(update, context):
    lang = await _prep(update, context)
    if not lang: return await view_lang(update, context)
    await (view_admin_home if can_admin(update.effective_user.id) else view_plans)(update, context)

async def cmd_admin(update, context):
    lang = await _prep(update, context)
    if not is_super(update.effective_user.id): return await go_home(update, context)
    if not lang: return await view_lang(update, context)
    await view_super_home(update, context)

async def cmd_man(update, context):
    lang = await _prep(update, context); uid = update.effective_user.id
    if not lang: return await view_lang(update, context)
    support_open(uid)   # آغاز/پایان نشست پشتیبانی به مدیر کلان اعلام نمی‌شود؛ فقط پیام‌های واقعی کاربر فرستاده می‌شوند
    await render(update, context, tr(lang, "sup_open"), [[B(tr(lang, "sup_close"), "u:man_close")]], force_new=True)

async def cmd_help(update, context):
    lang = await _prep(update, context)
    if not lang: return await view_lang(update, context)
    await render(update, context, gtext("help", lang), [[B(tr(lang, "home"), "home")]])

async def cmd_about(update, context):
    lang = await _prep(update, context)
    if not lang: return await view_lang(update, context)
    await render(update, context, gtext("about", lang), [[B(tr(lang, "home"), "home")]])

async def cmd_lang(update, context):
    """PH-05/UI-024: /lang فرمِ در حالِ تکمیل را **دور نمی‌ریزد** (keep_await) و انتخابِ زبان
    هم به کاربر اطلاع می‌دهد که متن‌های پیش‌فرضِ کانال‌ها به زبان تازه بازنویسی می‌شوند."""
    await _prep(update, context, keep_await=True); await view_lang(update, context)

async def cmd_cancel(update, context):
    """توقف واقعی عملیات جاری (تست/چرخه/انتشار) و بازگشت به خانه.

    اینجا نباید از ContextVar خوانده شود: هندلر دستور در تسک تازه اجرا می‌شود و
    `_current_operation` در آن None است. منبع حقیقت، دیکشنری سراسری `_operations` است.

    کاربرِ بی‌زبان به صفحهٔ زبان فرستاده نمی‌شود؛ وضعیت لغو برایش مهم‌تر است و
    اگر پPick زبان جایش را بگیرد پیام هرگز دیده نمی‌شود.
    """
    uid = update.effective_user.id
    lang = user_lang(uid) or "fa"
    from newsbot.ui_widgets import cancel_user_operation
    stopped = await cancel_user_operation(uid)
    await popup(update, context, operation_text(lang, "cancelled" if stopped else "nothing_to_cancel"))
    if not user_lang(uid): return          # بی‌زبان: پیام را دید و همین‌جا تمام
    await go_home(update, context)

async def cmd_ref(update, context):
    """بنر دعوت: لینک یکتا، تعداد دعوت‌شده‌ها و اعتبار کسب‌شده.

    برخلاف بقیهٔ دستورها، زبان‌نداشتن کاربر را به صفحهٔ زبان نمیفرستد: بنر با زبان
    پیشفرض فارسی نمایش داده میشود تا کاربر تازه هم بتواند لینک دعوتش را بگیرد.
    """
    await _prep(update, context)
    return await view_referral(update, context)

async def cmd_unknown(update, context):
    lang = await _prep(update, context)
    if not lang: return await view_lang(update, context)
    await go_home(update, context)
