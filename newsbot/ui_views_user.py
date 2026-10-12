# -*- coding: utf-8 -*-
"""لایه‌ی کاربر: زبان، خانه، پلن‌ها (دودویی، قیمت پس از کلیک، کد تخفیف)، رسید پرداخت
و صفحه‌ی دعوت/ارجاع (NEW v4). منطق پول (proration/کیف پول) در core_plans است؛ این‌جا فقط نمایش است.
"""
from newsbot import config
from newsbot.logutils import log_event
from newsbot.timeutils import fmt_date
from newsbot.ui_widgets import notify_supers
from newsbot.ratelimit import rate_ok, rate_left
from newsbot.core_users import admin_limits, admin_offset, get_user, is_super, set_lang, user_lang
from newsbot.core_channels import relocalize_all
from newsbot.core_plans import get_plan, plan_txt, list_plans, assign_plan, upgrade_quote, _fmt_price
from newsbot.core_discounts import disc_valid, discount_price
from newsbot.core_payments import pay_pending_for
from newsbot.core_referrals import referral_stats, referral_link
from newsbot.ui_i18n import tr, gtext
from newsbot.ui_widgets import B, U, pairs, esc, render, popup, ask, L, uname
from newsbot.ui_views_admin import view_admin_home


async def view_lang(update, context): await render(update, context, tr("fa", "choose_lang"), [[B("🇮🇷 فارسی", "lang:fa"), B("🇬🇧 English", "lang:en")]])

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
    lang = L(update); u = update.effective_user
    kb = [[B(tr(lang, "plans_btn"), "u:plans")]]
    if config.REFERRAL_ENABLED: kb.append([B(tr(lang, "ref_btn"), "u:ref")])   # NEW v4
    await render(update, context, gtext("welcome", lang, name=esc(u.first_name)), kb)

async def view_referral(update, context):
    """NEW v4: صفحه‌ی دعوت — لینک یکتا، شمار دعوت‌شده‌ها و اعتبار کسب‌شده."""
    lang = L(update); uid = update.effective_user.id
    if not config.REFERRAL_ENABLED:
        await popup(update, context, tr(lang, "ref_off")); return await view_user_home(update, context)
    invited, earned = referral_stats(uid)
    text = (tr(lang, "ref_title", p=f"{config.REFERRAL_PERCENT:g}")
            + tr(lang, "ref_line", n=invited, c=_fmt_price(earned))
            + tr(lang, "ref_link", link=referral_link(uid)))
    await render(update, context, text, [[B(tr(lang, "back"), "home")]])

async def view_plans(update, context):
    uid = update.effective_user.id; lang = L(update); lim = admin_limits(uid); text = tr(lang, "plans_title") + "\n\n"
    if lim["plan_id"]: text += tr(lang, "plans_current", name=esc(plan_txt(lim["plan"], "name", lang)), until=tr(lang, "until", d=fmt_date(lim["expires"], admin_offset(uid))) if lim["expires"] else "") + "\n"
    if lim["next_plan"]: text += tr(lang, "plans_next", name=esc(plan_txt(lim["next_plan"], "name", lang))) + "\n"
    text += "\n" + tr(lang, "plans_pick")
    kb = pairs([B(f"{'🎁' if p['is_free'] else '⭐'} {plan_txt(p, 'name', lang)}", f"u:plan:{p['id']}") for p in list_plans()])
    kb.append([B(tr(lang, "back"), "home")]); await render(update, context, text, kb)

def _applied_disc(context, pid):
    code = (context.user_data.get("disc") or {}).get(str(pid)); d, why = disc_valid(code) if code else (None, "")
    return d

def _price_text(p, lang, d):
    price = plan_txt(p, "price", lang) or tr(lang, "free_word")
    return (discount_price(price, d["percent"]), price) if d else (price, None)

async def view_plan(update, context, pid):
    uid = update.effective_user.id; lang = L(update); p = get_plan(pid); u = get_user(uid); lim = admin_limits(uid)
    if not p: await popup(update, context, tr(lang, "plan_notfound")); return await view_plans(update, context)
    d = _applied_disc(context, pid); new_price, old_price = _price_text(p, lang, d)
    text = f"{'🎁' if p['is_free'] else '⭐'} <b>{esc(plan_txt(p, 'name', lang))}</b>\n{esc(plan_txt(p, 'description', lang))}\n\n" + tr(lang, "plan_details", days=p["days"], posts=p["daily_posts"], src=p["max_sources"], ch=p["max_channels"], tests=p["daily_tests"], price=esc(new_price))
    if d: text += "\n" + tr(lang, "plan_disc_line", old=esc(old_price), new=esc(new_price), code=esc(d["code"]), p=d["percent"])
    kb = []
    if p["is_free"]: kb.append([B(tr(lang, "plan_free_used"), "noop") if u and u["free_used"] else B(tr(lang, "plan_free_btn"), f"u:free:{pid}")])
    else:
        text += "\n\n" + tr(lang, "plan_chain_note")
        from newsbot.core_plans import _price_num
        if _price_num(p) and lim["active"] and lim["plan_id"] not in (None, 0) and lim["plan_id"] != pid:
            text += "\n" + ("⚠️ ارتقا با کسر روزهای باقی‌ماندهٔ پلن فعلی محاسبه می‌شود؛ مبلغ دقیق در صفحهٔ پرداخت نمایش داده می‌شود." if lang == "fa" else "⚠️ Upgrade will be pro-rated against your remaining current plan time; the exact amount is shown on the payment page.")
        elif lim["active"] and lim["plan_id"] not in (None, 0) and lim["plan_id"] != pid:
            text += "\n" + ("⚠️ قیمت عددیِ پلن‌ها تنظیم نشده است؛ این خرید کاملِ پلن جدید محاسبه می‌شود (بدون کسر روزهای باقی‌مانده)." if lang == "fa" else "⚠️ No numeric price is set on the plans; this purchase is billed at the full new-plan price (no day credit).")
    if pay_pending_for(uid, pid): kb.append([B(tr(lang, "plan_pending_btn"), "noop")])
    else: kb.append([B(tr(lang, "plan_renew_btn" if lim["plan_id"] == pid and lim["active"] else "plan_req_btn"), f"u:req:{pid}"), B(tr(lang, "disc_remove") if d else tr(lang, "disc_btn"), f"u:disc{'x' if d else ''}:{pid}")])
    kb.append([B(tr(lang, "back"), "u:plans")]); await render(update, context, text, kb)

async def do_free(update, context, pid):
    uid = update.effective_user.id; lang = L(update); u = get_user(uid); p = get_plan(pid)
    if not p or not p["is_free"] or u["free_used"]: await popup(update, context, tr(lang, "free_once"), alert=True); return await view_plans(update, context)
    assign_plan(uid, pid); log_event("INFO", f"پلن رایگان فعال شد برای {uid}", uid); await popup(update, context, tr(lang, "free_done", name=plan_txt(p, "name", lang)), alert=True)
    await notify_supers(f"🎁 {esc(uname(u))} (<code>{uid}</code>) پلن رایگان را فعال کرد."); await view_admin_home(update, context)

async def do_request(update, context, pid):
    uid = update.effective_user.id; lang = L(update); p = get_plan(pid)
    if not is_super(uid) and not rate_ok(f"req:{uid}", config.ABUSE_RATE_SEC * 3):
        await popup(update, context, tr(lang, "test_wait", n=rate_left(f"req:{uid}", config.ABUSE_RATE_SEC * 3)), alert=True); return await view_plans(update, context)
    if not p: await popup(update, context, tr(lang, "plan_notfound")); return await view_plans(update, context)
    if p["is_free"]: return await do_free(update, context, pid)          # پلن رایگان مسیر خودش را دارد (قاعدهٔ یک‌بار دور زده نمی‌شود)
    if "active" in p.keys() and not p["active"]:
        await popup(update, context, tr(lang, "plan_notfound"), alert=True); return await view_plans(update, context)
    if pay_pending_for(uid, pid): await popup(update, context, tr(lang, "req_pending"), alert=True); return await view_plan(update, context, pid)
    lim = admin_limits(uid); cur = get_plan(lim["plan_id"]) if lim["plan_id"] else None
    d = _applied_disc(context, pid); new_price, _ = _price_text(p, lang, d)
    qt = upgrade_quote(uid, p, cur, d["percent"] if d else None); amt, rv = (qt["payable"] if qt["payable_num"] else None), qt["credit"]
    # single source of truth: amount (number) and display (text) computed exactly once here;
    # the same amount persists to pay_requests.final_price at receipt time (no later overwrite)
    if amt is not None:
        final_amount = str(amt)
        if qt["kind"] == "upgrade":
            b = [f"− اعتبار {plan_txt(cur, 'name', lang)} {_fmt_price(rv)}"]
            if qt["used_balance"]: b.append(f"− کیف پول {_fmt_price(qt['used_balance'])}")
            display = f"{_fmt_price(amt)}  (هزینه {plan_txt(p, 'name', lang)} برای {round(qt['rem_days'])} روز: {_fmt_price(qt['new_cost'])} · " + " · ".join(b) + ")"
        else:
            display = f"{_fmt_price(amt)}" + (f"  (از کیف پول −{_fmt_price(qt['used_balance'])})" if qt["used_balance"] else "")
    else:
        final_amount = new_price
        display = new_price
    context.user_data["await"] = {"kind": "receipt", "pid": pid, "back": f"u:plan:{pid}", "disc": d["code"] if d else None,
                                  "final": final_amount, "display": display, "upgrade_amt": amt, "pc": qt}
    await render(update, context, gtext("pay", lang, plan=esc(plan_txt(p, "name", lang)), price=esc(display)) + tr(lang, "receipt_hint"), [[B(tr(lang, "cancel"), "c:cancel")]])

async def view_receipt_ok(update, context): lang = L(update); await render(update, context, tr(lang, "receipt_ok"), [[B(tr(lang, "wait_btn"), "home")]], force_new=True)
