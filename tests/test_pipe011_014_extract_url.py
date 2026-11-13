# -*- coding: utf-8 -*-
"""PIPE-014 / PIPE-011: canonical URL independent of query order; per-article extraction deadline."""
import asyncio

from newsbot import config, extract
from newsbot.discover import clean_url


def test_clean_url_is_independent_of_query_parameter_order():
    a = clean_url("https://example.com/news?id=7&page=2&lang=fa")
    b = clean_url("https://example.com/news?lang=fa&id=7&page=2")
    c = clean_url("https://www.example.com/news?page=2&lang=fa&id=7&utm_source=x")
    assert a == b == c


def test_clean_url_still_distinguishes_different_values():
    assert clean_url("https://example.com/n?id=1&p=2") != clean_url("https://example.com/n?id=2&p=1")


def test_clean_url_no_query_unchanged_shape():
    assert clean_url("https://www.example.com/a/b/") == clean_url("https://example.com/a/b/")


def test_extract_article_returns_none_when_deadline_exceeded(monkeypatch):
    monkeypatch.setattr(config, "EXTRACT_ARTICLE_DEADLINE_SEC", 0.05)

    async def slow(*a, **k):
        await asyncio.sleep(5)
        return {"text": "x" * 500}

    monkeypatch.setattr(extract, "_extract_article_impl", slow)
    res = asyncio.run(extract.extract_article("https://slow.example.com/a"))
    assert res is None


def test_extract_article_passes_result_through_when_fast(monkeypatch):
    async def fast(url, fb="", tz=None):
        return {"url": url, "text": "ok" * 100}

    monkeypatch.setattr(extract, "_extract_article_impl", fast)
    res = asyncio.run(extract.extract_article("https://fast.example.com/a"))
    assert res["url"] == "https://fast.example.com/a"
