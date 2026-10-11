# -*- coding: utf-8 -*-
"""زمان‌بند هوشمند: عادلانه (قدیمی‌ترین اجرا اول)، سقف چرخه در هر تیک،
بازیابی ژورنال انتشار + بازیابی پرداخت‌های گیرکرده (FIX v4)، اعلان‌های انقضا و گزارش‌ها.
"""
import asyncio, html, os, time
from datetime import timedelta
from newsbot import config
from newsbot.db import q, gget, gset
from newsbot.logutils import log, log_event, metrics_text
from newsbot.timeutils import now_utc, now_iso, parse_dt, fmt_date, ago_text, off_label
from newsbot.notify import notify_user
from newsbot.core_users import expiring_users, list_admins, count_users, admin_limits, usage_today, is_super
from newsbot.core_plans import get_plan, plan_txt, list_plans, recover_stuck_payments
from newsbot.core_channels import list_channels, get_settings, get_channel
from newsbot.core_sources import list_sources
from newsbot.core_articles import ready_count, count_articles, recover_publications
from newsbot.core_payments import pay_pending
from newsbot.core_discounts import disc_list
from newsbot.ai_models import list_models, probe_down_models, any_model_available
from newsbot.cycle import run_channel_cycle, flush_ready
from newsbot.ui_i18n import tr

_tick_lock = asyncio.Lock()

async def _plan_notifications():
    config.SCHED_NOTIF_FAILURES.set(True)
    try:
        for u, key, left in expiring_users():
            if u["next_plan_id"]: continue
            lang = u["lang"] or "fa"; p = get_plan(u["plan_id"]); name = html.escape(plan_txt(p, "name", lang)) if p else "—"
            # کلید در DB رزرو شده؛ اگر ارسال واقعاً ناموفق باشد، claim آزاد می‌شود تا تیک بعدی دوباره تلاش کند
            try:
                await notify_user(u["id"], (f"⏳ پلن «{name}» حدود <b>{int(left)} ساعت</b> دیگر تمام می‌شود." if lang == "fa" else f"⏳ Plan “{name}” ends in about <b>{int(left)}h</b>."), [[("🔄 تمدید / ارتقا" if lang == "fa" else "🔄 Renew / Upgrade", "u:plans")]])
            except Exception:
                q("UPDATE users SET remind_key=NULL WHERE id=? AND remind_key=?", (u["id"], key), commit=True)
        for u in q("SELECT * FROM users WHERE plan_id IS NOT NULL AND plan_expires <=? AND next_plan_id IS NULL AND (remind_key IS NULL OR remind_key!='expired') AND banned=0", (now_iso(),)):
            lang = u["lang"] or "fa"
            # الگوی «فقط بعد از موفقیت ثبت شود» (مثل rate_free/rate_mark): به‌جای ثبت remind_key پیش از ارسال،
            # تا وقتی ارسال موفق نشده کلید مصرف نمی‌شود و تیک بعدی دوباره تلاش می‌کند.
            try:
                await notify_user(u["id"], ("🔴 <b>پلن شما تمام شد؛ انتشار خودکار متوقف است.</b>" if lang == "fa" else "🔴 <b>Your plan expired; automatic publishing is paused.</b>"), [[("🔄 تمدید / ارتقا" if lang == "fa" else "🔄 Renew / Upgrade", "u:plans")]])
            except Exception:
                continue   # ارسال ناموفق → کلید «expired» ثبت نشد تا تیک بعدی دوباره هشدار بدهد
            q("UPDATE users SET remind_key='expired' WHERE id=?", (u["id"],), commit=True)
    finally:
        config.SCHED_NOTIF_FAILURES.set(False)

async def _report_failed_cycle(uid, ch, s, res):
    """چرخه‌ی زمان‌بندی‌شده چیزی منتشر نکرد → یک بار (تا ۲۴ ساعت برای همان الگو) به مدیر اطلاع بده."""
    D = res["diag"]; fp = "|".join(D.keys()); last = (s.get("last_notified_diag") or "").split("@"); last_fp, last_ts = last[0], (parse_dt(last[1]) if len(last) > 1 else None)
    if fp == last_fp and last_ts and (now_utc() - last_ts) < timedelta(hours=24): return
    update_settings(ch["id"], last_notified_diag=f"{fp}@{now_iso()}"); lang = s["ui_lang"]
    head = f"⚠️ <b>{'چرخه بدون انتشار' if lang == 'fa' else 'Cycle published nothing'}</b> · 📢 {html.escape(ch['title'])}\n\n"
    await notify_user(uid, head + html.escape(D.render(lang)), [[("🛠 پنل کانال" if lang == "fa" else "🛠 Channel panel", f"a:ch:{ch['id']}")]])

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
    try:
        res = await run_channel_cycle(bot, uid, ch["id"])
        if res["published"]: await _report_published(uid, ch, s, res)
        elif not res["queued"] and s["mode"] == "auto" and "busy" not in [k for k, _ in res["diag"].items]: await _report_failed_cycle(uid, ch, s, res)
    except Exception as e: log_event("ERROR", f"چرخه {ch['title']}: {e}", uid)

async def scheduler_tick(bot):
    if _tick_lock.locked(): return
    async with _tick_lock:
        gset("heartbeat", now_iso()); _do_cleanup()
        try:
            # اگر ژورنالی وسط کار گیر کند (تسک لغو شود، پروسه kill شود) تا restart بعدی بازیابی نمی‌شد.
            # با فاصله‌ی زمانی اجرا می‌شود، نه هر تیک؛ کوئری روی pub_journal با PK است ⇒ ارزان.
            if (time.time() - float(gget("last_recover", 0))) >= config.RECOVER_EVERY_MIN * 60:
                gset("last_recover", time.time()); recover_publications(); recover_stuck_payments()   # FIX v4: پرداخت گیرکرده هم بازیابی می‌شود
        except Exception as e: log_event("ERROR", f"publication recovery: {e}")
        try: await probe_down_models()
        except Exception as e: log_event("WARN", f"probe_down_models failed: {type(e).__name__}: {e}")
        try: await _plan_notifications()
        except Exception as e: log_event("ERROR", f"plan notifications: {e}")
        if not gget("automation_enabled", True): return
        due = []
        skipped = []
        for u in list_admins():
            uid = u["id"]
            if not admin_limits(uid)["active"]:
                skipped.append((uid, "plan_expired")); continue
            for ch in list_channels(uid):
                s = get_settings(ch["id"])
                if not s["enabled"]:
                    skipped.append((uid, ch["id"], "disabled")); continue
                try: await flush_ready(bot, uid, ch["id"])
                except Exception as e: log_event("ERROR", f"flush {ch['title']}: {e}", uid)
                rem = remaining(uid, "posts", ch["id"])
                if rem is not None and rem <= 0:
                    skipped.append((uid, ch["id"], "quota")); continue
                if in_quiet(s) and s["mode"] == "auto":
                    skipped.append((uid, ch["id"], "quiet")); continue
                lr = parse_dt(s["last_run"])
                if not lr or (now_utc() - lr) >= timedelta(minutes=int(s["interval_minutes"])):
                    due.append((lr.isoformat() if lr else "", uid, ch, s))
                else:
                    skipped.append((uid, ch["id"], f"not_due (next in {int(s['interval_minutes']) - int((now_utc() - lr).total_seconds())//60}′)"))
        gset("load_due", len(due))
        if skipped:   # خلاصه‌ی هر تیک نویز INFO بود؛ جزئیات فقط در سطح debug
            log.debug("scheduler skip-summary: %s", "; ".join(f"{u}/{c}/{r}" for u, c, r in skipped[:10]))
        if not due:
            log.debug("scheduler: nothing due this tick")
            return
        # چرخه هرگز متوقف نمی‌شود: اگر مدلی در دسترس نباشد، خودِ چرخه مدل‌ها را به‌ترتیب امتحان می‌کند و در پایان دلیل واقعی را می‌گوید
        if not any(any_model_available(uid) for _, uid, _, _ in due): log_event("WARN", f"{len(due)} کانال با مدل‌های در حالت backoff اجرا می‌شوند (امتحان ترتیبی)")
        due.sort(key=lambda x: x[0]); batch = due[:config.MAX_CYCLES_PER_TICK]
        gset("last_cycle_start", now_iso()); await asyncio.gather(*[_scheduled(bot, uid, ch, s) for _, uid, ch, s in batch]); gset("last_cycle_end", now_iso())

def _do_cleanup():
    from newsbot.core_articles import cleanup
    try: cleanup()
    except Exception as e: log_event("ERROR", f"cleanup: {e}")

def report_text(uid=None, cid=None, lang="fa"):
    from newsbot.core_channels import get_settings as _gs
    fa = lang != "en"; hb = gget("heartbeat"); hb_age = (now_utc() - parse_dt(hb)).total_seconds() if hb else 1e9; hb_icon = "🟢" if hb_age < config.TICK_SECONDS * 4 else "🔴"
    if cid is None:
        roles = {r["role"]: r["c"] for r in count_users()}; models = list_models(); ok = sum(1 for m in models if m["active"] and m["status"] == "ok"); down = sum(1 for m in models if m["active"] and m["status"] == "down")
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
                f"📢 channels {q('SELECT COUNT(*) c FROM channels', one=True)['c']} · 🌐 active sources {q('SELECT COUNT(*) c FROM sources WHERE active=1', one=True)['c']}\n"
                f"📰 24h · 🔎 found {n()} · ✅ published {n('published')} · ♻️ rejected {n('rejected')}\n"
                f"❌ failed {n('failed')} · 📝 queue {nq}\n"
                f"🔗 deep links {q('SELECT COUNT(*) c FROM deeplinks', one=True)['c']} · {'☁️ CF KV' if config.CF_ENABLED else '💾 local'} · 💾 {size:.2f} MB\n"
                f"🕐 cycle {fmt_date(gget('last_cycle_start'), config.DEFAULT_UTC_OFFSET, True)} ➜ {fmt_date(gget('last_cycle_end'), config.DEFAULT_UTC_OFFSET, True)}\n"
                + metrics_text(lang))
    s = _gs(cid); ch = get_channel(cid); lim = admin_limits(uid); use = usage_today(uid); off = s["utc_offset"]; cap = lambda v: "∞" if v is None else v
    quiet = f"{s['quiet_start']:02d}→{s['quiet_end']:02d}" if s["quiet_start"] is not None and s["quiet_end"] is not None else ("—")
    pname = "∞" if is_super(uid) else (plan_txt(lim["plan"], "name", lang) or ("بدون پلن" if fa else "no plan")); until = f" · {fmt_date(lim['expires'], off)}" if lim["expires"] else ""
    c = lambda st=None: count_articles(cid=cid, hours=24, status=st)
    if fa: return (f"📊 <b>گزارش</b> · 📢 {html.escape(ch['title'])}\n\n"
                   f"{'🟢' if s['enabled'] else '🔴'} اتوماسیون · {'⚡ خودکار' if s['mode'] == 'auto' else '📝 بازبینی'} · 🧾 {html.escape(pname)}{until}\n"
                   f"🌐 منابع {len(list_sources(cid, True))} · 📥 صف {ready_count(cid)} · 📰 کشف ۲۴h {c()}\n"
                   f"📢 پست امروز {use['posts']}/{cap(lim['daily_posts'])} · 🧪 تست {use['tests']}/{cap(lim['daily_tests'])}\n"
                   f"♻️ رد ۲۴h {c('rejected')} · ❌ ناموفق {c('failed')}\n"
                   f"⭐ حداقل امتیاز {s['min_score']} · 🕰 {s['lookback_hours']}h · ⏱ هر {s['interval_minutes']}′\n"
                   f"🌙 {quiet} · 🌍 {off_label(off)} · 💓 {ago_text(hb, lang)} {hb_icon}\n"
                   f"🕐 چرخه {fmt_date(s['last_run'], off, True)} ➜ {fmt_date(s['last_end'], off, True)}")
    return (f"📊 <b>Report</b> · 📢 {html.escape(ch['title'])}\n\n"
            f"{'🟢' if s['enabled'] else '🔴'} automation · {'⚡ auto' if s['mode'] == 'auto' else '📝 review'} · 🧾 {html.escape(pname)}{until}\n"
            f"🌐 sources {len(list_sources(cid, True))} · 📥 queue {ready_count(cid)} · 📰 found 24h {c()}\n"
            f"📢 posts today {use['posts']}/{cap(lim['daily_posts'])} · 🧪 tests {use['tests']}/{cap(lim['daily_tests'])}\n"
            f"♻️ rejected 24h {c('rejected')} · ❌ failed {c('failed')}\n"
            f"⭐ min score {s['min_score']} · 🕰 {s['lookback_hours']}h · ⏱ every {s['interval_minutes']}′\n"
            f"🌙 {quiet} · 🌍 {off_label(off)} · 💓 {ago_text(hb, lang)} {hb_icon}\n"
            f"🕐 cycle {fmt_date(s['last_run'], off, True)} ➜ {fmt_date(s['last_end'], off, True)}")
