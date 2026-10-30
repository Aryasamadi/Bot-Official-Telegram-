# -*- coding: utf-8 -*-
"""مسیردهی کال‌بک‌ها (dispatch) + on_callback با محدودکننده‌ی نرخ.
NEW v4:
  • اکشن «a:mand» برای بررسی دوباره‌ی عضویت کانال اجباری
  • دروازه‌ی mand_gate پیش از روشن‌کردن اتوماسیون (a:tog enabled) و پیش از انتشار دستی (a:pub)
  • کاربر عادی هرگز به این دروازه‌ها نمی‌رسد (کل بخش «a:» پشت can_admin است)
"""
import asyncio
from telegram import Update
from telegram.error import RetryAfter
from telegram.ext import ContextTypes
from newsbot import config
from newsbot.db import q, gget, gset, arun
from newsbot.timeutils import today_str, local_clock, TZ_ZONES, offset_now
from newsbot.ratelimit import rate_ok, rate_free, rate_left, rate_mark, rate_clear, global_rate_ok
from newsbot.concurrency import ch_lock
from newsbot.core_users import (admin_limits, admin_offset, usage_reset, is_super, user_lang,
                                can_admin, get_user, list_users)
from newsbot.core_channels import (get_settings, update_settings, channel_owned,
                                   publication_pending, reset_channel_queue, del_channel,
                                   mandatory_membership, mandatory_cfg, mandatory_set, list_channels)
from newsbot.core_sources import get_source, toggle_source, del_source, count_sources, set_source_api
from newsbot.core_articles import get_article, article_update, delete_article
from newsbot.core_plans import (assign_plan, revoke_plan, get_plan, plan_txt, list_plans,
                                update_plan, delete_plan)
from newsbot.core_discounts import disc_get, disc_update, disc_delete
from newsbot.core_payments import support_close, support_open
from newsbot.ai_models import get_model, update_model, delete_model, test_model
from newsbot.discover import probe_source
from newsbot.publish import publish_article
from newsbot.cycle import _pub_err
from newsbot.scheduler import report_text
from newsbot.logutils import log, log_event
from newsbot.text_format import fit_html, strip_tags
from newsbot import rich_send
from newsbot.ui_i18n import tr, TOGGLABLE, tz_city, FIELD_LABEL, SCHED_FIELDS
from newsbot.ui_widgets import (B, pairs, esc, render, popup, ask, wiz_start, operation_status, onoff,
                                operation_text, run_user_operation, run_model_test,
                                clear_interaction, L)
from newsbot.opctx import _current_operation
from newsbot.ui_views_user import view_lang, set_language, view_plans, view_plan, do_free, do_request
from newsbot.ui_views_admin import (view_admin_home, view_channel, channel_recheck, view_sources, view_source,
                                    view_content, view_cats, view_cat, view_crits, view_crit, hour_grid,
                                    view_sched, view_quiet, view_tz, view_queue, view_article, view_my_ai,
                                    view_my_ai_model, view_my_plan, run_test, mand_gate,
                                    PLAN_FIELDS, MODEL_FIELDS, fl)
from newsbot.ui_views_super import (view_super_home, view_health, view_s_plans, view_s_plan, view_s_discs,
                                    view_s_disc, view_s_users, view_s_user, view_s_models, view_s_model,
                                    view_s_pays, view_s_texts, view_s_mand, decide_pay, view_s_ref)


async def dispatch(update, context, data):
    """نقطهٔ ورودِ همهٔ مسیرهای callback.

    PH-05/SEC-009: ``callback_data`` می‌تواند دست‌ساز باشد (یا از نسخهٔ قدیمی بیاید) ⇒
    ``int(c)`` روی قطعهٔ نامعتبر ``ValueError`` می‌داد ⇒ یک stack trace در ژورنال نوشته
    می‌شد (سیلِ لاگ) و کاربر فقط یک پیامِ عمومی می‌گرفت. حالا این حالت **محترمانه** به
    «پیدا نشد» تبدیل می‌شود و در ژورنال فقط یک هشدارِ کوتاه می‌ماند."""
    try:
        return await _dispatch(update, context, data)
    except ValueError:
        log_event("WARN", f"callback با قطعهٔ نامعتبر: {str(data)[:40]}")
        try:
            await popup(update, context, tr(L(update), "notfound"))
        except Exception: pass
        return await go_home_safe(update, context)


async def _dispatch(update, context, data):
    uid = update.effective_user.id; lang = L(update); p = data.split(":"); a = p[0]; b = p[1] if len(p) > 1 else ""; c = p[2] if len(p) > 2 else ""; d = p[3] if len(p) > 3 else ""; ud = context.user_data
    if data == "noop": return
    if a == "lang": return await set_language(update, context, b)
    if not user_lang(uid): return await view_lang(update, context)
    if data == "home": return await go_home_safe(update, context)
    if data == "c:cancel":
        st = ud.pop("await", None); await popup(update, context, operation_text(lang, "cancelled"))
        return await dispatch(update, context, (st or {}).get("back", "home"))
    # ---------------- کاربر
    if a == "u":
        if b == "plans": return await view_plans(update, context)
        if b == "plan": return await view_plan(update, context, int(c))
        if b == "free": return await do_free(update, context, int(c))
        if b == "req": return await do_request(update, context, int(c))
        if b == "ref":   # NEW v4: صفحه‌ی دعوت/ارجاع
            from newsbot.ui_views_user import view_referral
            return await view_referral(update, context)
        if b == "disc": return await ask(update, context, tr(lang, "disc_prompt"), "disc", f"u:plan:{c}", pid=int(c))
        if b == "discx": ud.setdefault("disc", {}).pop(c, None); return await view_plan(update, context, int(c))
        if b == "man_close": support_close(uid); return await render(update, context, tr(lang, "sup_closed"), [[B(tr(lang, "home"), "home")]])
        # PH-05/UI-014: دکمه‌های صفحهٔ خانه (پشتیبانی/راهنما/زبان) — پیش از این فقط با فرمان
        if b == "man":
            support_open(uid)
            return await render(update, context, tr(lang, "sup_open"), [[B(tr(lang, "sup_close"), "u:man_close")]], force_new=True)
        if b == "help":
            from newsbot.ui_i18n import gtext
            return await render(update, context, gtext("help", lang), [[B(tr(lang, "home"), "home")]])
        if b == "lang":
            return await view_lang(update, context)     # PH-05/UI-024: فرمِ در جریان نابود نمی‌شود
        # PH-05/UI-003: دکمه‌هایی که «کاری نمی‌کنند» حالا یک پیامِ روشن می‌دهند
        if b == "pending": return await popup(update, context, tr(lang, "u_pending_note"))
        if b == "locked":  return await popup(update, context, tr(lang, "u_locked_note"))
        if b == "freeused": return await popup(update, context, tr(lang, "free_once"))
        return await go_home_safe(update, context)
    # ---------------- کانال اجباری (همهٔ کاربران، پیش از دروازهٔ ادمین)
    if a == "a" and b == "mand":
        okm, _j = await mandatory_membership(context.bot, uid, lang)
        if okm:
            await popup(update, context, tr(lang, "mand_ok"))
            return await go_home_safe(update, context)
        await popup(update, context, tr(lang, "mand_still"))
        return await mand_gate(update, context, uid, lang)
    # ---------------- مدیر میانی
    if a == "a":
        if not can_admin(uid): await popup(update, context, tr(lang, "no_admin")); return await view_plans(update, context)
        lim = admin_limits(uid)
        # عملیاتی که محتوا تولید/منتشر می‌کنند فقط با پلن فعال (مشاهده و تنظیمات آزاد می‌ماند)
        if b in ("test", "pub", "srca", "chadd") and not lim["active"]: await popup(update, context, tr(lang, "plan_inactive")); return await view_my_plan(update, context)
        if b == "home": return await view_admin_home(update, context)
        if b == "plan": return await view_my_plan(update, context)
        if b == "myai":
            cid = int(c) if c.lstrip("-").isdigit() else 0
            ud["myai_back"] = f"a:ch:{cid}" if cid and channel_owned(cid, uid) else "a:home"
            return await view_my_ai(update, context)
        if b == "myai_add": return await wiz_start(update, context, "my_ai_add", "a:myai")
        if b in ("myai_v", "myai_t", "myai_o", "myai_d", "myai_d2", "myai_e"):
            mid = int(c); m = get_model(mid)
            if not m or (m["owner_id"] != uid and not is_super(uid)): await popup(update, context, tr(lang, "notfound")); return await view_my_ai(update, context)
            if b == "myai_v": return await view_my_ai_model(update, context, mid)
            if b == "myai_o": update_model(mid, active=0 if m["active"] else 1, status="ok", fail_count=0); await popup(update, context, tr(lang, "s_model_off" if m["active"] else "s_model_on")); return await view_my_ai_model(update, context, mid)
            if b == "myai_d": return await _confirm(update, context, lang, tr(lang, "confirm_del_q", name=esc(m["name"])), f"a:myai_d2:{mid}", f"a:myai_v:{mid}")
            if b == "myai_d2":
                # PH-05/SEC-015: حذفِ مدلِ شخصی — همان قاعدهٔ «فقط مالک» که در نما و ویرایش
                # هست (سوپرادمین هم اجازهٔ حذفِ مدلِ شخصیِ دیگران را ندارد؛ حذفِ مدلِ
                # عمومی از مسیرِ s:model_d2 انجام می‌شود).
                m = get_model(mid)
                if not m or m["owner_id"] != uid:
                    await popup(update, context, tr(lang, "notfound")); return await view_my_ai(update, context)
                delete_model(mid)
                log_event("WARN", f"مدلِ شخصی حذف شد: model={mid}", uid)     # PH-05/UI-011
                await popup(update, context, tr(lang, "deleted")); return await view_my_ai(update, context)
            if b == "myai_e": return await ask(update, context, tr(lang, "s_field_prompt", f=fl(MODEL_FIELDS, d, lang)), "model_field", f"a:myai_v:{mid}", mid=mid, field=d, own=1)
            await run_model_test(update, context, mid, personal=True)
            return await view_my_ai_model(update, context, mid)
        if b == "chadd":
            if lim["max_channels"] is not None and len(list_channels(uid)) >= lim["max_channels"]: await popup(update, context, tr(lang, "limit_channels", n=lim["max_channels"])); return await view_admin_home(update, context)
            return await ask(update, context, tr(lang, "ch_add_prompt"), "ch_add", "a:home")
        if b in ("srcv", "srct", "srctb", "srcd", "srcd2", "srcr", "srcapi", "srcapix", "srcapix2"):
            sid = int(c); s = get_source(sid)
            if not s or (s["admin_id"] != uid and not is_super(uid)): await popup(update, context, tr(lang, "notfound")); return await view_admin_home(update, context)
            if b == "srcv": return await view_source(update, context, sid)
            if b == "srct": on = toggle_source(sid, s["admin_id"], "active"); await popup(update, context, tr(lang, "src_ch_on" if on else "src_ch_off")); return await view_source(update, context, sid)
            if b == "srctb": on = toggle_source(sid, s["admin_id"], "bot_active"); await popup(update, context, tr(lang, "src_bot_on" if on else "src_bot_off")); return await view_source(update, context, sid)
            if b == "srcapi": return await ask(update, context, tr(lang, "src_api_url"), "src_api_url", f"a:srcv:{sid}", sid=sid)
            if b == "srcapix": return await _confirm(update, context, lang, tr(lang, "confirm_api_del_q", name=esc(s["title"] or s["url"])[:60]), f"a:srcapix2:{sid}", f"a:srcv:{sid}")
            if b == "srcapix2":
                set_source_api(sid, "", "", "")
                log_event("WARN", f"کلیدِ API منبع پاک شد: source={sid}", uid)     # PH-05/UI-011
                await popup(update, context, tr(lang, "src_api_del")); return await view_source(update, context, sid)
            if b == "srcr":
                if not is_super(uid) and not rate_free(f"srcr:{sid}", 60): await popup(update, context, tr(lang, "test_wait", n=rate_left(f"srcr:{sid}", 60))); return await view_source(update, context, sid)
                await render(update, context, tr(lang, "src_checking"))
                ok, msg = await probe_source(s, lang)
                if ok: rate_mark(f"srcr:{sid}")
                else: rate_clear(f"srcr:{sid}")
                await popup(update, context, msg); return await view_source(update, context, sid)
            if b == "srcd": return await _confirm(update, context, lang, tr(lang, "confirm_del_q", name=esc(s["title"] or s["url"])[:60]), f"a:srcd2:{sid}", f"a:srcv:{sid}")
            del_source(sid, s["admin_id"])
            log_event("WARN", f"منبع حذف شد: source={sid}", uid)                # PH-05/UI-011
            await popup(update, context, tr(lang, "src_deleted")); return await view_sources(update, context, s["channel_id"])
        if b in ("art", "arte", "artd", "artd2", "artr", "artf", "pub"):
            aid = int(c); art = get_article(aid)
            if not art or (art["admin_id"] != uid and not is_super(uid)): await popup(update, context, tr(lang, "notfound")); return await view_admin_home(update, context)
            cid = art["channel_id"]
            if b in ("arte", "artd", "artr") and publication_pending(cid):
                return await popup(update, context, tr(lang, "test_busy"))
            if b == "artr" and art["status"] not in ("rejected", "failed", "skipped"):
                return await popup(update, context, tr(lang, "notfound"))
            if b == "art": return await view_article(update, context, aid)
            if b == "arte": return await ask(update, context, tr(lang, "art_edit_prompt"), "art_edit", f"a:art:{aid}", aid=aid)
            if b == "artd": return await _confirm(update, context, lang, tr(lang, "confirm_del_q", name=esc(strip_tags(art["title"] or "")[:60] or f"#{aid}")), f"a:artd2:{aid}", f"a:art:{aid}")
            if b == "artd2":
                delete_article(aid, art["admin_id"])
                log_event("WARN", f"مطلب حذف شد: article={aid}", uid)            # PH-05/UI-011
                await popup(update, context, tr(lang, "deleted")); return await view_queue(update, context, cid, "ready" if art["status"] in ("ready", "published") else "rejected")
            if b == "artr": article_update(aid, status="ready", reason=""); await popup(update, context, tr(lang, "art_moved")); return await view_article(update, context, aid)
            if b == "artf":
                if art["full_html"]:
                    fv = art["full_html"]
                    if len(fv) > config.BOT_FULL_MAX: fv, _ = fit_html(fv, config.BOT_FULL_MAX)
                    await rich_send.send_content(context.bot, uid, tr(lang, "full_head") + fv)
                return await view_article(update, context, aid)
            if b == "pub":
                rem = remaining_pub(uid, cid)
                if rem is not None and rem <= 0: await popup(update, context, tr(lang, "limit_posts", n=lim["daily_posts"])); return await view_queue(update, context, cid)
                if ch_lock(cid).locked(): await popup(update, context, tr(lang, "test_busy")); return await view_queue(update, context, cid)
                if await mand_gate(update, context, uid, lang): return   # NEW v4: انتشار دستی هم از دروازه‌ی کانال اجباری می‌گذرد
                await render(update, context, tr(lang, "publishing"))
                try:
                    ok, out = await publish_article(context.bot, aid)
                except Exception as e:
                    log.exception("manual publish failed article=%s channel=%s", aid, cid)
                    ok, out = False, f"send_failed:{str(e)[:120]}"
                await popup(update, context, tr(lang, "published_ok") if ok else tr(lang, "pub_failed", e=_pub_err(out, lang))); return await view_queue(update, context, cid)
        if b == "chrec":
            cid = int(c) if c.lstrip("-").isdigit() else 0
            return await channel_recheck(update, context, cid)
        cid = int(c) if c.lstrip("-").isdigit() else 0; ch = channel_owned(cid, uid)
        if not ch: await popup(update, context, tr(lang, "notfound")); return await view_admin_home(update, context)
        s = get_settings(cid)
        if b == "ch": return await view_channel(update, context, cid)
        if b == "rep": return await render(update, context, report_text(uid, cid, lang), [[B(tr(lang, "refresh"), f"a:rep:{cid}")], [B(tr(lang, "back"), f"a:ch:{cid}")]])
        if b == "test": return await run_test(update, context, cid)
        if b == "qreset": return await render(update, context, tr(lang, "queue_reset_q"), [[B(tr(lang, "yes"), f"a:qreset2:{cid}"), B(tr(lang, "no"), f"a:que:{cid}")]])
        if b in ("qreset2", "chdel2") and publication_pending(cid):
            return await popup(update, context, tr(lang, "test_busy"))
        if b == "qreset2":
            n = reset_channel_queue(cid); log_event_warn(uid, f"ریست صف کانال {ch['title']}")
            await popup(update, context, tr(lang, "queue_reset_ok", n=n)); return await view_queue(update, context, cid)
        if b == "chdel": return await render(update, context, tr(lang, "ch_del_q", title=esc(ch["title"])), [[B(tr(lang, "yes"), f"a:chdel2:{cid}"), B(tr(lang, "no"), f"a:ch:{cid}")]])
        if b == "chdel2":
            del_channel(cid, ch["admin_id"])
            log_event("WARN", f"کانال حذف شد: channel={cid}", uid)        # PH-05/UI-011
            await popup(update, context, tr(lang, "ch_deleted")); return await view_admin_home(update, context)
        if b == "tog":
            if d not in TOGGLABLE: return await popup(update, context, tr(lang, "notfound"))
            new = not s.get(d)
            if d == "enabled" and new and await mand_gate(update, context, uid, lang): return   # NEW v4: روشن‌کردن اتوماسیون بدون عضویت تأییدشده ممکن نیست
            update_settings(cid, **{d: new})
            if d == "enabled": await popup(update, context, tr(lang, "tog_enabled" if new else "tog_disabled")); return await view_channel(update, context, cid)
            else: await popup(update, context, f"{tr(lang, 'tog_' + d)}: {onoff(new)}\n{tr(lang, 'togd_' + d)}")
            return await (view_sched if d == "allow_undated" else view_content)(update, context, cid)
        if b == "set":
            if d not in FIELD_LABEL: return await popup(update, context, tr(lang, "notfound"))
            shown = (tr(lang, "unlimited") if s.get(d) is None else strip_tags(str(s.get(d)))) if d == "daily_posts_cap" else strip_tags(str(s.get(d)))
            extra = ""
            if d == "daily_posts_cap":
                capv = admin_limits(uid).get("daily_posts")
                if capv is not None: extra = "\n" + tr(lang, "cap_range", n=capv)
            return await ask(update, context, f"{FIELD_LABEL[d][1 if lang == 'en' else 0]}\n<code>{esc(shown)[:3500]}</code>{extra}", "field", f"a:sch:{cid}" if d in SCHED_FIELDS else f"a:con:{cid}", cid=cid, field=d)
        if b == "mode": new = "review" if s["mode"] == "auto" else "auto"; update_settings(cid, mode=new); await popup(update, context, tr(lang, "mode_set_auto" if new == "auto" else "mode_set_review")); return await view_sched(update, context, cid)
        if b == "sch": return await view_sched(update, context, cid)
        if b == "quiet": return await view_quiet(update, context, cid)
        if b == "qs": ud["qs"] = int(d); return await render(update, context, tr(lang, "quiet_pick_end", h=f"{int(d):02d}"), hour_grid(f"a:qe:{cid}") + [[B(tr(lang, "back"), f"a:quiet:{cid}")]])
        if b == "qe": a0 = ud.pop("qs", 0); update_settings(cid, quiet_start=a0, quiet_end=int(d)); await popup(update, context, tr(lang, "quiet_set", a=f"{a0:02d}", b=f"{int(d):02d}")); return await view_sched(update, context, cid)
        if b == "qoff": update_settings(cid, quiet_start=None, quiet_end=None); await popup(update, context, tr(lang, "quiet_cleared")); return await view_sched(update, context, cid)
        if b == "tz": return await view_tz(update, context, cid)
        if b == "tzs":
            if not d.isdigit() or int(d) >= len(TZ_ZONES): return await popup(update, context, tr(lang, "notfound"))
            iana = TZ_ZONES[int(d)][1]   # ARCH-10: نام IANA ذخیره می‌شود؛ آفست فقط snapshot نمایشی است
            old_day, new_day = today_str(s.get("tz") or s["utc_offset"]), today_str(iana)
            update_settings(cid, tz=iana, utc_offset=offset_now(iana))
            if old_day != new_day:      # جابه‌جایی مرزِ روز نباید سهمیهٔ امروز را صفر کند (سوءاستفاده و بی‌اعتباری شمارش)
                prev = await arun(q, "SELECT posts,tests FROM usage WHERE admin_id=? AND day=?", (uid, old_day), one=True)
                if prev: await arun(q, "INSERT INTO usage(admin_id,day,posts,tests) VALUES(?,?,?,?) ON CONFLICT(admin_id,day) DO UPDATE SET posts=MAX(posts,?), tests=MAX(tests,?)",
                                   (uid, new_day, prev["posts"], prev["tests"], prev["posts"], prev["tests"]), commit=True)
            await popup(update, context, tr(lang, "tz_set", city=tz_city(iana, lang), tz=off_label_safe(iana), loc=local_clock(iana))); return await view_sched(update, context, cid)
        if b == "con": return await view_content(update, context, cid)
        if b == "cat": return await view_cats(update, context, cid, int(d) if d.isdigit() else 0)
        if b == "cata": return await wiz_start(update, context, "cat_add", f"a:cat:{cid}", cid=cid)
        if b == "catv": return await view_cat(update, context, cid, int(d))
        if b == "cats": return await ask(update, context, tr(lang, "cat_style_prompt"), "cat_style", f"a:cat:{cid}", cid=cid, idx=int(d))
        if b == "catd":
            cats = s["categories"]; i = int(d)
            if 0 <= i < len(cats) and len(cats) > 1:
                c0 = cats[i]; nm = (f"{c0['emoji']} {c0['name']}" if isinstance(c0, dict) else str(c0))
                return await _confirm(update, context, lang, tr(lang, "confirm_del_q", name=esc(nm)[:70]), f"a:catd2:{cid}:{d}", f"a:cat:{cid}")
            await popup(update, context, tr(lang, "cat_min")); return await view_cats(update, context, cid)
        if b == "catd2":
            cats = s["categories"]; i = int(d)
            if 0 <= i < len(cats) and len(cats) > 1:
                cats.pop(i); update_settings(cid, categories=cats)
                log_event("WARN", f"دسته حذف شد: channel={cid} idx={i}", uid)   # PH-05/UI-011
                await popup(update, context, tr(lang, "deleted"))
            else: await popup(update, context, tr(lang, "cat_min"))
            return await view_cats(update, context, cid)
        if b == "cri": return await view_crits(update, context, cid, int(d) if d.isdigit() else 0)
        if b == "cria": return await wiz_start(update, context, "crit_add", f"a:cri:{cid}", cid=cid)
        if b == "criv": return await view_crit(update, context, cid, int(d))
        if b == "criw": return await ask(update, context, tr(lang, "crit_w_prompt"), "crit_weight", f"a:cri:{cid}", cid=cid, idx=int(d))
        if b == "crid":
            cr = s["criteria"]; i = int(d)
            if 0 <= i < len(cr) and len(cr) > 1:
                c0 = cr[i]; nm = c0.get("name", "") if isinstance(c0, dict) else str(c0)
                return await _confirm(update, context, lang, tr(lang, "confirm_del_q", name=esc(nm)[:70]), f"a:crid2:{cid}:{d}", f"a:cri:{cid}")
            await popup(update, context, tr(lang, "crit_min")); return await view_crits(update, context, cid)
        if b == "crid2":
            cr = s["criteria"]; i = int(d)
            if 0 <= i < len(cr) and len(cr) > 1:
                cr.pop(i); update_settings(cid, criteria=cr)
                log_event("WARN", f"معیار حذف شد: channel={cid} idx={i}", uid)   # PH-05/UI-011
                await popup(update, context, tr(lang, "deleted"))
            else: await popup(update, context, tr(lang, "crit_min"))
            return await view_crits(update, context, cid)
        if b == "src": return await view_sources(update, context, cid, int(d) if d.isdigit() else 0)
        if b == "srca":
            if lim["max_sources"] is not None and count_sources(uid) >= lim["max_sources"]: await popup(update, context, tr(lang, "limit_sources", n=lim["max_sources"])); return await view_sources(update, context, cid)
            return await ask(update, context, tr(lang, "src_add_prompt"), "src_add", f"a:src:{cid}", cid=cid)
        if b in ("que", "rej"): return await view_queue(update, context, cid, "ready" if b == "que" else "rejected", d)
        return await view_channel(update, context, cid)
    # ---------------- مدیر کلان
    if a == "s":
        if not is_super(uid): return
        if b == "home": return await view_super_home(update, context)
        if b == "report": return await render(update, context, report_text(None, None, lang), [[B(tr(lang, "refresh"), "s:report")], [B(tr(lang, "back"), "s:home")]])
        if b == "health": return await view_health(update, context)
        if b == "auto":
            # PH-05/UI-012: خاموش/روشن کردنِ فعالیتِ **کلِ ربات** با یک ضربه ⇒ تأییدِ دومرحله‌ای
            new = not await arun(gget, "automation_enabled", True)
            return await _confirm(update, context, lang, tr(lang, "confirm_reset_q", what=tr(lang, "s_what_auto")),
                                  f"s:auto2:{1 if new else 0}", "s:home")
        if b == "auto2":
            new = bool(int(c or 0))
            await arun(gset, "automation_enabled", new)
            log_event("WARN", f"فعالیتِ کلِ ربات {'روشن' if new else 'خاموش'} شد", uid)   # PH-05/UI-011
            await popup(update, context, tr(lang, "s_auto_on" if new else "s_auto_off")); return await view_super_home(update, context)
        if b == "ref": return await view_s_ref(update, context)   # REF-3 (فاز ۱۲): تنظیمات پاداش دعوت
        if b == "ref_p": return await ask(update, context, tr(lang, "s_ref_prompt_p"), "refpct", "s:ref")
        if b == "ref_c": return await ask(update, context, tr(lang, "s_ref_prompt_c"), "refcap", "s:ref")
        if b == "texts": return await view_s_texts(update, context)
        if b == "mand": return await view_s_mand(update, context)
        if b == "mand_t":
            if c not in config.LANGS: return await popup(update, context, tr(lang, "notfound"))
            cur = mandatory_cfg(c); mandatory_set(c, on=not cur.get("on")); await popup(update, context, tr(lang, "saved")); return await view_s_mand(update, context)
        if b == "mand_e":
            if c not in config.LANGS or d not in ("id", "user"): return await popup(update, context, tr(lang, "notfound"))
            return await ask(update, context, tr(lang, "s_field_prompt", f=tr(lang, "s_mand_id" if d == "id" else "s_mand_user")), "mand_field", "s:mand", lg=c, field=d)
        if b == "txt": return await ask(update, context, tr(lang, "s_text_prompt", k=c, lg=d), "gtext", "s:texts", key=c, lg=d)
        if b == "txtr": return await _confirm(update, context, lang, tr(lang, "confirm_txtr_q"), f"s:txtr2:{c}:{d}", "s:texts")
        if b == "txtr2":
            # PH-05/SEC-014: فقط کلیدهای معتبر و زبان‌های معتبر ⇒ نباید سطرهای بی‌ربط در
            # settings ساخته شوند (مسیرِ s:txt همین اعتبارسنجی را دارد).
            from newsbot.ui_i18n import TEXT_KEYS
            if c not in TEXT_KEYS or d not in config.LANGS:
                await popup(update, context, tr(lang, "notfound")); return await view_s_texts(update, context)
            gtext_set_wrap(c, d)
            log_event("WARN", f"متنِ ربات بازنشانی شد: key={c} lang={d}", uid)   # PH-05/UI-011
            await popup(update, context, tr(lang, "s_text_reset_done")); return await view_s_texts(update, context)
        if b == "bc": return await ask(update, context, tr(lang, "s_bc_prompt"), "bc", "s:home")
        if b == "bc_go":
            src = ud.pop("bc_src", None)
            if not src: return await popup(update, context, tr(lang, "notfound"))
            users = list_users(); ok = fail = 0; total = len(users)
            await operation_status(context, tr(lang, "s_bc_sending", n=total))
            for i, u in enumerate(users, 1):
                try: await context.bot.copy_message(u["id"], src[0], src[1]); ok += 1
                except RetryAfter as e: await asyncio.sleep(min(config.RETRY_AFTER_MAX, float(e.retry_after) + 1)); fail += 1
                except Exception: fail += 1
                if i % 15 == 0 or i == total: await operation_status(context, tr(lang, "s_bc_progress", i=i, n=total))   # UX-04
                await asyncio.sleep(.06)
            await popup(update, context, tr(lang, "s_bc_done", ok=ok, fail=fail)); return await view_super_home(update, context)
        if b == "plans": return await view_s_plans(update, context)
        if b == "plan": return await view_s_plan(update, context, int(c))
        if b == "plan_new": return await wiz_start(update, context, "plan_new", "s:plans")
        if b == "plan_e": return await ask(update, context, tr(lang, "s_field_prompt", f=fl(PLAN_FIELDS, d, lang)), "plan_field", f"s:plan:{c}", pid=int(c), field=d)
        if b == "plan_t":
            pl = get_plan(int(c))
            if not pl: await popup(update, context, tr(lang, "notfound")); return await view_s_plans(update, context)
            update_plan(int(c), active=0 if pl["active"] else 1); await popup(update, context, tr(lang, "saved")); return await view_s_plan(update, context, int(c))
        if b == "plan_free":
            # PH-05/UI-012 + FIN-020: (۱) تأییدِ دومرحله‌ای (قبلاً یک ضربه، کلِ قیمت‌گذاری را
            # عوض می‌کرد) و (۲) فقط **همان** پلن علامت می‌خورد — قبلاً is_freeِ همهٔ پلن‌ها صفر
            # می‌شد و اگر پلنِ دیگری انتخاب شده بود، هیچ پلنِ رایگانی باقی نمی‌ماند.
            pl = get_plan(int(c))
            if not pl: await popup(update, context, tr(lang, "notfound")); return await view_s_plans(update, context)
            return await _confirm(update, context, lang, tr(lang, "confirm_reset_q", what=tr(lang, "s_what_plan_free")),
                                  f"s:plan_free2:{c}", f"s:plan:{c}")
        if b == "plan_free2":
            pid2 = int(c)
            await arun(q, "UPDATE plans SET is_free=0 WHERE id<>?", (pid2,), commit=True)
            update_plan(pid2, is_free=1)
            log_event("WARN", f"پلنِ رایگان تغییر کرد: plan={pid2}", uid)      # PH-05/UI-011
            await popup(update, context, tr(lang, "s_plan_free_done")); return await view_s_plan(update, context, pid2)
        if b == "plan_d":
            pl = get_plan(int(c))
            if not pl: await popup(update, context, tr(lang, "notfound")); return await view_s_plans(update, context)
            return await _confirm(update, context, lang, tr(lang, "confirm_del_q", name=esc(plan_txt(pl, "name", lang))), f"s:plan_d2:{c}", f"s:plan:{c}")
        if b == "plan_d2":
            ok = delete_plan(int(c))
            log_event("WARN", f"پلن حذف شد: plan={c} ok={bool(ok)}", uid)      # PH-05/UI-011
            await popup(update, context, tr(lang, "s_plan_deleted" if ok else "s_plan_in_use"))
            return await view_s_plans(update, context)
        if b == "discs": return await view_s_discs(update, context)
        if b == "disc_new": return await wiz_start(update, context, "disc_add", "s:discs")
        if b == "disc": return await view_s_disc(update, context, c)
        if b == "disc_e": return await ask(update, context, tr(lang, "s_field_prompt", f=d), "disc_field", f"s:disc:{c}", code=c, field=d)
        if b == "disc_t": dd = disc_get(c); disc_update(c, active=0 if dd and dd["active"] else 1); await popup(update, context, tr(lang, "saved")); return await view_s_disc(update, context, c)
        if b == "disc_d":
            dd = disc_get(c)
            if not dd: await popup(update, context, tr(lang, "notfound")); return await view_s_discs(update, context)
            return await _confirm(update, context, lang, tr(lang, "confirm_del_q", name=esc(dd["code"])), f"s:disc_d2:{c}", f"s:disc:{c}")
        if b == "disc_d2":
            disc_delete(c)
            log_event("WARN", f"کد تخفیف حذف شد: code={c}", uid)                # PH-05/UI-011
            await popup(update, context, tr(lang, "deleted")); return await view_s_discs(update, context)
        if b == "users": return await view_s_users(update, context, int(c or 0))
        if b == "admins": return await view_s_users(update, context, int(c or 0), role="admin")
        if b == "user": return await view_s_user(update, context, int(c), int(d) if d.isdigit() else 0)
        tid = int(c) if c.lstrip("-").isdigit() else 0
        if b == "uplan": return await render(update, context, tr(lang, "s_plan_pick"), pairs([B(f"{plan_txt(pl, 'name', lang)} ({pl['days']}d)", f"s:uassign:{tid}:{pl['id']}") for pl in list_plans(False)]) + [[B(tr(lang, "back"), f"s:user:{tid}")]])
        if b == "uassign":
            # PH-05/UI-012: اعطای پلن (اعطای ارزش) ⇒ تأییدِ دومرحله‌ای
            pl = get_plan(int(d))
            if not pl: await popup(update, context, tr(lang, "notfound")); return await view_s_user(update, context, tid)
            return await _confirm(update, context, lang, tr(lang, "confirm_reset_q", what=tr(lang, "s_what_uassign")),
                                  f"s:uassign2:{tid}:{d}", f"s:user:{tid}")
        if b == "uassign2":
            exp, queued = assign_plan(tid, int(d)); pl = get_plan(int(d)); ul = user_lang(tid) or "fa"; ds = fmt_date_safe(exp, admin_offset(tid))
            log_event("WARN", f"پلن اعطا شد: user={tid} plan={d} queued={bool(queued)}", uid)   # PH-05/UI-011
            await notify_user_wrap(tid, tr(ul, "pay_approved", name=esc(plan_txt(pl, "name", ul)), when=tr(ul, "pay_when_queued" if queued else "pay_when_now", d=ds)))
            await popup(update, context, tr(lang, "s_assigned_q" if queued else "s_assigned", d=ds) + "\n" + tr(lang, "assign_role_note")); return await view_s_user(update, context, tid)
        if b == "urevoke": return await _confirm(update, context, lang, tr(lang, "confirm_revoke_q"), f"s:urevoke2:{tid}", f"s:user:{tid}")
        if b == "urevoke2":
            revoke_plan(tid)
            log_event("WARN", f"پلنِ کاربر لغو شد: user={tid}", uid)            # PH-05/UI-011
            await popup(update, context, tr(lang, "s_revoked")); return await view_s_user(update, context, tid)
        if b == "uban":
            u2 = get_user(tid)
            if not u2: await popup(update, context, tr(lang, "notfound")); return await view_s_users(update, context, 0)
            return await _confirm(update, context, lang, tr(lang, "confirm_unban_q" if u2["banned"] else "confirm_ban_q"), f"s:uban2:{tid}", f"s:user:{tid}")
        if b == "uban2":
            await arun(q, "UPDATE users SET banned=1-banned WHERE id=?", (tid,), commit=True)
            log_event("WARN", f"مسدودی/رفعِ مسدودیِ کاربر: user={tid} banned_now={bool(get_user(tid)['banned'])}", uid)   # PH-05/UI-011
            await popup(update, context, tr(lang, "s_banned" if get_user(tid)["banned"] else "s_unbanned")); return await view_s_user(update, context, tid)
        if b in ("ufree", "utests", "uposts"):
            what = tr(lang, "s_what_free" if b == "ufree" else ("s_what_tests" if b == "utests" else "s_what_posts"))
            return await _confirm(update, context, lang, tr(lang, "confirm_reset_q", what=what), f"s:{b}2:{tid}", f"s:user:{tid}")
        if b == "ufree2":
            await arun(q, "UPDATE users SET free_used=0 WHERE id=?", (tid,), commit=True)
            log_event("WARN", f"بازنشانیِ سهمیهٔ رایگان: user={tid}", uid)       # PH-05/UI-011
            await popup(update, context, tr(lang, "s_free_reset")); return await view_s_user(update, context, tid)
        if b == "utests2":
            usage_reset(tid, "tests")
            log_event("WARN", f"بازنشانیِ شمارندهٔ تست: user={tid}", uid)          # PH-05/UI-011
            await popup(update, context, tr(lang, "s_tests_reset")); return await view_s_user(update, context, tid)
        if b == "uposts2":
            usage_reset(tid, "posts")
            log_event("WARN", f"بازنشانیِ شمارندهٔ پست: user={tid}", uid)          # PH-05/UI-011
            await popup(update, context, tr(lang, "s_posts_reset")); return await view_s_user(update, context, tid)
        if b == "umsg": return await ask(update, context, tr(lang, "s_umsg_prompt"), "umsg", f"s:user:{tid}", target=tid)
        if b == "ureport":
            chs = list_channels_wrap(tid)
            if not chs: await popup(update, context, tr(lang, "s_no_channels")); return await view_s_user(update, context, tid)
            return await render(update, context, tr(lang, "s_pick_channel"), pairs([B(f"📣 {x['title'][:22]}", f"s:urep:{tid}:{x['id']}") for x in chs]) + [[B(tr(lang, "back"), f"s:user:{tid}")]])
        if b == "urep": return await render(update, context, report_text(tid, int(d), lang), [[B(tr(lang, "back"), f"s:ureport:{tid}")]])
        if b == "models": return await view_s_models(update, context)
        if b == "model": return await view_s_model(update, context, int(c))
        if b == "model_add": return await wiz_start(update, context, "model_add", "s:models")
        if b == "model_t":
            m = get_model(int(c))
            if not m: await popup(update, context, tr(lang, "notfound")); return await view_s_models(update, context)
            turning_off = m["active"]
            update_model(int(c), active=0 if m["active"] else 1, status="ok", fail_count=0)
            if turning_off and await arun(gget, "test_model_id") == m["id"]: await arun(gset, "test_model_id", None)
            await popup(update, context, tr(lang, "s_model_off" if m["active"] else "s_model_on")); return await view_s_model(update, context, int(c))
        if b == "model_d":
            m = get_model(int(c))
            if not m: await popup(update, context, tr(lang, "notfound")); return await view_s_models(update, context)
            return await _confirm(update, context, lang, tr(lang, "confirm_del_q", name=esc(m["name"])), f"s:model_d2:{c}", f"s:model:{c}")
        if b == "model_d2":
            mid = int(c); m = get_model(mid)
            if not m or m["owner_id"] is not None:
                await popup(update, context, tr(lang, "notfound")); return await view_s_models(update, context)
            if await arun(gget, "test_model_id") == mid: await arun(gset, "test_model_id", None)
            delete_model(mid)
            log_event("WARN", f"مدلِ عمومی حذف شد: model={mid}", uid)                # PH-05/UI-011
            await popup(update, context, tr(lang, "deleted")); return await view_s_models(update, context)
        if b == "model_e": return await ask(update, context, tr(lang, "s_field_prompt", f=fl(MODEL_FIELDS, d, lang)), "model_field", f"s:model:{c}", mid=int(c), field=d)
        if b == "model_test":
            await run_model_test(update, context, int(c))
            return await view_s_model(update, context, int(c))
        if b == "model_test_pick":
            m = get_model(int(c))
            if not m or m["owner_id"] is not None or not m["active"]:
                return await popup(update, context, tr(lang, "notfound"))
            selected = await arun(gget, "test_model_id") == m["id"]
            await arun(gset, "test_model_id", None if selected else m["id"])
            await popup(update, context, tr(lang, "s_tm_cleared" if selected else "s_tm_picked"))
            return await view_s_model(update, context, m["id"])
        if b == "models_test":
            ms = list_models_wrap()
            await operation_status(context, tr(lang, "s_model_testing"))
            res = []
            for i, m in enumerate(ms, 1):
                await operation_status(context, tr(lang, "s_model_progress", i=i, n=len(ms), name=esc(str(m["name"] or m["model"]))))   # UX-04
                ok, out, t = await test_model(m["id"]); res.append(f"{'✅' if ok else '❌'} {esc(m['name'])} ({t}s)" + ("" if ok else f": {esc(out[:60])}"))
            ud["notice"] = "\n".join(res) or "—"; return await view_s_models(update, context)
        if b == "pays": return await view_s_pays(update, context)
        if b == "pay_rc":
            from newsbot.core_payments import pay_get
            r = pay_get(int(c))
            if r and r["receipt_msg"]:
                try: await context.bot.copy_message(uid, r["receipt_chat"], r["receipt_msg"])
                except Exception:
                    log.exception("pay receipt copy failed rid=%s", int(c))
                    await popup(update, context, tr(lang, "s_pay_rc_err"))
            return await view_s_pays(update, context)
        if b == "pay_no": return await _confirm(update, context, lang, tr(lang, "confirm_pay_no_q"), f"s:pay_no2:{c}" + (f":{d}" if d else ""), "s:pays")
        if b == "pay_refund":
            # PH-01/FIN-013: بازگشتِ وجه به کیف پولِ مشتری (با رسید رسمی) — تأییدِ دومرحله‌ای
            return await _confirm(update, context, lang, tr(lang, "confirm_pay_refund_q", id=c),
                                 f"s:pay_refund2:{c}", "s:pays")
        if b == "pay_refund2":
            from newsbot.core_payments import refund_payment
            rid = int(c)
            ok = False
            try: ok = bool(refund_payment(rid))
            except Exception: log.exception("refund failed rid=%s", rid)
            await popup(update, context, tr(lang, "s_pay_refund_done" if ok else "notfound"))
            if ok: log_event("WARN", f"بازگشت وجه برای پرداخت #{rid}", update.effective_user.id)
            return await view_s_pays(update, context)
        if b in ("pay_ok", "pay_no2"): return await decide_pay(update, context, int(c), b == "pay_ok", d == "l")
        return await view_super_home(update, context)
    return await go_home_safe(update, context)


# ── کمک‌های کوچک (import دیرهنگام برای شکستن چرخه‌ها) ──
async def _confirm(update, context, lang, what, yes, no):
    """تأیید دوسطحی اقدامات مخرب (UX-05/UISEC-05): دکمه‌ی «بله» به اکشن «...2» و «خیر» به نمای قبلی می‌رود."""
    return await render(update, context, tr(lang, "confirm_q", what=what), [[B(tr(lang, "yes"), yes), B(tr(lang, "no"), no)]])

async def go_home_safe(update, context):
    from newsbot.ui_widgets import go_home
    await go_home(update, context)

def remaining_pub(uid, cid):
    from newsbot.core_users import remaining
    return remaining(uid, "posts", cid)

def log_event_warn(uid, msg):
    from newsbot.logutils import log_event
    log_event("WARN", msg, uid)

def off_label_safe(off):
    from newsbot.timeutils import off_label
    return off_label(off)

def fmt_date_safe(d, off):
    from newsbot.timeutils import fmt_date
    return fmt_date(d, off)

def gtext_set_wrap(k, lg):
    from newsbot.ui_i18n import gtext_set
    gtext_set(k, lg, "")

async def notify_user_wrap(uid, text):
    from newsbot.ui_widgets import notify_user_fn
    await notify_user_fn(uid, text)

def list_channels_wrap(tid):
    from newsbot.core_channels import list_channels
    return list_channels(tid)

def list_models_wrap():
    from newsbot.ai_models import list_models
    return list_models()


# PH-05/UI-004: مسیرهایی که کارشان طول می‌کشد ⇒ پاسخِ فوری به کاربر (اسپینرِ دکمه)
SLOW_ROUTES = ("s:bc_go", "s:models_test", "s:model_test", "a:srcr", "a:pub", "a:myai_t", "a:artf", "s:bc")


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    qy = update.callback_query; u = update.effective_user; context._callback_answered = False
    if not global_rate_ok(u.id):   # SEC-03: محدودیت سراسری per-user (کلیک‌های سیل‌آسا)
        try: await qy.answer(tr(user_lang(u.id) or "fa", "busy_click"))
        except Exception: pass
        return
    if qy.data != "c:cancel" and not rate_ok(f"cb:{u.id}", config.CB_RATE):
        try: await qy.answer(tr(user_lang(u.id) or "fa", "busy_click"))
        except Exception: pass
        return
    from newsbot.core_users import ensure_user
    ensure_user(u.id, u.username, u.full_name, premium=getattr(u, "is_premium", None))
    if get_user(u.id)["banned"] and not is_super(u.id): return await qy.answer(tr(L(update), "banned"), show_alert=True)
    data = qy.data or "noop"
    # PH-05/UI-004: مسیرهای کند باید **فوراً** پاسخ بگیرند؛ وگرنه اسپینرِ دکمه روی
    # موبایل دقیقه‌ها می‌ماند و کاربر چند بار می‌زند (و کار تکراری می‌سازد).
    if any(data.startswith(r) for r in SLOW_ROUTES):
        try: await qy.answer(tr(user_lang(u.id) or "fa", "preparing"), show_alert=False)
        except Exception: pass
    parts = data.split(":"); action = parts[1] if len(parts) > 1 else ""
    navigation = data in ("home", "c:cancel") or action in (
        "home", "plans", "plan", "myai", "myai_v", "srcv", "src", "art", "ch",
        "sch", "con", "quiet", "tz", "cat", "catv", "cri", "criv", "que", "rej",
        "models", "model", "users", "admins", "user", "texts", "discs", "disc",
        "pays", "report", "rep", "qreset", "man_close", "ref")
    back = (context.user_data.get("await") or {}).get("back", "home")
    channel = int(parts[2]) if len(parts) > 2 and parts[0] == "a" and action == "test" and parts[2].isdigit() else None
    keep = ("disc",) if parts[0] == "u" and action in ("plan", "req", "disc", "discx") else ("bc_src",) if action == "bc_go" else ("qs", "qs_channel") if action == "qe" else ()
    async def work():
        if data == "noop": return
        if data == "c:cancel":
            op = _current_operation.get()
            if op and not op.replaced_status:
                await popup(update, context, operation_text(L(update), "cancelled"))
            return await dispatch(update, context, back)
        # PH-05/UI-010: اگر این ضربه، فرمِ در حالِ تکمیلِ کاربر را دور می‌ریزد، **بی‌صدا**
        # نریزد ⇒ یک هشدار می‌دهد (وگرنه کاربر دوباره همه‌چیز را تایپ می‌کند).
        _st = context.user_data.get("await") or {}
        _will_drop = bool(_st) and _st.get("kind") != "receipt" and "await" not in keep
        clear_interaction(context, keep=keep)
        if _will_drop:
            await popup(update, context, tr(L(update), "form_cancelled"))
        # PH-03/UI-009: فرمِ رسیدِ پرداخت باز می‌ماند ⇒ مشتری باید بداند هنوز می‌تواند
        # رسید را بفرستد (پیش از این، فرم بی‌صدا می‌پرید و عکسِ رسید «ناشناخته» رد می‌شد).
        if (context.user_data.get("await") or {}).get("kind") == "receipt":
            await popup(update, context, tr(L(update), "receipt_still_pending"))
        return await dispatch(update, context, data)
    # فاز انتظار: اگر عملیات اصلی کاربر (تست/چرخه/انتشار) در حال اجراست، بهجای جانشینی
    # که کاربر را از وسط کار پرت میکند، یک پیام بزرگ میگوییم صبر کند یا /cancel بزند.
    if await _wait_for_running_operation(update, context, u.id):
        return
    await run_user_operation(update, context, f"callback:{data}", channel, work, replace=navigation, keep=keep)
    if not getattr(context, "_callback_answered", False):
        try: await qy.answer()
        except Exception: pass


async def _wait_for_running_operation(update, context, uid) -> bool:
    """عملیات در حال اجرا ⇒ پیام بزرگ «صبر کنید یا /cancel». خروجی True یعنی با کلیک کار داشتیم."""
    from newsbot.opctx import user_control
    from newsbot.ui_widgets import _operations, L, running_op_text
    lock = user_control(uid)
    if not lock.locked() or uid not in _operations:
        return False
    _txt = operation_text(L(update), "busy_wait") + "\n\n" + running_op_text(L(update), _operations[uid])
    qy = update.callback_query
    if qy:
        try:
            await qy.answer(_txt, show_alert=True)   # پاپ‌آپ بزرگ تا کاربر قطع نشود
            context._callback_answered = True
            return True
        except Exception: pass
    await popup(update, context, _txt)
    return True
