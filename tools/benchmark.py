#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""بنچمارک پایهٔ بار (QA-08 — فاز ۱۳).

روی یک دیتابیس موقت SQLite اجرا می‌شود (هیچ اثری روی نصب واقعی ندارد) و زمان چهار مسیر
داغ را اندازه می‌گیرد: درج کاربر، خواندن کاربر، درج مقاله و مسیر تراکنشیِ اعتبار ارجاع
(شامل trigger + رسید credit_log). خروجی JSON + جدول خلاصه؛ برای مقایسهٔ «قبل/بعد» همین
دستور را تکرار کنید و اعداد را با `docs/PERFORMANCE.md` بسنجید.

استفاده:
    python tools/benchmark.py [--ops 2000] [--json PATH]
"""
import argparse, json, os, platform, sqlite3, sys, tempfile, time

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


def _block(n, fn):
    t0 = time.perf_counter()
    fn()
    dt = time.perf_counter() - t0
    return {"n": n, "seconds": round(dt, 4), "per_sec": round(n / dt, 1) if dt > 0 else None}


def run_benchmark(ops=2000):
    """اجرای بلوک‌ها و برگرداندن گزارشِ JSON-پذیر (بدون چاپ)."""
    from newsbot.app import init_core
    from newsbot.core_articles import article_insert
    from newsbot.core_referrals import credit_referral_on_payment, record_referral
    from newsbot.db import q
    from newsbot.timeutils import now_iso
    init_core()
    uid0 = 500000
    q("INSERT OR REPLACE INTO users(id,username,name,role,created_at,last_seen) VALUES(?,?,?,?,?,?)",
      (uid0, "bench", "Bench", "user", now_iso(), now_iso()), commit=True)
    cid = q("INSERT INTO channels(admin_id,chat_id,title,created_at) VALUES(?,?,?,?)",
            (uid0, 599999, "bench", now_iso()), commit=True)

    def users_insert():
        for i in range(ops):
            q("INSERT OR REPLACE INTO users(id,username,name,role,created_at,last_seen) VALUES(?,?,?,?,?,?)",
              (600000 + i, f"b{i}", f"B{i}", "user", now_iso(), now_iso()), commit=True)

    def users_select():
        for i in range(ops):
            q("SELECT id, credit FROM users WHERE id=?", (600000 + (i % ops),), one=True)

    def articles_insert():
        for i in range(ops):
            article_insert(uid0, cid, f"h{i}", f"https://bench.test/{i}", f"t{i}", 0, None, status="ready")

    nref = max(1, ops // 5)

    def referral_credit():
        for i in range(nref):
            r, b = 700000 + 2 * i, 700001 + 2 * i
            for u in (r, b):
                q("INSERT OR REPLACE INTO users(id,username,name,role,created_at,last_seen) VALUES(?,?,?,?,?,?)",
                  (u, f"r{u}", f"R{u}", "user", now_iso(), now_iso()), commit=True)
            record_referral(r, b)
            credit_referral_on_payment(b, 100.0, ref_key=f"bench:{i}")

    blocks = {}
    for name, n, fn in (("users_insert", ops, users_insert), ("users_select", ops, users_select),
                        ("articles_insert", ops, articles_insert), ("referral_credit_trx", nref, referral_credit)):
        blocks[name] = _block(n, fn)
    return {"ts": now_iso(), "python": platform.python_version(), "platform": platform.platform(),
            "sqlite": sqlite3.sqlite_version, "ops": ops, "blocks": blocks}


def main():
    ap = argparse.ArgumentParser(description="بنچمارک پایهٔ بار NewsBot (QA-08)")
    ap.add_argument("--ops", type=int, default=2000, help="تعداد عملیات هر بلوک (پیش‌فرض ۲۰۰۰)")
    ap.add_argument("--json", dest="json_path", help="ذخیرهٔ گزارش JSON در این مسیر")
    a = ap.parse_args()
    _prepare_env()
    rep = run_benchmark(max(10, a.ops))
    txt = json.dumps(rep, ensure_ascii=False, indent=2)
    if a.json_path:
        with open(a.json_path, "w", encoding="utf-8") as f:
            f.write(txt + "\n")
    print(txt)
    print("\n| بلوک | n | ثانیه | n/s |")
    print("|---|---|---|---|")
    for k, v in rep["blocks"].items():
        print(f"| {k} | {v['n']} | {v['seconds']} | {v['per_sec']} |")
    return 0


if __name__ == "__main__":
    sys.exit(main())
