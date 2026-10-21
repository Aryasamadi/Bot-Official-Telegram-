# -*- coding: utf-8 -*-
"""لایه‌ی کاربر: زبان، خانه، پلن‌ها (دودویی، قیمت پس از کلیک، کد تخفیف)، رسید پرداخت
و صفحه‌ی دعوت/ارجاع (NEW v4). منطق پول (proration/کیف پول) در core_plans است؛ این‌جا فقط نمایش است.
"""
from newsbot import config
import time
from newsbot.logutils import log_event, log
from newsbot.timeutils import fmt_date
from newsbot.ui_widgets import notify_supers
from newsbot.ratelimit import rate_ok, rate_left
from newsbot.core_users import admin_limits, admin_offset, get_user, is_super, set_lang
from newsbot.core_channels import relocalize_all, mandatory_configured, mandatory_membership
from newsbot.core_plans import get_plan, plan_txt, list_plans, assign_plan, upgrade_quote, plan_num, fmt_amt
from newsbot.core_discounts import disc_valid, discount_price
from newsbot.core_payments import pay_pending_for
from newsbot.core_referrals import referral_link, referral_cfg
from newsbot.ui_i18n import tr, gtext
from newsbot.ui_widgets import B, U, esc, render, popup, pairs, L, uname
from newsbot.ui_views_admin import view_admin_home


async def view_lang(update, context):
    """انتخاب زبان — با دکمهٔ بازگشت (PH-05/UI-024: پیش از این، کاربر برای خروج مجبور بود زبان انتخاب کند)."""
    await render(update, context, tr("fa", "choose_lang"),
                  [[B("🇮🇷 فارسی", "lang:fa"), B("🇬🇧 English", "lang:en")],
                   [B(tr(L(update), "back"), "home")]])

async def set_language(update, context, lang):
    """تغییر زبان صفر تا صد: زبانِ رابط، و در همه‌ی کانال‌ها پرامپت پیش‌فرض و متن‌های پیش‌فرضِ محتوا/گزارش هم به زبان تازه بازنویسی می‌شوند (فقط آن‌هایی که مدیر دستی عوض نکرده)."""
    uid = update.effective_user.id; set_lang(uid, lang)
    try: n = relocalize_all(uid, lang)
    except Exception as e: log_event("WARN", f"relocalize_all {uid}: {e}"); n = 0
    await popup(update, context, tr(lang, "lang_set") + (tr(lang, "lang_set_n", n=n) if n else "")); await go_home_safe(update, context)

async def go_home_safe(update, context):
    from newsbot.ui_widgets import go_home
    await go_home(update, context)

async def view_user_home(update, context):
    """صفحهٔ خانهٔ کاربر.

    PH-05/UI-014: پیش از این فقط **یک** دکمه (پلن‌ها) بود و مسیرِ دعوت (``u:ref``) از هیچ
    صفحه‌ای قابل‌دسترس نبود ⇒ مشتریِ غیرفنی نمی‌توانست پشتیبانی/دعوت/راهنما/زبان را
    پیدا کند (کاربر باید فرمان‌ها را از حفظ می‌دانست)."""
    lang = L(update); u = update.effective_user
    text = gtext("welcome", lang, name=esc(u.first_name))
    kb = [[B(tr(lang, "plans_btn"), "u:plans")],
          [B(tr(lang, "sup_btn"), "u:man"), B(tr(lang, "ref_btn"), "u:ref")],
          [B(tr(lang, "help_btn"), "u:help"), B(tr(lang, "lang_btn"), "u:lang")]]
    # کانال اجباریِ همین زبان: کاربر آن را می‌بیند، عضو می‌شود و عضویتش را بررسی می‌کند
    if mandatory_configured(lang):
        ok, join = await mandatory_membership(context.bot, u.id, lang)
        if not ok:
            kb.insert(0, ([U(tr(lang, "mand_join"), join)] if join else []) + [B(tr(lang, "mand_check"), "a:mand")])
            text += "\n\n" + tr(lang, "mand_user_notice")
    await render(update, context, text, kb)

def _referral_message(link, lang, percent):
    """متنِ صفحهٔ دعوت.

    PH-05/UI-015: این متن **سخت‌کد** بود و کلیدهای ``ref_title``/``ref_how``/``ref_link``/
    ``ref_line``/``ref_cap_line`` بی‌استفاده بودند ⇒ صفحهٔ دعوت قابل‌ویرایش و قابل‌محلی‌سازی
    نبود. حالا متن از همان کلیدهای i18n خوانده می‌شود (و درصد از تنظیمات زنده می‌آید)."""
    from newsbot.ui_i18n import TXT
    p = "" if percent is None else ("%g" % percent)
    def t(key, **kw):
        s = TXT[key][1 if lang == "en" else 0]
        try: return s.format(**kw)
        except Exception: return s
    reward = t("ref_how", p=p) if p else t("ref_how_nopct")
    return t("ref_title") + reward + "\n\n🔗 " + t("ref_link", link=link).strip()


async def view_referral(update, context):
    """صفحهٔ دعوت؛ متن کامل حتی در مسیر fallback و بدون وابستگی غیرضروری به آمار دیتابیس."""
    lang = L(update); uid = update.effective_user.id
    if not config.REFERRAL_ENABLED:
        await popup(update, context, tr(lang, "ref_off")); return await view_user_home(update, context)

    # آمار دعوت‌شده‌ها و اعتبار در متن جدید نمایش داده نمی‌شوند؛ خواندنشان نباید
    # مانع نمایش لینک و متن دعوت شود.
    link = referral_link(uid)
    try:
        pct, _ = referral_cfg()
    except Exception:
        log.warning("referral config unavailable uid=%s; displaying configured-rate notice", uid, exc_info=True)
        pct = None

    text = _referral_message(link, lang, pct)
    try:
        await render(update, context, text, [[B(tr(lang, "back"), "home")]])
    except Exception as e:
        log.warning("referral render failed uid=%s: %s", uid, e, exc_info=True)
        try:
            # مسیر جایگزین باید دقیقاً همان متن کامل را بفرستد، نه نسخهٔ قدیمی کوتاه.
            await context.bot.send_message(
                uid, text, parse_mode="HTML", disable_web_page_preview=True)
        except Exception:
            log.warning("referral fallback send failed uid=%s", uid, exc_info=True)

async def view_plans(update, context):
    uid = update.effective_user.id; lang = L(update); lim = admin_limits(uid); text = tr(lang, "plans_title") + "\n" + tr(lang, "plans_ref") + "\n\n"
    if lim["plan_id"]: text += tr(lang, "plans_current", name=esc(plan_txt(lim["plan"], "name", lang)), until=tr(lang, "until", d=fmt_date(lim["expires"], admin_offset(uid))) if lim["expires"] else "") + "\n"
    if lim["next_plan"]: text += tr(lang, "plans_next", name=esc(plan_txt(lim["next_plan"], "name", lang))) + "\n"
    text += "\n" + tr(lang, "plans_pick")
    kb = pairs([B(f"{'🎁' if p['is_free'] else '⭐'} {plan_txt(p, 'name', lang)}", f"u:plan:{p['id']}") for p in list_plans()])
    kb.append([B(tr(lang, "back"), "home")]); await render(update, context, text, kb)

def _applied_disc(context, pid):
    code = (context.user_data.get("disc") or {}).get(str(pid)); d, why = disc_valid(code) if code else (None, "")
    return d

def _price_text(p, lang, d):
    """متنِ قیمت برای پلن‌هایی که **تعرفهٔ عددی** ندارند (پلنِ مذاکره‌ای — Q-4c).

    تخفیف روی متنِ «توافقی» اعمال نمی‌شود (عددی در آن نیست) و متنِ نمایشی هرگز با
    مبلغِ دریافتی اشتباه گرفته نمی‌شود."""
    price = plan_txt(p, "price", lang) or tr(lang, "free_word")
    if d and plan_num(p, lang):
        return (discount_price(price, d["percent"]), price)
    return (price, None)

async def view_plan(update, context, pid):
    uid = update.effective_user.id; lang = L(update); p = get_plan(pid); u = get_user(uid); lim = admin_limits(uid)
    if not p: await popup(update, context, tr(lang, "plan_notfound")); return await view_plans(update, context)
    d = _applied_disc(context, pid); new_price, old_price = _price_text(p, lang, d)
    cur_plan = get_plan(lim["plan_id"]) if lim["plan_id"] else None
    # PH-01: قیمت همیشه از یک منبعِ واحد (upgrade_quote) می‌آید تا «نمایش‌داده‌شده» و
    # «دریافت‌شدنی» یکی باشند (FIN-004) و واحدِ پول هم همراهش باشد (Q-1c).
    qt = upgrade_quote(uid, p, cur_plan, d["percent"] if d else None, lang=lang)
    price_line = fmt_amt(qt["payable"], lang) if qt["payable_num"] else new_price
    text = f"{'🎁' if p['is_free'] else '⭐'} <b>{esc(plan_txt(p, 'name', lang))}</b>\n{esc(plan_txt(p, 'description', lang))}\n\n" + tr(lang, "plan_details", days=p["days"], posts=p["daily_posts"], src=p["max_sources"], ch=p["max_channels"], tests=p["daily_tests"], price=esc(price_line))
    if qt["payable_num"] and d: text += "\n" + tr(lang, "plan_disc_line", old=esc(old_price), new=esc(price_line), code=esc(d["code"]), p=d["percent"])
    kb = []; can_buy = True
    if p["is_free"]: kb.append([B(tr(lang, "plan_free_used"), f"u:freeused:{pid}") if u and u["free_used"] else B(tr(lang, "plan_free_btn"), f"u:free:{pid}")])
    else:
        u_active = lim["active"] and lim["plan_id"] not in (None, 0)
        if qt["payable_num"]:
            text += "\n" + tr(lang, "plan_num_line", v=esc(fmt_amt(qt["payable"], lang)))
            if qt["balance"]:
                text += "\n" + tr(lang, "plan_wallet_line", v=esc(fmt_amt(qt["balance"], lang)))
        if u_active and lim["plan_id"] == pid:
            text += "\n\n" + tr(lang, "plan_renew_note")           # تمدید: دورهٔ جدید پس از پلن فعلی
        elif u_active:
            # فاز ۱۴: صداقت پرداخت — نرخ روزانهٔ تعرفهٔ هم‌زبان تعیین‌کننده:
            # گران‌تر ⇒ ارتقای کسری · ارزان‌تر ⇒ نزول (فروش نداریم)
            text += "\n\n" + tr(lang, "plan_downgrade" if qt["blocked"] else "plan_upgrade_note")
        if not qt["payable_num"]:
            # Q-4c: پلنِ بدونِ تعرفهٔ عددی آنلاین خریداری نمی‌شود (قیمت توافقی است)
            can_buy = False
            text += "\n\n" + tr(lang, "plan_negotiable")
        elif qt["blocked"]:
            can_buy = False
    if pay_pending_for(uid, pid): kb.append([B(tr(lang, "plan_pending_btn"), f"u:pending:{pid}")])   # PH-05/UI-003
    elif can_buy: kb.append([B(tr(lang, "plan_renew_btn" if lim["plan_id"] == pid and lim["active"] else "plan_req_btn"), f"u:req:{pid}"), B(tr(lang, "disc_remove") if d else tr(lang, "disc_btn"), f"u:disc{'x' if d else ''}:{pid}")])
    else: kb.append([B(tr(lang, "plan_locked_btn"), f"u:locked:{pid}")])   # PH-05/UI-003
    kb.append([B(tr(lang, "back"), "u:plans")]); await render(update, context, text, kb)

async def do_free(update, context, pid):
    uid = update.effective_user.id; lang = L(update)
    from newsbot.concurrency import user_op_lock
    async with user_op_lock(uid):   # BIZ-05: یک عملیات پلن در لحظه برای هر کاربر
        u = get_user(uid); p = get_plan(pid)
        if not p or not p["is_free"] or u["free_used"]: await popup(update, context, tr(lang, "free_once")); return await view_plans(update, context)
        # PH-01/FIN-005: کاربرِ دارای پلنِ پولیِ فعال نباید با پلنِ رایگان اعتبارش را
        # از دست بدهد (پلنِ فعال عوض می‌شد و free_used هم مصرف می‌شد).
        if admin_limits(uid)["active"]:
            await popup(update, context, tr(lang, "plan_active_free")); return await view_plan(update, context, pid)
        # PHASE 4 (UISEC-10/SEC-04): claim اتمیک پیش از واگذاری — دو ضربهٔ هم‌زمان فقط یک‌بار برنده می‌شود
        from newsbot.core_plans import free_claim, free_release
        if not free_claim(uid): await popup(update, context, tr(lang, "free_once")); return await view_plans(update, context)
        try:
            exp, _ = assign_plan(uid, pid)
        except Exception:
            free_release(uid); raise
        if exp is None:
            free_release(uid); await popup(update, context, tr(lang, "notfound")); return await view_plans(update, context)
    log_event("INFO", f"پلن رایگان فعال شد برای {uid}", uid); await popup(update, context, tr(lang, "free_done", name=plan_txt(p, "name", lang)))
    await notify_supers(f"🎁 {esc(uname(u))} (<code>{uid}</code>) پلن رایگان را فعال کرد."); await view_admin_home(update, context)

async def do_request(update, context, pid):
    uid = update.effective_user.id; lang = L(update); p = get_plan(pid)
    if not is_super(uid) and not rate_ok(f"req:{uid}", config.ABUSE_RATE_SEC * 3):
        await popup(update, context, tr(lang, "test_wait", n=rate_left(f"req:{uid}", config.ABUSE_RATE_SEC * 3))); return await view_plans(update, context)
    if not p: await popup(update, context, tr(lang, "plan_notfound")); return await view_plans(update, context)
    if p["is_free"]: return await do_free(update, context, pid)          # پلن رایگان مسیر خودش را دارد (قاعدهٔ یک‌بار دور زده نمی‌شود)
    if "active" in p.keys() and not p["active"]:
        await popup(update, context, tr(lang, "plan_notfound")); return await view_plans(update, context)
    if pay_pending_for(uid, pid): await popup(update, context, tr(lang, "req_pending")); return await view_plan(update, context, pid)
    lim = admin_limits(uid); cur = get_plan(lim["plan_id"]) if lim["plan_id"] else None
    d = _applied_disc(context, pid); new_price, _ = _price_text(p, lang, d)
    qt = upgrade_quote(uid, p, cur, d["percent"] if d else None, lang=lang)
    if qt["blocked"]:
        await popup(update, context, tr(lang, "plan_downgrade")); return await view_plan(update, context, pid)
    if not qt["payable_num"]:
        # PH-01/Q-4c: پلنِ بدونِ تعرفهٔ عددی (قیمت توافقی) آنلاین خریداری نمی‌شود؛
        # به‌جای ساختنِ رسیدِ بدونِ مبلغ، مشتری به پشتیبانی هدایت می‌شود.
        await popup(update, context, tr(lang, "plan_negotiable")); return await view_plan(update, context, pid)
    # single source of truth: amount (number) and display (text) computed exactly once here;
    # the same amount persists to pay_requests.final_price at receipt time (no later overwrite)
    amt = qt["payable"]
    final_amount = str(amt)
    # display به‌صورت متن خام ساخته می‌شود؛ محل‌های استفاده خودشان esc می‌کنند (نه دوباره esc)
    if qt["kind"] == "upgrade" and cur:
        b = [tr(lang, "plan_credit_line", name=plan_txt(cur, "name", lang), v=fmt_amt(qt["credit"], lang))]
        if qt["used_balance"]: b.append(tr(lang, "plan_wallet_line", v=fmt_amt(qt["used_balance"], lang)))
        display = (tr(lang, "plan_upg_display", v=fmt_amt(amt, lang), plan=plan_txt(p, "name", lang),
                       days=round(qt["rem_days"]), cost=fmt_amt(qt["new_cost"], lang)) + "\n" + "\n".join(b))
    else:
        display = fmt_amt(amt, lang) + (("\n" + tr(lang, "plan_wallet_line", v=fmt_amt(qt["used_balance"], lang))) if qt["used_balance"] else "")
    context.user_data["await"] = {"kind": "receipt", "pid": pid, "back": f"u:plan:{pid}", "disc": d["code"] if d else None,
                                  "final": final_amount, "display": display, "upgrade_amt": amt, "pc": qt, "ts": time.time()}
    await render(update, context, gtext("pay", lang, plan=esc(plan_txt(p, "name", lang)), price=esc(display)) + tr(lang, "receipt_hint"), [[B(tr(lang, "cancel"), "c:cancel")]])

async def view_receipt_ok(update, context): lang = L(update); await render(update, context, tr(lang, "receipt_ok"), [[B(tr(lang, "wait_btn"), "home")]], force_new=True)
