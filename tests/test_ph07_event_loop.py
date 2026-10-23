# -*- coding: utf-8 -*-
"""PH-07 — event-loop correctness, bounded locks, publish throttling and pool sizing."""
import ast
import asyncio
import pathlib
import sqlite3
import threading
import time
from types import SimpleNamespace

import pytest

from newsbot import config
from newsbot.db import q, gget, arun, arun_maint

ROOT = pathlib.Path(__file__).resolve().parent.parent
DB_PRIMITIVES = {"q", "gget", "gset", "_trx", "_safe_fields", "db"}


def test_no_direct_db_primitive_calls_in_async_bodies():
    """PERF-001: async code must use arun(); direct sync DB primitives are forbidden."""
    bad = []
    for path in sorted((ROOT / "newsbot").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.AsyncFunctionDef):
                continue
            for call in ast.walk(node):
                if (isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
                        and call.func.id in DB_PRIMITIVES):
                    bad.append(f"{path.name}:{call.lineno} {node.name}() -> {call.func.id}()")
    assert not bad, "sync DB calls remain in async bodies:\n" + "\n".join(bad)


def test_runtime_db_query_runs_on_worker_thread(monkeypatch):
    import newsbot.db as dbmod
    seen = []
    real_q = dbmod.q

    def spy(*args, **kwargs):
        seen.append(threading.current_thread().name)
        return real_q(*args, **kwargs)

    monkeypatch.setattr(dbmod, "q", spy)

    async def go():
        return await arun(gget, "ph07_missing_key", None)

    assert asyncio.run(go()) is None
    assert seen and all(n.startswith("newsbot-db") for n in seen), seen


def test_strict_loop_guard_rejects_sync_q_but_allows_arun(monkeypatch):
    import newsbot.db as dbmod
    monkeypatch.setattr(dbmod, "_LOOP_GUARD_STRICT", True)

    async def go():
        with pytest.raises(RuntimeError, match="event-loop"):
            gget("ph07_missing_key")
        return await arun(gget, "ph07_missing_key", "ok")

    assert asyncio.run(go()) == "ok"


def test_busy_retry_does_not_sleep_on_event_loop(monkeypatch):
    """DB-006: a locked database on the loop must fail fast, never block for 20-160 ms."""
    import newsbot.db as dbmod

    def busy():
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(dbmod, "db", busy)

    async def go():
        ticks = 0

        async def ticker():
            nonlocal ticks
            while True:
                ticks += 1
                await asyncio.sleep(0.001)

        task = asyncio.create_task(ticker())
        await asyncio.sleep(0.02)
        before = ticks
        started = time.monotonic()
        with pytest.raises(RuntimeError, match="event-loop"):
            q("SELECT 1")
        elapsed = time.monotonic() - started
        await asyncio.sleep(0.02)
        after = ticks
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        return elapsed, before, after

    elapsed, before, after = asyncio.run(go())
    assert elapsed < 0.1, f"busy retry blocked the loop for {elapsed:.3f}s"
    assert after > before, "the event loop did not resume after the busy path"


def test_wal_mode_is_active():
    assert q("PRAGMA journal_mode", one=True)[0].lower() == "wal"


def test_maintenance_pool_does_not_starve_normal_db_pool():
    async def go():
        slow = asyncio.create_task(arun_maint(time.sleep, 0.4))
        await asyncio.sleep(0.03)
        started = time.monotonic()
        name = await arun(lambda: threading.current_thread().name)
        elapsed = time.monotonic() - started
        await slow
        return name, elapsed

    name, elapsed = asyncio.run(go())
    assert name.startswith("newsbot-db")
    assert elapsed < 0.2, f"normal DB work waited {elapsed:.3f}s behind maintenance"


def test_lock_pruning_is_bounded_and_db_free(monkeypatch):
    import newsbot.concurrency as cc
    import newsbot.db as dbmod

    def boom(*a, **k):
        raise AssertionError("lock pruning must not query the database")

    monkeypatch.setattr(dbmod, "q", boom)
    for i in range(1500):
        cc.ch_lock(100000 + i)
        cc.model_lock(200000 + i)
        cc.user_op_lock(300000 + i)
    assert len(cc._ch_locks) <= cc._LOCK_HARD_LIMIT + 1
    assert len(cc._model_locks) <= cc._LOCK_HARD_LIMIT + 1
    assert len(cc._user_op_locks) <= cc._LOCK_HARD_LIMIT + 1
    assert cc.ch_lock(999999) is cc.ch_lock(999999)


def test_cycle_takes_global_slot_before_channel_lock(monkeypatch):
    """PIPE-016: a waiting cycle must not hold the channel lock."""
    import newsbot.cycle as cycle

    events = []

    class RecSem:
        async def __aenter__(self):
            events.append("cycle_enter")
            return self

        async def __aexit__(self, *exc):
            events.append("cycle_exit")

    class RecLock:
        def locked(self):
            return False

        async def __aenter__(self):
            events.append("channel_enter")
            return self

        async def __aexit__(self, *exc):
            events.append("channel_exit")

    async def fake_cycle(*args, **kwargs):
        events.append("work")
        return {"ok": True}

    monkeypatch.setattr(cycle, "CYCLE_SEM", RecSem())
    monkeypatch.setattr(cycle, "ch_lock", lambda cid: RecLock())
    monkeypatch.setattr(cycle, "_cycle", fake_cycle)
    assert asyncio.run(cycle.run_channel_cycle(None, 1, 2)) == {"ok": True}
    assert events == ["cycle_enter", "channel_enter", "work", "channel_exit", "cycle_exit"]


def test_dns_resolution_is_cached(monkeypatch):
    """NET-008: public_url reuses a short-TTL resolution instead of resolving per hop."""
    import newsbot.net_http as net
    calls = []

    def fake_getaddrinfo(host, port, **kwargs):
        calls.append(host)
        return [(None, None, None, None, ("8.8.8.8", 0))]

    monkeypatch.setattr(net.socket, "getaddrinfo", fake_getaddrinfo)
    net._DNS_CACHE.clear()
    try:
        assert net.public_url("https://cache-bench.test/a")
        assert net.public_url("https://cache-bench.test/b")
        assert len(calls) == 1, calls
        assert len(net._DNS_CACHE) == 1
    finally:
        net._DNS_CACHE.clear()


def test_publish_semaphore_bounds_concurrent_uploads():
    from newsbot.publish import send_post

    class SlowBot:
        def __init__(self):
            self.active = 0
            self.peak = 0
            self.sent = 0

        async def send_message(self, chat_id, text, **kwargs):
            self.active += 1
            self.peak = max(self.peak, self.active)
            await asyncio.sleep(0.04)
            self.active -= 1
            self.sent += 1
            return SimpleNamespace(message_id=self.sent)

    bot = SlowBot()

    async def go():
        await asyncio.gather(*(send_post(bot, -1001, "<b>hello</b>") for _ in range(6)))

    asyncio.run(go())
    assert bot.sent == 6
    assert bot.peak <= config.PUBLISH_CONCURRENCY, f"peak uploads={bot.peak}"


def test_telegram_pool_is_explicitly_sized():
    from newsbot.app import build_telegram_request
    req = build_telegram_request()
    assert req._client_kwargs["limits"].max_connections == config.TELEGRAM_POOL_SIZE
    timeout = req._client_kwargs["timeout"]
    assert timeout.write == 600 and timeout.read == 60 and timeout.pool == 30
    assert config.TELEGRAM_POOL_SIZE >= config.PUBLISH_CONCURRENCY


def test_ph07_config_defaults_are_sane():
    assert config.DB_POOL_SIZE >= 1
    assert config.MAINT_POOL_SIZE >= 1
    assert config.PUBLISH_CONCURRENCY >= 1
    assert config.MAX_PUBLISHES_PER_TICK >= 0
    assert config.TELEGRAM_POOL_SIZE >= 4
