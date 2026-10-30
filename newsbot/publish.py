# -*- coding: utf-8 -*-
"""رسانه و انتشار — دانلود امن با retry/backoff/jitter و انتشار idempotent و crash-safe.
FIX v4:
  • _GuardedStream و download_media از public_url_strict استفاده می‌کنند (کاهش پنجرهٔ DNS rebinding)
  • download_media برای 429/5xx تا ۳ تلاش با backoff نمایی + jitter + Retry-After دارد
    (قبلاً ۲ تلاش بدون backoff بود و رسانه‌های سالم بی‌دلیل text-only می‌شدند)
  • media در ابتدای _publish_article_locked مقداردهی می‌شود (بررسی شد: از قبل امن بود؛ صریح نگه داشته شد)
"""
import asyncio, contextlib, html, json, os, random, re, tempfile, time
from types import SimpleNamespace
from urllib.parse import urlparse
import httpx
from telegram.error import BadRequest, RetryAfter, NetworkError, Forbidden
from newsbot import config
from newsbot import runtime
from newsbot.logutils import log, log_event, log_ctx, bump
from newsbot.timeutils import now_iso
from newsbot.net_http import http, public_url_strict, UA_BROWSER, UA_FEED, gen_deeplink_key, save_deeplink, retry_after_seconds
from newsbot.concurrency import FETCH_SEM, publish_sem, ch_lock
from newsbot.db import arun
from newsbot.discover import _ext_kind
from newsbot.core_channels import get_channel, get_settings, msg_link
from newsbot.core_articles import (get_article, article_update, get_pub_journal, set_pub_journal, reserve_publication,
                                   release_publication, settle_publication, posted_before, PublicationTransport)
from newsbot.text_format import clean_ai_text, strip_source_url, split_post_html, hard_html, downgrade_html, strip_tags
from newsbot import rich_send
from newsbot.generate import signature_of
from newsbot.opctx import operation_checkpoint, _current_operation
from newsbot.ui_widgets import esc


def _media_headers(media):
    """Referer دامنهٔ خبر: بسیاری از سایت‌ها بدون آن به تصویر و ویدیو پاسخ ۴۰۳ می‌دهند (علت اصلی «عکس برداشت نمی‌شود»)."""
    h = {"Accept": "image/avif,image/webp,image/*,video/*,*/*;q=0.8", "User-Agent": UA_BROWSER}
    page = (media or {}).get("page") or ""
    try:
        p = urlparse(page)
        if p.scheme and p.netloc: h["Referer"] = f"{p.scheme}://{p.netloc}/"
    except Exception: pass
    return h

class _GuardedStream:
    """جریان GET برای مدیا با ریدایرکت دستی: هر جهش قبل از رفتن با public_url_strict اعتبارسنجی می‌شود.
    کلاینت به‌طور پیش‌فرض follow_redirects=True است و همین راه فرارِ SSRF بود؛ fetch این کار را می‌کرد ولی اینجا نمی‌شد.
    روی تغییر میزبان، هدرهای اعتبارسنجی حذف می‌شوند."""
    def __init__(self, url, headers, hops=5, timeout=None, deadline=None):
        self.url = str(url); self.headers = dict(headers or {})
        self.hops = hops; self.timeout = timeout or httpx.Timeout(180, connect=15)
        self.deadline = deadline   # ARCH-02 (فاز ۷): سقف مطلق (monotonic) برای کل زنجیرهٔ ریدایرکت؛ None یعنی فقط timeout هر درخواست
        self._stack = contextlib.AsyncExitStack(); self.response = None
    async def __aenter__(self):
        await self._stack.__aenter__()
        current = self.url; headers = dict(self.headers)
        try:
            for _ in range(self.hops):
                if self.deadline is not None and time.monotonic() >= self.deadline:
                    await self._stack.aclose(); return None
                if not await asyncio.to_thread(public_url_strict, current):
                    await self._stack.aclose(); return None
                r = await self._stack.enter_async_context(http().stream("GET", current, headers=headers, follow_redirects=False, timeout=self.timeout))
                if r.status_code in (301, 302, 303, 307, 308):
                    loc = r.headers.get("location")
                    await r.aclose()
                    if not loc: await self._stack.aclose(); return None
                    nxt = urljoin_local(current, loc)
                    if (urlparse(nxt).hostname or "").lower().rstrip(".") != (urlparse(current).hostname or "").lower().rstrip("."):
                        headers = {k: v for k, v in headers.items() if k.lower() not in {"authorization", "x-api-key", "apikey", "cookie"}}
                    current = nxt
                    continue
                return r      # خودِ پاسخ برگردانده می‌شود تا فراخوان مثل قبل r.status_code / aiter_bytes داشته باشد
            await self._stack.aclose(); return None
        except BaseException:
            await self._stack.aclose(); raise
    async def __aexit__(self, *a): return await self._stack.__aexit__(*a)

def urljoin_local(a, b):
    from urllib.parse import urljoin as _uj
    return _uj(a, b)

def _retry_wait(r, attempt):
    """FIX v4 + فاز ۷: صبر بر پایهٔ Retry-After سرور (ثانیه یا HTTP-date با هلپر مشترک net_http)؛ در غیاب آن backoff نمایی + jitter."""
    wait = retry_after_seconds(r.headers.get("retry-after"))
    if wait is None or wait <= 0: wait = min(2 ** attempt, 8)
    return min(wait, config.RETRY_AFTER_MAX) + random.uniform(0, 1)

async def download_media(media):
    """دانلود جریانی تا سقف MAX_MEDIA_MB با retry + backoff + jitter + Retry-After برای 429/5xx.
    اگر نوبت اول رد شد، یک‌بار با هدر دیگری دوباره تلاش می‌شود. فایل موقت در هیچ مسیری روی دیسک جا نمی‌ماند."""
    if not media or not media.get("url"): return None
    if not await asyncio.to_thread(public_url_strict, media["url"]): return None
    # ARCH-02 (فاز ۷): سقف کلِ دانلود (همهٔ هدرها/تلاش‌ها) — بدنهٔ آهسته (slow-loris) نباید چرخه را بی‌نهایت نگه دارد
    hard_deadline = time.monotonic() + config.MEDIA_TOTAL_SEC
    for hdr in (_media_headers(media), {"User-Agent": UA_FEED, "Accept": "*/*"}):
        attempt = 0
        while attempt < 3:
            if time.monotonic() >= hard_deadline:
                log.info("media deadline exceeded "); return None
            tmp = None; path = None; wait = None; retryable = False
            try:
                async with FETCH_SEM:
                    async with _GuardedStream(media["url"], hdr, deadline=hard_deadline) as r:
                        if r is None: break
                        if r.status_code in (429, 500, 502, 503, 504):
                            retryable = attempt < 2; wait = _retry_wait(r, attempt)
                        elif r.status_code != 200: break
                        else:
                            ct = r.headers.get("content-type", "").lower()
                            if ct.startswith("text/") or "html" in ct: return None
                            try:
                                if int(r.headers.get("content-length") or 0) > config.MAX_MEDIA_BYTES: log.info(f"media skipped ( > {config.MAX_MEDIA_MB}MB upload ceiling) "); return None
                            except Exception: pass
                            kind = "animation" if "gif" in ct else "photo" if ct.startswith("image/") else "video" if ct.startswith("video/") else _ext_kind(media["url"], media.get("kind") or "document")
                            ext = {"photo": ".jpg", "animation": ".gif", "video": ".mp4"}.get(kind, os.path.splitext(urlparse(media["url"]).path)[1] or ".bin")
                            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=ext); size = 0
                            try:
                                # ARCH-02: خواندن بدنه هم داخل ددلاین کل است (read-timeout هرگز جای سقف کل را نمی‌گیرد)
                                async with asyncio.timeout(max(1.0, hard_deadline - time.monotonic())):
                                    async for chunk in r.aiter_bytes(262144):
                                        size += len(chunk)
                                        if size > config.MAX_MEDIA_BYTES: log.info(f"media skipped ( > {config.MAX_MEDIA_MB}MB upload ceiling) "); return None
                                        tmp.write(chunk)
                            finally:
                                tmp.close()
                            if size < 1024:
                                retryable = attempt < 2; wait = min(2 ** attempt, 8) + random.uniform(0, 1)   # فایل ناقص → تلاش دوباره
                            else:
                                if kind == "photo" and size > 10 * 1024 * 1024: kind = "document"
                                path = tmp.name
            except (httpx.HTTPError, asyncio.TimeoutError, OSError) as e:
                log.warning(f"media attempt {attempt}: {e}"); retryable = attempt < 2; wait = min(2 ** attempt, 8) + random.uniform(0, 1)
            except BaseException:
                raise
            finally:
                if tmp is not None and path is None:   # مالکیت فایل فقط پس از خروج سالم از استریم منتقل می‌شود
                    try: os.unlink(tmp.name)
                    except OSError: pass
            if path: return path, kind
            if not retryable: break
            await asyncio.sleep(wait); attempt += 1
    return None

def _is_parse_err(e): s = str(e).lower(); return "parse" in s or "entit" in s or "tag" in s or "unsupported start" in s

async def _send_media(bot, chat_id, caption, pm, media):
    """رسانه با کپشن؛ ابتدا با URL، سپس دانلود و آپلود. None ⇒ رسانه قابل ارسال نیست (متن تنها فرستاده می‌شود)."""
    kind = media.get("kind", "photo"); mf = media.get("_file")
    if not mf and not (str(media.get("url") or "").startswith(("http://", "https://")) and urlparse(str(media.get("url") or "")).netloc):
        log.info("media dropped: non-http(s) url ")   # آدرس نامعتبر به تلگرام داده نمی‌شود ⇒ پست بدون رسانه
        return None
    if not mf and kind == "photo":
        try: return await bot.send_photo(chat_id, media["url"], caption=caption, parse_mode=pm)
        except BadRequest as e:
            if _is_parse_err(e): raise
        except (NetworkError, RetryAfter, Forbidden): raise
        except Exception: raise
    if not mf: mf = await download_media(media); media["_file"] = mf
    for alt in (media.get("alt") or [])[:2]:          # منبع اصلی نیامد؟ نامزدهای جانشین همان مقاله
        if mf: break
        log.info(f"media fallback → {str(alt)[:90]}")
        mf = await download_media({**media, "url": alt}); media["_file"] = mf
    if not mf:
        log.info(f"media dropped ({kind}): دریافت فایل ممکن نشد — پست بدون رسانه می‌رود")
        return None
    path, k = mf
    try:
        with open(path, "rb") as f:
            fn = getattr(bot, f"send_{k}"); kw = {k: f}
            if k == "video": kw["supports_streaming"] = True
            if k in ("video", "animation", "document"): kw.update(read_timeout=240, write_timeout=600, connect_timeout=30, pool_timeout=120)
            return await fn(chat_id, caption=caption, parse_mode=pm, **kw)
    except RetryAfter: raise
    except BadRequest as e:
        if _is_parse_err(e): raise
        log.warning(f"send_{k}: {e}"); return None
    except (NetworkError, Forbidden): raise
    except Exception:
        raise

async def send_post(bot, chat_id, text, media=None):
    """PH-07/PERF-007: bound all Bot API uploads with one global semaphore."""
    async with publish_sem():
        return await _send_post_locked(bot, chat_id, text, media)


async def _send_post_locked(bot, chat_id, text, media=None):
    """رسانه + کپشنِ یک‌تکه (کپشن همیشه زیر محدودیت ۱۰۲۴ تلگرام می‌ماند) · خطای پارس → تنزل تدریجی فرمت (معمولی → ساده).
    هیچ‌وقت پست را به دو پیام نمی‌شکند: ادامهٔ محتوا فقط از راه «بیشتر» داخل ربات دیده می‌شود.
    مسیرِ متنی اول با پیامِ بومیِ تلگرام (تیتر/جدول/فهرست واقعی) امتحان می‌شود و در صورت
    عدم پشتیبانیِ سرور، خودکار به پیامِ کلاسیک تنزل می‌کند — محتوا هیچ‌وقت حذف نمی‌شود."""
    variants = [(text, "HTML"), (downgrade_html(text), "HTML"), (strip_tags(text), None)]
    sent_media = SimpleNamespace(message_id=media["_sent_id"]) if media and media.get("_sent_id") else None
    for i, (t, pm) in enumerate(variants):
        cap = None
        try:
            if media:
                # کپشن هرگز از محدودیت تلگرام نمی‌گذرد؛ اگر گذشت همان‌جا بریده می‌شود نه با پیام دوم (تک‌پیام می‌ماند)
                cap_ok = len(t) <= config.CAPTION_LIMIT
                cap = t if cap_ok else hard_html(t, max(200, config.CAPTION_LIMIT - 60))   # سقف سخت: هرگز از محدودیت کپشن رد نمی‌شود
                if isinstance(bot, PublicationTransport): bot.caption_complete = True
                m = sent_media or await _send_media(bot, chat_id, cap, pm, media)
                if m: return m
            if len(t) > config.MSG_LIMIT: t = hard_html(t, config.MSG_LIMIT - 100)   # وگرنه پست بالای ۴۰۹۶ با «too long» هرگز منتشر نمی‌شد
            reply = {"reply_to_message_id": sent_media.message_id} if sent_media else {}
            if i == 0 and pm == "HTML" and not media:
                return await rich_send.send_content(bot, chat_id, t, disable_web_page_preview=True,
                                                    split_legacy=False, **reply)
            return await bot.send_message(chat_id, t, parse_mode=pm, disable_web_page_preview=True, **reply)
        except RetryAfter as e:
            wait = float(e.retry_after) + 1
            # قبلاً سقف ثابت ۳۰ ثانیه بود: اگر تلگرام ۶۰ ثانیه می‌خواست، بعد از ۳۰ ثانیه دوباره محدود می‌شدیم.
            if wait > config.RETRY_AFTER_MAX: raise   # بیشتر از سقف ما: تسلیم به مسیر انتشار (delivery_unknown) بهتر از اصرار است
            await asyncio.sleep(wait)
            # PUB-03: همان مسیر دوباره — رسانه با تلاش مجدد همان رسانه حفظ می‌شود، نه اینکه به «متن تنها» تنزل یابد
            if media and not sent_media and cap is not None:
                m = await _send_media(bot, chat_id, cap, pm, media)
                if m: return m
            return await bot.send_message(chat_id, t, parse_mode=pm, disable_web_page_preview=True, **({"reply_to_message_id": sent_media.message_id} if sent_media else {}))
        except BadRequest as e:
            if _is_parse_err(e) and i < len(variants) - 1: continue
            raise
    raise RuntimeError("format")

# ── حافظهٔ کوتاه خطای تلگرام ──
_tg_status: dict = {}   # chat_id -> (ts, reason)

def _tg_cache_fail(chat_id, reason):
    """ثبتِ کوتاه‌مدتِ شکست در _tg_status؛ مثل بقیهٔ کش‌ها سقف دارد تا با هر چتِ دیده‌شده بی‌کران رشد نکند."""
    _tg_status[chat_id] = (time.monotonic(), reason)
    if len(_tg_status) > 2000: [_tg_status.pop(k) for k in list(_tg_status)[:1000]]

def _is_rights_err(e):
    """True فقط وقتی خطا قطعاً یعنی «ربات ادمین/عضو نیست» — نه تایم‌اوت، نه خطای شبکه، نه محدودیت نرخ."""
    if isinstance(e, Forbidden): return True
    if isinstance(e, BadRequest):
        s = str(e).lower()
        return any(k in s for k in ("not enough rights", "chat not found", "member list is inaccessible", "not a member", "user not found"))
    return False

async def bot_can_post(bot, chat_id):
    st = _tg_status.get(chat_id)
    if st and time.monotonic() - st[0] < 180: return False
    try:
        me = await bot.get_chat_member(chat_id, bot.id)
        ok = me.status == "creator" or (me.status == "administrator" and getattr(me, "can_post_messages", True) is not False)
        if ok: _tg_status.pop(chat_id, None)
        else: _tg_cache_fail(chat_id, "mem")
        return ok
    except Exception as e:
        # خطای گذرا (شبکه/تایم‌اوت/محدودیت نرخ) کش نمی‌شود؛ وگرنه کانالِ سالم ۳ دقیقه بی‌دلیل رد می‌شد
        if _is_rights_err(e): _tg_cache_fail(chat_id, "err")
        else: _tg_status.pop(chat_id, None)
        return False

def channel_caption(s, ch, post_html, full_html, title, url):
    """متنِ کپشن کانال + نسخهٔ کاملِ «بیشتر».
    قواعد: فقط یک امضا و همیشه در انتهای همان پیام · طول کل ≤ سقف کپشن (POST_LIMIT_MAX) تا تلگرام پست را به دو پیام نشکند ·
    لینک «بیشتر» هر وقت نسخهٔ ربات محتوای بیشتری از کپشن داشته باشد.
    خروجی: (cut, tail, full_or_None) — کلید لینک «بیشتر» را فراخوان می‌سازد و خودش به متن می‌چسباند."""
    lang = s.get("ui_lang") or "fa"
    sig = signature_of(s, ch)
    tail = ("\n" + sig) if sig else ""
    post = clean_ai_text(strip_source_url(post_html or "", url), s)
    if sig:                                     # امضای نوشتهٔ مدل → یک نسخه، همیشه از سمت tail
        sig_txt = esc(sig).strip()
        while sig_txt and sig_txt in post:
            post = post.replace(sig_txt, "", 1)
        post = re.sub(r"\n{3,}", "\n\n", post).strip()
    full = clean_ai_text(strip_source_url(full_html or "", url), s) if full_html else ""
    limit = min(int(s.get("post_limit") or config.POST_LIMIT_DEFAULT), config.POST_LIMIT_MAX)
    more_len = len(f'\n\n<a href="https://t.me/{runtime.BOT_USERNAME}?start=r_XXXXXXXX">📖 {"بیشتر" if lang == "fa" else "more..."}</a>')
    budget = max(200, limit - len(tail) - more_len)
    has_more = bool(full.strip()) and (len(post) > budget or len(full.strip()) > len(post) + 150)
    if not has_more:
        return post, tail, None
    cut, rest = split_post_html(post, budget)
    body_full = full.strip() or ((f"<b>{html.escape(title or '')}</b>\n\n" + rest) if rest else "")
    # قرارداد split_post_html همان (بخش، باقی‌مانده) است — fit_html که (متن، بولین) برمی‌گرداند این‌جا کار نمی‌کند
    parts = []
    while body_full:
        chunk, body_full = split_post_html(body_full, config.BOT_FULL_MAX)
        parts.append(chunk)
    return cut, tail, "\n".join(parts)

async def publish_article(bot, aid, count_usage=True, test_user=None):
    a = await arun(get_article, aid)
    if not a: return False, "not_ready"
    async with ch_lock(a["channel_id"]):
        return await _publish_article_locked(bot, aid, count_usage, test_user)

def _release_with_current_reason(aid, journal):
    """آزادسازی ژورنال با دلیل *جاری* همان مقاله — یک تابع تا در مسیر لغو هم یک‌جا با arun اجرا شود."""
    current = get_article(aid)
    return release_publication(aid, journal, current["reason"] if current else "")

async def _publish_article_locked(bot, aid, count_usage=True, test_user=None):
    a = await arun(get_article, aid)
    if not a or a["status"] != "ready" or not a["post_html"]: return False, "not_ready"
    ch = await arun(get_channel, a["channel_id"]); admin_id = a["admin_id"]
    if not ch: return False, "no_channel"
    operation_checkpoint()
    journal = await arun(get_pub_journal, aid)
    if journal and (journal.get("link") or journal.get("phase") == "ack"):
        link = journal.get("link") or msg_link(ch, SimpleNamespace(message_id=journal["message_id"]))
        await arun(settle_publication, aid, ch, journal)
        return True, link
    if journal:
        phase = journal.get("phase")
        # PUB-01: بین in_flight و unsettledِ بدون رسانه تفاوتی نیست — هر دو یعنی «شاید واقعاً رفته باشد»؛
        # دیگر هرگز ارسال خودکار نمی‌شود (خطر پست تکراری). unsettled با media_id یعنی رسانه رسیده و
        # کپشن/رسانه قبلاً تحویل شده — همان مسیر settle بدون ارسال دوباره ادامه می‌یابد.
        if phase == "in_flight" or (phase == "unsettled" and not journal.get("media_id")):
            if phase == "in_flight":
                # فقط فاز را می‌بندد؛ reserved_day حفظ می‌شود (سهمیه در ابهام تحویل برنمی‌گردد — PUB-05)
                await arun(release_publication, aid, journal, "delivery_unknown")
            # تحویل نامعلوم است: دوباره منتشر نمی‌شود و در صف «آماده» هم نمی‌ماند،
            # چون ماندن در صف سرِ صف را برای همیشه می‌بست و بقیهٔ آماده‌ها هرگز منتشر نمی‌شدند.
            await arun(article_update, aid, status="failed", reason="delivery_unknown")
            bump("posts_fail"); log_ctx(stage="delivery_unknown")
            return False, "delivery_unknown"
        journal = await arun(release_publication, aid, journal, a["reason"] or "")
    if ch["admin_id"] != admin_id: return False, "no_channel"
    if await arun(posted_before, ch["chat_id"], a["hash"]): await arun(article_update, aid, status="rejected", reason="duplicate"); return False, "duplicate"
    journal = await arun(reserve_publication, aid, admin_id, count_usage, test_user, journal)
    if journal is None: return False, "quota"
    media = None; published = False
    try:
        s = await arun(get_settings, ch["id"])
        if not await bot_can_post(bot, ch["chat_id"]):
            await arun(article_update, aid, reason="bot_not_admin"); return False, "bot_not_admin"
        operation_checkpoint()
        if await arun(posted_before, ch["chat_id"], a["hash"]):
            await arun(article_update, aid, status="rejected", reason="duplicate"); return False, "duplicate"
        try: media = json.loads(a["media"]) if a["media"] and s["include_media"] else None
        except (ValueError, TypeError): media = None; log.warning("corrupt media payload article=%s", aid)
        cut, tail, full_for_bot = await asyncio.to_thread(channel_caption, s, ch, a["post_html"], a["full_html"], a["title"], a["url"])
        text = cut + tail
        dl_key = dl_payload = None
        if full_for_bot:
            # PUB-04: کلید فقط تولید می‌شود (برای متن لازم است)؛ ردیف دیپ‌لینک پس از موفقیت ارسال نوشته می‌شود
            dl_key = gen_deeplink_key()
            dl_payload = {"short": cut, "full": full_for_bot, "title": a["title"], "url": a["url"], "show_source": bool(s.get("include_link", True)), "media": {k: v for k, v in media.items() if not k.startswith("_")} if media else None, "ts": now_iso()}
            text = cut + f'\n\n<a href="https://t.me/{runtime.BOT_USERNAME}?start=r_{dl_key}">📖 {"بیشتر" if (s.get("ui_lang") or "fa") == "fa" else "more..."}</a>' + tail
        if journal.get("media_id"):
            text = journal["text"]
            media = {"_sent_id": journal["media_id"]}
        elif media and media.get("kind") != "photo": media["_file"] = await download_media(media)
        journal["text"] = text
        await arun(set_pub_journal, aid, journal)
        operation_checkpoint()
        parent = asyncio.current_task()
        async def commit_send():
            nonlocal published
            operation_checkpoint()
            if parent.cancelling(): raise asyncio.CancelledError
            try: msg = await send_post(PublicationTransport(bot, aid, journal, parent), ch["chat_id"], text, media)
            except Exception as e:
                if journal.get("phase") == "ack":
                    published = True
                    raise
                if isinstance(e, RetryAfter):      # محدودیت نرخ تلگرام → به بالا گزارش می‌شود تا صف drain موقتاً بخوابد
                    await arun(article_update, aid, reason="rate_limited")
                    return False, f"retry_after:{min(max(int(getattr(e, 'retry_after', 30) or 30), 5), 300)}"
                if journal.get("phase") == "in_flight":
                    await arun(article_update, aid, reason="delivery_unknown")
                else:
                    n = (int(a["send_fails"]) if "send_fails" in a.keys() and a["send_fails"] is not None else 0) + 1
                    # شکست قطعیِ ارسال (پست بلند، Forbidden، خطای پارس): بعد از سه تلاش از صف بیرون می‌رود،
                    # وگرنه همیشه سرِ صف می‌ماند و هیچ‌کدام از آماده‌های بعدی هرگز منتشر نمی‌شوند.
                    await arun(article_update, aid, reason=f"send: {str(e)[:110]}", send_fails=n, **({"status": "failed"} if n >= 3 else {}))
                log.exception("publication failed article=%s channel=%s", aid, ch["id"])
                bump("posts_fail"); log_ctx(stage="send_failed")
                return False, f"send_failed:{str(e)[:120]}"
            published = True
            op = _current_operation.get()
            if op: op.published_count += 1
            link = msg_link(ch, msg)
            journal.update(phase="ack", link=link)
            await arun(set_pub_journal, aid, journal)
            await arun(settle_publication, aid, ch, journal)
            if dl_key and dl_payload:
                # PUB-04: ردیف دیپ‌لینک فقط پس از موفقیت ارسال نوشته می‌شود تا شکست قطعی ردیف یتیم نسازد
                try: await save_deeplink(dl_key, admin_id, dl_payload)
                except Exception: log.warning("deeplink save failed key=%s", dl_key[:6], exc_info=True)
            bump("posts_ok"); log_ctx(stage="published")
            log_event("INFO", f"منتشر شد در {ch['title']}: {(a['title'] or '')[:60]}", admin_id)
            return True, link
        sending = asyncio.create_task(commit_send(), name=f"publication:{aid}")
        try: return await asyncio.shield(sending)
        except asyncio.CancelledError:
            # Keep the channel locked until an in-flight delivery has settled, even on repeated cancellation.
            while not sending.done():
                try: await asyncio.shield(sending)
                except asyncio.CancelledError: continue
                except Exception: break
            if not sending.cancelled():
                try: sending.result()
                except Exception: log.exception("publication settlement failed article=%s", aid)
            raise
    except asyncio.CancelledError:
        if not published: await asyncio.shield(arun(article_update, aid, reason="delivery_unknown" if journal.get("phase") == "in_flight" else "cancelled"))
        raise
    finally:
        try:
            if not published and journal.get("phase") != "ack":
                await asyncio.shield(arun(_release_with_current_reason, aid, journal))
        finally:
            if media and media.get("_file"):
                try: os.unlink(media["_file"][0])
                except OSError: log.debug("media cleanup failed", exc_info=True)
