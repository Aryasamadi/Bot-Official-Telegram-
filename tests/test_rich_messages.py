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
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest
from telegram.error import BadRequest, NetworkError, RetryAfter

from newsbot import config
from newsbot import rich_format
from newsbot import rich_send
from newsbot.text_format import strip_tags

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
            return 200, {"ok": True, "result": {"message_id": 1}}
        if self.mode == "ok":
            return 200, {"ok": True, "result": {"message_id": 4242, "date": 1700000000,
                                                "chat": {"id": data.get("chat_id"), "type": "private"}}}
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


def test_report_markdown_renders_as_structured_legacy_html():
    from newsbot.scheduler import report_markdown
    md = report_markdown(None, None, "fa")
    assert "# 📊 گزارش سیستم" in md
    assert "## وضعیت" in md
    doc = rich_format.parse_content(md, kind=rich_format.KIND_REPORT)
    legacy = rich_format.render_legacy(doc)
    assert "<b>وضعیت</b>" in legacy
    assert "• " in legacy
    assert "🧮" in legacy, "خطِ توکن باید در گزارش بماند"
