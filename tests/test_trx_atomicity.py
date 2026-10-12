# -*- coding: utf-8 -*-
"""فاز ۳ — اتمیک‌بودن تراکنش‌ها روی هر دو بک‌اند (C3).

مسئلهٔ C3: روی D1 دستور `BEGIN IMMEDIATE` در شیم no-op بود و `_trx` عملاً «اجرای ترتیبی»
می‌شد. یعنی اگر تابعِ داخل تراکنش وسط کار خطا می‌داد، دستوراتِ نوشته‌شده *قبلاً* به D1
فرستاده شده و اعمال شده بودند و rollback هیچ اثری نداشت ⇒ نیمه‌کاره‌ماندن داده
(مثلاً کانال با منابعِ پاک‌شده ولی مالکِ قبلی).

راه‌حلِ ریشه‌ای: `_trx` روی D1 دستورات نوشتاری را در `D1BatchConn` جمع می‌کند و در پایان
یک‌جا به‌صورت یک `batch` اتمیک می‌فرستد؛ D1 کل batch را در یک تراکنش اجرا و در صورت خطا
کاملاً رول‌بک می‌کند. اگر تابعِ درونی خطا بدهد، *هیچ* دستوری ارسال نمی‌شود.
"""
import json
import sqlite3
import pytest

import newsbot.db as dbmod
from newsbot import config


# ── sqlite: rollbackِ واقعی ──

def test_sqlite_trx_rolls_back_on_error():
    """اگر fn خطا بدهد، هیچ تغییری روی sqlite باقی نمی‌ماند."""
    dbmod.gset("trx_probe", "before")
    def _go(conn):
        conn.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('trx_probe','after')")
        raise RuntimeError("boom")
    with pytest.raises(RuntimeError):
        dbmod._trx(_go)
    assert dbmod.gget("trx_probe") == "before"


def test_sqlite_trx_commits_on_success():
    dbmod.gset("trx_probe2", "old")
    def _go(conn):
        conn.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('trx_probe2',?)", (json.dumps("new"),))
        return "done"
    assert dbmod._trx(_go) == "done"
    assert dbmod.gget("trx_probe2") == "new"


# ── D1: اتمیک بودن batch ──

class _FakeResp:
    def __init__(self, payload, status=200):
        self._p = payload; self.status_code = status; self.text = json.dumps(payload)
    def json(self): return self._p


class _FakeClient:
    """کلاینت ساختگی D1: درخواست‌ها را ثبت می‌کند و batch را شبیه‌سازی می‌کند.
    اگر هر دستور در batch خطا بدهد، D1 واقعی *کل* batch را رول‌بک می‌کند؛ این کلاس هم
    دقیقاً همین را مدل می‌کند تا بتوانیم اتمیک‌بودن را بدون اعتبارنامهٔ Cloudflare بیازماییم."""
    def __init__(self, fail_on=None):
        self.calls = []          # هر درخواست: بدنهٔ JSON
        self.applied = []        # دستوراتی که «اعمال شده» تلقی می‌شوند
        self.fail_on = fail_on   # ایندکس دستور داخل batch که باید خطا بدهد

    def post(self, url, json=None, headers=None):
        body = json or {}
        self.calls.append(body)
        if "batch" in body:
            batch = body["batch"]
            # ابتدا همه را «اعتبارسنجی» می‌کنیم: اگر یکی خطا داشته باشد کل batch رد می‌شود
            if self.fail_on is not None and self.fail_on < len(batch):
                return _FakeResp({"success": False, "errors": [{"text": "simulated failure"}]}, status=400)
            for st in batch:
                self.applied.append(st["sql"])
            return _FakeResp({"success": True, "result": [
                {"results": [], "meta": {"changes": 1, "last_row_id": 1}} for _ in batch]})
        sql = (body.get("sql") or "").upper()
        if sql.startswith("SELECT"):
            return _FakeResp({"success": True, "result": [{"results": [{"lid": 1}], "meta": {"changes": 0}}]})
        self.applied.append(body.get("sql"))
        return _FakeResp({"success": True, "result": [{"results": [], "meta": {"changes": 1, "last_row_id": 1}}]})


@pytest.fixture
def d1_env(monkeypatch):
    """بک‌اند را به d1 سوییچ می‌کند و کلاینت ساختگی را جای‌گزین می‌کند.

    نکته: `db()` کانکشن را در thread-local کش می‌کند؛ برای اینکه خواندن‌ها هم واقعاً به
    مسیر D1 بروند، کانکشنِ کش‌شدهٔ sqlite را موقتاً با یک D1Conn (روی کلاینت ساختگی) عوض می‌کنیم.
    """
    fake = _FakeClient()
    monkeypatch.setattr(config, "DB_BACKEND", "d1")
    monkeypatch.setattr(dbmod, "_d1_client", lambda: fake)
    monkeypatch.setattr(dbmod, "_d1_url", lambda: "https://fake/d1/query")
    # کانکشن کش‌شدهٔ همین ترد را با D1Conn جای‌گزین کن (خواندن‌ها هم از D1 رد شوند)
    monkeypatch.setattr(dbmod._tls, "conn", dbmod.D1Conn(), raising=False)
    monkeypatch.setattr(dbmod, "_SCHEMA_READY", True, raising=False)
    yield fake


def test_d1_trx_sends_single_atomic_batch(d1_env):
    """همهٔ نوشتن‌ها باید در *یک* درخواست batch بروند (نه چند درخواست جدا)."""
    def _go(conn):
        conn.execute("DELETE FROM sources WHERE channel_id=?", (7,))
        conn.execute("UPDATE channels SET admin_id=? WHERE id=?", (99, 7))
        conn.execute("DELETE FROM dedup WHERE channel_id=?", (7,))
    dbmod._trx(_go)

    batches = [c for c in d1_env.calls if "batch" in c]
    assert len(batches) == 1, "نوشتن‌ها باید در یک batch واحد جمع شوند"
    assert len(batches[0]["batch"]) == 3
    singles = [c for c in d1_env.calls if "sql" in c and not c["sql"].upper().startswith("SELECT")]
    assert singles == [], "نباید نوشتنِ تک‌دستورهٔ جداگانه‌ای خارج از batch برود"


def test_d1_trx_failure_sends_nothing(d1_env):
    """کلید C3: اگر fn وسط کار خطا بدهد، هیچ نوشتنی نباید به D1 برسد."""
    def _go(conn):
        conn.execute("DELETE FROM sources WHERE channel_id=?", (7,))
        conn.execute("UPDATE channels SET admin_id=? WHERE id=?", (99, 7))
        raise RuntimeError("boom after two writes")
    with pytest.raises(RuntimeError):
        dbmod._trx(_go)

    writes = [c for c in d1_env.calls if "batch" in c] + \
             [c for c in d1_env.calls if "sql" in c and not c["sql"].upper().startswith("SELECT")]
    assert writes == [], "هیچ نوشتنی نباید ارسال شود (اتمیسیتی D1)"


def test_d1_trx_batch_failure_is_atomic(d1_env):
    """اگر D1 خودش batch را رد کند، هیچ دستوری اعمال نمی‌شود (rollback سمت سرور)."""
    d1_env.fail_on = 1   # دستور دوم باعث خطای کل batch می‌شود
    def _go(conn):
        conn.execute("DELETE FROM sources WHERE channel_id=?", (7,))
        conn.execute("UPDATE channels SET admin_id=? WHERE id=?", (99, 7))
    with pytest.raises(sqlite3.OperationalError):
        dbmod._trx(_go)
    assert d1_env.applied == [], "batchِ ردشده نباید هیچ دستوری اعمال کند"


def test_d1_trx_reads_are_immediate(d1_env):
    """SELECT باید فوری اجرا شود (نه بافر)، وگرنه منطق خواندن می‌شکند."""
    def _go(conn):
        r = conn.execute("SELECT COUNT(*) c FROM channels")
        assert r.fetchone() is not None
    dbmod._trx(_go)
    assert any("SELECT" in (c.get("sql") or "").upper() for c in d1_env.calls)


def test_d1_batch_conn_ignores_begin_commit(d1_env):
    """BEGIN/COMMIT/ROLLBACK داخل fn نباید دستور جداگانه بفرستد (D1 پشتیبانی نمی‌کند)."""
    def _go(conn):
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("UPDATE users SET credit=0 WHERE id=?", (1,))
        conn.execute("COMMIT")
    dbmod._trx(_go)
    batches = [c for c in d1_env.calls if "batch" in c]
    assert len(batches) == 1
    sqls = [st["sql"].upper() for st in batches[0]["batch"]]
    assert all(not s.startswith(("BEGIN", "COMMIT", "ROLLBACK")) for s in sqls)
