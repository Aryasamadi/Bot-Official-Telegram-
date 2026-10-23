# -*- coding: utf-8 -*-
"""زمان‌بند هوشمند: عادلانه (قدیمی‌ترین اجرا اول)، سقف چرخه در هر تیک،
بازیابی ژورنال انتشار + بازیابی پرداخت‌های گیرکرده (FIX v4)، اعلان‌های انقضا و گزارش‌ها.
"""
import asyncio, html, os, time
from datetime import timedelta
from newsbot import config
from newsbot.db import q, gget, gset, arun, arun_maint
from newsbot.logutils import log, log_event, metrics_text
from newsbot.timeutils import now_utc, now_iso, parse_dt, fmt_date, ago_text, off_label
from newsbot.notify import notify_user, notify_super
from newsbot.core_users import expiring_users, list_admins, count_users, admin_limits, usage_today, is_super, remaining, channel_posts_today
from newsbot.core_plans import get_plan, plan_txt, list_plans, recover_stuck_payments
from newsbot.core_channels import list_channels, get_settings, get_channel, in_quiet
from newsbot.core_sources import list_sources
from newsbot.core_articles import ready_count, count_articles, recover_publications
from newsbot.core_payments import pay_pending
from newsbot.core_discounts import disc_list
from newsbot.ai_models import list_models, probe_down_models, any_model_available, tokens_today
from newsbot.cycle import run_channel_cycle, flush_ready
from newsbot.ui_i18n import tr

_tick_lock = asyncio.Lock()

async def _plan_notifications():
    config.SCHED_NOTIF_FAILURES.set(True)
    try:
        for u, key, left in await arun(expiring_users):
            if u["next_plan_id"]: continue
            lang = u["lang"] or "fa"; p = await arun(get_plan, u["plan_id"]); name = html.escape(plan_txt(p, "name", lang)) if p else "—"
            # کلید در DB رزرو شده؛ اگر ارسال واقعاً ناموفق باشد، claim آزاد می‌شود تا تیک بعدی دوباره تلاش کند
            try:
                ok = await notify_user(u["id"], (f"⏳ پلن «{name}» حدود <b>{int(left)} ساعت</b> دیگر تمام می‌شود." if lang == "fa" else f"⏳ Plan “{name}” ends in about <b>{int(left)}h</b>."), [[("🔄 تمدید / ارتقا" if lang == "fa" else "🔄 Renew / Upgrade", "u:plans")]])
                if ok is False:
                    raise RuntimeError("user notification not delivered")
            except Exception:
                await arun(q, "UPDATE users SET remind_key=NULL WHERE id=? AND remind_key=?", (u["id"], key), commit=True)
        expired = await arun(q, "SELECT * FROM users WHERE plan_id IS NOT NULL AND plan_expires <=? AND next_plan_id IS NULL AND (remind_key IS NULL OR remind_key!='expired') AND banned=0", (now_iso(),))
        for u in expired:
            lang = u["lang"] or "fa"
            # الگوی «فقط بعد از موفقیت ثبت شود» (مثل rate_free/rate_mark): به‌جای ثبت remind_key پیش از ارسال،
            # تا وقتی ارسال موفق نشده کلید مصرف نمی‌شود و تیک بعدی دوباره تلاش می‌کند.
            try:
                ok = await notify_user(u["id"], ("🔴 <b>پلن شما تمام شد؛ انتشار خودکار متوقف است.</b>" if lang == "fa" else "🔴 <b>Your plan expired; automatic publishing is paused.</b>"), [[("🔄 تمدید / ارتقا" if lang == "fa" else "🔄 Renew / Upgrade", "u:plans")]])
                if ok is False:
                    raise RuntimeError("user notification not delivered")
            except Exception:
                continue   # ارسال ناموفق → کلید «expired» ثبت نشد تا تیک بعدی دوباره هشدار بدهد
            await arun(q, "UPDATE users SET remind_key='expired' WHERE id=?", (u["id"],), commit=True)
    finally:
        config.SCHED_NOTIF_FAILURES.set(False)

async def _report_published(uid, ch, s, res):
    """چرخه‌ی خودکار منتشر کرد → یک پیام کوتاه با نام منبعِ هر پست (یک پیام برای کل چرخه، نه برای هر پست)."""
    lang = s["ui_lang"]; fa = lang != "en"
    head = f"✅ <b>{'منتشر شد' if fa else 'Published'}</b> · 📢 {html.escape(ch['title'])}\n"
    body = "".join(f"\n• {html.escape(t or '—')}" + (f"\n   {tr(lang, 'test_src', name=html.escape(sn))}" if sn else "") for t, sn, _ in res["pub"][:3])
    kb = []
    if res["pub"] and res["pub"][0][2]: kb.append([("🔗 " + ("مشاهده در کانال" if fa else "View in channel"), res["pub"][0][2])])
    kb.append([("🛠 " + ("پنل کانال" if fa else "Channel panel"), f"a:ch:{ch['id']}")])
    await notify_user(uid, head + body, kb)

async def _scheduled(bot, uid, ch, s):
    """اجرای چرخهٔ زمانبندشده.

    نتیجه در تنظیمات همان کانال (last_result / last_diag) و در گزارش کانال ثبت میشود.
    اعلان جداگانه ارسال نمیشود: هر بار همان متن طولانی مدیر را شلوغ میکرد، در حالی که
    همان اطلاعات با دکمهٔ «گزارش» در دسترس است.
    """
    try:
        res = await run_channel_cycle(bot, uid, ch["id"])
        if res["published"]: await _report_published(uid, ch, s, res)
    except Exception as e:
        log_event("ERROR", f"چرخه {ch['title']}: {type(e).__name__}: {e}", uid,
                  exc_info=True, error_class=type(e).__name__)

def _recover_state():
    """بازیابی دوره‌ای (ژورنال انتشار + پرداخت گیرکرده + رول‌بک مزایای منقضی) — روی ترد دیتابیس، بیرون از حلقه."""
    gset("last_recover", time.time()); recover_publications(); recover_stuck_payments()   # FIX v4: پرداخت گیرکرده هم بازیابی می‌شود
    from newsbot.core_plans import rollback_expired_benefits
    rollback_expired_benefits()   # BIZ-03: نقش ادمینِ پلنِ منقضی پایین می‌آید


async def _renotify_payments(bot=None):
    """PH-01/FIN-021 — پرداخت‌های تأییدشده‌ای که مشتری هنوز مطلع نشده، دوباره اعلام می‌شوند.

    بدونِ این مسیر، یک خطای موقتِ تلگرام می‌توانست بگذارد مشتری پرداخت‌شده بی‌خبر بماند
    (پول دریافت شده، پلن فعال، ولی هیچ اعلامی به مشتری نشده).

    PH-03/OBS-013 — علاوه بر آن، **رسیدهایی که هیچ مدیر کلانی ندیده** دوباره اعلام
    می‌شوند (بدونِ این، یک رسیدِ پرداخت می‌توانست بی‌صدا در صف بماند و هرگز تأیید نشود)."""
    from newsbot.core_payments import (pay_unnotified, pay_mark_notified, pay_unnotified_super,
                                        pay_mark_super_notified, pay_mark_super_failed)
    from newsbot.core_plans import get_plan, plan_txt
    from newsbot.core_users import user_lang
    from newsbot.ui_widgets import notify_user_fn
    from newsbot.timeutils import fmt_date
    rows = await arun(pay_unnotified, 20)
    for r in rows:
        try:
            uid = r["user_id"]; lang = user_lang(uid) or "fa"
            p = await arun(get_plan, r["plan_id"])
            when = tr(lang, "pay_when_now", d=fmt_date(r["decided_at"], 0)) if r["decided_at"] else ""
            ok = await notify_user_fn(uid, tr(lang, "pay_approved", name=html.escape(plan_txt(p, "name", lang) if p else ""), when=when))
            if ok is False:
                raise RuntimeError("user notification not delivered")
            await arun(pay_mark_notified, r["id"])
        except Exception as e:
            log_event("WARN", f"payment re-notify failed rid={r['id']}: {type(e).__name__}: {e}")
    # ── OBS-013: رسیدِ «اعلام‌نشده به مدیر» ──────────────────────────────────────────
    if bot is None: return
    srows = await arun(pay_unnotified_super, 10)
    for r in srows:
        try:
            await _notify_super_about_receipt(bot, r)
            await arun(pay_mark_super_notified, r["id"])
        except Exception as e:
            await arun(pay_mark_super_failed, r["id"])
            log_event("WARN", f"super re-notify failed rid={r['id']}: {type(e).__name__}: {e}")


async def _notify_super_about_receipt(bot, r):
    """PH-03/OBS-013 — رسید را دوباره به مدیران کلان می‌فرستد (عکس اگر باشد، وگرنه متن)."""
    from telegram import InlineKeyboardMarkup
    from newsbot.core_payments import pay_receipt_caption
    from newsbot.core_plans import get_plan, plan_txt
    from newsbot.core_users import user_lang
    from newsbot.config import SUPER_ADMIN_IDS
    from newsbot.ui_widgets import B
    rid = r["id"]; p = await arun(get_plan, r["plan_id"])
    p_price = p["price"] if p is not None and "price" in p.keys() else ""
    price_txt = r["final_price"] or p_price or ""
    kb = InlineKeyboardMarkup([[B("✅ تأیید / Approve", f"s:pay_ok:{rid}"), B("❌ رد / Reject", f"s:pay_no:{rid}")]])
    last = None
    for sid in SUPER_ADMIN_IDS:
        sl = user_lang(sid) or "fa"
        header = pay_receipt_caption(rid, "", r["user_id"], plan_txt(p, "name", sl) if p else "",
                                     plan_txt(p, "price", sl) if p else "—", price_txt,
                                     r["discount"], r["note"], sl)
        try:
            if r["receipt_chat"] and r["receipt_msg"]:
                await bot.copy_message(sid, r["receipt_chat"], r["receipt_msg"], caption=header, parse_mode="HTML", reply_markup=kb)
            else:
                await bot.send_message(sid, header, parse_mode="HTML", reply_markup=kb)
            return True
        except Exception as e:
            last = e
    if last: raise last
    return False


async def _retry_alerts():
    """PH-03/OBS-011 — اعلان‌های نرسیدهٔ مدیر کلان را دوباره می‌فرستد.

    تحویل فقط وقتی «موفق» ثبت می‌شود که wrapper واقعاً دستِ‌کم یک گیرنده را تأیید کند؛
    پیام‌هایی که از سقفِ تلاش گذشته‌اند «مرده» می‌شوند (نه حذف) تا در سلامت دیده شوند."""
    from newsbot.notify import deliver_pending_alerts
    delivered, failed = await deliver_pending_alerts(10)
    if failed:
        log_event("WARN", f"{failed} alert(s) not delivered; will retry with backoff")
    return delivered, failed

async def _deadman_check():
    """PH-06/OBS-002 — «سبز اما بیکار»: اتوماسیون روشن و کانال هست، ولی انتشار موفق قدیمی است.

    هشدار حداکثر یک‌بار در هر پنجرهٔ DEADMAN_STALE_HOURS فرستاده می‌شود تا مدیر غرق
    پیام تکراری نشود. اگر هنوز هیچ انتشار موفقی ثبت نشده باشد (نصب تازه) هشدار نمی‌دهیم.
    """
    if not await arun(gget, "automation_enabled", True):
        return False
    row = await arun(q, "SELECT COUNT(*) c FROM channels", one=True)
    if not row or not row["c"]:
        return False
    last = await arun(gget, "last_successful_publish")
    if not last:
        return False
    try:
        age = (now_utc() - parse_dt(last)).total_seconds()
    except Exception:
        return False
    stale_sec = int(config.DEADMAN_STALE_HOURS) * 3600
    if age < stale_sec:
        return False
    prev = await arun(gget, "last_deadman_alert")
    if prev:
        try:
            if (now_utc() - parse_dt(prev)).total_seconds() < stale_sec:
                return False
        except Exception:
            pass
    await arun(gset, "last_deadman_alert", now_iso())
    hours = max(1, int(age // 3600))
    await notify_super(f"🟠 <b>اتوماسیون سبز است ولی انتشار ندارد</b>\n"
                       f"آخرین انتشار موفق {hours} ساعت پیش بود (آستانه: {config.DEADMAN_STALE_HOURS} ساعت).\n"
                       f"کانال‌ها، منابع و صف را بررسی کنید.")
    log_event("WARN", f"dead-man: no successful publish for {hours}h")
    return True


async def scheduler_tick(bot):
    if _tick_lock.locked(): return
    async with _tick_lock:
        await arun(gset, "heartbeat", now_iso()); await arun_maint(_do_cleanup)
        try:
            # اگر ژورنالی وسط کار گیر کند (تسک لغو شود، پروسه kill شود) تا restart بعدی بازیابی نمی‌شد.
            # با فاصله‌ی زمانی اجرا می‌شود، نه هر تیک؛ کوئری روی pub_journal با PK است ⇒ ارزان.
            if (time.time() - float(await arun(gget, "last_recover", 0))) >= config.RECOVER_EVERY_MIN * 60:
                await arun(_recover_state)
        except Exception as e:
            log_event("ERROR", f"publication recovery: {type(e).__name__}: {e}",
                      exc_info=True, error_class=type(e).__name__)
        try:
            await probe_down_models()
        except Exception as e:
            log_event("WARN", f"probe_down_models failed: {type(e).__name__}: {e}",
                      exc_info=True, error_class=type(e).__name__)
        try:
            await _renotify_payments(bot)
        except Exception as e:
            log_event("ERROR", f"payment re-notify failed: {type(e).__name__}: {e}",
                      exc_info=True, error_class=type(e).__name__)
        try:
            await _retry_alerts()
        except Exception as e:
            log_event("ERROR", f"alert re-notify failed: {type(e).__name__}: {e}",
                      exc_info=True, error_class=type(e).__name__)
        try:
            await _deadman_check()
        except Exception as e:
            log_event("ERROR", f"dead-man check failed: {type(e).__name__}: {e}",
                      exc_info=True, error_class=type(e).__name__)
        try:
            await _plan_notifications()
        except Exception as e:
            log_event("ERROR", f"plan notifications: {type(e).__name__}: {e}",
                      exc_info=True, error_class=type(e).__name__)
        if not await arun(gget, "automation_enabled", True): return
        due = []
        skipped = []
        publish_budget = config.MAX_PUBLISHES_PER_TICK
        for u in await arun(list_admins):
            uid = u["id"]
            if not (await arun(admin_limits, uid))["active"]:
                skipped.append((uid, "plan_expired")); continue
            for ch in await arun(list_channels, uid):
                s = await arun(get_settings, ch["id"])
                if not s["enabled"]:
                    skipped.append((uid, ch["id"], "disabled")); continue
                rem = await arun(remaining, uid, "posts", ch["id"])
                if rem is not None and rem <= 0:
                    skipped.append((uid, ch["id"], "quota")); continue
                if in_quiet(s) and s["mode"] == "auto":
                    skipped.append((uid, ch["id"], "quiet")); continue
                # PH-07/PERF-007: flush only eligible channels, with a bounded per-tick
                # publication budget; never start one unbounded drain per channel.
                if s["mode"] == "auto" and publish_budget > 0:
                    try:
                        sent = await flush_ready(bot, uid, ch["id"], max_n=min(2, publish_budget))
                        publish_budget -= max(0, sent)
                    except Exception as e: log_event("ERROR", f"flush {ch['title']}: {e}", uid)
                lr = parse_dt(s["last_run"])
                if not lr or (now_utc() - lr) >= timedelta(minutes=int(s["interval_minutes"])):
                    due.append((lr.isoformat() if lr else "", uid, ch, s))
                else:
                    skipped.append((uid, ch["id"], f"not_due (next in {int(s['interval_minutes']) - int((now_utc() - lr).total_seconds())//60}′)"))
        await arun(gset, "load_due", len(due))
        if skipped:   # خلاصه‌ی هر تیک نویز INFO بود؛ جزئیات فقط در سطح debug
            log.debug("scheduler skip-summary: %s", "; ".join(f"{u}/{c}/{r}" for u, c, r in skipped[:10]))
        if not due:
            log.debug("scheduler: nothing due this tick")
            return
        # چرخه هرگز متوقف نمی‌شود: اگر مدلی در دسترس نباشد، خودِ چرخه مدل‌ها را به‌ترتیب امتحان می‌کند و در پایان دلیل واقعی را می‌گوید
        if not any([await arun(any_model_available, uid) for _, uid, _, _ in due]): log_event("WARN", f"{len(due)} کانال با مدل‌های در حالت backoff اجرا می‌شوند (امتحان ترتیبی)")
        due.sort(key=lambda x: x[0]); batch = due[:config.MAX_CYCLES_PER_TICK]
        await arun(gset, "last_cycle_start", now_iso()); await asyncio.gather(*[_scheduled(bot, uid, ch, s) for _, uid, ch, s in batch]); await arun(gset, "last_cycle_end", now_iso())

def _do_cleanup():
    from newsbot.core_articles import cleanup
    try:
        cleanup()
    except Exception as e:
        log_event("ERROR", f"cleanup: {type(e).__name__}: {e}", exc_info=True, error_class=type(e).__name__)

def report_text(uid=None, cid=None, lang="fa"):
    from newsbot.core_channels import get_settings as _gs
    fa = lang != "en"; hb = gget("heartbeat"); hb_age = (now_utc() - parse_dt(hb)).total_seconds() if hb else 1e9; hb_icon = "🟢" if hb_age < config.TICK_SECONDS * 4 else "🔴"
    if cid is None:
        roles = {r["role"]: r["c"] for r in count_users()}; models = list_models(); ok = sum(1 for m in models if m["active"] and m["status"] == "ok"); down = sum(1 for m in models if m["active"] and m["status"] == "down")
        tk = tokens_today()   # PLB-03 (فاز ۷): متریک توکن/هزینهٔ AI از جدول ai_usage
        size = os.path.getsize(config.DB_FILE) / 1024 / 1024 if os.path.exists(config.DB_FILE) else 0; auto = gget("automation_enabled", True)
        # یک اسکن گروه‌بندی‌شده به‌جای ۵ کوئری شمارش جدا (گزارش مالک روی دیتابیس بزرگ محسوس بود)
        _a24 = {r["status"]: r["c"] for r in q("SELECT status, COUNT(*) c FROM articles WHERE created_at >=? GROUP BY status", ((now_utc() - timedelta(hours=24)).isoformat(),))}
        _a72r = q("SELECT COUNT(*) c FROM articles WHERE created_at >=? AND status='ready'", ((now_utc() - timedelta(hours=72)).isoformat(),), one=True)["c"]
        n = lambda st=None, h=24: sum(_a24.values()) if st is None else _a24.get(st, 0)
        nq = _a72r
        if fa: return (f"📊 <b>گزارش سیستم</b>\n\n"
                       f"{'🟢' if auto else '🔴'} اتوماسیون · 💓 {ago_text(hb, lang)} {hb_icon}\n"
                       f"⚖️ صف زمان‌بند {gget('load_due', 0)}\n"
                       f"👥 کاربران {sum(roles.values())} · مدیران {roles.get('admin', 0)} · کلان {roles.get('super', 0)}\n"
                       f"🧾 پلن‌ها {len(list_plans())} · 🛎 پرداخت معلق {len(pay_pending())} · 🎟 کد تخفیف {len(disc_list())}\n"
                       f"🤖 مدل‌ها ✅{ok} 🔴{down} از {len(models)}\n"
                       f"🧮 توکن AI ۲۴h: {tk['calls']} فراخوانی · ↑{tk['prompt']} ↓{tk['completion']}\n"
                       f"📢 کانال‌ها {q('SELECT COUNT(*) c FROM channels', one=True)['c']} · 🌐 منابع فعال {q('SELECT COUNT(*) c FROM sources WHERE active=1', one=True)['c']}\n"
                       f"📰 ۲۴h · 🔎 کشف {n()} · ✅ منتشر {n('published')} · ♻️ ردشده {n('rejected')}\n"
                       f"❌ ناموفق {n('failed')} · 📝 صف {nq}\n"
                       f"🔗 دیپ‌لینک {q('SELECT COUNT(*) c FROM deeplinks', one=True)['c']} · {'☁️ CF KV' if config.CF_ENABLED else '💾 محلی'} · 💾 {size:.2f} MB\n"
                       f"🕐 چرخه {fmt_date(gget('last_cycle_start'), config.DEFAULT_UTC_OFFSET, True)} ➜ {fmt_date(gget('last_cycle_end'), config.DEFAULT_UTC_OFFSET, True)}\n"
                       + metrics_text(lang))
        return (f"📊 <b>System report</b>\n\n"
                f"{'🟢' if auto else '🔴'} automation · 💓 {ago_text(hb, lang)} {hb_icon}\n"
                f"⚖️ scheduler queue {gget('load_due', 0)}\n"
                f"👥 users {sum(roles.values())} · admins {roles.get('admin', 0)} · super {roles.get('super', 0)}\n"
                f"🧾 plans {len(list_plans())} · 🛎 pending payments {len(pay_pending())} · 🎟 discount codes {len(disc_list())}\n"
                f"🤖 models ✅{ok} 🔴{down} of {len(models)}\n"
                f"🧮 AI tokens 24h: {tk['calls']} calls · ↑{tk['prompt']} ↓{tk['completion']}\n"
                f"📢 channels {q('SELECT COUNT(*) c FROM channels', one=True)['c']} · 🌐 active sources {q('SELECT COUNT(*) c FROM sources WHERE active=1', one=True)['c']}\n"
                f"📰 24h · 🔎 found {n()} · ✅ published {n('published')} · ♻️ rejected {n('rejected')}\n"
                f"❌ failed {n('failed')} · 📝 queue {nq}\n"
                f"🔗 deep links {q('SELECT COUNT(*) c FROM deeplinks', one=True)['c']} · {'☁️ CF KV' if config.CF_ENABLED else '💾 local'} · 💾 {size:.2f} MB\n"
                f"🕐 cycle {fmt_date(gget('last_cycle_start'), config.DEFAULT_UTC_OFFSET, True)} ➜ {fmt_date(gget('last_cycle_end'), config.DEFAULT_UTC_OFFSET, True)}\n"
                + metrics_text(lang))
    s = _gs(cid); ch = get_channel(cid); lim = admin_limits(uid); use = usage_today(uid); off = s.get("tz") or s["utc_offset"]; cap = lambda v: "∞" if v is None else v
    quiet = f"{s['quiet_start']:02d}→{s['quiet_end']:02d}" if s["quiet_start"] is not None and s["quiet_end"] is not None else ("—")
    pname = "∞" if is_super(uid) else (plan_txt(lim["plan"], "name", lang) or ("بدون پلن" if fa else "no plan")); until = f" · {fmt_date(lim['expires'], off)}" if lim["expires"] else ""
    c = lambda st=None: count_articles(cid=cid, hours=24, status=st)
    if fa: return (f"📊 <b>گزارش</b> · 📢 {html.escape(ch['title'])}\n\n"
                   f"{'🟢' if s['enabled'] else '🔴'} اتوماسیون: {'روشن' if s['enabled'] else 'خاموش'}\n"
                   f"⚙️ حالت: {'خودکار' if s['mode'] == 'auto' else 'بازبینی'}\n"
                   f"🧾 پلن: {html.escape(pname)}{until}\n\n"
                   f"🌐 منابع فعال: {len(list_sources(cid, True))}\n"
                   f"📥 در صف انتشار: {ready_count(cid)}\n"
                   f"📰 کشف ۲۴ ساعت: {c()}\n"
                   f"♻️ رد‌شده ۲۴ ساعت: {c('rejected')}\n"
                   f"❌ ناموفق ۲۴ ساعت: {c('failed')}\n\n"
                   f"📢 پست امروز: {use['posts']}/{cap(lim['daily_posts'])} · این کانال: {channel_posts_today(cid, off)}/{cap(s.get('daily_posts_cap'))}\n"
                   f"🧪 تست امروز: {use['tests']}/{cap(lim['daily_tests'])}\n\n"
                   f"⭐ حداقل امتیاز: {s['min_score']}\n"
                   f"🕰 بازهٔ خبر: {s['lookback_hours']} ساعت\n"
                   f"⏱ فاصلهٔ چرخه: {s['interval_minutes']} دقیقه\n"
                   f"🌙 ساعت خاموشی: {quiet}\n"
                   f"🌍 منطقه: {off_label(off)}\n\n"
                   f"🕐 آخرین چرخه: {fmt_date(s['last_run'], off, True)}\n"
                   f"💓 {ago_text(hb, lang)} {hb_icon}")
    return (f"📊 <b>Report</b> · 📢 {html.escape(ch['title'])}\n\n"
            f"{'🟢' if s['enabled'] else '🔴'} Automation: {'on' if s['enabled'] else 'off'}\n"
            f"⚙️ Mode: {'auto' if s['mode'] == 'auto' else 'review'}\n"
            f"🧾 Plan: {html.escape(pname)}{until}\n\n"
            f"🌐 Active sources: {len(list_sources(cid, True))}\n"
            f"📥 In publish queue: {ready_count(cid)}\n"
            f"📰 Found 24h: {c()}\n"
            f"♻️ Rejected 24h: {c('rejected')}\n"
            f"❌ Failed 24h: {c('failed')}\n\n"
            f"📢 Posts today: {use['posts']}/{cap(lim['daily_posts'])} · this channel: {channel_posts_today(cid, off)}/{cap(s.get('daily_posts_cap'))}\n"
            f"🧪 Tests today: {use['tests']}/{cap(lim['daily_tests'])}\n\n"
            f"⭐ Min score: {s['min_score']}\n"
            f"🕰 News window: {s['lookback_hours']}h\n"
            f"⏱ Cycle interval: {s['interval_minutes']} min\n"
            f"🌙 Quiet hours: {quiet}\n"
            f"🌍 Time zone: {off_label(off)}\n\n"
            f"🕐 Last cycle: {fmt_date(s['last_run'], off, True)}\n"
            f"💓 {ago_text(hb, lang)} {hb_icon}")
