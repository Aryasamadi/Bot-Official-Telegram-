# -*- coding: utf-8 -*-
"""پنل مدیر میانی: خانه، پنل کانال، انتقال مالکیت، منابع، محتوا، زمان‌بندی، صف، مقاله‌ها،
هوش مصنوعی شخصی، پلن من، تست فوری — و دروازه‌ی کانال اجباری (NEW v4، فقط ادمین).
فیلدهای ویرایشی (PLAN_FIELDS/MODEL_FIELDS/TEXT_KEYS/fl) این‌جا تعریف می‌شوند و
ui_views_super و ui_dispatch از همین‌جا import می‌کنند.
"""
import asyncio, html, json, time
from newsbot import config
from newsbot.db import q, gget
from newsbot.logutils import log, log_event, redact
from newsbot.timeutils import now_utc, ago_text, fmt_date, local_clock, utc_clock, today_str, off_label
from newsbot.ui_widgets import notify_user_fn, notify_supers
from newsbot.ratelimit import rate_free, rate_left, rate_mark, rate_clear
from newsbot.concurrency import ch_lock
from newsbot.core_users import (admin_limits, admin_offset, usage_today, get_user, is_super,
                                user_lang, channel_posts_today)
from newsbot.core_plans import get_plan, plan_txt, revoke_plan, assign_plan
from newsbot.core_channels import (list_channels, get_settings, get_channel, channel_owned, first_registrant,
                                   issue_transfer_token, revert_channel_owner, publication_pending,
                                   update_channel_meta, save_settings, update_settings, TRANSFER_TTL_MIN,
                                   mandatory_configured, mandatory_membership)
from newsbot.core_sources import list_sources, get_source, count_sources
from newsbot.discover import _has_api, probe_source
from newsbot.ai_models import (hostname, list_models, get_model, update_model, delete_model, test_model,
                               _model_label, ai_err_text, err_code, _ai_events)
from newsbot.core_articles import ready_count, count_articles, articles_by_status, get_article, article_update, delete_article
from newsbot.cycle import run_channel_cycle, Diag
from newsbot.scheduler import report_text
from newsbot.text_format import strip_tags, sanitize_html, fit_html, split_html
from newsbot.ui_i18n import tr, STATUS, reason_text, TZ_NAMES, tz_city, INT_FIELDS, FIELD_LABEL, SCHED_FIELDS
from newsbot.ui_widgets import (B, U, pairs, esc, onoff, cap, L, uname, render, popup, ask, wiz_start,
                                operation_status, _ai_status_reporter, run_model_test)
from newsbot.opctx import _current_operation

# ── فیلدهای ویرایشی پنل‌ها (منبع یکتا برای super/dispatch/inputs) ──
PLAN_FIELDS = [("name", "نام (فا)", "Name (fa)"), ("name_en", "نام (en)", "Name (en)"), ("days", "مدت (روز)", "Days"), ("daily_posts", "پست/روز", "Posts/day"), ("max_sources", "منابع", "Sources"), ("max_channels", "کانال‌ها", "Channels"), ("daily_tests", "تست/روز", "Tests/day"), ("price", "قیمت (فا)", "Price (fa)"), ("price_en", "قیمت (en)", "Price (en)"), ("price_num", "قیمت عددی (برای proration)", "Numeric price (proration)"), ("description", "توضیح (فا)", "Description (fa)"), ("description_en", "توضیح (en)", "Description (en)")]
MODEL_FIELDS = [("model", "نام مدل", "Model"), ("base_url", "Base URL", "Base URL"), ("api_key", "کلید API", "API key"), ("name", "نام نمایشی", "Display name"), ("priority", "اولویت", "Priority"), ("temperature", "دما (0–2)", "Temperature (0–2)"), ("max_tokens", "حداکثر توکن", "Max tokens")]
TEXT_KEYS = ["welcome", "help", "about", "pay"]

def fl(fields, key, lang): return next((f[2] if lang == "en" else f[1] for f in fields if f[0] == key), key)
def _mic(m): return "⏸" if not m["active"] else "🟢" if m["status"] == "ok" else "🔴"
def _mname(m): return str(m["name"] or m["model"] or f"#{m['id']}")

# ══════════════════════════════════════════════════════════════════════════
# NEW v4 — دروازه‌ی کانال اجباری (فقط برای ادمین‌ها)
# ══════════════════════════════════════════════════════════════════════════
async def mand_gate(update, context, uid, lang):
    """دروازه‌ی عضویت در کانال اجباری — فقط در مسیرهای ادمین (افزودن/فعال‌سازی کانال) نصب شده است.
    کاربر عادی هرگز به این تابع نمی‌رسد. خروجی True یعنی جریان باید همین‌جا متوقف شود."""
    if not mandatory_configured(): return False
    ok, join = await mandatory_membership(context.bot, uid)
    if ok: return False
    kb = []
    if join: kb.append([U(tr(lang, "mand_join"), join)])
    kb.append([B(tr(lang, "mand_check"), "a:mand"), B(tr(lang, "back"), "a:home")])
    await render(update, context, tr(lang, "mand_missing", join=join or "—"), kb)
    return True

# ══════════════════════════════════════════════════════════════════════════
# خانه‌ی مدیر + پنل کانال
# ══════════════════════════════════════════════════════════════════════════
async def view_admin_home(update, context):
    uid = update.effective_user.id; lang = L(update); lim = admin_limits(uid); use = usage_today(uid); chs = list_channels(uid)
    pname = "∞" if is_super(uid) else (plan_txt(lim["plan"], "name", lang) or tr(lang, "no_plan"))
    text = (f"{tr(lang, 'panel')}\n🧾 <b>{esc(pname)}</b>" + (tr(lang, "until", d=fmt_date(lim["expires"], admin_offset(uid))) if lim["expires"] else "") + "\n" +
            tr(lang, "today_line", p=use["posts"], pc=cap(lim["daily_posts"]), t=use["tests"], tc=cap(lim["daily_tests"]), c=len(chs), cc=cap(lim["max_channels"]), s=count_sources(uid), sc=cap(lim["max_sources"])))
    text += quota_bars_safe([( "📝", use["posts"], lim["daily_posts"]), ("🧪", use["tests"], lim["daily_tests"]),
                             ("📢", len(chs), lim["max_channels"]), ("🔗", count_sources(uid), lim["max_sources"])])
    if lim["next_plan"]: text += "\n" + tr(lang, "plans_next", name=esc(plan_txt(lim["next_plan"], "name", lang)))
    if not lim["active"]: text += "\n\n" + tr(lang, "plan_inactive")
    text += "\n\n" + (tr(lang, "pick_channel") if chs else tr(lang, "no_channels"))
    kb = pairs([B(f"{'🟢' if get_settings(c['id'])['enabled'] else '🔴'} {c['title'][:22]}", f"a:ch:{c['id']}") for c in chs])
    kb.append([B(tr(lang, "ch_add"), "a:chadd")]); kb.append([B(tr(lang, "my_plan"), "a:plan"), B(tr(lang, "my_ai"), "a:myai")])
    if is_super(uid): kb.append([B(tr(lang, "super_panel"), "s:home")])
    await render(update, context, text, kb)

def quota_bars_safe(rows):
    from newsbot.ui_widgets import quota_bars
    return quota_bars(rows)

async def view_channel(update, context, cid):
    uid = update.effective_user.id; lang = L(update); ch = channel_owned(cid, uid)
    if not ch: await popup(update, context, tr(lang, "notfound")); return await view_admin_home(update, context)
    s = get_settings(cid); on = s["enabled"]
    text = (tr(lang, "ch_panel", title=esc(ch["title"])) + (f" · @{ch['username']}" if ch["username"] else "") + "\n" + tr(lang, "ch_status", i="🟢" if on else "🔴", st=tr(lang, "on" if on else "off"), mode=tr(lang, "auto" if s["mode"] == "auto" else "review")) + "\n" +
            tr(lang, "ch_stats", s=len(list_sources(cid, True)), q=ready_count(cid), p=count_articles(cid=cid, hours=24, status="published"), r=count_articles(cid=cid, hours=24, status="rejected"), a=ago_text(s["last_run"], lang)))
    text += quota_bars_safe([("📝", channel_posts_today(cid, admin_offset(uid)), s.get("daily_posts_cap"))])
    kb = [[B(tr(lang, "test"), f"a:test:{cid}"), B(tr(lang, "report"), f"a:rep:{cid}")],
          [B(tr(lang, "sources", n=len(list_sources(cid))), f"a:src:{cid}"), B(tr(lang, "queue", n=ready_count(cid)), f"a:que:{cid}")],
          [B(tr(lang, "sched"), f"a:sch:{cid}"), B(tr(lang, "content"), f"a:con:{cid}")],
          [B(tr(lang, "rejected"), f"a:rej:{cid}"), B(tr(lang, "automation", i="🟢" if on else "🔴"), f"a:tog:{cid}:enabled")],
          [B(tr(lang, "ch_del"), f"a:chdel:{cid}"), B(tr(lang, "back"), "a:home")]]
    # NEW v4: اگر کانال اجباری تنظیم شده و این ادمین عضو نیست، هشدار + دکمه‌های Join/Check بالای پنل
    if mandatory_configured():
        okm, join = await mandatory_membership(context.bot, uid)
        if not okm:
            row = ([U(tr(lang, "mand_join"), join)] if join else []) + [B(tr(lang, "mand_check"), "a:mand")]
            kb.insert(0, row)
            text += "\n\n🔐 " + tr(lang, "mand_still")
    await render(update, context, text, kb)

async def channel_claim(update, context, existing, uid, lang, back, ud):
    """کانالی که در پنل دیگری است: ثبت‌کنندهٔ نخست «بررسی» می‌گیرد؛ دیگران باید توکن ۱۰ رقمی مالک را بیاورند."""
    cid = existing["id"]; owner = first_registrant(cid)
    if owner is None: return await popup(update, context, tr(lang, "notfound"), alert=True)
    cur = existing["admin_id"]; ow = get_user(cur)
    owho = f"{esc(uname(ow))} (<code>{cur}</code>)"
    ud.pop("await", None)
    if uid == owner or is_super(uid):
        return await render(update, context, tr(lang, "ch_recover_text", title=esc(existing["title"]), who=owho),
                            [[B(tr(lang, "b_recheck"), f"a:chrec:{cid}")], [B(tr(lang, "cancel"), "c:cancel")]])
    tok = issue_transfer_token(cid, owner)
    if not tok: return await popup(update, context, tr(lang, "ch_recheck_err"), alert=True)
    me = get_user(uid); who_me = f"{esc(uname(me))} (<code>{uid}</code>)"
    await notify_user_fn(owner, tr(user_lang(owner) or "fa", "ch_token_dm", title=esc(existing["title"]), who=who_me, token=tok, min=TRANSFER_TTL_MIN))
    await notify_supers(f"«{esc(existing['title'])}» transfer token issued for {who_me}")
    log_event("WARN", f"توکن انتقال «{existing['title']}» صادر شد (متقاضی {uid})", uid)
    ud["await"] = {"kind": "chtoken", "cid": cid, "back": back, "tries": 0, "title": existing["title"]}
    text = tr(lang, "ch_taken", who=owho) + "\n\n" + tr(lang, "ch_token_sent", who=esc(uname(ow)), min=TRANSFER_TTL_MIN)
    await render(update, context, text, [[B(tr(lang, "cancel"), "c:cancel")]])

async def channel_recheck(update, context, cid):
    """بازیابی مالکیت: ثبت‌کنندهٔ نخست، پس از سلب دسترسی مالک فعلی در تلگرام، مالکیت را برمی‌گرداند."""
    from newsbot.ui_dispatch import dispatch   # import درون‌تابعی: شکستن چرخه‌ی views ↔ dispatch
    uid = update.effective_user.id; lang = L(update)
    ch = get_channel(cid); owner = first_registrant(cid)
    if not ch or owner is None or (uid != owner and not is_super(uid)):
        return await popup(update, context, tr(lang, "notfound"), alert=True)
    cur = ch["admin_id"]
    if cur == uid:
        await popup(update, context, tr(lang, "ch_exists_mine")); return await view_channel(update, context, cid)
    ow = get_user(cur); owho = f"{esc(uname(ow))} (<code>{cur}</code>)"
    try:
        mem = await context.bot.get_chat_member(ch["chat_id"], cur); still = mem.status in ("administrator", "creator")
    except Exception:
        return await popup(update, context, tr(lang, "ch_recheck_err"), alert=True)
    if still: return await popup(update, context, tr(lang, "ch_recheck_still", who=owho), alert=True)
    try: old = revert_channel_owner(cid, uid)
    except RuntimeError: return await popup(update, context, tr(lang, "test_busy"), alert=True)
    log_event("WARN", f"مالکیت «{ch['title']}» از {old} به ثبت‌کنندهٔ نخست {uid} برگشت", uid)
    await notify_user_fn(cur, tr(user_lang(cur) or "fa", "ch_reverted", title=esc(ch["title"]), who=owho))
    await notify_supers(f"«{esc(ch['title'])}» ownership reverted {old} → {uid}")
    await popup(update, context, tr(lang, "ch_recheck_ok", title=esc(ch["title"])))
    return await dispatch(update, context, f"a:ch:{cid}")

# ══════════════════════════════════════════════════════════════════════════
# منابع
# ══════════════════════════════════════════════════════════════════════════
async def view_sources(update, context, cid):
    uid = update.effective_user.id; lang = L(update); ch = channel_owned(cid, uid)
    if not ch: return await view_admin_home(update, context)
    srcs = list_sources(cid); lim = admin_limits(uid)
    text = tr(lang, "src_title", title=esc(ch["title"]), n=count_sources(uid), cap=cap(lim["max_sources"])) + "\n" + "".join(
        tr(lang, "src_line", i="🟢" if s["active"] else "🔴", b="🤖" if (s["bot_active"] is None or s["bot_active"]) else "🚫", host=esc(hostname(s["url"])) + (" 🔌" if _has_api(s) else ""), n=s["found_total"] or 0, err=tr(lang, "src_err", n=s["fail_count"]) if s["fail_count"] else "") for s in srcs)
    kb = pairs([B(f"{'🟢' if s['active'] else '🔴'} {hostname(s['url'])[:22]}", f"a:srcv:{s['id']}") for s in srcs]) + [[B(tr(lang, "src_add"), f"a:srca:{cid}")], [B(tr(lang, "back"), f"a:ch:{cid}")]]
    await render(update, context, text, kb)

async def view_source(update, context, sid):
    from newsbot.core_sources import toggle_source, del_source, set_source_api
    lang = L(update); s = get_source(sid)
    if not s or (s["admin_id"] != update.effective_user.id and not is_super(update.effective_user.id)): return await view_admin_home(update, context)
    bot_on = s["bot_active"] is None or s["bot_active"]
    st = tr(lang, "src_st", c="🟢" if s["active"] else "🔴", b="🤖" if bot_on else "🚫")
    api = tr(lang, "src_api_on", u=esc(str(s["api_url"])[:60])) if _has_api(s) else tr(lang, "src_api_none")
    text = tr(lang, "src_view", host=esc(hostname(s["url"])), url=esc(s["url"]), feed=tr(lang, "src_feed", u=esc(s["feed_url"])) if s["feed_url"] and s["feed_url"] != s["url"] else "", st=st, last=ago_text(s["last_fetch"], lang), n=s["found_total"], f=s["fail_count"], err=f"\n⚠️ <code>{esc(redact(s['last_error'] or '')[:120])}</code>" if s["last_error"] else "") + "\n" + api
    kb = [[B(tr(lang, "src_recheck"), f"a:srcr:{sid}")], [B(("🟢 " if s["active"] else "🔴 ") + tr(lang, "tog_ch"), f"a:srct:{sid}"), B(("🟢 " if bot_on else "🔴 ") + tr(lang, "tog_bot"), f"a:srctb:{sid}")],
          [B(tr(lang, "src_api"), f"a:srcapi:{sid}")] + ([B(tr(lang, "src_api_del"), f"a:srcapix:{sid}")] if _has_api(s) else []), [B(tr(lang, "delete"), f"a:srcd:{sid}")], [B(tr(lang, "back"), f"a:src:{s['channel_id']}")]]
    await render(update, context, text, kb)

# ══════════════════════════════════════════════════════════════════════════
# محتوا، دسته‌ها، معیارها
# ══════════════════════════════════════════════════════════════════════════
async def view_content(update, context, cid):
    uid = update.effective_user.id; lang = L(update); ch = channel_owned(cid, uid)
    if not ch: return await view_admin_home(update, context)
    s = get_settings(cid)
    text = tr(lang, "content_title", title=esc(ch["title"]), topic=esc(s["topic"] or tr(lang, "general")), lang=esc(s["language"]), prompt=esc(s["prompt"][:2500]) + ("…" if len(s["prompt"]) > 2500 else ""), sig=esc(strip_tags(s["signature"])[:60]) or "—")
    kb = [[B(tr(lang, "b_topic"), f"a:set:{cid}:topic"), B(tr(lang, "b_lang"), f"a:set:{cid}:language")], [B(tr(lang, "b_prompt"), f"a:set:{cid}:prompt"), B(tr(lang, "b_sig"), f"a:set:{cid}:signature")],
          [B(tr(lang, "b_cats", n=len(s["categories"])), f"a:cat:{cid}"), B(tr(lang, "b_crits", n=len(s["criteria"])), f"a:cri:{cid}")], [B(tr(lang, "b_min", n=s["min_score"]), f"a:set:{cid}:min_score"), B(tr(lang, "b_words", n=s["max_words"]), f"a:set:{cid}:max_words")],
          [B(tr(lang, "b_limit", n=s["post_limit"]), f"a:set:{cid}:post_limit"), B(tr(lang, "b_hashtags", i=onoff(s["hashtags"])), f"a:tog:{cid}:hashtags")], [B(tr(lang, "b_link", i=onoff(s["include_link"])), f"a:tog:{cid}:include_link"), B(tr(lang, "b_media", i=onoff(s["include_media"])), f"a:tog:{cid}:include_media")],
          [B(tr(lang, "b_strict", i=onoff(s["strict_ads"])), f"a:tog:{cid}:strict_ads")], [B(tr(lang, "back"), f"a:ch:{cid}")]]
    await render(update, context, text, kb)

async def view_cats(update, context, cid):
    lang = L(update); s = get_settings(cid)
    text = tr(lang, "cats_title") + "\n" + "".join(f"\n{esc(c['emoji'])} <b>{esc(c['name'])}</b>: {esc(c['style'][:70])}" for c in s["categories"])
    kb = pairs([B(f"{esc(c['emoji'])} {c['name'][:18]}", f"a:catv:{cid}:{i}") for i, c in enumerate(s["categories"])]) + [[B(tr(lang, "cat_add"), f"a:cata:{cid}")], [B(tr(lang, "back"), f"a:con:{cid}")]]
    await render(update, context, text, kb)

async def view_cat(update, context, cid, i):
    lang = L(update); s = get_settings(cid); c = s["categories"][i] if 0 <= i < len(s["categories"]) else None
    if not c: return await view_cats(update, context, cid)
    await render(update, context, tr(lang, "cat_view", e=c["emoji"], name=esc(c["name"]), style=esc(c["style"])), [[B(tr(lang, "cat_edit"), f"a:cats:{cid}:{i}"), B(tr(lang, "delete"), f"a:catd:{cid}:{i}")], [B(tr(lang, "back"), f"a:cat:{cid}")]])

async def view_crits(update, context, cid):
    lang = L(update); s = get_settings(cid); tot = sum(float(c["weight"]) for c in s["criteria"]) or 1
    text = tr(lang, "crits_title", m=s["min_score"]) + "\n" + "".join(tr(lang, "crit_line", name=esc(c["name"]), w=c["weight"], p=f"{float(c['weight']) / tot * 100:.0f}") for c in s["criteria"])
    kb = pairs([B(tr(lang, "crit_btn", name=c["name"][:16], w=c["weight"]), f"a:criv:{cid}:{i}") for i, c in enumerate(s["criteria"])]) + [[B(tr(lang, "crit_add"), f"a:cria:{cid}")], [B(tr(lang, "back"), f"a:con:{cid}")]]
    await render(update, context, text, kb)

async def view_crit(update, context, cid, i):
    lang = L(update); s = get_settings(cid); c = s["criteria"][i] if 0 <= i < len(s["criteria"]) else None
    if not c: return await view_crits(update, context, cid)
    await render(update, context, tr(lang, "crit_view", name=esc(c["name"]), w=c["weight"]), [[B(tr(lang, "crit_w"), f"a:criw:{cid}:{i}"), B(tr(lang, "delete"), f"a:crid:{cid}:{i}")], [B(tr(lang, "back"), f"a:cri:{cid}")]])

# ══════════════════════════════════════════════════════════════════════════
# زمان‌بندی، خاموشی، منطقه‌ی زمانی شهری
# ══════════════════════════════════════════════════════════════════════════
def hour_grid(prefix): return [[B(f"{h:02d}", f"{prefix}:{h}") for h in range(r, r + 6)] for r in range(0, 24, 6)]

async def view_sched(update, context, cid):
    uid = update.effective_user.id; lang = L(update); ch = channel_owned(cid, uid)
    if not ch: return await view_admin_home(update, context)
    s = get_settings(cid); off = s["utc_offset"]; quiet = f"{s['quiet_start']:02d}→{s['quiet_end']:02d}" if s["quiet_start"] is not None and s["quiet_end"] is not None else tr(lang, "none")
    text = tr(lang, "sched_title", title=esc(ch["title"]), iv=s["interval_minutes"], ppc=s["posts_per_cycle"], lb=s["lookback_hours"], ud=onoff(s["allow_undated"]), quiet=quiet, city=tz_city(off, lang), tz=off_label(off), loc=local_clock(off), utc=utc_clock(), mode=tr(lang, "mode_auto" if s["mode"] == "auto" else "mode_review"))
    kb = [[B(tr(lang, "b_interval", n=s["interval_minutes"]), f"a:set:{cid}:interval_minutes"), B(tr(lang, "b_ppc", n=s["posts_per_cycle"]), f"a:set:{cid}:posts_per_cycle")], [B(tr(lang, "b_daily_cap", n=str(s.get("daily_posts_cap") or "∞")), f"a:set:{cid}:daily_posts_cap")], [B(tr(lang, "b_lookback", n=s["lookback_hours"]), f"a:set:{cid}:lookback_hours"), B(tr(lang, "b_undated", i=onoff(s["allow_undated"])), f"a:tog:{cid}:allow_undated")],
          [B(tr(lang, "b_quiet"), f"a:quiet:{cid}"), B(tr(lang, "b_tz"), f"a:tz:{cid}")], [B(tr(lang, "b_mode", m=tr(lang, "auto" if s["mode"] == "auto" else "review")), f"a:mode:{cid}")], [B(tr(lang, "back"), f"a:ch:{cid}")]]
    await render(update, context, text, kb)

async def view_quiet(update, context, cid):
    lang = L(update); s = get_settings(cid)
    await render(update, context, tr(lang, "quiet_pick_start", tz=off_label(s["utc_offset"]), loc=local_clock(s["utc_offset"])), hour_grid(f"a:qs:{cid}") + [[B(tr(lang, "quiet_off"), f"a:qoff:{cid}")], [B(tr(lang, "back"), f"a:sch:{cid}")]])

async def view_tz(update, context, cid):
    from newsbot.timeutils import TZ_ZONES
    lang = L(update); s = get_settings(cid); cur = float(s["utc_offset"]); i = 1 if lang == "en" else 0
    rows = pairs([B(("✅ " if abs(o - cur) < 0.01 else "") + f"{TZ_NAMES[z][i]} {off_label(o)}", f"a:tzs:{cid}:{k}") for k, (z, o) in enumerate(TZ_ZONES)])
    await render(update, context, tr(lang, "tz_pick", utc=utc_clock()), rows + [[B(tr(lang, "back"), f"a:sch:{cid}")]])

# ══════════════════════════════════════════════════════════════════════════
# صف انتشار و مقاله‌ها
# ══════════════════════════════════════════════════════════════════════════
async def view_queue(update, context, cid, status="ready"):
    uid = update.effective_user.id; lang = L(update); ch = channel_owned(cid, uid)
    if not ch: return await view_admin_home(update, context)
    arts = articles_by_status(cid, status, 15 if status == "ready" else 5)
    if status == "ready": text = tr(lang, "queue_title", title=esc(ch["title"]), n=len(arts))
    else: text = tr(lang, "rej_title", title=esc(ch["title"])) + "\n" + "".join(f"\n• {esc((r['title'] or hostname(r['url']))[:40])} — <i>{esc(reason_text(r['reason'], lang))}</i>" for r in arts)
    kb = [[B(f"{'⭐' + str(a['score']) if a['score'] else '•'} {(a['title'] or hostname(a['url']))[:35]}", f"a:art:{a['id']}")] for a in arts]
    if status == "ready" and arts: kb.append([B(tr(lang, "pub_first"), f"a:pub:{arts[0]['id']}")])
    if status == "ready": kb.append([B(tr(lang, "queue_reset"), f"a:qreset:{cid}")])
    kb.append([B(tr(lang, "back"), f"a:ch:{cid}")]); await render(update, context, text, kb)

async def view_article(update, context, aid):
    uid = update.effective_user.id; lang = L(update); a = get_article(aid)
    if not a or (a["admin_id"] != uid and not is_super(uid)): await popup(update, context, tr(lang, "notfound")); return await view_admin_home(update, context)
    cid = a["channel_id"]; body, _ = fit_html(a["post_html"] or tr(lang, "art_nobody"), 2600); i = 1 if lang == "en" else 0
    rt = reason_text(a["reason"], lang)
    text = tr(lang, "art_title", title=esc((a["title"] or "")[:80]), score=a["score"] or "—", cat=esc(a["category"] or "—"), st=STATUS.get(a["status"], (a["status"], a["status"]))[i], reason=f" · {esc(rt)}" if rt else "", host=esc(hostname(a["url"])), body=body)
    kb = []
    if a["status"] == "ready":
        kb.append([B(tr(lang, "art_pub"), f"a:pub:{aid}"), B(tr(lang, "art_edit"), f"a:arte:{aid}")]); row = [B(tr(lang, "delete"), f"a:artd:{aid}")]
        if a["full_html"]: row.insert(0, B(tr(lang, "art_full"), f"a:artf:{aid}"))
        kb.append(row)
    elif a["status"] == "published":
        links = json.loads(a["links"] or "[]")
        if links: kb.append([U(tr(lang, "art_view"), links[0])])
        kb.append([B(tr(lang, "art_del_arch"), f"a:artd:{aid}")])
    else:
        row = [B(tr(lang, "delete"), f"a:artd:{aid}")]
        if a["post_html"]: row.insert(0, B(tr(lang, "art_to_ready"), f"a:artr:{aid}"))
        kb.append(row)
    kb.append([B(tr(lang, "back"), f"a:que:{cid}" if a["status"] in ("ready", "published") else f"a:rej:{cid}")])
    await render(update, context, text, kb)

# ══════════════════════════════════════════════════════════════════════════
# هوش مصنوعی شخصی + پلن من
# ══════════════════════════════════════════════════════════════════════════
async def view_my_ai(update, context):
    """مدل‌های شخصیِ همین مدیر (جدا از مدل‌های عمومی مدیر کلان)."""
    uid = update.effective_user.id; lang = L(update); ms = list_models(owner_id=uid)
    text = tr(lang, "my_ai_title") + ("".join(tr(lang, "my_ai_line", i=_mic(m), name=esc(_mname(m)), model=esc(m["model"]), ok=m["ok_count"] or 0) for m in ms) if ms else tr(lang, "my_ai_none"))
    kb = pairs([B(f"{_mic(m)} {_mname(m)[:20]}", f"a:myai_v:{m['id']}") for m in ms]) + [[B(tr(lang, "my_ai_add"), "a:myai_add")], [B(tr(lang, "back"), "a:home")]]
    await render(update, context, text, kb)

async def view_my_ai_model(update, context, mid):
    uid = update.effective_user.id; lang = L(update); m = get_model(mid)
    if not m or m["owner_id"] != uid: await popup(update, context, tr(lang, "notfound")); return await view_my_ai(update, context)
    text = tr(lang, "my_ai_view", name=esc(m["name"]), kind=m["kind"], model=esc(m["model"]), base=esc(m["base_url"]), st=tr(lang, "s_m_off" if not m["active"] else "s_m_ok" if m["status"] == "ok" else "s_m_down"), ok=m["ok_count"] or 0, fc=m["fail_count"] or 0, lo=ago_text(m["last_ok"], lang), lf=ago_text(m["last_fail"], lang))
    kb = [[B(tr(lang, "s_model_test"), f"a:myai_t:{mid}"), B(tr(lang, "toggle"), f"a:myai_o:{mid}")]] + pairs([B(f"✏️ {f[2] if lang == 'en' else f[1]}", f"a:myai_e:{mid}:{f[0]}") for f in MODEL_FIELDS]) + [[B(tr(lang, "delete"), f"a:myai_d:{mid}")], [B(tr(lang, "back"), "a:myai")]]
    await render(update, context, text, kb)

async def view_my_plan(update, context):
    uid = update.effective_user.id; lang = L(update); lim = admin_limits(uid); use = usage_today(uid)
    pname = "∞" if is_super(uid) else (plan_txt(lim["plan"], "name", lang) or tr(lang, "no_plan"))
    text = tr(lang, "myplan", name=esc(pname), st=tr(lang, "st_active" if lim["active"] else "st_expired"), exp=tr(lang, "exp_at", d=fmt_date(lim["expires"], admin_offset(uid), True)) if lim["expires"] else "", nxt=("\n" + tr(lang, "plans_next", name=esc(plan_txt(lim["next_plan"], "name", lang)))) if lim["next_plan"] else "",
              p=use["posts"], pc=cap(lim["daily_posts"]), t=use["tests"], tc=cap(lim["daily_tests"]), s=count_sources(uid), sc=cap(lim["max_sources"]), c=len(list_channels(uid)), cc=cap(lim["max_channels"]))
    await render(update, context, text, [[B(tr(lang, "upgrade_btn"), "u:plans")], [B(tr(lang, "back"), "a:home")]])

# ══════════════════════════════════════════════════════════════════════════
# تست فوری (با محدودیت نرخ + گزارش زنده)
# ══════════════════════════════════════════════════════════════════════════
async def run_test(update, context, cid):
    from newsbot.core_users import remaining
    uid = update.effective_user.id; lang = L(update); ch = channel_owned(cid, uid); qy = update.callback_query
    if not ch: return await view_admin_home(update, context)
    # NEW v4: تست فوری هم از دروازه‌ی کانال اجباری می‌گذرد (عملیات ادمین است)
    if await mand_gate(update, context, uid, lang): return
    rem = remaining(uid, "tests")
    if rem is not None and rem <= 0: await popup(update, context, tr(lang, "limit_tests", n=admin_limits(uid)["daily_tests"]), alert=True); return await view_channel(update, context, cid)
    if ch_lock(cid).locked(): await popup(update, context, tr(lang, "test_busy"), alert=True); return await view_channel(update, context, cid)
    if not is_super(uid) and not rate_free(f"test:{cid}", config.TEST_COOLDOWN_SEC): await popup(update, context, tr(lang, "test_wait", n=rate_left(f"test:{cid}", config.TEST_COOLDOWN_SEC)), alert=True); return await view_channel(update, context, cid)
    try: await qy.answer(); context._callback_answered = True
    except Exception: pass
    last = [0.0]
    async def progress(pct, txt):
        if time.time() - last[0] < 1.6 and pct < 100:
            return
        last[0] = time.time()
        await operation_status(context, txt)
    await progress(1, tr(lang, "preparing"))
    model_labels = []; report_ai = _ai_status_reporter(context, lang)
    async def ai_progress(event, label, code):
        if event == "success" and label not in model_labels: model_labels.append(label)
        await report_ai(event, label, code)
    ai_token = _ai_events.set(ai_progress)
    try:
        res = await run_channel_cycle(context.bot, uid, cid, test_mode=True, progress=progress)
    except Exception as e:
        log_event("ERROR", f"تست کانال {ch['title']}: {e}", uid); d = Diag(); d.add("ai_fail", err=ai_err_text(err_code(e), lang)); res = {"published": 0, "queued": 0, "links": [], "diag": d, "src": ""}
    except asyncio.CancelledError:
        raise
    finally:
        _ai_events.reset(ai_token)
    ok = bool(res["published"] or res.get("queued"))
    if ok and not res.get("published"): rate_mark(f"test:{cid}")   # تستِ موفقِ صف‌شده
    elif not ok: rate_clear(f"test:{cid}")
    diag = esc(res["diag"].render(lang)); kb = []
    src = (res.get("src") or "").strip(); src_line = ("\n" + tr(lang, "test_src", name=esc(src[:60]))) if src else ""
    if res["published"]: text = f"{tr(lang, 'test_ok')}{src_line}\n\n{tr(lang, 'test_log', d=diag)}"; kb.append([U(f"{tr(lang, 'art_view')} {i + 1}", l) for i, l in enumerate(res["links"][:3])])
    elif res.get("queued"): text = f"{tr(lang, 'test_queued')}{src_line}\n\n{tr(lang, 'test_log', d=diag)}"; kb.append([B(tr(lang, "queue", n=ready_count(cid)), f"a:que:{cid}")])
    else: text = f"{tr(lang, 'test_fail')}{src_line}\n\n{tr(lang, 'test_log', d=diag)}\n\n{tr(lang, 'test_retry_note')}"; kb.append([B(tr(lang, "rejected"), f"a:rej:{cid}"), B(tr(lang, "sched"), f"a:sch:{cid}")])
    op = _current_operation.get()
    def _final_label(lbl):
        if lbl in ("مدل عمومی", "Public model", "مدل عمومی/پیش‌فرض", "Public/default model"):
            try:
                mm = get_model(gget("test_model_id")) if gget("test_model_id") else None
                if mm and mm.get("model"): return f"{'مدل عمومی' if lang == 'fa' else 'Public model'} ({mm['model']})"
            except Exception: pass
            return "مدل عمومی" if lang == "fa" else "Public model"
        return lbl
    model_line = ("مدل: " if lang == "fa" else "Model: ") + esc(", ".join(_final_label(l) for l in model_labels)) if model_labels else ""
    links_count = len(res.get("links", []))
    final = tr(lang, "test_summary_ok", n=links_count) if ok else tr(lang, "test_summary_fail")
    if model_line: final += "\n\n" + model_line
    await operation_status(context, final, failed=not ok, terminal=True)
    if op: op.failed = not ok; op.final_status = True
    kb.append([B(tr(lang, "back_panel"), f"a:ch:{cid}")]); await render(update, context, text, kb)
