#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""آزمونِ زندهٔ پیام بومی تلگرام (Rich Messages) — فقط خواندنی + ارسال به چتِ مدیر کلان.

این ابزار هیچ تغییری در استقرار یا دیتابیس نمی‌دهد: فقط چند پیامِ نمونه به چتِ
مدیر کلان می‌فرستد و **ساختارِ رندرشده‌ی سرور** را بررسی می‌کند.

نکتهٔ مهم: «پاسخِ موفق API» به‌تنهایی proofِ رندرِ درست نیست. تلگرام در پاسخِ
``sendRichMessage`` ساختارِ *解析‌شده* را برمی‌گرداند (``result.rich_message``):
بلوک‌ها، جدول با سطر/ستون/سلول، فهرست، لینک و پرچمِ ``is_rtl``. همین ساختار
بررسی می‌شود، نه فقط ``ok: true``.

نحوهٔ اجرا:
    BOT_TOKEN=... SUPER_ADMIN_IDS=123 python tools/rich_live_test.py
    python tools/rich_live_test.py --env-file /opt/bot-official-telegram/.env

خروجی: یک گزارشِ JSON روی stdout (بدون هیچ راز).
"""
import argparse
import asyncio
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

SAMPLES = {
    "analysis_fa": """# تحلیل بازار فناوری در سه‌ماههٔ دوم

## مقدمه
بازار در این سه‌ماهه مسیر **صعودیِ** پرنوسانی را طی کرد و حجم معاملات حدود ۱۲٪ رشد داشت.

## نکات کلیدی
- رشد ۱۲ درصدیِ حجم معاملات نسبت به فصل قبل
- تمرکز بیشتر سرمایه روی زیرساخت ابری
- کاهش هزینهٔ نگهداری برای تیم‌های کوچک

## نتیجه‌گیری
> جمع‌بندی در یک جمله: بازار سالم است، اما ریسکِ نقدشوندگی بالا رفته است.
""",
    "comparison_table": """## مقایسهٔ سه سرویس ابری

| سرویس | قیمت پایه | سرعت پاسخ | پشتیبانی | رتبهٔ کلی |
|---|---|---|---|---|
| سرویس الف | ۱۲۰ هزار | ۹۵ میلی‌ثانیه | ۲۴/۷ | ۱ |
| سرویس ب | ۹۰ هزار | ۱۲۰ میلی‌ثانیه | ۸/۲۴ | ۲ |
| سرویس ج | ۱۵۰ هزار | ۷۰ میلی‌ثانیه | ۲۴/۷ | ۳ |
""",
    "stats_report": """# گزارش آماری روزانه

- پست‌های منتشرشده: ۱۲
- پست‌های ناموفق: ۲
- منابع فعال: ۴۵
- میانگین زمانِ چرخه: ۸۲ ثانیه

| شاخص | دیروز | امروز | تغییر |
|---|---|---|---|
| پست | ۹ | ۱۲ | ↑ ۳ |
| خطا | ۵ | ۲ | ↓ ۳ |
""",
    "final_summary": """## جمع‌بندی نهایی

> **نکتهٔ کلیدی:** استقرار با موفقیت انجام شد و هیچ پستِ تکراری ثبت نشد.

- ✅ زیرساخت پایدار است
- ✅ گزارش‌ها سر وقت ارسال می‌شوند
- ⚠️ برای فصل بعد پایشِ خودکارِ هشدارها لازم است
""",
    "mixed_fa_en": """## Mixed message 🌐

این یک پیام **ترکیبی** است: متن فارسی همراهِ کلمات English و یک لینک:
[مستندات رسمی تلگرام](https://core.telegram.org/bots/api) 🎯

- Release v2.4.0 🚀 shipped
- لینکِ دوم: https://example.com/docs?a=1&b=2
""",
}


def load_env_file(path):
    """خواندنِ سادهٔ KEY=VALUE بدون هیچ eval/sourcing (همان رویکردِ خودِ پروژه)."""
    if not path or not os.path.exists(path):
        return {}
    out = {}
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            key, val = key.strip(), val.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = val
                out[key] = True
    return out


def rich_text(node):
    """متنِ ساده از ساختارِ RichText تلگرام (رشته یا آرایه یا دیکشنری)."""
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        return "".join(rich_text(x) for x in node)
    if isinstance(node, dict):
        return rich_text(node.get("text"))
    return str(node)


def collect_urls(node, out):
    if isinstance(node, dict):
        if node.get("type") == "url" and node.get("url"):
            out.append(node["url"])
        for v in node.values():
            collect_urls(v, out)
    elif isinstance(node, list):
        for v in node:
            collect_urls(v, out)


def summarize_blocks(blocks, depth=0):
    """خلاصهٔ ساختارِ بلوک‌ها به‌همراه متن و لینک‌ها."""
    out = []
    for b in blocks or []:
        if not isinstance(b, dict):
            continue
        kind = b.get("type")
        item = {"type": kind}
        if kind == "heading":
            item["size"] = b.get("size")
            item["text"] = rich_text(b.get("text"))
        elif kind == "paragraph":
            item["text"] = rich_text(b.get("text"))
        elif kind == "list":
            items = []
            for it in (b.get("items") or []):
                if isinstance(it, dict):
                    items.append(rich_text(it.get("blocks") or it.get("text")))
                else:
                    items.append(rich_text(it))
            item["items"] = items
        elif kind == "table":
            cells = b.get("cells") or []
            item["rows"] = len(cells)
            item["cols"] = max([len(r) for r in cells] or [0])
            item["header_flags"] = [bool((c or {}).get("is_header")) for c in (cells[0] if cells else [])]
            item["first_row"] = [rich_text((c or {}).get("text")) for c in (cells[0] if cells else [])]
            item["second_row"] = [rich_text((c or {}).get("text")) for c in (cells[1] if len(cells) > 1 else [])]
        elif kind in ("quotation", "blockquote", "expandable_quotation", "pull_quotation"):
            item["text"] = rich_text(b.get("text"))
        elif kind == "details":
            item["summary"] = rich_text(b.get("summary"))
            item["children"] = summarize_blocks(b.get("blocks"), depth + 1)
        elif kind == "divider":
            pass
        else:
            item["text"] = rich_text(b.get("text"))
        out.append(item)
    return out


def all_text(node):
    parts = []
    if isinstance(node, str):
        parts.append(node)
    elif isinstance(node, list):
        for v in node:
            parts.append(all_text(v))
    elif isinstance(node, dict):
        for v in node.values():
            parts.append(all_text(v))
    return "".join(parts)


class RecordingBot:
    """پوششی روی بطریِ واقعی: فراخوانی‌های کلاسیک را ثبت می‌کند."""

    def __init__(self, bot):
        self._bot = bot
        self.classic = []

    @property
    def bot(self):
        return self._bot

    @property
    def base_url(self):
        return self._bot.base_url

    @property
    def token(self):
        return self._bot.token

    async def send_message(self, chat_id, text, **kw):
        msg = await self._bot.send_message(chat_id, text, **kw)
        self.classic.append({"chat_id": chat_id, "text": text, "keys": sorted(kw.keys())})
        return msg

    def __getattr__(self, name):
        return getattr(self._bot, name)


async def main_async(env_file, chat_id, do_probe=True):
    from newsbot import config
    from newsbot import rich_format, rich_send

    token = os.environ.get("BOT_TOKEN") or config.BOT_TOKEN
    if not token:
        raise SystemExit("BOT_TOKEN تنظیم نشده است")
    base = config.API_BASE or "https://api.telegram.org/bot"
    import telegram
    raw_bot = telegram.Bot(token=token, base_url=base)
    bot = RecordingBot(raw_bot)

    captured = {"calls": []}
    orig_api_call = rich_send.api_call

    async def spy(bot_, method, payload):
        try:
            result = await orig_api_call(bot_, method, payload)
        except Exception as e:
            captured["calls"].append({"method": method, "payload": payload, "error": "%s: %s" % (type(e).__name__, e),
                                      "status": getattr(e, "status_code", None)})
            raise
        captured["calls"].append({"method": method, "payload": payload, "result": result})
        return result

    rich_send.api_call = spy

    report = {"api_base": ("custom" if config.API_BASE else "api.telegram.org"),
              "chat_id_masked": ("…" + str(chat_id)[-2:]),
              "mode": config.RICH_MESSAGES,
              "samples": {}}

    # ── ۱) پنج نمونهٔ اصلی از مسیرِ واقعیِ موتور ───────────────────────────────
    for name, text in SAMPLES.items():
        captured["calls"] = []
        bot.classic.clear()
        rich_send.reset_rich_state()
        entry = {}
        try:
            msg = await rich_send.send_content(bot, chat_id, text)
            entry["message_id"] = getattr(msg, "message_id", None)
            entry["classic_fallback_used"] = bool(bot.classic)
            rich_calls = [c for c in captured["calls"] if c["method"] == "sendRichMessage"]
            entry["rich_call_count"] = len(rich_calls)
            if rich_calls:
                call = rich_calls[0]
                entry["payload_keys"] = sorted(call["payload"].keys())
                entry["rich_message_keys"] = sorted((call["payload"].get("rich_message") or {}).keys())
                entry["html_length"] = len((call["payload"].get("rich_message") or {}).get("html") or "")
                res = call.get("result") or {}
                rm = res.get("rich_message")
                entry["native_rich"] = isinstance(rm, dict)
                if isinstance(rm, dict):
                    entry["is_rtl"] = rm.get("is_rtl")
                    entry["blocks"] = summarize_blocks(rm.get("blocks"))
                    urls = []
                    collect_urls(rm, urls)
                    entry["urls"] = sorted(set(urls))
                    entry["plain_text"] = all_text(rm)[:4000]
                entry["message_has_text"] = bool(res.get("text"))
            else:
                entry["native_rich"] = False
                entry["error"] = captured["calls"][-1].get("error") if captured["calls"] else None
        except Exception as e:
            entry["exception"] = "%s: %s" % (type(e).__name__, e)
        report["samples"][name] = entry

    # ── ۲) مسیرِ کلاسیک (RICH_MESSAGES=off) ────────────────────────────────────
    captured["calls"] = []
    bot.classic.clear()
    old_mode = config.RICH_MESSAGES
    config.RICH_MESSAGES = "off"
    try:
        msg = await rich_send.send_content(bot, chat_id, SAMPLES["comparison_table"])
        classic = report.setdefault("classic_mode", {})
        classic["message_id"] = getattr(msg, "message_id", None)
        classic["rich_calls"] = len([c for c in captured["calls"] if c["method"] == "sendRichMessage"])
        classic["classic_calls"] = len(bot.classic)
        classic["has_table_markup"] = any("|" in (c["text"] or "") for c in bot.classic)
        classic["text_preview"] = (bot.classic[0]["text"][:300] if bot.classic else "")
        classic["content_kept"] = all(w in (bot.classic[0]["text"] if bot.classic else "")
                                       for w in ("سرویس الف", "۱۲۰ هزار", "۹۵ میلی‌ثانیه"))
    except Exception as e:
        report.setdefault("classic_mode", {})["exception"] = "%s: %s" % (type(e).__name__, e)
    finally:
        config.RICH_MESSAGES = old_mode

    # ── ۳) fallback واقعی: سرور پیامِ بومی را رد می‌کند ────────────────────────
    captured["calls"] = []
    bot.classic.clear()
    rich_send.reset_rich_state()
    fallback = {}
    orig_render = rich_format.render_rich
    try:
        rich_format.render_rich = lambda *a, **kw: "<h2>broken</h2><table><tr><td>x</td></tr>"
        try:
            msg = await rich_send.send_content(bot, chat_id, SAMPLES["final_summary"])
            fallback["rich_rejected"] = any(c.get("error") for c in captured["calls"]
                                            if c["method"] == "sendRichMessage")
            fallback["classic_calls"] = len(bot.classic)
            fallback["message_id"] = getattr(msg, "message_id", None)
            fallback["content_kept"] = all(w in (bot.classic[0]["text"] if bot.classic else "")
                                           for w in ("نکتهٔ کلیدی", "زیرساخت پایدار است"))
            fallback["exactly_one_message"] = (len(bot.classic) == 1)
        except Exception as e:
            fallback["exception"] = "%s: %s" % (type(e).__name__, e)
    finally:
        rich_format.render_rich = orig_render
    report["fallback_on_rejection"] = fallback

    # ── ۴) fallback قطعی: سرور اصلاً متد را ندارد (۴۰۴) ────────────────────────
    captured["calls"] = []
    bot.classic.clear()
    rich_send.reset_rich_state()
    orig_call = rich_send.api_call
    not_found = {}
    try:
        async def fake_404(bot_, method, payload):
            captured["calls"].append({"method": method, "payload": payload,
                                      "error": "Not Found: method not found", "status": 404})
            from telegram.error import BadRequest
            err = BadRequest("Not Found: method not found")
            err.status_code = 404
            raise err
        rich_send.api_call = fake_404
        limit = max(1, config.RICH_FAIL_LIMIT)
        for _ in range(limit):
            bot.classic.clear()
            msg = await rich_send.send_content(bot, chat_id, SAMPLES["final_summary"])
            not_found.setdefault("per_try", []).append({
                "classic_calls": len(bot.classic),
                "message_id": getattr(msg, "message_id", None),
                "content_kept": bool(bot.classic) and "نکتهٔ کلیدی" in bot.classic[0]["text"]})
        not_found["status_after_limit"] = rich_send.rich_status()
        captured["calls"] = []
        bot.classic.clear()
        await rich_send.send_content(bot, chat_id, SAMPLES["final_summary"])
        not_found["rich_calls_after_limit"] = len([c for c in captured["calls"]
                                                   if c["method"] == "sendRichMessage"])
        not_found["classic_calls_after_limit"] = len(bot.classic)
    except Exception as e:
        not_found["exception"] = "%s: %s" % (type(e).__name__, e)
    finally:
        rich_send.api_call = orig_call
    fallback["not_found"] = not_found
    report["fallback_on_rejection"] = fallback

    # ── ۵) کاوشِ سقفِ طول: کاراکتر یا بایت؟ ───────────────────────────────────
    if do_probe:
        probe = {}
        for label, size in (("fa_16k", 16000), ("fa_32k", 32000)):
            body = ("ا" * size)
            try:
                await rich_send.send_rich_raw(bot, chat_id, "<p>%s</p>" % body,
                                              disable_notification=True)
                probe[label] = {"chars": size, "accepted": True}
            except Exception as e:
                probe[label] = {"chars": size, "accepted": False,
                                "error": "%s: %s" % (type(e).__name__, e)[:200],
                                "status": getattr(e, "status_code", None)}
        report["length_probe"] = probe

    report["rich_status"] = rich_send.rich_status()
    return report


def main():
    ap = argparse.ArgumentParser(description="آزمون زندهٔ پیام بومی تلگرام")
    ap.add_argument("--env-file", default=os.environ.get("NEWSBOT_ENV_FILE", ""),
                    help="مسیرِ فایل .env (توکن هرگز چاپ نمی‌شود)")
    ap.add_argument("--chat-id", default="", help="شناسهٔ چتِ مقصد (پیش‌فرض: اولین مدیر کلان)")
    ap.add_argument("--no-probe", action="store_true", help="حذفِ کاوشِ سقفِ طول")
    args = ap.parse_args()

    load_env_file(args.env_file)

    from newsbot import config
    chat_id = args.chat_id or (sorted(config.SUPER_ADMIN_IDS)[0] if config.SUPER_ADMIN_IDS else "")
    if not chat_id:
        raise SystemExit("SUPER_ADMIN_IDS تنظیم نشده است و --chat-id داده نشده است")

    report = asyncio.run(main_async(args.env_file, chat_id, do_probe=not args.no_probe))
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
