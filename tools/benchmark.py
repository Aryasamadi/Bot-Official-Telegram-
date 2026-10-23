#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Offline benchmark harness for NewsBot (QA-08 + PH-07/PERF-004).

The harness uses a temporary SQLite database and never touches the network. It measures
single-threaded SQLite paths, a concurrent channel-cycle simulation, and a mocked-HTTP
discovery -> insert -> publish pipeline. Every block runs several repetitions and reports
min/median/max so a regression can be distinguished from noise.

Usage:
    python tools/benchmark.py [--ops 2000] [--reps 3] [--json PATH]
"""
import argparse, asyncio, html, json, logging, os, platform, sqlite3, statistics, sys, tempfile, time
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def _prepare_env():
    """دیتابیس موقت + env جعلی — پیش از import بستهٔ newsbot (config در import env می‌خواند)."""
    tmp = tempfile.mkdtemp(prefix="newsbot-bench-")
    os.environ["DB_BACKEND"] = "sqlite"
    os.environ["DB_FILE"] = os.path.join(tmp, "bench.db")
    os.environ.setdefault("BOT_TOKEN", "123456789:AAtest_only_dummy_token_0000000000000")
    os.environ.setdefault("SUPER_ADMIN_IDS", "1")
    os.environ.setdefault("REFERRAL_ENABLED", "1")
    return tmp


def _timed(n, fn):
    t0 = time.perf_counter()
    fn()
    dt = time.perf_counter() - t0
    return {"n": n, "seconds": dt, "per_sec": n / dt if dt > 0 else None}


def _aggregate(n, samples):
    per_sec = [s["per_sec"] for s in samples if s["per_sec"] is not None]
    return {
        "n": n,
        "seconds": round(statistics.median(s["seconds"] for s in samples), 4),
        "per_sec": round(statistics.median(per_sec), 1) if per_sec else None,
        "min_per_sec": round(min(per_sec), 1) if per_sec else None,
        "max_per_sec": round(max(per_sec), 1) if per_sec else None,
        "runs": [{"seconds": round(s["seconds"], 4), "per_sec": round(s["per_sec"], 1) if s["per_sec"] else None}
                 for s in samples],
    }


def _async_block(factory, repeats):
    samples = []
    for rep in range(repeats):
        t0 = time.perf_counter()
        result = asyncio.run(factory(rep))
        dt = time.perf_counter() - t0
        samples.append({"seconds": dt, "per_sec": result / dt if dt > 0 else None})
        if not result:
            raise RuntimeError("async benchmark block produced no operations")
    return samples


def _make_channels(uid, count):
    from newsbot.core_channels import add_channel
    ids = []
    for i in range(count):
        cid = add_channel(uid, -700000 - i, f"bench-{i}", f"bench_{i}", uid)
        if cid:
            ids.append(cid)
    return ids


def _prepare_cycle_articles(uid, channels):
    from newsbot.core_articles import article_insert
    for i, cid in enumerate(channels):
        article_insert(uid, cid, f"cycle-setup-{i}", f"https://bench.test/cycle/{i}",
                       f"cycle {i}", 0, None, status="ready")


async def _concurrent_cycles_factory(rep, channels):
    from newsbot.concurrency import ch_lock
    from newsbot.core_articles import articles_by_status
    from newsbot.core_channels import get_settings
    from newsbot.db import arun, gget

    async def one(cid):
        async with ch_lock(cid):
            await arun(get_settings, cid)
            await arun(articles_by_status, cid, "ready", 2, automatic=True)
            await arun(gget, "automation_enabled", True)

    await asyncio.gather(*(one(cid) for cid in channels))
    return len(channels)


async def _pipeline_factory(rep, ops, uid, channels):
    import httpx
    from newsbot.core_articles import article_insert
    from newsbot.db import arun
    from newsbot.publish import send_post

    n = max(10, min(200, ops // 5))
    payload = {"title": "Bench article", "body": "<p>benchmark payload</p>", "ok": True}

    def handler(request):
        return httpx.Response(200, json=payload)

    class BenchBot:
        async def send_message(self, chat_id, text, **kwargs):
            return SimpleNamespace(message_id=1)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler),
                               limits=httpx.Limits(max_connections=16, max_keepalive_connections=8))
    cid = channels[0]
    bot = BenchBot()
    try:
        async def one(i):
            r = await client.get(f"https://bench.test/feed/{rep}-{i}")
            data = r.json()
            aid = await arun(article_insert, uid, cid, f"pipe-{rep}-{i}",
                             f"https://bench.test/p/{rep}/{i}", data["title"], 0, None, status="ready")
            body = await asyncio.to_thread(html.escape, data["body"])
            await send_post(bot, -700000, f"<b>{data['title']}</b>\n{body}")
            return aid

        await asyncio.gather(*(one(i) for i in range(n)))
    finally:
        await client.aclose()
    return n


def run_benchmark(ops=2000, reps=3):
    """اجرای بلوک‌ها و برگرداندن گزارشِ JSON-پذیر (بدون چاپ)."""
    from newsbot.app import init_core
    from newsbot.core_articles import article_insert
    from newsbot.core_referrals import credit_referral_on_payment, record_referral
    from newsbot.db import q
    from newsbot.timeutils import now_iso
    logging.getLogger().setLevel(logging.WARNING)
    init_core()
    uid0 = 500000
    q("INSERT OR REPLACE INTO users(id,username,name,role,created_at,last_seen) VALUES(?,?,?,?,?,?)",
      (uid0, "bench", "Bench", "user", now_iso(), now_iso()), commit=True)
    cid = q("INSERT INTO channels(admin_id,chat_id,title,created_at) VALUES(?,?,?,?)",
            (uid0, 599999, "bench", now_iso()), commit=True)

    def users_insert(rep):
        for i in range(ops):
            q("INSERT OR REPLACE INTO users(id,username,name,role,created_at,last_seen) VALUES(?,?,?,?,?,?)",
              (600000 + rep * ops + i, f"b{i}", f"B{i}", "user", now_iso(), now_iso()), commit=True)

    def users_select(rep):
        for i in range(ops):
            q("SELECT id, credit FROM users WHERE id=?", (600000 + rep * ops + i,), one=True)

    def articles_insert(rep):
        for i in range(ops):
            article_insert(uid0, cid, f"h{rep}-{i}", f"https://bench.test/{rep}/{i}",
                           f"t{i}", 0, None, status="ready")

    nref = max(1, ops // 5)

    def referral_credit(rep):
        for i in range(nref):
            r, b = 700000 + rep * 2 * nref + 2 * i, 700001 + rep * 2 * nref + 2 * i
            for u in (r, b):
                q("INSERT OR REPLACE INTO users(id,username,name,role,created_at,last_seen) VALUES(?,?,?,?,?,?)",
                  (u, f"r{u}", f"R{u}", "user", now_iso(), now_iso()), commit=True)
            record_referral(r, b)
            credit_referral_on_payment(b, 100.0, ref_key=f"bench:{rep}:{i}")

    blocks = {}
    for name, fn in (("users_insert", users_insert), ("users_select", users_select),
                     ("articles_insert", articles_insert), ("referral_credit_trx", referral_credit)):
        samples = [_timed(ops if name != "referral_credit_trx" else nref, lambda f=fn, r=rep: f(r))
                   for rep in range(reps)]
        blocks[name] = _aggregate(ops if name != "referral_credit_trx" else nref, samples)

    channels = _make_channels(uid0, min(64, max(8, ops // 100)))
    _prepare_cycle_articles(uid0, channels)
    blocks["concurrent_cycle"] = _aggregate(
        len(channels), _async_block(lambda rep: _concurrent_cycles_factory(rep, channels), reps))

    pipeline_n = max(10, min(200, ops // 5))
    blocks["mocked_http_pipeline"] = _aggregate(
        pipeline_n, _async_block(lambda rep: _pipeline_factory(rep, ops, uid0, channels), reps))

    return {"ts": now_iso(), "python": platform.python_version(), "platform": platform.platform(),
            "sqlite": sqlite3.sqlite_version, "ops": ops, "reps": reps, "blocks": blocks}


def main():
    ap = argparse.ArgumentParser(description="بنچمارک پایهٔ بار NewsBot (QA-08 + PH-07)")
    ap.add_argument("--ops", type=int, default=2000, help="تعداد عملیات هر بلوک (پیش‌فرض ۲۰۰۰)")
    ap.add_argument("--reps", type=int, default=3, help="تعداد تکرار هر بلوک (پیش‌فرض ۳)")
    ap.add_argument("--json", dest="json_path", help="ذخیرهٔ گزارش JSON در این مسیر")
    a = ap.parse_args()
    _prepare_env()
    rep = run_benchmark(max(10, a.ops), max(1, a.reps))
    txt = json.dumps(rep, ensure_ascii=False, indent=2)
    if a.json_path:
        with open(a.json_path, "w", encoding="utf-8") as f:
            f.write(txt + "\n")
    print(txt)
    print("\n| بلوک | n | ثانیه (median) | n/s (median) | min n/s | max n/s | reps |")
    print("|---|---|---|---|---|---|---|")
    for k, v in rep["blocks"].items():
        print(f"| {k} | {v['n']} | {v['seconds']} | {v['per_sec']} | {v['min_per_sec']} | {v['max_per_sec']} | {len(v['runs'])} |")
    return 0


if __name__ == "__main__":
    sys.exit(main())
