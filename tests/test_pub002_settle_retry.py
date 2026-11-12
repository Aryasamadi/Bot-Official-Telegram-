# -*- coding: utf-8 -*-
"""PUB-002: خطای دیتابیس بین ارسال پیام و ثبت در جدول posted نباید پست را بدون تلاش مجدد یا دلیل مشخص گم کند."""
import asyncio

import pytest

from newsbot import publish
from newsbot.core_articles import article_insert, article_update, get_article
from newsbot.core_channels import add_channel, list_channels


def test_settle_retries_on_transient_db_error_and_succeeds(monkeypatch):
    monkeypatch.setattr(publish, "_SETTLE_RETRY_DELAYS", (0.01, 0.01, 0.01))
    add_channel(1, -10099001, "pub002_ch1", "Pub002 Ch1", 1)
    ch = [c for c in list_channels(1) if c["chat_id"] == -10099001][0]
    aid = article_insert(1, ch["id"], "h-pub002-1", "https://example.com/1", "Title 1", 0, None, status="ready")
    article_update(aid, post_html="<b>Hi</b>")

    calls = {"n": 0}
    orig_settle = publish.settle_publication

    def flaky_settle(a_id, c_dict, j_dict):
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("database is locked (transient)")
        return orig_settle(a_id, c_dict, j_dict)

    monkeypatch.setattr(publish, "settle_publication", flaky_settle)

    journal = {"phase": "ack", "link": "https://t.me/c/1/99", "admin_id": 1}
    asyncio.run(publish._settle_with_retry(aid, ch, journal))

    assert calls["n"] == 3
    art = get_article(aid)
    assert art["status"] == "published"
    assert art["reason"] == ""


def test_settle_records_settle_failed_when_all_retries_exhausted(monkeypatch):
    monkeypatch.setattr(publish, "_SETTLE_RETRY_DELAYS", (0.01, 0.01))
    add_channel(1, -10099002, "pub002_ch2", "Pub002 Ch2", 1)
    ch = [c for c in list_channels(1) if c["chat_id"] == -10099002][0]
    aid = article_insert(1, ch["id"], "h-pub002-2", "https://example.com/2", "Title 2", 0, None, status="ready")
    article_update(aid, post_html="<b>Hi 2</b>")

    def always_fail(a_id, c_dict, j_dict):
        raise RuntimeError("persistent disk I/O error")

    monkeypatch.setattr(publish, "settle_publication", always_fail)

    journal = {"phase": "ack", "link": "https://t.me/c/2/100", "admin_id": 1}
    with pytest.raises(RuntimeError, match="persistent disk I/O error"):
        asyncio.run(publish._settle_with_retry(aid, ch, journal))

    art = get_article(aid)
    assert art["reason"] == "settle_failed"
