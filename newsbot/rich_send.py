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
from newsbot.logutils import log
from newsbot.net_http import http
from newsbot import rich_format
from newsbot import text_format

_state = {"ok": 0, "fail": 0, "disabled": False}


def rich_status():
    """وضعیتِ مدار پیام بومی (برای پایش و تست)."""
    st = dict(_state)
    st["mode"] = config.RICH_MESSAGES
    st["enabled"] = rich_available()
    return st


def reset_rich_state():
    """بازنشانیِ مدار (تست‌ها)."""
    _state.update(ok=0, fail=0, disabled=False)


def rich_available():
    """آیا پیام بومی در این فرایند فعال است؟"""
    if config.RICH_MESSAGES == "off":
        return False
    return not _state["disabled"]


def _note_success():
    _state["ok"] += 1
    _state["fail"] = 0


def _note_failure(err):
    _state["fail"] += 1
    if _state["fail"] >= max(1, config.RICH_FAIL_LIMIT):
        _state["disabled"] = True
        log.warning("rich messages disabled after %d failures: %s", _state["fail"], err)


def _real_bot(bot):
    """بطریِ واقعیِ PTB (پوشش‌هایی مثل PublicationTransport خودش را نگه می‌دارند)."""
    return getattr(bot, "bot", bot)


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
    real = _real_bot(bot)
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
        raise BadRequest(desc or "HTTP %s" % status)
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
                        protect_content=False, disable_notification=False):
    """یک پیام بومی (Rich Message) می‌فرستد؛ هیچ fallbackی در این تابع نیست."""
    rich = {"html": rich_html}
    if rich_format.detect_rtl(rich_html):
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
                       disable_notification=False, split_legacy=True):
    """ارسالِ محتوا با موتور مشترک.

    ابتدا پیام بومی تلگرام (تیتر/جدول/فهرست واقعی) امتحان می‌شود؛ اگر سرور یا
    کلاینت پشتیبانی نکند، همان متنِ اصلی با مسیر کلاسیک فرستاده می‌شود.
    """
    raw = "" if text is None else str(text)
    document = doc if doc is not None else rich_format.parse_content(raw, kind=kind)
    if rich_available() and document.blocks and document.faithful and can_raw_send(bot):
        rich_html = rich_format.render_rich(document)
        parts = rich_format.split_doc(document, config.RICH_MAX_CHARS, rich_format.render_rich)
        if len(parts) == 1 and len(rich_html) <= rich_format.RICH_CHAR_LIMIT:
            try:
                sender = getattr(bot, "send_rich_message", None)
                if sender is not None:
                    msg = await sender(chat_id, rich_html, reply_to_message_id=reply_to_message_id,
                                       reply_markup=reply_markup, protect_content=protect_content,
                                       disable_notification=disable_notification)
                else:
                    msg = await send_rich_raw(bot, chat_id, rich_html,
                                              reply_to_message_id=reply_to_message_id,
                                              reply_markup=reply_markup,
                                              protect_content=protect_content,
                                              disable_notification=disable_notification)
                _note_success()
                return msg
            except BadRequest as e:
                _note_failure(e)
                log.info("rich message rejected; falling back to classic send: %s", e)
            except (RetryAfter, Forbidden, NetworkError):
                raise
            except Exception as e:
                _note_failure(e)
                log.warning("rich message failed; falling back to classic send: %s", type(e).__name__)
        elif len(parts) > 1:
            log.info("rich message skipped: content needs %d parts", len(parts))
    return await send_legacy(bot, chat_id, raw, reply_to_message_id=reply_to_message_id,
                             reply_markup=reply_markup, split=split_legacy,
                             disable_web_page_preview=disable_web_page_preview)
