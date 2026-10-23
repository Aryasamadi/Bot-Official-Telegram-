# -*- coding: utf-8 -*-
"""پنل مدیر کلان: سلامت سیستم، پلن‌ها، کدها، کاربران، مدل‌ها، پرداخت‌ها، متن‌ها، همگانی.
NEW v4: پس از تأیید پرداخت، اعتبار ارجاع (referral) به‌صورت اتمیک و idempotent ثبت می‌شود؛
شکست ارجاع هرگز جریان پرداخت را نمی‌شکند (فقط لاگ).
فیلدهای ویرایشی (PLAN_FIELDS/MODEL_FIELDS/TEXT_KEYS/fl/_mic/_mname) از ui_views_admin می‌آیند.
"""
import os
from newsbot import config
from newsbot.db import q, gget
from newsbot.logutils import log, log_event, redact
from newsbot.timeutils import now_utc, parse_dt, fmt_date, ago_text
from newsbot.core_users import list_users, get_user, admin_limits, admin_offset, usage_today, is_super, user_lang
from newsbot.core_plans import get_plan, plan_txt, list_plans, plan_num, fmt_amt
from newsbot.core_discounts import disc_list, disc_get, disc_valid
from newsbot.core_channels import list_channels, mandatory_cfg
from newsbot.core_sources import count_sources
from newsbot.core_articles import count_articles
from newsbot.core_payments import (pay_pending, pay_get, pay_set, pay_approve_atomic, pay_mark_notified,
                                    pay_unnotified, pay_approved_recent, PayError,
                                    pay_recover_processing, pay_super_unnotified_count)
from newsbot.core_referrals import referral_cfg
from newsbot.secret_store import decrypt as decrypt_key   # SEC-09: نمایش ماسک‌شدهٔ کلید رمزنگاری‌شده
from newsbot.ai_models import list_models, get_model
from newsbot.text_format import strip_tags
from newsbot.ui_i18n import tr, gtext, TEXT_KEYS
from newsbot.ui_widgets import (B, pairs, esc, onoff, L, uname, render, popup,
                                notify_user_fn)
from newsbot.ui_views_admin import PLAN_FIELDS, MODEL_FIELDS, _mic, _mname


def rss_mb():
    """مصرف حافظهٔ فرایند بدون پکیج اضافه (RSS از /proc؛ لینوکس)."""
    try:
        with open("/proc/self/statm") as f: pages = int(f.read().split()[1])
        return pages * (os.sysconf("SC_PAGE_SIZE") / 1048576.0)
    except Exception: return 0.0

async def view_health(update, context):
    """سلامت سیستم در یک پیام: تیک، کانال، مدل، دیتابیس، ژورنال معلق، حافظه."""
    lang = L(update); fa = lang != "en"
    hb = gget("heartbeat"); hb_age = int((now_utc() - parse_dt(hb)).total_seconds()) if hb else -1
    size = os.path.getsize(config.DB_FILE) / 1048576.0 if os.path.exists(config.DB_FILE) else 0
    try: integ = q("PRAGMA quick_check(1)", one=True)[0]
    except Exception as e: integ = f"error {type(e).__name__}"
    models = list_models(); ok = sum(1 for m in models if m["active"] and m["status"] == "ok")
    try: pending = q("SELECT COUNT(*) c FROM pub_journal", one=True)["c"]
    except Exception: pending = 0
    try: unnot = len(pay_unnotified(50))         # PH-01/OBS-014: پرداخت‌های تأییدشدهٔ اعلام‌نشده
    except Exception: unnot = 0
    # PH-03/OBS-013 · OBS-011: رسیدهایی که هیچ مدیری ندیده + صفِ اعلانِ نرسیده
    try: superunnot = pay_super_unnotified_count()
    except Exception: superunnot = 0
    try:
        from newsbot.core_alerts import alert_counts
        alert_pending_n, alert_dead_n = alert_counts()
    except Exception:
        alert_pending_n = alert_dead_n = 0
    chans = q("SELECT COUNT(*) c FROM channels", one=True)["c"]; ready = q("SELECT COUNT(*) c FROM articles WHERE status='ready'", one=True)["c"]
    head = "🩺 <b>System health</b>" if not fa else "🩺 <b>سلامت سیستم</b>"
    # چیدمان مینیمال: هر سنجه یک خط، با فاصله بین گروه‌های مرتبط
    healthy = 0 <= hb_age < config.TICK_SECONDS * 4
    tick_fa = f"{hb_age} ثانیه پیش" if hb_age >= 0 else "هنوز ثبت نشده"
    tick_en = f"{hb_age}s ago" if hb_age >= 0 else "not recorded yet"
    body = (
        f"\n\n{'OK' if healthy else 'STALE'} — {'سالم' if healthy else 'کهنه'}"
        f"\n\n⚙️ آخرین تیک: {tick_fa}"
        f"\n⏱ بازهٔ اجرای تیک: {config.TICK_SECONDS} ثانیه"
        f"\n🔄 چرخه‌های هم‌زمان: {config.CYCLE_CONCURRENCY}"
        f"\n\n📡 کانال‌های ثبت‌شده: {chans}"
        f"\n📥 موارد آماده در صف: {ready}"
        f"\n📌 موارد معلق ژورنال: {pending}"
        f"\n🧾 پرداخت‌های تأییدشدهٔ اعلام‌نشده: {unnot}"
        f"\n📨 رسیدهای اعلام‌نشده به مدیر: {superunnot}"
        f"\n🔔 اعلان‌های نرسیده: {alert_pending_n} (مرده: {alert_dead_n})"
        f"\n\n🤖 وضعیت مدل‌های هوش مصنوعی: {ok}/{len(models)} سالم"
        f"\n\n💾 حجم دیتابیس: {size:.1f} MB"
        f"\n🧠 مصرف حافظهٔ فرایند: {rss_mb():.0f} MB"
        f"\n🧩 صحت یکپارچگی دیتابیس: {integ}"
        f"\n🔁 فاصلهٔ بازیابی خودکار: {config.RECOVER_EVERY_MIN} دقیقه"
        f"\n\n🏷 نسخهٔ کد: {_code_version()}"
    ) if fa else (
        f"\n\n{'OK' if healthy else 'STALE'}"
        f"\n\n⚙️ Last tick: {tick_en}"
        f"\n⏱ Tick interval: {config.TICK_SECONDS}s"
        f"\n🔄 Concurrent cycles: {config.CYCLE_CONCURRENCY}"
        f"\n\n📡 Registered channels: {chans}"
        f"\n📥 Ready in queue: {ready}"
        f"\n📌 Pending journals: {pending}"
        f"\n🧾 Unnotified approved payments: {unnot}"
        f"\n📨 Receipts not seen by an admin: {superunnot}"
        f"\n🔔 Undelivered alerts: {alert_pending_n} (dead: {alert_dead_n})"
        f"\n\n🤖 AI models healthy: {ok}/{len(models)}"
        f"\n\n💾 Database size: {size:.1f} MB"
        f"\n🧠 Process memory: {rss_mb():.0f} MB"
        f"\n🧩 Database integrity: {integ}"
        f"\n🔁 Auto-recovery interval: {config.RECOVER_EVERY_MIN} min"
        f"\n\n🏷 Code version: {_code_version()}"
    )
    await render(update, context, head + body, [[B(tr(lang, "back"), "s:home")]])

async def cmd_health(update, context):
    if not is_super(update.effective_user.id): return
    await view_health(update, context)

def _code_version():
    """نسخهٔ کدِ در حال اجرا از git — تا کاربر مطمئن شود deploy شده است."""
    _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # newsbot/ → ریشهٔ مخزن
    try:
        import subprocess
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=_root,
                             capture_output=True, text=True, timeout=3)
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except Exception:
        pass
    # بدون git CLI: خواندن مستقیم .git (deployها/محیط‌هایی که git نصب نیست)
    try:
        with open(os.path.join(_root, ".git", "HEAD"), encoding="ascii") as f:
            head = f.read().strip()
        if head.startswith("ref: "):
            ref = head[5:].strip()
            ref_path = os.path.join(_root, ".git", ref.replace("/", os.sep))
            sha = None
            if os.path.isfile(ref_path):
                with open(ref_path, encoding="ascii") as f:
                    sha = f.read().strip()
            else:  # refها ممکن است packed باشند
                packed = os.path.join(_root, ".git", "packed-refs")
                with open(packed, encoding="ascii") as f:
                    for ln in f:
                        if ln.rstrip("\n").endswith(" " + ref):
                            sha = ln.split()[0]
                            break
        else:
            sha = head
        if sha and len(sha) >= 7:
            return sha[:7]
    except Exception:
        pass
    return "unknown"


async def view_s_ref(update, context):
    """REF-3/تصمیم ۸ (فاز ۱۲): تنظیم درصد پاداش و سقف مبلغی از پنل سوپرادمین."""
    lang = L(update); pct, cap = referral_cfg()
    text = tr(lang, "s_ref_title") + tr(lang, "s_ref_help") + tr(lang, "s_ref_line_p", p=f"{pct:g}") + tr(lang, "s_ref_line_c", c=(tr(lang, "s_ref_unlimited") if cap <= 0 else fmt_amt(cap, lang)))
    if not config.REFERRAL_ENABLED: text += "\n" + tr(lang, "ref_off")
    kb = [[B(tr(lang, "s_ref_setp"), "s:ref_p"), B(tr(lang, "s_ref_setc"), "s:ref_c")], [B(tr(lang, "back"), "s:home")]]
    await render(update, context, text, kb)

async def view_super_home(update, context):
    lang = L(update); models = list_models()
    down = sum(1 for m in models if m["active"] and m["status"] == "down"); auto = gget("automation_enabled", True); n_pay = len(pay_pending())
    text = tr(lang, "s_title", m=len(models), ms=tr(lang, "s_all_ok") if not down else tr(lang, "s_down", n=down), a=len(list_users("admin")), u=len(list_users()), p=n_pay, dc=len(disc_list()), due=gget("load_due", 0), hb=ago_text(gget("heartbeat"), lang))
    kb = [[B(tr(lang, "s_report"), "s:report"), B(tr(lang, "s_health"), "s:health")], [B(tr(lang, "s_plans"), "s:plans"), B(tr(lang, "s_discs"), "s:discs")], [B(tr(lang, "s_users"), "s:users:0"), B(tr(lang, "s_admins"), "s:admins:0")],
          [B(tr(lang, "s_models"), "s:models"), B(tr(lang, "s_pays", n=n_pay), "s:pays")], [B(tr(lang, "s_mand"), "s:mand"), B(tr(lang, "s_texts"), "s:texts")], [B(tr(lang, "s_bc"), "s:bc"), B(tr(lang, "s_auto", i=onoff(auto)), "s:auto")], [B(tr(lang, "s_ref"), "s:ref")], [B(tr(lang, "s_me"), "a:home")]]
    await render(update, context, text, kb)

async def view_s_mand(update, context):
    """کانال اجباری — تنظیم مستقل برای هر زبان."""
    lang = L(update); lines, kb = [], []
    for lg in config.LANGS:
        c = mandatory_cfg(lg); on = bool(c.get("on")); tgt = (str(c.get("id") or "").strip() or (("@" + str(c.get("user")).lstrip("@")) if c.get("user") else "") or "—")
        lines.append(tr(lang, "s_mand_line", lg=lg.upper(), i=onoff(on), tgt=esc(tgt)))
        kb.append([B(f"{onoff(on)} {lg.upper()}", f"s:mand_t:{lg}"), B(tr(lang, "s_mand_id"), f"s:mand_e:{lg}:id")])
        kb.append([B(tr(lang, "s_mand_user"), f"s:mand_e:{lg}:user")])
    kb.append([B(tr(lang, "back"), "s:home")])
    await render(update, context, tr(lang, "s_mand_title") + "".join(lines), kb)

async def view_s_plans(update, context):
    lang = L(update); plans = list_plans(active_only=False)
    lines_p = []
    for p in plans:
        try:
            lines_p.append(f"\n{'🟢' if p['active'] else '🔴'}{'🎁' if p['is_free'] else ''} <b>{esc(plan_txt(p, 'name', lang))}</b> · {p['days']}d · {p['daily_posts']}p · {p['max_sources']}s · {p['max_channels']}c · {esc(plan_txt(p, 'price', lang))}")
        except Exception:
            lines_p.append("\n" + esc(str(dict(p))))
    text = tr(lang, "s_plans_title") + "\n" + "".join(lines_p)
    kb = pairs([B(f"{'🟢' if p['active'] else '🔴'} {plan_txt(p, 'name', lang)[:20]}", f"s:plan:{p['id']}") for p in plans]) + [[B(tr(lang, "s_plan_new"), "s:plan_new")], [B(tr(lang, "back"), "s:home")]]
    await render(update, context, text, kb)

async def view_s_plan(update, context, pid):
    lang = L(update); p = get_plan(pid)
    if not p: return await view_s_plans(update, context)
    n = q("SELECT COUNT(*) c FROM users WHERE plan_id=?", (pid,), one=True)["c"]
    pn = f"{fmt_amt(plan_num(p, 'en'), 'en')} · {fmt_amt(plan_num(p, 'fa'), 'fa')}" if (plan_num(p, "en") or plan_num(p, "fa")) else "—"
    try:
        text = tr(lang, "s_plan_view", name=esc(p["name"]), name_en=esc(p["name_en"] or "—"), free="🎁" if p["is_free"] else "", st="🟢" if p["active"] else "🔴", desc=esc((p["description_en"] or "") if lang == "en" else (p["description"] or "")), days=p["days"], posts=p["daily_posts"], tests=p["daily_tests"], src=p["max_sources"], ch=p["max_channels"], price=esc(p["price"]), price_en=esc(p["price_en"] or "—"), price_num=pn, n=n)
    except Exception:
        text = tr(lang, "s_plans_title") + "\n" + esc(str(dict(p)))
    kb = pairs([B(f"✏️ {f[2] if lang == 'en' else f[1]}", f"s:plan_e:{pid}:{f[0]}") for f in PLAN_FIELDS]) + [[B(tr(lang, "toggle"), f"s:plan_t:{pid}"), B(tr(lang, "s_plan_free_set"), f"s:plan_free:{pid}")], [B(tr(lang, "s_plan_del"), f"s:plan_d:{pid}")], [B(tr(lang, "back"), "s:plans")]]
    await render(update, context, text, kb)

def _disc_st(d, lang):
    _, why = disc_valid(d["code"]); return {"ok": "🟢", "inactive": "⏸", "expired": "⌛", "exhausted": "🔚"}.get(why, "🔴")

async def view_s_discs(update, context):
    lang = L(update); ds = disc_list()
    text = tr(lang, "s_discs_title") + "".join(tr(lang, "s_disc_line", i=_disc_st(d, lang), code=esc(d["code"]), p=d["percent"], exp=fmt_date(d["expires"]) if d["expires"] else "∞", used=d["used"], max=d["max_uses"] or "∞") for d in ds)
    if not ds: text += "\n" + tr(lang, "s_disc_none")          # PH-05/UI-023: حالتِ خالیِ صریح
    kb = pairs([B(f"{_disc_st(d, lang)} {d['code'][:18]} −{d['percent']}%", f"s:disc:{d['code']}") for d in ds]) + [[B(tr(lang, "s_disc_new"), "s:disc_new")], [B(tr(lang, "back"), "s:home")]]
    await render(update, context, text, kb)

async def view_s_disc(update, context, code):
    lang = L(update); d = disc_get(code)
    if not d: return await view_s_discs(update, context)
    text = tr(lang, "s_disc_view", code=esc(d["code"]), st=_disc_st(d, lang), p=d["percent"], exp=fmt_date(d["expires"], config.DEFAULT_UTC_OFFSET, True) if d["expires"] else "∞", used=d["used"], max=d["max_uses"] or "∞")
    kb = [[B(tr(lang, "s_disc_p"), f"s:disc_e:{code}:percent"), B(tr(lang, "s_disc_e"), f"s:disc_e:{code}:expires")], [B(tr(lang, "s_disc_m"), f"s:disc_e:{code}:max_uses"), B(tr(lang, "toggle"), f"s:disc_t:{code}")], [B(tr(lang, "s_disc_del"), f"s:disc_d:{code}")], [B(tr(lang, "back"), "s:discs")]]
    await render(update, context, text, kb)

async def view_s_users(update, context, page=0, role=None):
    lang = L(update); users = list_users(role); per = 10; pages = max(1, (len(users) + per - 1) // per); page = max(0, min(page, pages - 1)); chunk = users[page * per:(page + 1) * per]; pre = "admins" if role else "users"
    def ic(u): return "⛔" if u["banned"] else {"user": "👤", "admin": "🧑‍"}.get(u["role"], "👑")
    text = tr(lang, "s_users_title", what=tr(lang, "s_admins_w" if role else "s_users_w"), n=len(users), p=page + 1, pp=pages) + "\n" + "".join(f"\n{ic(u)} {esc(uname(u))} · {esc(plan_txt(admin_limits(u['id'])['plan'], 'name', lang) or tr(lang, 'no_plan'))}{' 🟢' if admin_limits(u['id'])['active'] else ''}" for u in chunk)
    if not users: text += "\n" + tr(lang, "s_admins_none" if role else "s_users_none")   # PH-05/UI-023
    kb = pairs([B(f"{ic(u)} {uname(u)[:22]}", f"s:user:{u['id']}:{page}") for u in chunk]); nav = []
    if page > 0: nav.append(B(tr(lang, "s_prev"), f"s:{pre}:{page - 1}"))
    if page + 1 < pages: nav.append(B(tr(lang, "s_next"), f"s:{pre}:{page + 1}"))
    if nav: kb.append(nav)
    kb.append([B(tr(lang, "back"), "s:home")]); await render(update, context, text, kb)

async def view_s_user(update, context, tid, page=0):
    lang = L(update); u = get_user(tid)
    if not u: return await view_s_users(update, context)
    lim = admin_limits(tid); use = usage_today(tid); off = admin_offset(tid)
    role_txt = tr(lang, "role_admin" if u["role"] == "admin" else ("role_user" if u["role"] == "user" else "role_super"))
    text = tr(lang, "s_user_view", name=esc(uname(u)), id=tid, role=role_txt, ban=tr(lang, "s_ban_y" if u["banned"] else "s_ban_n"), lang=esc(u["lang"]) or "—", prem="💎" if u["premium"] else "", plan=esc(plan_txt(lim["plan"], "name", lang) or tr(lang, "no_plan")), act="🟢" if lim["active"] else "🔴", exp=tr(lang, "until", d=fmt_date(lim["expires"], off)) if lim["expires"] else "",
              nxt=("\n" + tr(lang, "plans_next", name=esc(plan_txt(lim["next_plan"], "name", lang)))) if lim["next_plan"] else "", p=use["posts"], t=use["tests"], c=len(list_channels(tid)), s=count_sources(tid), d=count_articles(tid, 24), pub=count_articles(tid, 24, "published"), free=onoff(u["free_used"]), join=(u["created_at"] or "")[:10], seen=ago_text(u["last_seen"], lang))
    kb = [[B(tr(lang, "s_uplan"), f"s:uplan:{tid}"), B(tr(lang, "s_urevoke"), f"s:urevoke:{tid}")], [B(tr(lang, "s_uban"), f"s:uban:{tid}"), B(tr(lang, "s_umsg"), f"s:umsg:{tid}")], [B(tr(lang, "s_ureport"), f"s:ureport:{tid}"), B(tr(lang, "s_ufree"), f"s:ufree:{tid}")], [B(tr(lang, "s_utests"), f"s:utests:{tid}"), B(tr(lang, "s_uposts"), f"s:uposts:{tid}")], [B(tr(lang, "back"), f"s:{'admins' if (u['role'] == 'admin') else 'users'}:{int(page or 0)}")]]
    await render(update, context, text, kb)

async def view_s_models(update, context):
    lang = L(update); ms = list_models()
    text = tr(lang, "s_models_title") + "\n"
    if not ms: text += "\n" + tr(lang, "s_model_none") + "\n"
    else: text += "".join(f"\n{_mic(m)} <b>{esc(_mname(m))}</b> · <code>{esc(m['model'])}</code> · p{m['priority']} · ✅{m['ok_count']}" for m in ms)
    kb = pairs([B(f"{_mic(m)} {_mname(m)[:20]} (p{m['priority']})", f"s:model:{m['id']}") for m in ms]) + [[B(tr(lang, "s_model_add"), "s:model_add"), B(tr(lang, "s_models_test"), "s:models_test")], [B(tr(lang, "back"), "s:home")]]
    await render(update, context, text, kb)

async def view_s_model(update, context, mid):
    lang = L(update); m = get_model(mid)
    if not m: return await view_s_models(update, context)
    key = decrypt_key(m["api_key"] or ""); masked = (key[:3] + "…" + key[-2:]) if len(key) > 12 else "—"   # FIX v4: ماسک سخت‌گیرانه‌تر (قبلاً ۹ کاراکتر واقعی لو می‌رفت)
    text = tr(lang, "s_model_view", name=esc(m["name"]), kind=esc(m["kind"]), model=esc(m["model"]), base=esc(m["base_url"]), key=esc(masked), pr=m["priority"] or 10, temp=0.5 if m["temperature"] is None else m["temperature"], mx=m["max_tokens"] or 2500, st=tr(lang, "s_m_off" if not m["active"] else "s_m_ok" if m["status"] == "ok" else "s_m_down"), ok=m["ok_count"] or 0, fc=m["fail_count"] or 0, lo=ago_text(m["last_ok"], lang), lf=ago_text(m["last_fail"], lang), err=f"\n<code>{esc(redact(m['last_error'] or '')[:150])}</code>" if m["last_error"] else "")
    kb = [[B(tr(lang, "s_model_test"), f"s:model_test:{mid}"), B(tr(lang, "toggle"), f"s:model_t:{mid}")]] + pairs([B(f"✏️ {f[2] if lang == 'en' else f[1]}", f"s:model_e:{mid}:{f[0]}") for f in MODEL_FIELDS]) + [[B(tr(lang, "s_model_del"), f"s:model_d:{mid}")], [B(tr(lang, "back"), "s:models")]]
    if m["owner_id"] is None:
        selected = gget("test_model_id") == mid
        text += "\n" + tr(lang, "s_tm_line", st=tr(lang, "s_tm_selected" if selected else "s_tm_not_sel"))
        kb.insert(1, [B(tr(lang, "s_tm_unset" if selected else "s_tm_use"), f"s:model_test_pick:{mid}")])
    await render(update, context, text, kb)

async def view_s_pays(update, context):
    lang = L(update); ps = pay_pending()
    shown, hidden = ps[:10], max(0, len(ps) - 10)   # UX-06: حداکثر ۱۰ رسید در یک صفحه
    text = tr(lang, "s_pays_title") + ("".join(tr(lang, "s_pay_line", id=r["id"], who=esc(("@" + r["username"]) if r["username"] else (r["name"] or r["user_id"])), plan=esc(r["plan_name"]), price=esc(r["final_price"] or r["plan_price"]), t=r["created_at"][5:16]) for r in shown) or "\n" + tr(lang, "s_pay_none"))
    if hidden: text += tr(lang, "s_pays_more", n=hidden)
    kb = [[B(tr(lang, "s_pay_rc", id=r["id"]), f"s:pay_rc:{r['id']}"), B(tr(lang, "s_pay_ok"), f"s:pay_ok:{r['id']}:l"), B(tr(lang, "s_pay_no"), f"s:pay_no:{r['id']}:l")] for r in shown] + [[B(tr(lang, "back"), "s:home")]]
    # PH-01/FIN-013: پرداخت‌های اخیرِ تأییدشده با دکمهٔ «بازگشتِ وجه»
    done = pay_approved_recent(8)
    if done:
        text += "\n\n" + tr(lang, "s_pays_done_title")
        for r in done:
            text += tr(lang, "s_pay_line", id=r["id"], who=esc(("@" + r["username"]) if r["username"] else (r["name"] or r["user_id"])),
                        plan=esc(r["plan_name"] or "—"), price=esc(r["final_price"] or "—"),
                        t=(r["decided_at"] or r["created_at"] or "")[5:16])
        kb = kb[:-1] + [[B(tr(lang, "s_pay_refund_btn", id=r["id"]), f"s:pay_refund:{r['id']}") for r in done[:4]]] + [kb[-1]]
    await render(update, context, text, kb)

async def view_s_texts(update, context):
    lang = L(update); text = tr(lang, "s_texts_title") + "\n"
    for k in TEXT_KEYS: text += f"\n<b>{k}</b>\n🇮🇷 <i>{esc(strip_tags(gtext(k, 'fa'))[:60])}…</i>\n🇬🇧 <i>{esc(strip_tags(gtext(k, 'en'))[:60])}…</i>"
    kb = []
    for k in TEXT_KEYS:
        kb.append([B(f"✏️ {k} 🇮🇷", f"s:txt:{k}:fa"), B(f"✏️ {k} 🇬🇧", f"s:txt:{k}:en")])
        kb.append([B(f"{tr(lang, 's_text_reset')} 🇮🇷", f"s:txtr:{k}:fa"), B(f"{tr(lang, 's_text_reset')} 🇬🇧", f"s:txtr:{k}:en")])
    kb.append([B(tr(lang, "back"), "s:home")])
    await render(update, context, text, kb)

def _pay_decision_ready(lang, rid):
    """Return (row, None) only when the receipt is genuinely still pending.

    A ``processing`` row is not "already handled": either another admin has a fresh
    claim (wait) or the claim is stale and is returned to pending on demand (FIN-022).
    """
    r = pay_get(rid)
    if r and r["status"] == "processing":
        r = pay_recover_processing(rid)
    if not r: return None, tr(lang, "notfound")
    if r["status"] == "processing": return None, tr(lang, "s_pay_processing")
    if r["status"] != "pending": return None, tr(lang, "s_pay_seen")
    return r, None

def _pay_failure_text(lang, e):
    """Only ``PayAlreadyDecided`` is "already handled"; every other failure is honest."""
    code = getattr(e, "code", None)
    if code == "seen": return tr(lang, "s_pay_seen")
    if code == "quote_stale": return tr(lang, "s_pay_stale")
    if code == "disc_invalid": return tr(lang, "s_pay_disc_invalid")
    if code == "disc_exhausted": return tr(lang, "disc_exhausted")
    return tr(lang, "s_pay_failed")

async def decide_pay(update, context, rid, approve, from_list):
    lang = L(update); r, why = _pay_decision_ready(lang, rid)
    if not r: await popup(update, context, why); return await view_s_pays(update, context) if from_list else None
    from newsbot.concurrency import user_op_lock
    async with user_op_lock(r["user_id"]):   # BIZ-05: یک عملیات مالی در لحظه برای هر کاربر
        return await _decide_pay_locked(update, context, rid, approve, from_list, lang)

async def _decide_pay_locked(update, context, rid, approve, from_list, lang):
    # دوباره وضعیت را بخوان؛ شاید در صفِ قفل، تصمیم دیگری ثبت شده باشد
    r, why = _pay_decision_ready(lang, rid)
    if not r: await popup(update, context, why); return await view_s_pays(update, context) if from_list else None
    ul = user_lang(r["user_id"]) or "fa"; p = get_plan(r["plan_id"])
    if not p or p["is_free"] or ("active" in p.keys() and not p["active"]):
        pay_set(rid, "pending"); await popup(update, context, tr(lang, "notfound")); return await view_s_pays(update, context) if from_list else None
    if approve:
        # PHASE 4: claim + تخفیف + کیف پول/سابقه + واگذاری پلن + approved در یک تراکنش (PAY-1/DB-04).
        # PH-01: قیمت دوباره محاسبه و در صورت اختلافِ مؤثر، تأیید متوقف می‌شود (Q-2a)؛
        # پاداشِ ارجاع هم درونِ همین تراکنش اعطا می‌شود (FIN-009) و اینجا فقط اعلام می‌گردد.
        try:
            exp, queued, ref_reward = pay_approve_atomic(rid, by_uid=update.effective_user.id)
        except PayError as e:
            await popup(update, context, _pay_failure_text(lang, e))
            return await view_s_pays(update, context) if from_list else None
        except Exception as e:
            log.exception("payment approval failed rid=%s uid=%s error=%s", rid, r["user_id"], type(e).__name__)
            await popup(update, context, tr(lang, "s_pay_failed")); return await view_s_pays(update, context) if from_list else None
        # معرف مطلع می‌شود: مبلغ دقیقِ واریزشده به کیف پولِ **خودش** (با واحدِ خودش)
        if ref_reward:
            try:
                ref_id, amt, ref_cur = ref_reward
                rl = user_lang(ref_id) or "fa"
                from newsbot.core_wallet import lang_of_currency
                rl = lang_of_currency(ref_cur) or rl          # واحدِ پاداش = واحدِ پرداخت
                await notify_user_fn(ref_id, tr(rl, "ref_reward", amount=fmt_amt(amt, rl)))
            except Exception: log.exception("referral notify failed rid=%s", rid)
        await notify_user_fn(r["user_id"], tr(ul, "pay_approved", name=esc(plan_txt(p, "name", ul)), when=tr(ul, "pay_when_queued" if queued else "pay_when_now", d=fmt_date(exp, admin_offset(r["user_id"])))))
        try: pay_mark_notified(rid)
        except Exception: pass
        await popup(update, context, tr(lang, "s_pay_done_ok"))
    else:
        pay_set(rid, "rejected"); await notify_user_fn(r["user_id"], tr(ul, "pay_rejected")); await popup(update, context, tr(lang, "s_pay_done_no"))
    log_event("INFO", f"پرداخت #{rid} {'تأیید' if approve else 'رد'} شد", update.effective_user.id)
    if from_list: return await view_s_pays(update, context)
    try: await update.callback_query.edit_message_reply_markup(None)
    except Exception: pass
