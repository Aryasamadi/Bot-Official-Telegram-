# -*- coding: utf-8 -*-
"""تست‌های موتور قالب‌بندی و پیام بومی تلگرام (Rich Messages).

پوشش:
  • واحد: تجزیهٔ محتوا، تشخیص نوع، رندر بومی/کلاسیک، جدول واقعی، RTL، وفاداریِ محتوا.
  • یکپارچه: سرورِ آزمایشیِ Bot API روی لوپ‌بک ⇒ payload واقعیِ ``sendRichMessage``،
    fallback به پیام کلاسیک، نگاشت خطاها، مدار قطع‌کننده، مسیر انتشار کانال،
    مسیر «نسخهٔ کامل» در ربات و ژورنالِ انتشار.
"""
import asyncio
import json
import threading
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest
from telegram.error import BadRequest, NetworkError, RetryAfter

from newsbot import config
from newsbot import rich_format
from newsbot import rich_send
from newsbot.text_format import strip_tags


def rich_text_of(node):
    """متنِ ساده از ساختارِ RichText (همانندِ آنچه سرور برمی‌گرداند)."""
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        return "".join(rich_text_of(x) for x in node)
    if isinstance(node, dict):
        return rich_text_of(node.get("text"))
    return str(node)


def parse_rich_html(html, is_rtl=False):
    """تجزیهٔ HTMLِ بومی ⇒ ساختاری همانندِ آنچه سرورِ تلگرام برمی‌گرداند.

    این «شبیه‌سازِ سمتِ سرور» است: اگر payloadِ ما واقعاً به بلوک‌های بومی
    (تیتر/جدول/فهرست) تبدیل شود، اینجا هم باید همان ساختار دیده شود."""
    blocks = []

    class P(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.buf = []
            self.heading = None
            self.quote = None
            self.list_items = None
            self.list_item = None
            self.table = None
            self.row = None
            self.cell = None
            self.cell_header = False
            self.details = None
            self.summary = None
            self.in_summary = False
            self.link = None
            self.link_text = []

        def _sink(self):
            """مخزنِ متنِ جاری (بسته به بلوکِ باز)."""
            if self.heading is not None:
                return self.heading[1]
            if self.quote is not None:
                return self.quote
            if self.cell is not None:
                return self.cell
            if self.list_item is not None:
                return self.list_item
            if self.details is not None:
                return self.details
            return self.buf

        def _text(self):
            out = []
            for x in self.buf:
                if isinstance(x, str):
                    out.append(x)
                elif isinstance(x, dict) and x.get("type") == "url":
                    out.append(rich_text_of(x.get("text")))
            return "".join(out).strip()

        def handle_starttag(self, tag, attrs):
            if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
                self.heading = [int(tag[1]), []]
            elif tag == "p":
                self.buf = []
            elif tag in ("ul", "ol"):
                self.list_items = []
            elif tag == "li":
                self.list_item = []
            elif tag == "table":
                self.table = []
            elif tag == "tr":
                self.row = []
            elif tag in ("td", "th"):
                self.cell = []
                self.cell_header = (tag == "th")
            elif tag == "blockquote":
                self.quote = []
            elif tag == "details":
                self.details = []
            elif tag == "summary":
                self.in_summary = True
                self.buf = []
            elif tag == "a":
                href = dict(attrs).get("href")
                if href:
                    self.link = href
                    self.link_text = []

        def handle_endtag(self, tag):
            if tag == "a" and self.link:
                node = {"type": "url", "url": self.link, "text": "".join(self.link_text).strip()}
                self._sink().append(node)
                self.link = None
                self.link_text = []
            elif tag in ("h1", "h2", "h3", "h4", "h5", "h6") and self.heading:
                blocks.append({"type": "heading", "size": self.heading[0],
                               "text": "".join(self.heading[1]).strip()})
                self.heading = None
            elif tag == "p":
                t = self._text()
                if t:
                    blocks.append({"type": "paragraph", "text": t})
                self.buf = []
            elif tag in ("ul", "ol") and self.list_items is not None:
                if self.list_items:
                    blocks.append({"type": "list", "items": self.list_items})
                self.list_items = None
            elif tag == "li" and self.list_item is not None:
                item = "".join(self.list_item).strip()
                if self.list_items is not None:
                    self.list_items.append({"label": "", "blocks": [{"type": "paragraph", "text": item}]})
                self.list_item = None
            elif tag == "table" and self.table is not None:
                blocks.append({"type": "table", "cells": self.table})
                self.table = None
            elif tag == "tr" and self.row is not None:
                if self.table is not None:
                    self.table.append(self.row)
                self.row = None
            elif tag in ("td", "th") and self.cell is not None:
                cell = {"text": "".join(self.cell).strip()}
                if self.cell_header:
                    cell["is_header"] = True
                if self.row is not None:
                    self.row.append(cell)
                self.cell = None
            elif tag == "blockquote" and self.quote is not None:
                blocks.append({"type": "quotation", "text": "".join(self.quote).strip()})
                self.quote = None
            elif tag == "details" and self.details is not None:
                blocks.append({"type": "details", "summary": self.summary or "",
                               "blocks": self.details})
                self.details = None
                self.summary = None
            elif tag == "summary":
                self.summary = self._text()
                self.in_summary = False
                self.buf = []
            elif tag == "hr":
                blocks.append({"type": "divider"})

        def handle_data(self, data):
            if self.link is not None:
                self.link_text.append(data)
                return
            if self.in_summary:
                self.buf.append(data)
                return
            self._sink().append(data)

    p = P()
    p.feed(html or "")
    return {"blocks": blocks, "is_rtl": bool(is_rtl)}


POST_HTML = ("<b>عنوان خبر</b>\n\n"
             "متن اول این پاراگراف دربارهٔ موضوع است.\n\n"
             "• نکتهٔ اول\n"
             "• نکتهٔ دوم با <b>تأکید</b>\n\n"
             "<blockquote>مهم‌ترین جملهٔ خبر</blockquote>\n\n"
             "پایان پست.")

TABLE_MD = ("| معیار | مدل الف | مدل ب |\n"
            "|---|---|---|\n"
            "| سرعت | ۹۰ | ۷۰ |\n"
            "| دقت | ۸۰ | ۹۵ |")


# ══════════════════════════════════════════════════════════════════════════
# سرور آزمایشی Bot API (همان HTTP واقعی، فقط روی لوپ‌بک)
# ══════════════════════════════════════════════════════════════════════════
class FakeTelegram:
    def __init__(self, mode="ok"):
        self.mode = mode
        self.calls = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def do_POST(self):
                size = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(size)
                try:
                    data = json.loads(raw.decode("utf-8"))
                except Exception:
                    data = {}
                outer.calls.append({"path": self.path, "body": data})
                method = self.path.rsplit("/", 1)[-1]
                status, payload = outer.reply_for(method, data)
                out = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    @property
    def base_url(self):
        return "http://127.0.0.1:%d/bot" % self.port

    def methods_called(self):
        return [c["path"].rsplit("/", 1)[-1] for c in self.calls]

    def reply_for(self, method, data):
        if method != "sendRichMessage":
            return 200, {"ok": True, "result": {"message_id": 101, "date": 1700000000,
                                                "chat": {"id": data.get("chat_id"), "type": "private"},
                                                "text": data.get("text") or ""}}
        if self.mode == "ok":
            rich = (data.get("rich_message") or {})
            # مثلِ سرورِ واقعی: ساختارِ解析‌شده را برمی‌گردانیم (نه فقط «ok»)
            parsed = parse_rich_html(rich.get("html") or "", bool(rich.get("is_rtl")))
            return 200, {"ok": True, "result": {"message_id": 4242, "date": 1700000000,
                                                "chat": {"id": data.get("chat_id"), "type": "private"},
                                                "rich_message": parsed}}
        if self.mode == "no_method":
            return 404, {"ok": False, "error_code": 404, "description": "Not Found: method not found"}
        if self.mode == "bad_parse":
            return 400, {"ok": False, "error_code": 400, "description": "Bad Request: can't parse rich message"}
        if self.mode == "server_error":
            return 500, {"ok": False, "error_code": 500, "description": "Internal Server Error"}
        if self.mode == "flood":
            return 429, {"ok": False, "error_code": 429, "description": "Too Many Requests",
                         "parameters": {"retry_after": 7}}
        return 200, {"ok": True, "result": {"message_id": 1}}

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class Bot:
    """بطریِ ساختگی با همان قراردادِ PTB (base_url شامل توکن است)."""

    id = 999

    def __init__(self, server):
        self.server = server
        self.token = "123456789:AAtest_only_dummy_token_0000000000000"
        self.base_url = server.base_url + self.token
        self.sent = []

    async def send_message(self, chat_id, text, **kw):
        self.sent.append({"chat_id": chat_id, "text": text, "kw": kw})
        return SimpleNamespace(message_id=1000 + len(self.sent))


@pytest.fixture(autouse=True)
def _rich_state():
    rich_send.reset_rich_state()
    old_mode, old_limit = config.RICH_MESSAGES, config.RICH_FAIL_LIMIT
    config.RICH_MESSAGES = "auto"
    config.RICH_FAIL_LIMIT = 3
    yield
    rich_send.reset_rich_state()
    config.RICH_MESSAGES, config.RICH_FAIL_LIMIT = old_mode, old_limit


@pytest.fixture
def telegram_ok():
    srv = FakeTelegram("ok")
    try:
        yield srv, Bot(srv)
    finally:
        srv.close()


# ══════════════════════════════════════════════════════════════════════════
# واحد — موتور قالب‌بندی
# ══════════════════════════════════════════════════════════════════════════
def test_post_becomes_structured_native_blocks():
    doc = rich_format.parse_content(POST_HTML)
    assert doc.faithful, "تجزیهٔ پستِ استاندارد نباید محتوایی از دست بدهد"
    html = rich_format.render_rich(doc)
    assert "<h2>عنوان خبر</h2>" in html, "تیتر بولد باید تیترِ بومی شود"
    assert "<ul><li>نکتهٔ اول</li>" in html, "فهرست باید فهرستِ بومی شود"
    assert "<blockquote>مهم‌ترین جملهٔ خبر</blockquote>" in html, "نقل‌قول باید حفظ شود"
    assert "•" not in html, "پیام بومی نباید نشانهٔ متنیِ فهرست داشته باشد"


def test_legacy_render_keeps_the_current_project_format():
    doc = rich_format.parse_content(POST_HTML)
    out = rich_format.render_legacy(doc)
    assert "<b>عنوان خبر</b>" in out
    assert "• نکتهٔ اول" in out
    assert "• نکتهٔ دوم با <b>تأکید</b>" in out
    assert "<blockquote>مهم‌ترین جملهٔ خبر</blockquote>" in out
    assert "<h2>" not in out and "<ul>" not in out, "قالب کلاسیک نباید تگِ بومی داشته باشد"


def test_table_becomes_a_real_native_table_not_a_pipe_dump():
    doc = rich_format.parse_content(TABLE_MD)
    html = rich_format.render_rich(doc)
    assert "<table>" in html and "<th>معیار</th>" in html and "<td>سرعت</td>" in html
    assert "|" not in html, "جدول بومی نباید متنِ GFM داشته باشد"
    assert "---" not in html, "جدول بومی نباید خطِ جداکنندهٔ مارک‌داون داشته باشد"
    legacy = rich_format.render_legacy(doc)
    assert "|" not in legacy and "---" not in legacy, "fallback هم نباید GFM را نشت دهد"
    assert "معیار" in legacy and "سرعت" in legacy and "۹۰" in legacy, "fallback باید خوانا و کامل باشد"
    assert rich_format.detect_kind(TABLE_MD) == rich_format.KIND_COMPARISON


def test_table_fallback_is_readable_without_pipes():
    doc = rich_format.parse_content(TABLE_MD)
    legacy = rich_format.render_legacy(doc)
    lines = [l for l in legacy.split("\n") if l.strip()]
    assert any(l.startswith("•") for l in lines), "ردیف‌های جدول باید فهرستِ خوانا باشند"
    assert "<b>معیار · مدل الف · مدل ب</b>" in legacy


def test_persian_rtl_zwnj_and_emoji_survive():
    text = "نیم‌فاصله در متن و ایموجی 🚀 و عدد ۱۲۳"
    doc = rich_format.parse_content(text)
    assert doc.faithful
    plain = rich_format.render_plain(doc)
    assert "نیم‌فاصله" in plain and "🚀" in plain and "۱۲۳" in plain
    assert rich_format.detect_rtl(text) is True
    assert rich_format.detect_rtl("pure english text") is False


def test_markdown_characters_are_escaped_not_executed():
    doc = rich_format.parse_content("قیمت 5 * 3 و متنِ **پررنگ**")
    html = rich_format.render_rich(doc)
    assert "<b>پررنگ</b>" in html
    plain = rich_format.render_plain(doc)
    assert "5 * 3" in plain, "ستارهٔ تنها نباید نشانه‌گذاری شود"


def test_links_are_preserved_and_dangerous_schemes_are_dropped():
    doc = rich_format.parse_content("[لینک](https://example.com/a?b=1&c=2) و <a href='javascript:alert(1)'>بد</a>")
    html = rich_format.render_rich(doc)
    assert '<a href="https://example.com/a?b=1&amp;c=2">لینک</a>' in html, "آدرس باید HTML-امن escape شود"
    assert "javascript" not in html
    assert "بد" in html, "متنِ لینکِ ناامن باید بماند، فقط لینک حذف شود"


def test_kind_detection_covers_the_documented_patterns():
    assert rich_format.detect_kind("⚠️ هشدار: سرویس قطع است") == rich_format.KIND_WARNING
    assert rich_format.detect_kind("مزایا:\n• خوب\nمعایب:\n• بد") == rich_format.KIND_REVIEW
    assert rich_format.detect_kind("نتیجه‌گیری: بازار صعودی است") == rich_format.KIND_SUMMARY
    assert rich_format.detect_kind("تحلیل بازار در دو پاراگراف") == rich_format.KIND_ANALYSIS
    assert rich_format.detect_kind("گزارش وضعیت سیستم") == rich_format.KIND_REPORT
    assert rich_format.detect_kind("یک خبر ساده دربارهٔ هوا") == rich_format.KIND_NEWS


def test_content_is_never_lost_for_a_corpus_of_samples():
    samples = [POST_HTML, TABLE_MD,
               "# تیتر\n\nمتن **پررنگ**\n\n- یک\n- دو\n\n> نقل\n\n---\n\n## بخش",
               "<b>یک</b>\n\nپاراگراف اول\nپاراگراف دوم\n\n<pre>code()\n</pre>",
               "متن سادهٔ فارسی با ایموجی 🎯 و عدد ۵",
               "<details><summary>بیشتر</summary><p>متن</p></details>",
               "خط اول\nخط دوم با <code>کد</code>\nخط سوم"]
    for s in samples:
        doc = rich_format.parse_content(s)
        assert doc.faithful, "تجزیه محتوا را از دست داد: %r" % s[:60]
        assert rich_format._fingerprint_source(s) == rich_format._fingerprint_doc(doc), \
            "متنِ سند با متنِ ورودی فرق کرد: %r" % s[:60]


def test_legacy_render_of_a_channel_post_is_the_same_as_the_current_format():
    out = rich_format.render_legacy(rich_format.parse_content(POST_HTML))
    assert out == POST_HTML, "قالبِ فعلیِ کانال باید بدون تغییر بازتولید شود"


def test_split_doc_respects_the_limit_and_keeps_everything():
    body = "\n\n".join("پاراگراف %d %s" % (i, "متن " * 20) for i in range(200))
    doc = rich_format.parse_content(body)
    parts = rich_format.split_doc(doc, 400, rich_format.render_legacy)
    assert len(parts) > 1
    joined = "".join(rich_format.render_plain(p) for p in parts)
    assert strip_tags(joined).count("پاراگراف") == 200, "هیچ پاراگرافی نباید گم شود"
    for p in parts:
        assert len(rich_format.render_legacy(p)) <= 400 + 200


# ══════════════════════════════════════════════════════════════════════════
# یکپارچه — لایهٔ ارسال
# ══════════════════════════════════════════════════════════════════════════
def test_rich_send_posts_a_native_rich_message(telegram_ok):
    srv, bot = telegram_ok
    msg = asyncio.run(rich_send.send_content(bot, 55, POST_HTML))
    assert srv.methods_called() == ["sendRichMessage"]
    body = srv.calls[0]["body"]
    assert body["chat_id"] == 55
    assert body["rich_message"]["html"].count("<blockquote>") == 1
    assert body["rich_message"]["is_rtl"] is True
    assert msg.message_id == 4242
    assert bot.sent == [], "در مسیر بومی نباید پیام کلاسیک هم فرستاده شود"


def test_rich_send_sends_a_real_table_block(telegram_ok):
    srv, bot = telegram_ok
    asyncio.run(rich_send.send_content(bot, 55, TABLE_MD))
    html = srv.calls[0]["body"]["rich_message"]["html"]
    assert "<table>" in html and "<td>۹۰</td>" in html


def test_fallback_to_classic_when_the_server_has_no_rich_method():
    srv = FakeTelegram("no_method")
    try:
        bot = Bot(srv)
        asyncio.run(rich_send.send_content(bot, 55, POST_HTML))
        assert srv.methods_called() == ["sendRichMessage"], "سرور باید یک‌بار تلاش کند"
        assert len(bot.sent) == 1, "باید به پیام کلاسیک تنزل کند"
        assert bot.sent[0]["text"] == POST_HTML, "محتوای پیام نباید تغییر کند"
        assert bot.sent[0]["kw"]["parse_mode"] == "HTML"
    finally:
        srv.close()


def test_fallback_keeps_the_content_when_rich_parsing_fails():
    srv = FakeTelegram("bad_parse")
    try:
        bot = Bot(srv)
        asyncio.run(rich_send.send_content(bot, 55, POST_HTML))
        assert len(bot.sent) == 1
        assert "مهم‌ترین جملهٔ خبر" in bot.sent[0]["text"]
        assert "<blockquote>" in bot.sent[0]["text"]
    finally:
        srv.close()


def test_server_error_propagates_and_never_duplicates_the_message():
    srv = FakeTelegram("server_error")
    try:
        bot = Bot(srv)
        with pytest.raises(NetworkError):
            asyncio.run(rich_send.send_content(bot, 55, POST_HTML))
        assert bot.sent == [], "خطای شبکه نباید به ارسالِ دوباره منجر شود"
    finally:
        srv.close()


def test_flood_is_mapped_to_retry_after():
    srv = FakeTelegram("flood")
    try:
        bot = Bot(srv)
        with pytest.raises(RetryAfter) as e:
            asyncio.run(rich_send.send_content(bot, 55, POST_HTML))
        assert int(e.value.retry_after) == 7
        assert bot.sent == []
    finally:
        srv.close()


def test_circuit_breaker_disables_rich_after_repeated_rejections():
    srv = FakeTelegram("no_method")
    try:
        bot = Bot(srv)
        for _ in range(3):
            asyncio.run(rich_send.send_content(bot, 55, POST_HTML))
        assert rich_send.rich_available() is False
        calls_before = len(srv.calls)
        asyncio.run(rich_send.send_content(bot, 55, POST_HTML))
        assert len(srv.calls) == calls_before, "بعد از خاموش شدن، دیگر نباید تلاش کند"
        assert len(bot.sent) == 4
    finally:
        srv.close()


def test_mode_off_never_touches_the_rich_endpoint(telegram_ok):
    srv, bot = telegram_ok
    config.RICH_MESSAGES = "off"
    asyncio.run(rich_send.send_content(bot, 55, POST_HTML))
    assert srv.calls == []
    assert len(bot.sent) == 1


def test_bot_without_token_skips_the_raw_path():
    class NoToken:
        id = 1
        def __init__(self): self.sent = []

        async def send_message(self, chat_id, text, **kw):
            self.sent.append(text)
            return SimpleNamespace(message_id=1)

    bot = NoToken()
    asyncio.run(rich_send.send_content(bot, 55, POST_HTML))
    assert len(bot.sent) == 1


# ══════════════════════════════════════════════════════════════════════════
# یکپارچه — مسیرهای واقعیِ ربات
# ══════════════════════════════════════════════════════════════════════════
def test_channel_post_path_uses_the_native_rich_message(telegram_ok):
    from newsbot import publish
    srv, bot = telegram_ok
    msg = asyncio.run(publish.send_post(bot, -100123, POST_HTML))
    assert srv.methods_called() == ["sendRichMessage"], "پستِ متنیِ کانال باید پیام بومی باشد"
    assert msg.message_id == 4242
    assert bot.sent == []


def test_channel_post_path_falls_back_without_losing_the_post():
    srv = FakeTelegram("bad_parse")
    try:
        from newsbot import publish
        bot = Bot(srv)
        asyncio.run(publish.send_post(bot, -100123, POST_HTML))
        assert len(bot.sent) == 1
        assert "مهم‌ترین جملهٔ خبر" in bot.sent[0]["text"]
    finally:
        srv.close()


def test_publication_journal_closes_with_ack_after_a_rich_send(telegram_ok):
    from newsbot import publish
    from newsbot.core_articles import PublicationTransport
    from newsbot.db import q
    srv, bot = telegram_ok
    journal = {"phase": "prepared"}
    parent = SimpleNamespace(cancelling=lambda: False)

    async def _run():
        transport = PublicationTransport(bot, 9001, journal, parent)
        return await publish.send_post(transport, -100123, POST_HTML)

    asyncio.run(_run())
    row = q("SELECT journal FROM pub_journal WHERE article_id=?", (9001,), one=True)
    assert row, "ژورنال باید نوشته شود"
    saved = json.loads(row["journal"])
    assert saved["phase"] == "ack", "ارسالِ متنی باید فاز را ack کند"
    assert saved["message_id"] == 4242


def test_legacy_path_downgrades_when_telegram_rejects_the_html():
    class PickyBot:
        id = 1

        def __init__(self):
            self.calls = []

        async def send_message(self, chat_id, text, **kw):
            self.calls.append(text)
            if len(self.calls) == 1:
                raise BadRequest("Bad Request: can't parse entities")
            return SimpleNamespace(message_id=2)

    bot = PickyBot()
    asyncio.run(rich_send.send_content(bot, 55, POST_HTML))
    assert len(bot.calls) == 2
    assert "<tg-spoiler>" not in bot.calls[1], "در تنزل باید قابلیت‌های خاص حذف شوند"
    assert "مهم‌ترین جملهٔ خبر" in bot.calls[1], "محتوا باید حتی در تنزل حفظ شود"


def test_media_posts_keep_the_caption_path(telegram_ok):
    from newsbot import publish
    srv, bot = telegram_ok
    media = {"kind": "photo", "url": "https://example.test/p.jpg", "_file": None}

    class MediaBot(Bot):
        async def send_photo(self, chat_id, url, caption=None, **kw):
            self.sent.append({"chat_id": chat_id, "text": caption, "kw": kw})
            return SimpleNamespace(message_id=77)

    mb = MediaBot(srv)
    asyncio.run(publish.send_post(mb, -100123, POST_HTML, media))
    assert srv.methods_called() == [], "رسانهٔ همراه، کپشنِ کلاسیک می‌ماند"
    assert len(mb.sent) == 1 and "<blockquote>" in mb.sent[0]["text"]


def test_notifications_go_through_the_shared_engine(telegram_ok):
    from newsbot import ui_widgets
    from newsbot import config as cfg
    srv, bot = telegram_ok
    old_bot, old_ids = ui_widgets.BOT, cfg.SUPER_ADMIN_IDS
    ui_widgets.BOT, cfg.SUPER_ADMIN_IDS = bot, {5}
    try:
        delivered = asyncio.run(ui_widgets.notify_supers("<b>هشدار</b>\n\n• سرویس قطع است"))
        assert delivered == 1
        assert srv.methods_called() == ["sendRichMessage"]
        html = srv.calls[0]["body"]["rich_message"]["html"]
        assert "<h2>هشدار</h2>" in html
        assert "<ul><li>سرویس قطع است</li></ul>" in html
    finally:
        ui_widgets.BOT, cfg.SUPER_ADMIN_IDS = old_bot, old_ids


def test_full_post_in_bot_uses_the_native_rich_message(telegram_ok):
    from newsbot import rich_send as rs
    srv, bot = telegram_ok
    body = "<b>نسخهٔ کامل</b>\n\n" + POST_HTML
    asyncio.run(rs.send_content(bot, 7, body))
    assert srv.methods_called() == ["sendRichMessage"]
    html = srv.calls[0]["body"]["rich_message"]["html"]
    assert "<h2>نسخهٔ کامل</h2>" in html
    assert html.count("<blockquote>") == 1


# ══════════════════════════════════════════════════════════════════════════
# انطباق با مشخصاتِ رسمیِ تلگرام (نه فقط «پاسخِ موفق»)
# ══════════════════════════════════════════════════════════════════════════
# فهرستِ تگ‌های مجازِ «Rich HTML style» طبقِ core.telegram.org/bots/api/rich-messages.md
ALLOWED_TAGS = {
    "a", "b", "strong", "i", "em", "u", "ins", "s", "strike", "del", "code", "mark",
    "sub", "sup", "tg-spoiler", "tg-reference", "tg-emoji", "tg-time", "tg-math",
    "h1", "h2", "h3", "h4", "h5", "h6", "p", "pre", "footer", "hr", "ul", "ol", "li",
    "input", "blockquote", "aside", "img", "video", "audio", "tg-document", "figure",
    "figcaption", "cite", "tg-map", "tg-collage", "tg-slideshow", "table", "tr", "th",
    "td", "details", "summary", "tg-math-block", "tg-button", "tg-button-row", "br",
}
ALLOWED_NAMED_ENTITIES = {"lt", "gt", "amp", "quot", "apos", "nbsp", "hellip", "mdash",
                          "ndash", "lsquo", "rsquo", "ldquo", "rdquo"}
CORPUS = [POST_HTML, TABLE_MD,
          "# تیتر\n\nمتن **پررنگ** با *مورب* و __زیرخط__ و `کد`\n\n- یک\n- دو\n\n"
          "> نقل\n\n---\n\n## بخش\n\n[لینک](https://example.com/a?b=1&c=2) 🎯",
          "<details><summary>بیشتر</summary><p>متن</p></details>",
          "متن سادهٔ فارسی با ایموجی 🚯 و عدد ۵ و <code>کد</code>"]


def test_the_rich_html_only_uses_tags_and_entities_allowed_by_telegram():
    import re as _re
    for src in CORPUS:
        html = rich_format.render_rich(rich_format.parse_content(src))
        tags = {t.lower() for t in _re.findall(r"<\s*/?\s*([a-zA-Z][a-zA-Z0-9-]*)", html)}
        bad = tags - ALLOWED_TAGS
        assert not bad, "تگِ غیرمجاز در پیام بومی: %s" % sorted(bad)
        ents = {e.lower() for e in _re.findall(r"&([a-zA-Z][a-zA-Z]{1,10});", html)}
        bad_ents = ents - ALLOWED_NAMED_ENTITIES
        assert not bad_ents, "نامِ entityِ غیرمجاز: %s" % sorted(bad_ents)


def test_the_rich_html_respects_the_documented_limits():
    import re as _re
    wide = "| " + " | ".join("ستون%d" % i for i in range(25)) + " |\n"
    wide += "|" + "---|" * 25 + "\n"
    wide += "| " + " | ".join("مقدار%d" % i for i in range(25)) + " |\n"
    for src in CORPUS + [wide]:
        doc = rich_format.parse_content(src)
        html = rich_format.render_rich(doc)
        assert len(html) <= rich_format.RICH_CHAR_LIMIT
        assert rich_format.count_blocks(doc) <= rich_format.RICH_BLOCK_LIMIT
        depth = 0
        max_depth = 0
        for tag in _re.findall(r"<\s*/?\s*([a-zA-Z][a-zA-Z0-9-]*)", html):
            if tag in ("details", "table", "ul", "ol", "blockquote"):
                max_depth = max(max_depth, depth + 1)
        assert max_depth <= rich_format.RICH_NEST_LIMIT


def test_a_table_wider_than_20_columns_is_degraded_not_rejected():
    wide = "| " + " | ".join("ستون%d" % i for i in range(21)) + " |\n"
    wide += "|" + "---|" * 21 + "\n"
    wide += "| " + " | ".join("مقدار%d" % i for i in range(21)) + " |\n"
    doc = rich_format.parse_content(wide)
    html = rich_format.render_rich(doc)
    assert "<table>" not in html, "جدولِ بیش از ۲۰ ستون باید تنزل کند، نه اینکه رد شود"
    assert "مقدار20" in html and "ستون20" in html, "محتوای جدول نباید حذف شود"


def test_a_document_with_more_than_500_blocks_is_not_sent_as_one_rich_message():
    srv = FakeTelegram("ok")
    try:
        bot = Bot(srv)
        body = "\n\n".join("پاراگراف %d" % i for i in range(600))
        asyncio.run(rich_send.send_content(bot, 55, body, split_legacy=True))
        assert "sendRichMessage" not in srv.methods_called(), \
            "سندِ بیش از ۵۰۰ بلوک نباید یک‌جا فرستاده شود (تلگرام رد می‌کند)"
        assert bot.sent, "باید به مسیر کلاسیک رفته باشد"
    finally:
        srv.close()


def test_rtl_is_decided_by_the_text_not_by_the_html_tags():
    assert rich_format.detect_rtl("<h2>سلام</h2>") is True
    assert rich_format.detect_rtl("Hello world") is False
    assert rich_format.detect_rtl("سلام") is True


def test_a_content_error_does_not_disable_the_rich_path():
    srv = FakeTelegram("bad_parse")
    try:
        bot = Bot(srv)
        for _ in range(5):
            asyncio.run(rich_send.send_content(bot, 55, POST_HTML))
            assert rich_send.rich_available() is True, \
                "خطای محتوایی نباید پیام بومی را برای کل فرایند خاموش کند"
    finally:
        srv.close()


def test_an_unknown_failure_after_the_send_never_resends_the_message():
    """اگر بعد از ارسال خطایی رخ دهد، نباید fallback کنیم وگرنه پست تکراری می‌شود."""
    class HalfBrokenBot:
        id = 1
        token = "123456789:AAtest_only_dummy_token_0000000000000"

        def __init__(self, srv):
            self.srv = srv
            self.base_url = srv.base_url + self.token
            self.classic = []

        async def send_message(self, chat_id, text, **kw):
            self.classic.append(text)
            return SimpleNamespace(message_id=1)

        async def send_rich_message(self, chat_id, html, **kw):
            await rich_send.send_rich_raw(self, chat_id, html, **kw)   # واقعاً ارسال می‌شود
            raise RuntimeError("journal update failed after the message was sent")

    srv = FakeTelegram("ok")
    try:
        bot = HalfBrokenBot(srv)
        with pytest.raises(RuntimeError):
            asyncio.run(rich_send.send_content(bot, 55, POST_HTML))
        assert bot.classic == [], "بعد از ارسال نباید پیامِ دوم فرستاده شود"
        assert srv.methods_called() == ["sendRichMessage"], "فقط یک فراخوانیِ ارسال"
    finally:
        srv.close()


def test_the_server_parsed_structure_matches_the_intended_native_blocks():
    """آنچه سرور از HTML می‌سازد باید همان بلوک‌های بومیِ موردنظر باشد."""
    srv = FakeTelegram("ok")
    try:
        bot = Bot(srv)
        src = ("# تحلیل بازار\n\n## نکات\n- یک\n- دو\n\n"
               "| معیار | مقدار |\n|---|---|\n| سرعت | ۹۰ |\n\n"
               "> نتیجهٔ مهم")
        asyncio.run(rich_send.send_content(bot, 55, src))
        parsed = srv.calls[0]["body"]  # payload
        assert parsed["rich_message"]["is_rtl"] is True
        html = parsed["rich_message"]["html"]
        struct = parse_rich_html(html, True)
        kinds = [b["type"] for b in struct["blocks"]]
        assert "heading" in kinds and "list" in kinds and "table" in kinds and "quotation" in kinds, kinds
        heading = [b for b in struct["blocks"] if b["type"] == "heading"][0]
        assert "تحلیل بازار" in heading["text"]
        table = [b for b in struct["blocks"] if b["type"] == "table"][0]
        assert len(table["cells"]) == 2 and len(table["cells"][0]) == 2
        assert table["cells"][0][0].get("is_header") is True
        assert "معیار" in table["cells"][0][0]["text"] and "۹۰" in table["cells"][1][1]["text"]
        lst = [b for b in struct["blocks"] if b["type"] == "list"][0]
        assert len(lst["items"]) == 2
    finally:
        srv.close()


def test_the_panel_report_keeps_the_previous_format_byte_for_byte():
    """گزارشِ پنل (پیامِ کلاسیک) نباید با قابلیتِ جدید تغییرِ ظاهری کند."""
    from newsbot.scheduler import report_text
    txt = report_text(None, None, "fa")
    assert txt.startswith("📊 <b>گزارش سیستم</b>"), "تیترِ گزارش مثل قبل بماند"
    assert "👥 کاربران" in txt and " · " in txt, "چیدمانِ «یک سنجه در هر خط» مثل قبل بماند"
    assert "🧮" in txt, "خطِ توکن باید در گزارش بماند"
    assert "<h2>" not in txt and "<ul>" not in txt, "پنل کلاسیک است، نه پیامِ بومی"
