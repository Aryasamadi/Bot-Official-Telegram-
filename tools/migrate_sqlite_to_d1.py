#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""مهاجرت یک‌باره‌ی داده‌ها: SQLite محلی → Cloudflare D1 (Source of Truth).
Idempotent: همه‌ی درج‌ها INSERT OR REPLACE هستند؛ اجرای دوباره داده‌ای را تکراری/خراب نمی‌کند.
در پایان شمارش هر جدول بین مبدأ و مقصد verify می‌شود؛ در صورت عدم تطابق exit code = 1.
استفاده:
    export CF_ACCOUNT_ID=... CF_D1_DATABASE_ID=... CF_D1_TOKEN=... DB_FILE=newsbot.db
    python tools/migrate_sqlite_to_d1.py
"""
import asyncio, hashlib, json, os, sqlite3, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import httpx
from newsbot import config
from newsbot.db import SCHEMA, DB_INDEXES, MIGRATIONS, REFERRAL_CREDIT_TRIGGER

TABLES = ["users", "plans", "discounts", "channels", "sources", "usage", "articles", "posted",
          "ch_tokens", "ai_models", "deeplinks", "kv_cache", "pub_journal", "ai_cache", "dedup",
          "settings", "support", "support_map", "plan_changes", "pay_requests", "referrals",
          "rate_limits", "meta"]
SEM = asyncio.Semaphore(8)


async def d1(client, sql, params=()):
    r = await client.post(
        f"https://api.cloudflare.com/client/v4/accounts/{config.CF_ACCOUNT_ID}/d1/database/{config.CF_D1_DATABASE_ID}/query",
        json={"sql": sql, "params": list(params)},
        headers={"Authorization": f"Bearer {config.CF_D1_TOKEN}"}, timeout=60)
    r.raise_for_status()
    j = r.json()
    if not j.get("success"):
        raise RuntimeError(str(j.get("errors"))[:300])
    return (j.get("result") or [{}])[0]


async def ensure_schema(client):
    for stmt in [s.strip() for s in (SCHEMA + "\n" + DB_INDEXES).split(";") if s.strip()]:
        await d1(client, stmt)
    # meta is created by db._ensure_schema(), not included in SCHEMA; migration copies it too.
    await d1(client, "CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT)")
    for tbl, col, decl in MIGRATIONS:
        res = await d1(client, "SELECT name FROM pragma_table_info(?)", (tbl,))
        cols = {row["name"] for row in (res.get("results") or [])}
        if col not in cols:
            await d1(client, f"ALTER TABLE {tbl} ADD COLUMN {col} {decl}")
    # Keep the atomic referral-wallet trigger in sync with the application schema.
    await d1(client, REFERRAL_CREDIT_TRIGGER)


async def copy_table(client, con, table):
    rows = con.execute(f"SELECT * FROM {table}").fetchall()
    if not rows:
        return 0
    cols = list(rows[0].keys())
    sql = f"INSERT OR REPLACE INTO {table}({','.join(cols)}) VALUES({','.join('?' * len(cols))})"

    async def one(row):
        async with SEM:
            await d1(client, sql, tuple(row))
    for i in range(0, len(rows), 200):
        await asyncio.gather(*[one(r) for r in rows[i:i + 200]])
    return len(rows)


def _row_fingerprint(row):
    """Canonical hash of every column; row counts alone cannot detect same-size data corruption."""
    value = dict(row)
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


async def verify(client, con):
    bad = []
    for t in TABLES:
        local_rows = con.execute(f"SELECT * FROM {t}").fetchall()
        local_hashes = sorted(_row_fingerprint(row) for row in local_rows)
        remote_hashes, offset = [], 0
        while True:
            res = await d1(client, f"SELECT * FROM {t} LIMIT 500 OFFSET {offset}")
            rows = res.get("results") or []
            if not rows:
                break
            remote_hashes.extend(_row_fingerprint(row) for row in rows)
            offset += len(rows)
        remote_hashes.sort()
        same = local_hashes == remote_hashes
        print(f"  {t}: local={len(local_hashes)} d1={len(remote_hashes)} {'DATA OK' if same else 'MISMATCH'}")
        if not same:
            bad.append(t)
    return bad


async def main():
    if not (config.CF_ACCOUNT_ID and config.CF_D1_DATABASE_ID and config.CF_D1_TOKEN):
        sys.exit("⛔ CF_ACCOUNT_ID / CF_D1_DATABASE_ID / CF_D1_TOKEN لازم است.")
    if not os.path.exists(config.DB_FILE):
        sys.exit(f"⛔ فایل مبدأ پیدا نشد: {config.DB_FILE}")
    con = sqlite3.connect(config.DB_FILE)
    con.row_factory = sqlite3.Row
    async with httpx.AsyncClient() as client:
        print("→ ساخت/به‌روزرسانی اسکیمای D1 …")
        await ensure_schema(client)
        total = 0
        for t in TABLES:
            n = await copy_table(client, con, t)
            total += n
            print(f"  ↳ {t}: {n} ردیف")
        print(f"→ مجموع {total} ردیف کپی شد. بررسی یکپارچگی …")
        bad = await verify(client, con)
    con.close()
    if bad:
        sys.exit(f"⛔ داده‌های SQLite و D1 یکسان نیستند: {', '.join(bad)}. هیچ داده‌ای از مقصد حذف نشده؛ DB_BACKEND را تغییر ندهید و اختلاف‌ها را جداگانه بررسی کنید.")
    print("✅ مهاجرت کامل و verify شد.")


if __name__ == "__main__":
    asyncio.run(main())
