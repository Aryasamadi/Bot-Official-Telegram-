# -*- coding: utf-8 -*-
"""ریشهٔ خرابیِ «همهٔ مدل‌ها پاسخ ندادند» — آزمونِ سرتاسریِ مسیرِ تولید.

این فایل عمداً *شبیه‌سازیِ monkeypatch‌شده* ندارد: یک سرورِ HTTP واقعی روی
127.0.0.1 بالا می‌آید که رفتارِ همان پروایدرهایی را تقلید می‌کند که در تولیدِ
واقعی خرابی می‌ساختند:

* پاسخِ **جریانی** (SSE) با تأخیرِ کوتاه تا اولین بایت (مدلِ کند + لبهٔ Cloudflare
  که بدونِ جریان ۵۲۴ می‌داد)؛
* خطای **۵۲۴** (مبدأ پاسخ نداد) در تلاشِ اول یا همیشه؛
* چند مدل که فقط یکی‌شان سالم است.

مسیرِ واقعیِ زیر اجرا می‌شود: net_http.fetch_post → httpx → سرور → ادغامِ SSE →
_openai_content → اعتبارسنجیِ خروجی → generate() → compose().

هدف: ثابت کردن اینکه (۱) یک خطای گذرای پروایدر مسیرِ تولید را نمی‌کشد،
(۲) خرابیِ یک مدل اتوماسیون را متوقف نمی‌کند (مدلِ سالمِ بعدی کار می‌کند)،
(۳) خطای واقعی گم نمی‌شود (کدِ خطا و وضعیتِ مدل درست است).
"""
import asyncio
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from newsbot import config
from newsbot import ai_models as amod
from newsbot import generate as gen_mod
from newsbot.core_channels import add_channel, channel_by_chat, get_settings
from newsbot.db import q
from newsbot.timeutils import now_iso


class _Provider:
    """پروایدرِ سازگار با OpenAI (JSON یا SSE) + خطاهای لبه."""

    def __init__(self):
        self.calls = []            # بدنهٔ هر درخواست
        self.payload = ""
        self.first_byte_delay = 0.05
        self.responder = None      # responder(body, index) → ("sse"|"json"|"status", ...)
        self._server = None

    # -- HTTP -------------------------------------------------------------
    def _handler(self):
        outer = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):        # سکوتِ لاگِ سرور
                pass

            def _send(self, code, ctype, body: bytes):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(n) if n else b"{}"
                try:
                    body = json.loads(raw.decode("utf-8", "replace"))
                except Exception:
                    body = {}
                outer.calls.append(body)
                idx = len(outer.calls) - 1
                mode, data = ("sse", outer.payload) if outer.responder is None else outer.responder(body, idx)
                if mode == "status":
                    self._send(int(data[0]), "text/html", str(data[1]).encode())
                    return
                if mode == "json":
                    self._send(200, "application/json", json.dumps(
                        {"choices": [{"message": {"content": data}, "finish_reason": "stop"}],
                         "usage": {"prompt_tokens": 7, "completion_tokens": 11}}).encode())
                    return
                # SSE: همان قالبِ OpenAI (data: {...} در چند قطعه + [DONE])
                chunks = [data[i:i + 40] for i in range(0, len(data), 40)] or [""]
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()
                time.sleep(outer.first_byte_delay)          # تأخیرِ قبلِ اولین بایت
                for c in chunks:
                    piece = ("data: " + json.dumps({"choices": [{"delta": {"content": c}, "finish_reason": None}]}) + "\n\n").encode()
                    self.wfile.write(("%x\r\n" % len(piece)).encode() + piece + b"\r\n")
                    self.wfile.flush()
                if isinstance(body, dict) and (body.get("stream_options") or {}).get("include_usage"):
                    usage = ("data: " + json.dumps({"choices": [], "usage": {"prompt_tokens": 7, "completion_tokens": 11}}) + "\n\n").encode()
                    self.wfile.write(("%x\r\n" % len(usage)).encode() + usage + b"\r\n")
                done = b"data: [DONE]\n\n"
                self.wfile.write(("%x\r\n" % len(done)).encode() + done + b"\r\n")
                self.wfile.write(b"0\r\n\r\n")
                self.wfile.flush()

        return H

    def start(self):
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self._server.daemon_threads = True
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        return self

    @property
    def port(self):
        return self._server.server_address[1]

    @property
    def base_url(self):
        return "http://127.0.0.1:%d/v1" % self.port

    def stop(self):
        try:
            self._server.shutdown()
            self._server.server_close()
        except Exception:
            pass


@pytest.fixture
def provider(monkeypatch):
    p = _Provider().start()
    # دروازهٔ خروج: میزبانِ خصوصی فقط با فهرستِ سفیدِ آزمون اجازه دارد
    monkeypatch.setattr(config, "ALLOW_PRIVATE_MODEL_HOSTS", "127.0.0.1:%d" % p.port)
    monkeypatch.setattr(config, "AI_TRANSIENT_RETRIES", 1)
    monkeypatch.setattr(config, "AI_RETRY_BASE_SEC", 0.01)
    yield p
    p.stop()


def _add_model(base_url, name, priority=10, model_name=None):
    q("DELETE FROM ai_models WHERE name=?", (name,), commit=True)
    amod.add_model(base_url, "sk-test-not-a-real-key", model_name or ("test-model-" + name), name=name, priority=priority)
    mid = q("SELECT id FROM ai_models WHERE name=?", (name,), one=True)["id"]
    return mid


def _channel(chat_id, title):
    add_channel(1, chat_id, title, "", 1)
    return channel_by_chat(chat_id)["id"]


def _article(tag):
    return {"url": "https://news.test/%s" % tag, "title": "تیتر %s" % tag,
            "text": "متن خبری برای آزمون مسیر تولید. " * 12, "media": None, "published": now_iso()}


def _valid_payload(s):
    return json.dumps({"category": s["categories"][0]["name"], "title": "تیتر معتبر",
                       "post": "<b>تیتر معتبر</b>\n\n" + "متن خبری معتبر برای انتشار. " * 10,
                       "scores": {c["name"]: 9 for c in s["criteria"]}, "full": None}, ensure_ascii=False)


# ── آزمون‌ها ──────────────────────────────────────────────────────────────────
def test_slow_streaming_provider_is_consumed_end_to_end(provider):
    """مسیرِ واقعی: پاسخِ SSEِ کند ⇒ خروجیِ معتبر ⇒ پستِ آمادهٔ انتشار.

    ریشهٔ خرابیِ قبلی همین بود: بدونِ جریان، لبه پاسخ را با ۵۲۴ می‌بست و
    «همهٔ مدل‌ها پاسخ ندادند» دیده می‌شد.
    """
    cid = _channel(-100970001, "کانال e2e")
    s = get_settings(cid)
    provider.payload = _valid_payload(s)
    _add_model(provider.base_url, "e2e-sse")
    j, model, err = asyncio.run(gen_mod.generate(s, _article("e2e-1")))
    assert err is None, f"تولید نباید شکست بخورد: {err}"
    assert j and j["post"].startswith("<b>تیتر معتبر</b>")
    assert model == "e2e-sse"
    row = q("SELECT * FROM ai_models WHERE name=?", ("e2e-sse",), one=True)
    assert row["status"] == "ok" and row["fail_count"] == 0, "پروایدرِ سالم نباید down شود"
    assert provider.calls, "واقعاً باید درخواستِ HTTP زده شده باشد"
    assert provider.calls[0].get("stream") is True, "درخواست باید جریانی باشد"
    # مصرفِ توکن هم باید ثبت شده باشد (پاسخِ جریانی مصرف را گم نمی‌کند)
    assert q("SELECT 1 FROM ai_usage WHERE model_id=(SELECT id FROM ai_models WHERE name='e2e-sse')",
             one=True) is not None, "مصرفِ توکنِ پاسخِ جریانی باید ثبت شود"


def test_edge_524_is_retried_and_the_article_is_still_produced(provider):
    """۵۲۴ِ لبه ⇒ گذراست ⇒ تلاشِ دوباره ⇒ همان مقاله تولید می‌شود."""
    cid = _channel(-100970002, "کانال 524")
    s = get_settings(cid)
    provider.payload = _valid_payload(s)

    def responder(body, idx):
        if idx == 0:
            return ("status", (524, "<html>524: A timeout occurred</html>"))
        return ("sse", provider.payload)

    provider.responder = responder
    _add_model(provider.base_url, "e2e-524")
    j, model, err = asyncio.run(gen_mod.generate(s, _article("e2e-524")))
    assert err is None, f"پس از ۵۲۴ باید موفق می‌شد: {err}"
    assert j and j["post"]
    assert len(provider.calls) >= 2, "باید دوباره امتحان شده باشد"


def test_one_broken_model_does_not_stop_the_pipeline(provider):
    """خرابیِ یک مدل نباید اتوماسیون را متوقف کند: مدلِ سالمِ بعدی باید کار کند."""
    cid = _channel(-100970003, "کانال دومدل")
    s = get_settings(cid)
    provider.payload = _valid_payload(s)
    _add_model(provider.base_url, "e2e-broken", priority=1, model_name="broken-model")
    _add_model(provider.base_url, "e2e-good", priority=2, model_name="good-model")
    seen = {"broken": 0}

    def responder(body, idx):
        # مدلِ «خراب» همیشه ۵۲۴ می‌دهد؛ مدلِ سالم پاسخِ کامل می‌دهد
        if body.get("model") == "broken-model":
            seen["broken"] += 1
            return ("status", (524, "<html>524: A timeout occurred</html>"))
        return ("json", provider.payload)

    provider.responder = responder
    j, model, err = asyncio.run(gen_mod.generate(s, _article("e2e-two")))
    assert err is None, f"مدلِ سالم باید نجات بدهد: {err}"
    assert j and j["post"]
    rows = {r["name"]: r for r in q("SELECT * FROM ai_models WHERE name IN ('e2e-broken','e2e-good')")}
    assert rows["e2e-broken"]["fail_count"] >= 1, "مدلِ خراب باید شکستش ثبت شود"
    assert rows["e2e-good"]["status"] == "ok", "مدلِ سالم باید سالم بماند"


def test_all_models_failing_gives_a_precise_error_and_marks_the_model_down(provider):
    """اگر واقعاً همهٔ مدل‌ها خراب باشند: خطای دقیق، مدل down، و خطا پنهان نمی‌شود."""
    cid = _channel(-100970004, "کانال خراب")
    s = get_settings(cid)
    provider.payload = _valid_payload(s)
    provider.responder = lambda body, idx: ("status", (524, "<html>524: A timeout occurred</html>"))
    mid = _add_model(provider.base_url, "e2e-dead")
    j, model, err = asyncio.run(gen_mod.generate(s, _article("e2e-dead")))
    assert j is None and err, "نباید خروجیِ ساختگی ساخته شود"
    assert err in ("ai_timeout", "ai_busy", "ai_all_failed"), f"کدِ خطا باید دقیق باشد: {err}"
    row = q("SELECT * FROM ai_models WHERE id=?", (mid,), one=True)
    assert row["fail_count"] >= 1 and row["last_error"], "خطای واقعی باید ثبت شود، نه پنهان"
