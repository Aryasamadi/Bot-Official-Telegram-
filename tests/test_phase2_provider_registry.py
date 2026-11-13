# -*- coding: utf-8 -*-
"""نگهبانِ «رجیستریِ فراهم‌کننده» (فاز ۲ · ADR-۲).

سه چیز را قفل می‌کند:

1. **مهاجرتِ افزایشی و idempotent**: یک دیتابیسِ v9 با چند مدلِ تخت به v10 می‌رود، هر
   ``(owner, base_url)`` یک ``ai_providers`` می‌گیرد، مدل‌ها به آن وصل می‌شوند و اجرای دوباره
   هیچ ردیفی را تکراری نمی‌کند.
2. **کلید هرگز plaintext نیست** و در پیامِ خطا/خروجی هم افشا نمی‌شود.
3. **آزمونِ اتصال و کشفِ مدل** با یک ``transport`` جعلی سنجیده می‌شوند (بدونِ شبکه): مسیرِ
   ``/models``، هدرِ متناظر با هر ``kind``، و رفتارِ خطا.

چرخشِ round-robin و مکان‌نمای اتمی **فاز ۳** است و اینجا آزمون داده نشده.
"""
import asyncio
import json
import sqlite3

import pytest

from newsbot import db as dbmod
from newsbot import providers


@pytest.fixture()
def conn(isolated_db):
    """دیتابیسِ واقعیِ فایل‌محورِ مخزن (SQLite) — همان مسیرِ ``isolated_db`` در conftest."""
    from newsbot import db as _db

    c = _db.db()
    c.row_factory = sqlite3.Row
    return c


# ── ۱) مهاجرت ─────────────────────────────────────────────────────────────────────
def _make_v9(path):
    """دیتابیسِ «قبل از فاز ۲»: ai_models بدونِ provider_id، بدونِ ai_providers."""
    c = sqlite3.connect(str(path))
    c.row_factory = sqlite3.Row
    c.execute("CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT)")
    c.execute("INSERT INTO meta(key,value) VALUES('schema_version','9')")
    c.execute("CREATE TABLE ai_models(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, kind TEXT, "
              "base_url TEXT, api_key TEXT, model TEXT, priority INTEGER DEFAULT 10, active INTEGER DEFAULT 1, "
              "status TEXT DEFAULT 'ok', fail_count INTEGER DEFAULT 0, last_error TEXT, last_ok TEXT, last_fail TEXT, "
              "next_try_after TEXT DEFAULT '', ok_count INTEGER DEFAULT 0, temperature REAL DEFAULT 0.5, "
              "max_tokens INTEGER DEFAULT 2500, owner_id INTEGER)")
    # سه ردیف: دو مدل روی یک میزبان (یکی با کلید متفاوت) + یکی روی میزبانِ دیگر
    c.execute("INSERT INTO ai_models(name,kind,base_url,api_key,model,priority,owner_id) VALUES('a','openai','https://api.example.com','k1','m1',10,NULL)")
    c.execute("INSERT INTO ai_models(name,kind,base_url,api_key,model,priority,owner_id) VALUES('b','openai','https://api.example.com','k2','m2',20,NULL)")
    c.execute("INSERT INTO ai_models(name,kind,base_url,api_key,model,priority,owner_id) VALUES('c','openai','https://other.example.com','k3','m3',30,7)")
    c.commit()
    return c


def _migrate(path):
    c = sqlite3.connect(str(path))
    c.row_factory = sqlite3.Row
    for stmt in (s.strip() for s in dbmod.SCHEMA.split(";") if s.strip()):
        c.execute(stmt)
    c.execute("CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT)")
    dbmod._apply_migrations(c)
    dbmod._backfill_providers(c)
    c.commit()
    return c


def test_backfill_groups_models_by_host_and_is_idempotent(tmp_path):
    path = tmp_path / "v9.db"
    c = _make_v9(path)
    c.close()

    c = _migrate(path)
    provs = c.execute("SELECT * FROM ai_providers ORDER BY id").fetchall()
    assert len(provs) == 2, "دو میزبان ⇒ دو فراهم‌کننده"
    assert {p["base_url"] for p in provs} == {"https://api.example.com", "https://other.example.com"}
    # هر مدل به فراهم‌کنندهٔ هم‌میزبانِ خودش وصل شده
    for m in c.execute("SELECT id, provider_id FROM ai_models ORDER BY id").fetchall():
        assert m["provider_id"] is not None
    # مدلِ بدونِ owner به فراهم‌کنندهٔ بدونِ owner رفته و مدلِ owner=7 به محدودهٔ خودش
    rows = {m["id"]: m["provider_id"] for m in c.execute("SELECT id, provider_id FROM ai_models").fetchall()}
    pids = {p["id"]: p["owner_id"] for p in provs}
    assert pids[rows[3]] == 7

    # اجرای دوباره: هیچ ردیفِ تکراری و هیچ مدلِ جاافتاده
    dbmod._backfill_providers(c)
    c.commit()
    assert c.execute("SELECT COUNT(*) AS n FROM ai_providers").fetchone()["n"] == 2
    assert c.execute("SELECT COUNT(*) AS n FROM ai_models WHERE provider_id IS NULL").fetchone()["n"] == 0
    c.close()


def test_backfill_keeps_the_first_key_and_does_not_touch_model_tuning(tmp_path):
    path = tmp_path / "v9b.db"
    c = _make_v9(path)
    c.close()
    c = _migrate(path)
    p = c.execute("SELECT * FROM ai_providers WHERE base_url='https://api.example.com'").fetchone()
    assert p["api_key"] in ("k1",)          # کلیدِ اولین ردیف (id=1)
    m = c.execute("SELECT * FROM ai_models WHERE id=1").fetchone()
    assert m["priority"] == 10 and m["model"] == "m1"   # تنظیمِ سطحِ مدل دست‌نخورده
    c.close()


# ── ۲) رمزنگاری ───────────────────────────────────────────────────────────────────
def test_api_key_is_encrypted_at_rest_and_masked_in_output(conn, monkeypatch):
    from newsbot import config as cfg

    monkeypatch.setattr(cfg, "SECRETS_KEY", "phase2-test-key")
    monkeypatch.setattr(providers, "_now", lambda: "2026-11-13T00:00:00Z")
    pid = providers.create_provider("Example", "https://api.example.com/", "sk-secret-123456", owner_id=None)
    raw = conn.execute("SELECT api_key FROM ai_providers WHERE id=?", (pid,)).fetchone()["api_key"]
    assert raw and raw != "sk-secret-123456", "کلید باید رمزنگاری‌شده ذخیره شود"
    # خروجیِ عمومی هرگز کلیدِ کامل را نشان نمی‌دهد
    pub = providers.get_provider(pid)
    assert pub["api_key"] != "sk-secret-123456"
    assert pub["has_key"] is True
    conn.commit()


def test_delete_is_blocked_while_models_reference_the_provider(conn, monkeypatch):
    monkeypatch.setattr(providers, "_now", lambda: "2026-11-13T00:00:00Z")
    from newsbot import ai_models

    pid = providers.create_provider("P", "https://api.example.com", "k", owner_id=None)
    ai_models.add_model("https://api.example.com", "ignored", "m1", provider_id=pid)
    with pytest.raises(ValueError):
        providers.delete_provider(pid)
    assert providers.provider_model_count(pid) == 1
    # بعد از حذفِ مدل، حذفِ فراهم‌کننده آزاد می‌شود
    conn.execute("DELETE FROM ai_models WHERE provider_id=?", (pid,))
    conn.commit()
    providers.delete_provider(pid)
    assert providers.get_provider(pid) is None
    conn.commit()


# ── ۳) آزمونِ اتصال و کشفِ مدل (بدونِ شبکه) ────────────────────────────────────────
class _Resp:
    def __init__(self, status, text):
        self.status_code = status
        self.text = text


def _fake_fetch(recorder, status=200, body=None):
    async def fetch(url, **kw):
        recorder["url"] = url
        recorder["headers"] = kw.get("headers") or {}
        return _Resp(status, json.dumps(body if body is not None else {"data": [{"id": "m1"}, {"id": "m2"}]}))

    return fetch


def test_probe_uses_models_endpoint_and_bearer_header(monkeypatch):
    from newsbot import net_http

    rec = {}
    monkeypatch.setattr(net_http, "fetch", _fake_fetch(rec))
    ok, ids, err = asyncio.run(providers.probe("https://api.example.com", "sk-1"))
    assert ok and ids == ["m1", "m2"] and not err
    assert rec["url"] == "https://api.example.com/models"
    assert rec["headers"].get("Authorization") == "Bearer sk-1"


def test_probe_uses_anthropic_and_gemini_headers(monkeypatch):
    from newsbot import net_http

    rec = {}
    monkeypatch.setattr(net_http, "fetch", _fake_fetch(rec))
    asyncio.run(providers.probe("https://api.anthropic.com", "ak-1"))
    assert rec["headers"].get("x-api-key") == "ak-1"

    rec2 = {}
    monkeypatch.setattr(net_http, "fetch", _fake_fetch(rec2))
    asyncio.run(providers.probe("https://generativelanguage.googleapis.com", "gk-1"))
    assert rec2["headers"].get("x-goog-api-key") == "gk-1"


def test_probe_parses_gemini_shape_and_deduplicates(monkeypatch):
    from newsbot import net_http

    body = {"models": [{"name": "models/gemini-x"}, {"name": "models/gemini-x"}, {"name": "models/gemini-y"}]}
    monkeypatch.setattr(net_http, "fetch", _fake_fetch({}, body=body))
    ok, ids, err = asyncio.run(providers.probe("https://generativelanguage.googleapis.com", "k"))
    assert ok and ids == ["gemini-x", "gemini-y"]


def test_probe_error_never_echoes_the_key(monkeypatch):
    from newsbot import net_http

    async def boom(url, **kw):
        raise RuntimeError("connect failed for sk-topsecret")

    monkeypatch.setattr(net_http, "fetch", boom)
    ok, ids, err = asyncio.run(providers.probe("https://api.example.com", "sk-topsecret"))
    assert ok is False and ids == []
    assert "sk-topsecret" not in err


def test_probe_http_error_is_reported_without_body(monkeypatch):
    from newsbot import net_http

    monkeypatch.setattr(net_http, "fetch", _fake_fetch({}, status=401, body={"error": "nope"}))
    ok, ids, err = asyncio.run(providers.probe("https://api.example.com", "k"))
    assert ok is False and "401" in err


def test_test_connection_persists_the_result(conn, monkeypatch):
    from newsbot import config as cfg
    from newsbot import net_http

    monkeypatch.setattr(cfg, "SECRETS_KEY", "phase2-test-key")
    monkeypatch.setattr(providers, "_now", lambda: "2026-11-13T00:00:00Z")
    pid = providers.create_provider("P", "https://api.example.com", "k", owner_id=None)

    monkeypatch.setattr(net_http, "fetch", _fake_fetch({}, body={"data": [{"id": "m1"}]}))
    out = asyncio.run(providers.test_connection(pid))
    assert out["ok"] is True and out["model_count"] == 1
    row = conn.execute("SELECT * FROM ai_providers WHERE id=?", (pid,)).fetchone()
    assert row["last_test_ok"] == 1 and row["last_test_at"] == "2026-11-13T00:00:00Z"

    async def down(url, **kw):
        from newsbot.net_http import FetchError
        raise FetchError(503, url)

    monkeypatch.setattr(net_http, "fetch", down)
    out2 = asyncio.run(providers.test_connection(pid))
    assert out2["ok"] is False and out2["error"]
    row2 = conn.execute("SELECT * FROM ai_providers WHERE id=?", (pid,)).fetchone()
    assert row2["last_test_ok"] == 0 and row2["last_test_error"]
    conn.commit()


def test_discovery_returns_candidates_and_does_not_insert_anything(conn, monkeypatch):
    from newsbot import config as cfg
    from newsbot import net_http

    monkeypatch.setattr(cfg, "SECRETS_KEY", "phase2-test-key")
    monkeypatch.setattr(providers, "_now", lambda: "2026-11-13T00:00:00Z")
    pid = providers.create_provider("P", "https://api.example.com", "k", owner_id=None)
    monkeypatch.setattr(net_http, "fetch", _fake_fetch({}, body={"data": [{"id": "m1"}, {"id": "m2"}]}))
    out = asyncio.run(providers.discover_models(pid))
    assert out["ok"] and out["ids"] == ["m1", "m2"]
    assert providers.provider_model_count(pid) == 0, "کشف فقط کاندید می‌دهد؛ ثبت با تصمیمِ مدیر است"
    conn.commit()


def test_add_model_from_discovery_links_the_provider_and_leaves_the_key_there(conn, monkeypatch):
    from newsbot import config as cfg

    monkeypatch.setattr(cfg, "SECRETS_KEY", "phase2-test-key")
    monkeypatch.setattr(providers, "_now", lambda: "2026-11-13T00:00:00Z")
    pid = providers.create_provider("P", "https://api.example.com", "sk-1", owner_id=None)
    providers.add_model_from_discovery(pid, "m1", name="Model One")
    row = conn.execute("SELECT * FROM ai_models WHERE provider_id=?", (pid,)).fetchone()
    assert row is not None and row["model"] == "m1"
    assert not (row["api_key"] or "").strip(), "کلید در ai_models تکرار نمی‌شود"
    conn.commit()


def test_resolve_endpoint_prefers_the_provider(conn, monkeypatch):
    """اگر مدل به فراهم‌کننده وصل باشد، هویت/کلید از آن می‌آید نه از ردیفِ مدل."""
    from newsbot import ai_models
    from newsbot.secret_store import encrypt

    monkeypatch.setattr(providers, "_now", lambda: "2026-11-13T00:00:00Z")
    pid = providers.create_provider("P", "https://provider.example.com", "sk-provider", owner_id=None)
    conn.execute("INSERT INTO ai_models(name,kind,base_url,api_key,model,provider_id) "
                 "VALUES('m','openai','https://stale.example.com',?,'m1',?)",
                 (encrypt("sk-stale"), pid))
    conn.commit()
    row = conn.execute("SELECT * FROM ai_models WHERE provider_id=?", (pid,)).fetchone()
    kind, base, key = ai_models.resolve_endpoint(row)
    assert kind == "openai" and base == "https://provider.example.com" and key == "sk-provider"


def test_resolve_endpoint_falls_back_to_the_model_row(conn, monkeypatch):
    """ردیفِ مهاجرت‌نشده (provider_id=NULL) باید مثلِ قبل کار کند."""
    from newsbot import ai_models
    from newsbot.secret_store import encrypt

    conn.execute("INSERT INTO ai_models(name,kind,base_url,api_key,model,provider_id) "
                 "VALUES('m','openai','https://legacy.example.com',?,'m1',NULL)",
                 (encrypt("sk-legacy"),))
    conn.commit()
    row = conn.execute("SELECT * FROM ai_models WHERE provider_id IS NULL").fetchone()
    kind, base, key = ai_models.resolve_endpoint(row)
    assert base == "https://legacy.example.com" and key == "sk-legacy"
