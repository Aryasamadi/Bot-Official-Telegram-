# -*- coding: utf-8 -*-
"""لایهٔ ارسالِ محتوا با پیام بومی تلگرام (Rich Messages) و fallbackِ کلاسیک.

``python-telegram-bot==22.8`` متد ``sendRichMessage`` را ندارد (آخرین Bot API پشتیبانی‌شده
آن پیش از معرفی Rich Messages است). بنابراین این لایه، فراخوانی را خودش روی HTTP
انجام می‌دهد و خطاها را به استثناهای هم‌خانوادهٔ PTB نگاشت می‌کند تا مسیرهای
موجودِ انتشار (RetryAfter / Forbidden / BadRequest) بدون تغییر کار کنند.

قواعد:

  • خطای ۴xx (یعنی «چیزی ارسال نشد») ⇒ fallback به ``send_message`` و شمارش خطا.
  • خطای شبکه/۵xx/۴۲۹ ⇒ استثنا بالا می‌رود؛ چون ممکن است پیام رفته باشد، ارسالِ دوباره
    می‌تواند پست تکراری بسازد.
  • بعد از ``RICH_FAIL_LIMIT`` خطای پیاپی، پیام بومی برای کلِ فرایند غیرفعال می‌شود
    (مثلاً سرور Bot API محلیِ قدیمی) و همه‌چیز از مسیر کلاسیک می‌رود.
  • در هیچ حالتی محتوای پیام از بین نمی‌رود: fallback دقیقاً همان متنِ اصلی را
    با مسیرِ ``split_html``/``downgrade_html`` می‌فرستد.
"""
import asyncio

import httpx
from types import SimpleNamespace

from telegram.error import BadRequest, Forbidden, NetworkError, RetryAfter, TelegramError

from newsbot import config
from newsbot.logutils import log, bump, redact
from newsbot.net_http import http
from newsbot import rich_format
from newsbot import text_format

_state = {"ok": 0, "fail": 0, "disabled": False, "fallback": {}, "last_error": ""}

# دلیل‌های ثابتِ تنزل به قالب کلاسیک. هر دلیل یک شمارنده و یک لاگِ قابل‌جست‌وجو دارد
# ⇒ در لاگِ Production می‌شود دید *چرا* پیام بومی فرستاده نشد (قبلاً بی‌صدا بود).
FALLBACK_MODE_OFF = "mode_off"        # RICH_MESSAGES=off (انتخابِ صریحِ اپراتور)
FALLBACK_CIRCUIT = "circuit_open"     # مدار خاموش شد (سرور متد را ندارد)
FALLBACK_NO_API = "no_bot_api"        # بطری اجازهٔ فراخوانیِ مستقیمِ Bot API را نداد
FALLBACK_NO_BLOCKS = "no_blocks"       # محتوا بلوکِ بومی نداشت (متنِ ساده)
FALLBACK_UNFAITHFUL = "unfaithful"    # تجزیه وفادار نبود ⇒ حفظِ متنِ اصلی
FALLBACK_TOO_LONG = "too_long"        # باید به چند پیام شکسته می‌شد
FALLBACK_REJECTED = "rejected"        # سرور پیامِ بومی را رد کرد (۴xx)
FALLBACK_NOT_SUPPORTED = "not_supported"   # سرور اصلاً متدِ sendRichMessage را ندارد (۴۰۴)

# نشانه‌هایی که یعنی «این سرور اصلاً پیامِ بومی را نمی‌شناسد» (نه «این پیام مشکل دارد»)
_UNSUPPORTED_MARKERS = ("method not found", "unknown method", "method_not_found",
                        "not supported", "unsupported", "not implemented", "unrecognized")
# نشانه‌های «خودِ قابلیت» در متنِ خطا: بدونِ آن‌ها، کلمهٔ «unsupported» فقط دربارهٔ
# *محتوا* است (مثلاً «unsupported element») و نباید مدارِ پیام بومی را خاموش کند.
_CAPABILITY_HINTS = ("method", "endpoint", "sendrichmessage")


def rich_status():
    """وضعیتِ مدار پیام بومی (برای پایش و تست)."""
    st = dict(_state)
    st["fallback"] = dict(_state.get("fallback") or {})
    st["mode"] = config.RICH_MESSAGES
    st["enabled"] = rich_available()
    return st


def reset_rich_state():
    """بازنشانیِ مدار (تست‌ها)."""
    _state.update(ok=0, fail=0, disabled=False, fallback={}, last_error="")


def note_fallback(reason, detail=""):
    """ثبتِ دلیلِ تنزل به قالب کلاسیک.

    قبلاً این مسیر کاملاً بی‌صدا بود (یا فقط یک لاگِ INFO بدونِ ساختار) ⇒ از روی لاگِ
    Production معلوم نبود که پیام بومی اصلاً امتحان شده یا نه. حالا هر تنزل یک شمارنده
    و یک لاگِ قابل‌جست‌وجو (``rich_fallback reason=…``) دارد."""
    fb = _state.setdefault("fallback", {})
    fb[reason] = int(fb.get(reason) or 0) + 1
    if detail:
        _state["last_error"] = redact(str(detail))[:200]
    try:
        bump("rich_fallback", sub=reason)
    except Exception:
        pass
    log.warning("rich_fallback reason=%s%s", reason, (" detail=%s" % str(detail)[:140]) if detail else "")
    return reason


def rich_available():
    """آیا پیام بومی در این فرایند فعال است؟"""
    if config.RICH_MESSAGES == "off":
        return False
    return not _state["disabled"]


def _note_success():
    _state["ok"] += 1
    _state["fail"] = 0


def _note_failure(err, unsupported=False):
    """شمارشِ خطا؛ فقط «نبودِ قابلیت» مدار را خاموش می‌کند.

    یک خطای محتوایی (مثلاً پیامِ خیلی بلند) نباید پیامِ بومی را برای کلِ فرایند
    خاموش کند؛ ولی ۴۰۴ (متد وجود ندارد) یعنی این سرور پشتیبانی نمی‌کند."""
    if not unsupported:
        return
    _state["fail"] += 1
    if _state["fail"] >= max(1, config.RICH_FAIL_LIMIT):
        _state["disabled"] = True
        log.warning("rich messages disabled after %d failures: %s", _state["fail"], err)


def _is_unsupported(err):
    """آیا این خطا یعنی «سرور پیامِ بومی را پشتیبانی نمی‌کند»؟

    ۴۰۴ ⇒ متد وجود ندارد ⇒ قطعاً ناتوانیِ سرور. ولی خطای ۴۰۰ با واژهٔ «unsupported»
    دربارهٔ *محتوا* (مثلاً «unsupported element») ناتوانیِ سرور نیست ⇒ مدار نباید
    خاموش شود (وگرنه یک پیامِ بد، پیامِ بومی را برای کلِ فرایند می‌کُشد).

    ۴۰۴ فقط وقتی «نبودِ متد» است که متنِ خطا هم به متد/endpoint اشاره کند؛ ۴۰۴ِ
    مربوط به *رسانه* (مثلاً «عکس پیدا نشد») نباید مدارِ پیام بومی را خاموش کند."""
    text = ("%s %s" % (type(err).__name__, err)).lower()
    has_hint = any(h in text for h in _CAPABILITY_HINTS)
    if getattr(err, "status_code", None) == 404:
        return has_hint
    if not has_hint:
        return False
    return any(m in text for m in _UNSUPPORTED_MARKERS)


def _real_bot(bot):
    """بطریِ واقعی (پوشش‌هایی مثل ``PublicationTransport`` خودش را نگه می‌دارند).

    نکتهٔ حیاتی (ریشهٔ «پیامِ بومی در Production دیده نمی‌شد»): در PTB 20+ صفتِ
    ``Bot.bot`` **پروفایلِ ``User``ِ ربات** است، نه «بطریِ زیرین». کدِ قدیمی
    ``getattr(bot, "bot", bot)`` در Production یک ``User`` برمی‌گرداند که نه
    ``base_url`` دارد و نه ``token`` ⇒ ``can_raw_send`` همیشه False می‌شد و پیامِ
    بومی هرگز امتحان نمی‌شد (و همه‌چیز بی‌صدا به قالبِ کلاسیک می‌افتاد).

    اینجا اول خودِ شیء را (که ``base_url``/``token`` دارد) برمی‌داریم و فقط در
    صورتِ نبود، یک لایه پوشش (مثل ``PublicationTransport.bot``) را باز می‌کنیم."""
    obj = bot
    for _ in range(4):
        if obj is None: break
        try:
            if getattr(obj, "base_url", None) and getattr(obj, "token", None):
                return obj
            nxt = getattr(obj, "bot", None)
        except Exception:
            break
        if nxt is None or nxt is obj: break
        # در PTB 20+ ``bot`` یک User است؛ User بطری نیست ⇒ ادامه نمی‌دهیم
        if not (getattr(nxt, "base_url", None) or getattr(nxt, "token", None)): break
        obj = nxt
    return obj


def _endpoint(bot, method):
    real = _real_bot(bot)
    base = getattr(real, "base_url", None)
    if base:
        return "%s/%s" % (str(base).rstrip("/"), method)
    token = getattr(real, "token", None)
    if not token:
        raise BadRequest("bot token is not available")
    return "https://api.telegram.org/bot%s/%s" % (token, method)


def can_raw_send(bot):
    """آیا این بطری اجازهٔ فراخوانیِ مستقیمِ Bot API را می‌دهد؟"""
    try:
        real = _real_bot(bot)
    except Exception:
        return False
    return bool(getattr(real, "base_url", None)) and bool(getattr(real, "token", None))


def _raise_for_payload(status, data):
    desc = str((data or {}).get("description") or "").strip()
    if status == 429:
        params = (data or {}).get("parameters") or {}
        try:
            wait = int(params.get("retry_after") or 1)
        except (TypeError, ValueError):
            wait = 1
        raise RetryAfter(wait)
    if status == 403:
        raise Forbidden(desc or "Forbidden")
    if status in (400, 404, 409, 422):
        err = BadRequest(desc or "HTTP %s" % status)
        # کدِ وضعیت نگه داشته می‌شود تا «متد وجود ندارد» (۴۰۴) از «محتوا مشکل دارد» (۴۰۰)
        # قابلِ تشخیص باشد؛ مدار فقط در حالتِ اول خاموش می‌شود.
        try:
            err.status_code = status
        except Exception:
            pass
        raise err
    if status >= 500:
        raise NetworkError(desc or "HTTP %s" % status)
    raise TelegramError(desc or "HTTP %s" % status)


async def api_call(bot, method, payload):
    """فراخوانیِ مستقیم Bot API؛ خروجی: ``result`` یا استثنای هم‌خانوادهٔ PTB."""
    url = _endpoint(bot, method)
    try:
        r = await http().post(url, json=payload, timeout=httpx.Timeout(30, connect=10))
    except httpx.HTTPError as e:
        raise NetworkError("%s: %s" % (method, e)) from e
    try:
        data = r.json()
    except Exception as e:
        raise NetworkError("%s: invalid response (%s)" % (method, type(e).__name__)) from e
    if not isinstance(data, dict):
        raise NetworkError("%s: unexpected response" % method)
    if data.get("ok"):
        return data.get("result") or {}
    _raise_for_payload(r.status_code, data)


def _to_message(bot, data):
    real = _real_bot(bot)
    try:
        from telegram import Message
        return Message.de_json(data, real)
    except Exception:
        chat = data.get("chat") or {}
        return SimpleNamespace(message_id=data.get("message_id"),
                               chat=SimpleNamespace(id=chat.get("id")),
                               text=data.get("text"))


def _markup_payload(markup):
    if markup is None:
        return None
    if hasattr(markup, "to_dict"):
        return markup.to_dict()
    return markup


async def send_rich_raw(bot, chat_id, rich_html, *, reply_to_message_id=None, reply_markup=None,
                        protect_content=False, disable_notification=False, is_rtl=None):
    """یک پیام بومی (Rich Message) می‌فرستد؛ هیچ fallbackی در این تابع نیست."""
    rich = {"html": rich_html}
    if is_rtl is None:
        is_rtl = rich_format.detect_rtl(rich_html)
    if is_rtl:
        rich["is_rtl"] = True
    payload = {"chat_id": chat_id, "rich_message": rich}
    if reply_to_message_id:
        payload["reply_parameters"] = {"message_id": int(reply_to_message_id)}
    markup = _markup_payload(reply_markup)
    if markup is not None:
        payload["reply_markup"] = markup
    if protect_content:
        payload["protect_content"] = True
    if disable_notification:
        payload["disable_notification"] = True
    data = await api_call(bot, "sendRichMessage", payload)
    return _to_message(bot, data)


async def send_legacy(bot, chat_id, text, *, reply_to_message_id=None, reply_markup=None,
                      disable_web_page_preview=True, split=True, delay=0.3):
    """مسیرِ کلاسیکِ فعلیِ پروژه: تکه‌تکه، HTML و در صورت رد شدنِ پارس، تنزل به HTML ساده.

    ``split=False`` متن را دقیقاً همان‌طور که هست (بدون پاک‌سازیِ دوباره) می‌فرستد؛
    برای مسیرهایی که خودشان سقفِ طول را اعمال کرده‌اند.
    """
    last = None
    extra = {"reply_to_message_id": reply_to_message_id} if reply_to_message_id else {}
    chunks = text_format.split_html(text or "") if split else [text or ""]
    for n, chunk in enumerate(chunks):
        kwargs = dict(extra)
        if reply_markup is not None:
            kwargs["reply_markup"] = reply_markup
        try:
            last = await bot.send_message(chat_id, chunk, parse_mode="HTML",
                                          disable_web_page_preview=disable_web_page_preview,
                                          **kwargs)
        except BadRequest:
            last = await bot.send_message(chat_id, text_format.downgrade_html(chunk),
                                          parse_mode="HTML",
                                          disable_web_page_preview=disable_web_page_preview,
                                          **kwargs)
        if delay and n < len(chunks) - 1:
            await asyncio.sleep(delay)
    return last


async def send_content(bot, chat_id, text, *, kind=None, doc=None, reply_to_message_id=None,
                       reply_markup=None, disable_web_page_preview=True, protect_content=False,
                       disable_notification=False, split_legacy=True, media_html="",
                       fallback_legacy=True):
    """ارسالِ محتوا با موتور مشترک.

    ابتدا پیام بومی تلگرام (تیتر/جدول/فهرست واقعی) امتحان می‌شود؛ اگر سرور یا
    کلاینت پشتیبانی نکند، همان متنِ اصلی با مسیر کلاسیک فرستاده می‌شود.

    ``media_html``: بلوکِ رسانه (مثلاً ``<img src="https://…"/>``) که **قبل از**
    متنِ پیامِ بومی می‌نشیند ⇒ عکس و متنِ ساختاریافته در *یک* پیام.

    ``fallback_legacy=False``: وقتی سرور پیامِ بومی را رد کند، به‌جای فرستادنِ
    متنِ کلاسیک، ``None`` برمی‌گردد تا فراخوان (مثلاً مسیرِ «رسانه + کپشن»)
    تصمیمِ خودش را بگیرد.

    هر مسیری که به قالب کلاسیک می‌رود **دلیلش ثبت و لاگ می‌شود** (``rich_fallback
    reason=…``) تا در Production معلوم باشد پیام بومی امتحان شده یا نه.
    """
    raw = "" if text is None else str(text)
    document = doc if doc is not None else rich_format.parse_content(raw, kind=kind)
    media_html = str(media_html or "").strip()
    if config.RICH_MESSAGES == "off":
        note_fallback(FALLBACK_MODE_OFF, "RICH_MESSAGES=off")
        return await _legacy_or_none(bot, chat_id, raw, reply_to_message_id, reply_markup,
                                     split_legacy, disable_web_page_preview, fallback_legacy)
    if _state["disabled"]:
        note_fallback(FALLBACK_CIRCUIT, _state.get("last_error") or "rich circuit open")
        return await _legacy_or_none(bot, chat_id, raw, reply_to_message_id, reply_markup,
                                     split_legacy, disable_web_page_preview, fallback_legacy)
    if not document.blocks:
        note_fallback(FALLBACK_NO_BLOCKS, "no native blocks in the content")
        return await _legacy_or_none(bot, chat_id, raw, reply_to_message_id, reply_markup,
                                     split_legacy, disable_web_page_preview, fallback_legacy)
    if not document.faithful:
        note_fallback(FALLBACK_UNFAITHFUL, "the parsed document lost content")
        return await _legacy_or_none(bot, chat_id, raw, reply_to_message_id, reply_markup,
                                     split_legacy, disable_web_page_preview, fallback_legacy)
    if not can_raw_send(bot):
        note_fallback(FALLBACK_NO_API, "the bot cannot call the Bot API directly")
        return await _legacy_or_none(bot, chat_id, raw, reply_to_message_id, reply_markup,
                                     split_legacy, disable_web_page_preview, fallback_legacy)
    rich_html = rich_format.render_rich(document)
    parts = rich_format.split_doc(document, config.RICH_MAX_CHARS, rich_format.render_rich)
    if len(parts) > 1 or len(rich_html) + len(media_html) > rich_format.RICH_CHAR_LIMIT:
        # محتوا باید به چند پیام شکسته می‌شد ⇒ پیامِ بومیِ تک‌پیام ممکن نیست
        note_fallback(FALLBACK_TOO_LONG, "content needs %d part(s)" % len(parts))
        return await _legacy_or_none(bot, chat_id, raw, reply_to_message_id, reply_markup,
                                     split_legacy, disable_web_page_preview, fallback_legacy)
    payload_html = ("%s\n%s" % (media_html, rich_html)) if media_html else rich_html
    # جهتِ نوشتن از روی متنِ اصلی سنجیده می‌شود (نه از روی تگ‌های HTML)
    is_rtl = rich_format.detect_rtl(raw) if raw else rich_format.detect_rtl(rich_html)
    try:
        sender = getattr(bot, "send_rich_message", None)
        if sender is not None:
            msg = await sender(chat_id, payload_html, reply_to_message_id=reply_to_message_id,
                               reply_markup=reply_markup, protect_content=protect_content,
                               disable_notification=disable_notification)
        else:
            msg = await send_rich_raw(bot, chat_id, payload_html,
                                      reply_to_message_id=reply_to_message_id,
                                      reply_markup=reply_markup,
                                      protect_content=protect_content,
                                      disable_notification=disable_notification,
                                      is_rtl=is_rtl)
        _note_success()
        _state["last_error"] = ""
        # لاگِ موفقیت: تا وقتی این نبود، از روی لاگ معلوم نبود که پیام بومی
        # واقعاً فرستاده شده یا به مسیر کلاسیک افتاده است.
        log.info("rich message sent natively: chat=%s blocks=%s chars=%s rtl=%s media=%s",
                 chat_id, len(document.blocks), len(payload_html), bool(is_rtl),
                 "yes" if media_html else "no")
        return msg
    except BadRequest as e:
        # ۴xx یعنی «چیزی ارسال نشد» ⇒ fallback بی‌خطر است.
        unsupported = _is_unsupported(e)
        _note_failure(e, unsupported=unsupported)
        note_fallback(FALLBACK_NOT_SUPPORTED if unsupported else FALLBACK_REJECTED, str(e))
        return await _legacy_or_none(bot, chat_id, raw, reply_to_message_id, reply_markup,
                                     split_legacy, disable_web_page_preview, fallback_legacy)
    except (RetryAfter, Forbidden, NetworkError):
        raise
    except Exception as e:
        # خطای ناشناخته ⇒ ممکن است پیام رفته باشد؛ fallback نکن تا پست تکراری نشود.
        _note_failure(e, unsupported=_is_unsupported(e))
        _state["last_error"] = redact(str(e))[:200]
        raise


async def _legacy_or_none(bot, chat_id, raw, reply_to_message_id, reply_markup,
                          split_legacy, disable_web_page_preview, fallback_legacy):
    """مسیر کلاسیک؛ اگر فراخوان اجازه نداده، فقط ``None`` برمی‌گردد."""
    if not fallback_legacy:
        return None
    return await send_legacy(bot, chat_id, raw, reply_to_message_id=reply_to_message_id,
                             reply_markup=reply_markup, split=split_legacy,
                             disable_web_page_preview=disable_web_page_preview)
