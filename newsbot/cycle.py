# -*- coding: utf-8 -*-
"""چرخه‌ی یک کانال (کشف → استخراج → فیلتر → تولید → انتشار) + تشخیص مرحله‌ای.
FIX v4:
  • backpressure صف: اگر ready_count >= MAX_QUEUE_SIZE باشد، نامزدهای تازه درج نمی‌شوند
    (قبلاً صف بدون سقف رشد می‌کرد و دیتابیس زیر بار می‌ماند)
  • کلیدهای DIAG «soft_window» و «queue_full» اضافه شدند (در نسخهٔ قبل soft_window ثبت می‌شد
    ولی در دیکشنری DIAG نبود ⇒ render با KeyError می‌ترکد)
  • دروازه‌ی کانال اجباری (فقط ادمین): اگر عضو نباشد، چرخه با تشخیص واضح متوقف می‌شود
"""
import asyncio, json, secrets, time
from datetime import timedelta
from newsbot import config
from newsbot.db import q, gset, arun
from newsbot.logutils import log_ctx, log_event, bump, _metrics
from newsbot.timeutils import now_utc, now_iso, parse_dt, url_hash
from newsbot.concurrency import ch_lock, CYCLE_SEM
from newsbot.core_users import admin_limits, admin_limits_cached, remaining, usage_today, _LIMITS_TTL
from newsbot.core_channels import get_channel, get_settings, update_settings, in_quiet, mandatory_configured, mandatory_membership
from newsbot.core_sources import list_sources, source_of_article
from newsbot.core_articles import (article_insert, article_update, get_article, articles_by_status,
                                   ready_count, dedup_mark, title_fp, soft_window_candidate)
from newsbot.discover import discover_source, hostname
from newsbot.extract import extract_article
from newsbot.adfilter import heuristic_ad_check
from newsbot.generate import generate, compose
from newsbot.publish import _publish_article_locked, bot_can_post
from newsbot.ai_models import ai_err_text
from newsbot.opctx import _current_operation, step_add, operation_checkpoint
from newsbot.ui_i18n import DIAG, STAGES


class Diag:
    def __init__(self): self.items, self.hints, self.stage = [], [], "start"
    def add(self, key, **kw):
        self.items.append((key, kw))
        op = _current_operation.get()
        if op is None: return
        mapping = {
            "busy": ("run", "⏳ در حال بررسیِ قبلی…"), "checks": ("run", "🔎 بررسی راه‌اندازی…"),
            "no_channel": ("fail", "❌ کانال یافت نشد"), "plan_inactive": ("fail", "⛔ پلن فعال نیست"),
            "quota_posts": ("fail", "📉 سهمیه‌ی امروز تمام شد"), "quota_tests": ("fail", "🧪 سهمیه‌ی تست تمام شد"),
            "no_sources": ("fail", "📭 هیچ منبعی فعال نیست"), "src_cooldown": ("run", "⏳ منبع در حال بازیابی…"),
            "src_fail": ("fail", "❌ خطای منبع"), "src_notmod": ("run", "⏳ منبع تغییر نکرده است"),
            "src_empty": ("fail", "📭 خروجی منبع خالی"), "found": ("ok", "📥 مورد یافت شد"),
            "extract": ("run", "🧾 استخراج محتوا…"), "low": ("run", "📏 بررسی امتیاز…"), "ad": ("run", "🚫 بررسی تبلیغ…"),
            "ai": ("run", "⏳ در حال تولید با AI…"), "queue": ("run", "⏳ در صف انتشار…"),
            "published": ("ok", "✅ منتشر شد"), "queued": ("ok", "📌 در انتظار"),
            "mand_missing": ("fail", "🔐 عضویت کانال اجباری تأیید نشده"),
        }
        st = mapping.get(key)
        if st: step_add(op, key, status=st[0], **kw)
    def hint(self, key, **kw):
        if key not in [h[0] for h in self.hints]: self.hints.append((key, kw))
    def keys(self): return sorted({k for k, _ in self.items if k not in ("src_ok", "src_notmod", "found", "detail", "leftover", "retry")})
    def render(self, lang="fa", with_stage=True):
        i = 1 if lang == "en" else 0; lines = []
        for k, kw in self.items:
            d = DIAG.get(k)
            if not d: continue                      # FIX v4: کلید ناشناخته هرگز render را نمی‌ترکاند
            try: lines.append(d[i].format(**kw))
            except Exception: lines.append(d[i])
        if with_stage: lines.append(DIAG["stage"][i].format(stage=STAGES.get(self.stage, STAGES["start"])[i]))
        for k, kw in self.hints:
            d = DIAG.get(k)
            if not d: continue
            try: lines.append(d[i].format(**kw))
            except Exception: lines.append(d[i])
        return "\n".join(lines)
    def to_json(self): return json.dumps({"items": self.items, "hints": self.hints, "stage": self.stage}, ensure_ascii=False)


def _within_lookback(pub_iso, s, hours=None):
    if not pub_iso: return bool(s["allow_undated"])
    d = parse_dt(pub_iso)
    if d is None: return bool(s.get("allow_undated", True))     # بدون تاریخ ⇒ تصمیم با تنظیم کانال (پیش‌تر همیشه رد می‌شد)
    h = int(hours if hours is not None else (s.get("lookback_hours") or 72))
    return (now_utc() - d) <= timedelta(hours=h)

def _pub_err(code, lang):
    if code == "bot_not_admin": return DIAG["bot_not_admin"][1 if lang == "en" else 0]
    if code == "duplicate": return "duplicate" if lang == "en" else "تکراری"
    return code.replace("send_failed:", "")

def article_src_name(aid):
    """نام منبعِ تولیدکننده‌ی یک مقاله (برای نمایش به مدیر در تست فوری و چرخه‌ی خودکار)."""
    a = get_article(aid); src = source_of_article(a) if a else None
    return ((src["title"] or "").strip() or hostname(src["url"])) if src else ""


PROG = {"fa": {"start": "شروع…", "src": "بررسی {n} منبع…", "found": "{n} مقاله پیدا شد", "extract": "استخراج {i}/{n}: {t}", "ai": "تولید با AI: {t}", "queue": "⏳ همه‌ی مدل‌ها مشغول‌اند؛ در صف پردازش…", "pub": "انتشار…", "done": "پایان"},
        "en": {"start": "Starting…", "src": "Checking {n} sources…", "found": "{n} articles found", "extract": "Extracting {i}/{n}: {t}", "ai": "Generating with AI: {t}", "queue": "⏳ All models busy; queued…", "pub": "Publishing…", "done": "Done"}}

def _res(D): return {"found": 0, "processed": 0, "accepted": 0, "rejected": 0, "queued": 0, "published": 0, "errors": 0, "links": [], "src": "", "pub": [], "diag": D, "msg": ""}

async def run_channel_cycle(bot, uid, cid, test_mode=False, progress=None):
    lk = ch_lock(cid)
    if lk.locked():
        D = Diag(); D.add("busy"); r = _res(D); r["msg"] = D.render((await arun(get_settings, cid))["ui_lang"], False); return r
    async with lk:
        async with CYCLE_SEM: return await _cycle(bot, uid, cid, test_mode, progress)

async def _cycle(bot, uid, cid, test_mode, progress):
    _cy0 = time.monotonic(); cyid = f"cy{cid}-{secrets.token_hex(2)}"   # شناسهٔ همبستگی این چرخه
    log_ctx(op=cyid, channel=cid, article=None, source=None, model=None, stage="start")
    D = Diag(); res = _res(D); s = await arun(get_settings, cid); ch = await arun(get_channel, cid); lim = await arun(admin_limits, uid); lang = s["ui_lang"]; P = PROG[lang if lang in PROG else "fa"]
    ch_tz = s.get("tz") or s["utc_offset"]   # ARCH-10/11: منطقهٔ کانال برای تاریخ‌های بی‌tz فید/صفحه و مرزهای روز
    ran = False
    async def p(pct, txt):
        if progress:
            try: await progress(pct, txt)
            except Exception: pass
    async def fin():
        _ms = int((time.monotonic() - _cy0) * 1000)
        bump("cycle_ms", _ms); bump("cycle_n")
        _metrics["queue"] = await arun(ready_count, cid)
        await arun(gset, "metrics", _metrics)          # snapshot ماندگار (بعد از restart هم دیده می‌شود)
        log_event("INFO", f"چرخه تمام شد در {_ms}ms (found={res.get('found', 0)} accepted={res.get('accepted', 0)} published={res.get('published', 0)})")
        res["msg"] = D.render(lang, with_stage=False)
        # PLB-09 (فاز ۷): last_run فقط اگر خط لوله واقعاً آغاز شده باشد به‌روز می‌شود (نه در خروج‌های زودهنگام،
        # نه وقتی چرخه وسط کار کرش/لغو شده) — وگرنه یک شکست، کانال را تا interval بعدی در سکوت نگه می‌داشت.
        await arun(update_settings, cid, last_end=now_iso(), last_result=res["msg"][:1500], last_diag=D.to_json(), **({"last_run": now_iso()} if ran else {})); return res
    D.stage = "checks"
    if not ch: D.add("no_channel"); return await fin()
    if not lim["active"]: D.add("plan_inactive"); D.hint("hint_quota"); return await fin()
    sources = await arun(list_sources, cid, active_only=True)
    if not sources: D.add("no_sources"); D.hint("hint_sources"); return await fin()
    await arun(admin_limits_cached, uid, max_age=_LIMITS_TTL)
    field = "tests" if test_mode else "posts"; rem = await arun(remaining, uid, field, cid if field == "posts" else None)
    if rem is not None and rem <= 0: D.add("quota_tests" if test_mode else "quota_posts", used=(await arun(usage_today, uid))[field], cap=lim["daily_tests" if test_mode else "daily_posts"]); D.hint("hint_quota"); return await fin()
    if not await bot_can_post(bot, ch["chat_id"]): D.add("bot_not_admin"); D.hint("hint_admin"); return await fin()
    # دروازه‌ی کانال اجباری — کانالِ همان زبانِ کاربر (fa/en مستقل)
    if mandatory_configured(lang):
        okm, _join = await mandatory_membership(bot, uid, lang)
        if not okm: D.add("mand_missing"); D.hint("hint_mand"); return await fin()
    target = 1 if test_mode else min(rem if rem is not None else 99, int(s["posts_per_cycle"])); ran = True; await p(5, P["start"])
    # ══ stage 1/5: discover ══ منابع هم‌زمان (FETCH_SEM) و هر منبع مستقل خطا می‌دهد
    D.stage = "discover"; await p(10, P["src"].format(n=len(sources)))
    async def one(src):
        try:
            name = hostname(src["url"]); lf = parse_dt(src["last_fetch"])
            fc = int(src["fail_count"] or 0) if "fail_count" in src.keys() else 0
            nta = parse_dt(src["next_try_after"]) if "next_try_after" in src.keys() else None
            if fc >= config.SOURCE_FAIL_LIMIT and nta and now_utc() < nta: return src, name, None, False, "", "paused"
            if not test_mode and lf and (now_utc() - lf) < timedelta(minutes=config.SOURCE_COOLDOWN_MIN): return src, name, None, False, "", "cooldown"
            items, changed, method = await discover_source(src, use_cache=not test_mode, tz=ch_tz); return src, name, items, changed, method, None
        except Exception as e: return src, str(src["url"])[:40], None, False, "", str(e)[:80]
    results = await asyncio.gather(*[one(src) for src in sources], return_exceptions=True)
    leftovers = await arun(q, "SELECT id,url,title,published_at FROM articles WHERE channel_id=? AND status='discovered' AND source_id IN (SELECT id FROM sources WHERE active=1 AND channel_id=articles.channel_id) ORDER BY id DESC LIMIT 10", (cid,))
    candidates, all_items, n_old, n_dup, n_qfull, seen = [], [], 0, 0, 0, set()
    qfull = (await arun(ready_count, cid)) >= config.MAX_QUEUE_SIZE   # FIX v4: backpressure — صف پر ⇒ درج تازه متوقف
    for _r in results:
        if isinstance(_r, BaseException): res["errors"] += 1; log_event("WARN", f"منبع بی‌نام: {_r}"); continue
        src, name, items, changed, method, err = _r
        log_ctx(source=name, stage="source")            # لاگ‌های همین منبع با نامش می‌آیند
        step_add(_current_operation.get(), "src_check", key=name, status="run")   # هر منبع جداگانه: «دارم X را بررسی می‌کنم»
        if err in ("cooldown", "paused"): D.add("src_cooldown", name=name); step_add(_current_operation.get(), "src_cooldown", key=name, status="run"); continue
        if err: res["errors"] += 1; D.add("src_fail", name=name, err=err); step_add(_current_operation.get(), "src_fail", key=name, status="fail"); log_event("WARN", f"منبع {src['url']}: {err}", uid); continue
        if not changed: D.add("src_notmod", name=name); step_add(_current_operation.get(), "src_notmod", key=name, status="run"); continue
        if not items: D.add("src_empty", name=name); step_add(_current_operation.get(), "src_empty", key=name, status="fail"); continue
        new = 0
        # ── stage: filter ── دسته‌ای‌سازی: قبلاً برای هر آیتم ۳ کوئری جدا (article_exists/posted_before/dedup_seen)
        # می‌رفت ⇒ ۲۵ آیتم × ۱۰ منبع = ۷۵۰ کوئری در هر چرخه؛ حالا ۳ کوئری برای هر منبع (items با limit≤۲۵ ⇒ پارامتر < ۹۹).
        _ph = ", ".join("?" * len(items))
        _hs = [url_hash(it["url"]) for it in items]
        _have = {r["hash"] for r in await arun(q, f"SELECT hash FROM articles WHERE channel_id=? AND hash IN ({_ph})", (cid, *_hs))} if _hs else set()
        _done = {r["hash"] for r in await arun(q, f"SELECT hash FROM posted WHERE channel_id=? AND hash IN ({_ph})", (ch["chat_id"], *_hs))} if _hs else set()
        _fps = {url_hash(it["url"]): title_fp(it["title"] or it["url"]) for it in items}
        _dups = {r["fp"] for r in await arun(q, f"SELECT fp FROM dedup WHERE channel_id=? AND fp IN ({','.join('?' * len(set(_fps.values())))})", (cid, *set(_fps.values())))} if _fps else set()
        for it in items:
            h = url_hash(it["url"]); all_items.append((it, h))
            if h in seen or h in _have or h in _done: continue   # همان آدرس قبلاً ساخته/منتشر شده
            seen.add(h)
            fp = _fps[h]
            if fp in _dups:        # همان خبر با آدرس/منبع دیگر → تولید دوباره نمی‌شود (این‌کار با خود ربات، نه AI)
                n_dup += 1; step_add(_current_operation.get(), "src_dup", key=name, status="fail"); continue
            if it.get("published") and not _within_lookback(it["published"], s): await arun(article_insert, uid, cid, h, it["url"], it["title"], src["id"], it["published"], "skipped", "old"); n_old += 1; continue
            if qfull: n_qfull += 1; continue   # FIX v4: صف پر است؛ آیتم دور ریخته نمی‌شود، فقط درج نمی‌شود (چرخهٔ بعدی برمی‌دارد)
            aid = await arun(article_insert, uid, cid, h, it["url"], it["title"], src["id"], it.get("published"))
            if aid: candidates.append({"aid": aid, "url": it["url"], "title": it["title"], "published": it.get("published"), "html": it.get("html") or "", "media": it.get("media")}); new += 1; await arun(dedup_mark, cid, fp); _dups.add(fp)   # دو آیتم هم‌عنوان در همین دسته هم باید دومی رد شود
        D.add("src_ok", name=name, m=method, n=len(items), new=new)
        step_add(_current_operation.get(), "src_found", key=name, status="ok") if new else step_add(_current_operation.get(), "src_empty", key=name, status="fail")
        if new: await arun(q, "UPDATE sources SET found_total=found_total+? WHERE id=?", (new, src["id"]), commit=True)
    candidates.sort(key=lambda c: c["published"] or "", reverse=True)
    lo = [] if qfull else [{"aid": r["id"], "url": r["url"], "title": r["title"], "published": r["published_at"], "html": "", "media": None} for r in leftovers if r["id"] not in {c["aid"] for c in candidates}]
    if lo: D.add("leftover", n=len(lo)); candidates += lo
    if n_old: D.add("old", n=n_old, h=s["lookback_hours"])
    if n_dup: D.add("dup", n=n_dup)
    if n_qfull: D.add("queue_full", n=n_qfull)
    if not candidates and all_items and not qfull:
        # پنجره‌ی نرم: هیچ‌چیز در بازه‌ی کانال نبود ⇒ تازه‌ترین خبرهای همین منابع را برمی‌داریم تا چرخه دست‌خالی نماند
        retry = 0
        for it, h in all_items:
            # PUB-01: soft_window_candidate مقالاتِ delivery_unknown را رد می‌کند (ری‌کیو آن‌ها پست تکراری می‌ساخت)
            aid_sw = await arun(soft_window_candidate, cid, ch["chat_id"], h)
            if aid_sw:
                await arun(article_update, aid_sw, status="discovered", reason=""); candidates.append({"aid": aid_sw, "url": it["url"], "title": it["title"], "published": it.get("published"), "html": it.get("html") or "", "media": it.get("media"), "soft": True}); retry += 1
            if retry >= 8: break
        if retry: D.add("soft_window", n=retry)
    res["found"] = len(candidates); await p(40, P["found"].format(n=len(candidates)))
    if not candidates:
        if not n_qfull: D.add("none_found"); D.hint("hint_sources")
        return await fin()
    D.add("found", n=len(candidates))
    # ══ stage 3/5: extract + stage 4/5: generate ══ استخراج ۳تایی هم‌زمان، تولید ترتیبی تا رسیدن به هدف
    quiet = in_quiet(s); cnt = {"extract": 0, "undated": 0, "ad": 0, "ai_ad": 0, "low": 0}; best = 0.0; details = []; ai_err = None; i = 0; stop = False
    # مانده‌ی صف ready از چرخه‌های قبلی اول منتشر می‌شود تا FIFO حفظ شود؛ داخل سقف همان چرخه (posts_per_cycle) و فقط در حالت خودکار خارج از خاموشی
    drained = 0
    if not test_mode and s["mode"] == "auto" and not quiet:
        backlog = [r["id"] for r in await arun(articles_by_status, cid, "ready", 50, automatic=True)]
        rem_now = await arun(remaining, uid, "posts", cid)
        if backlog and (rem_now is None or rem_now > 0):
            D.add("leftover", n=len(backlog)); D.stage = "publish"
            for aid_b in backlog:
                if (rem_now is not None and rem_now <= 0) or res["published"] >= target: break
                try:
                    ok, out = await _publish_article_locked(bot, aid_b, count_usage=True)
                except asyncio.CancelledError:
                    a_b = await asyncio.shield(arun(get_article, aid_b))
                    if a_b and a_b["status"] == "ready": await asyncio.shield(arun(article_update, aid_b, reason="cancelled"))
                    raise
                if ok:
                    res["published"] += 1; res["links"].append(out); res["pub"].append((((await arun(get_article, aid_b))["title"] or "")[:60], await arun(article_src_name, aid_b), out))
                    rem_now = None if rem_now is None else rem_now - 1; drained += 1
                elif out == "duplicate" or out == "delivery_unknown" or (isinstance(out, str) and out.startswith("send_failed")):
                    continue   # GAP-08: شکست/ابهامِ یک مقاله صفِ drain را نمی‌شکند؛ مقالهٔ بعدی تلاش می‌شود
                else:
                    break
        target -= drained
    while i < len(candidates) and not stop:
        chunk = candidates[i:i + 3]; i += len(chunk); D.stage = "extract"
        await p(40 + int(50 * i / len(candidates)), P["extract"].format(i=i, n=len(candidates), t=(chunk[0]["title"] or chunk[0]["url"])[:40]))
        arts = await asyncio.gather(*[extract_article(c["url"], c["html"], tz=ch_tz) for c in chunk], return_exceptions=True)
        for c, art in zip(chunk, arts):
            if res["accepted"] >= target: stop = True; break
            aid = c["aid"]; res["processed"] += 1
            src_name = await arun(article_src_name, aid) or hostname(c["url"])
            if not art or isinstance(art, Exception):
                # PLB-06 (فاز ۷): شکست استخراج موقتی است (شبکه/فیلتر موقت سایت) و نباید «ردشدهٔ» قطعی شود؛
                # failed از پنجرهٔ نرم دوباره برگردانده می‌شود (soft_window_candidate وضعیت‌های failed را می‌پذیرد).
                await arun(article_update, aid, status="failed", reason="extract_failed"); res["errors"] += 1; cnt["extract"] += 1; step_add(_current_operation.get(), "src_extract_fail", key=src_name, status="fail"); continue
            if not art["title"]: art["title"] = c["title"] or hostname(c["url"])
            if not art["media"] and c["media"]: art["media"] = c["media"]
            D.stage = "filter"
            # هر نامزد (کشف‌شده، مانده‌ی صف یا تلاش مجدد) بلافاصله پیش از تولید دوباره با پنجره‌ی زمانی سنجیده می‌شود
            pub_when = art.get("published") or c.get("published")
            window_ok = _within_lookback(pub_when, s)
            if not window_ok and c.get("soft"): window_ok = _within_lookback(pub_when, s, hours=config.MAX_LOOKBACK)
            if not window_ok:
                await arun(article_update, aid, status="skipped", reason="old" if pub_when else "undated", **({"published_at": pub_when} if pub_when else {}))
                if not pub_when: cnt["undated"] += 1
                step_add(_current_operation.get(), "src_skip_old", key=src_name, status="fail")
                continue
            bad, why, _ = await asyncio.to_thread(heuristic_ad_check, art, s["strict_ads"])
            if bad: await arun(article_update, aid, status="rejected", reason=f"ad_filter: {why}"); res["rejected"] += 1; cnt["ad"] += 1; details.append((art["title"], "ad: " + why)); step_add(_current_operation.get(), "src_ad", key=src_name, status="fail"); continue
            D.stage = "ai"; pct = min(94, 40 + int(50 * i / len(candidates))); await p(pct, P["ai"].format(t=art["title"][:40]))
            async def _on_queue(_pct=pct): await p(_pct, P["queue"])
            log_ctx(article=aid, stage="ai")
            gen, model, err = await generate(s, art, on_queue=_on_queue, owner_id=uid)
            log_ctx(model=model, stage="compose" if gen else "ai-fail")
            if not gen:
                # FIX سیاست v4: شکست AI «rejected» نیست؛ وضعیت failed (قابل تلاش مجدد) ثبت می‌شود
                await arun(article_update, aid, status="failed", reason=f"ai: {err}"); res["errors"] += 1; ai_err = err
                step_add(_current_operation.get(), "src_ai_fail", key=src_name, status="fail", model=model)
                continue
            D.stage = "score"
            best = max(best, gen["score"])
            if gen["score"] < float(s["min_score"]): await arun(article_update, aid, status="rejected", reason=f"score {gen['score']} < {s['min_score']}", score=gen["score"]); res["rejected"] += 1; cnt["low"] += 1; details.append((art["title"], f"score {gen['score']}")); step_add(_current_operation.get(), "src_low_score", key=src_name, status="fail", score=gen["score"]); continue
            post, full = await asyncio.to_thread(compose, s, ch, art, gen)
            await arun(article_update, aid, title=gen.get("title") or art["title"], post_html=post, full_html=full, score=gen["score"], category=gen.get("category"), model=model, media=json.dumps(art["media"]) if art["media"] else None, published_at=art["published"], status="ready", reason=""); res["accepted"] += 1
            sname = await arun(article_src_name, aid)
            if sname: res["src"] = sname; D.add("from_src", name=sname)
            if test_mode or (s["mode"] == "auto" and not quiet):
                D.stage = "publish"
                try:
                    await p(96, P["pub"])
                    ok, out = await _publish_article_locked(bot, aid, count_usage=not test_mode, test_user=uid if test_mode else None)
                except asyncio.CancelledError:
                    a = await asyncio.shield(arun(get_article, aid))
                    if a and a["status"] == "ready": await asyncio.shield(arun(article_update, aid, reason="cancelled"))
                    raise
                if ok:
                    res["published"] += 1; res["links"].append(out); res["pub"].append(((gen.get("title") or art["title"] or "")[:60], sname, out))
                else:
                    res["errors"] += 1; D.add("pub_fail", err=_pub_err(out, lang))
                    if out == "bot_not_admin": D.hint("hint_admin"); stop = True; break
                    if isinstance(out, str) and out.startswith("retry_after"): stop = True; break
            else: res["queued"] += 1
    # --- جمع‌بندی
    if cnt["extract"]: D.add("extract_fail", n=cnt["extract"])
    if cnt["undated"]: D.add("undated", n=cnt["undated"]); D.hint("hint_undated")
    if cnt["ad"]: D.add("ad", n=cnt["ad"])
    if ai_err: D.add("ai_fail", err=ai_err_text(ai_err, lang)); D.hint("hint_ai")
    if cnt["ai_ad"]: D.add("ai_ad", n=cnt["ai_ad"])
    if cnt["low"]: D.add("low", n=cnt["low"], min=s["min_score"], best=best)
    for t, w in details[:3]: D.add("detail", title=(t or "")[:40], why=w[:50])
    if res["published"]: D.add("published", n=res["published"]); D.stage = "done"
    elif res["queued"]:
        D.add("quiet" if quiet else "review", n=res["queued"]); D.stage = "done"
        if not quiet: D.hint("hint_review")
    elif not ai_err and res["accepted"] == 0:
        top = max(cnt["ad"], cnt["low"], cnt["extract"])
        if top and cnt["ad"] == top: D.hint("hint_ads")
        elif top and cnt["low"] == top: D.hint("hint_score")
        elif top and cnt["extract"] == top: D.hint("hint_extract")
    await p(100, P["done"]); return await fin()

async def flush_ready(bot, uid, cid, max_n=2):
    lk = ch_lock(cid)
    if lk.locked(): return 0
    async with lk:
        s = await arun(get_settings, cid); ch = await arun(get_channel, cid)
        if not ch or ch["admin_id"] != uid or not (await arun(admin_limits_cached, uid))["active"]: return 0
        if s["mode"] != "auto" or in_quiet(s) or not s["enabled"]: return 0
        if mandatory_configured(s["ui_lang"]):   # اتوماسیون بدون عضویت تأییدشدهٔ ادمین در کانال اجباری اجرا نمی‌شود
            okm, _join = await mandatory_membership(bot, uid, s["ui_lang"])
            if not okm: return 0
        ru = parse_dt(s.get("_retry_until") or "")
        if ru and now_utc() < ru: return 0
        rem = await arun(remaining, uid, "posts", cid); sent = 0
        for a in await arun(articles_by_status, cid, "ready", max_n, automatic=True):
            if rem is not None and rem <= 0: break
            operation_checkpoint()
            ok, out = await _publish_article_locked(bot, a["id"])
            if ok: sent += 1; rem = None if rem is None else rem - 1
            elif out in ("duplicate", "delivery_unknown"): continue
            elif isinstance(out, str) and out.startswith("retry_after:"):
                # توقف موقت drain با ذخیره‌ی زمان بازگشتی (۵ دقیقه سقف) تا چرخه‌ی بعدی دوباره تلاش کند
                try: secs = int(out.split(":", 1)[1])
                except ValueError: secs = 60
                await arun(update_settings, cid, _retry_until=(now_utc() + timedelta(seconds=min(max(secs, 5), 300))).isoformat()); break
            else:
                # یک مقالهٔ خراب سرِ صف را نمی‌بندد؛ بقیهٔ آماده‌ها در همین گذر تلاش می‌شوند
                await arun(update_settings, cid, _retry_until=(now_utc() + timedelta(seconds=90)).isoformat()); continue
        return sent
