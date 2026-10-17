#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""بازیابی معکوس: Cloudflare D1 → فایل SQLite محلی (برای تعویض VPS یا بازگشت به بک‌اند sqlite).
خروجی پیش‌فرض: restored.db — هیچ داده‌ای در D1 تغییر نمی‌کند (فقط خواندن).
پس از کپی، فایل بازگردانده‌شده با همان verify ابزار مهاجرت (fingerprint هر ردیف) صحه‌گذاری می‌شود.
استفاده:
    export CF_ACCOUNT_ID=... CF_D1_DATABASE_ID=... CF_D1_TOKEN=...
    python tools/restore_d1_to_sqlite.py [output.db]
"""
import asyncio, os, sqlite3, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import httpx
from newsbot import config
from newsbot.db import SCHEMA, DB_INDEXES, MIGRATIONS, REFERRAL_CREDIT_TRIGGER, UNIQUE_INDEXES
from tools.migrate_sqlite_to_d1 import TABLES, d1, verify


async def fetch_all(client, table):
    """DB-11: صفحه‌بندی با ORDER BY rowid — بدون ترتیبِ تضمین‌شده، ردیف‌ها بین صفحه‌ها
    تکرار یا جاافتاده می‌شوند و مقصد ناقص/مغشوش ساخته می‌شود."""
    out, offset = [], 0
    while True:
        res = await d1(client, f"SELECT * FROM {table} ORDER BY rowid LIMIT 1000 OFFSET {offset}")
        rows = res.get("results") or []
        if not rows:
            return out
        out.extend(rows)
        offset += len(rows)


def ensure_unique_indexes_local(con):
    """DB-12: همان تعریف مشترک UNIQUE_INDEXES — با محافظ ردیف تکراری (بدون حذف داده)."""
    dups = []
    for label, check_sql, create_sql in UNIQUE_INDEXES:
        if con.execute(check_sql).fetchone():
            print(f"  ⚠ unique index {label} skipped: existing duplicate rows")
            dups.append(label)
        else:
            con.execute(create_sql)
    return dups


async def main():
    if not (config.CF_ACCOUNT_ID and config.CF_D1_DATABASE_ID and config.CF_D1_TOKEN):
        sys.exit("⛔ CF_ACCOUNT_ID / CF_D1_DATABASE_ID / CF_D1_TOKEN لازم است.")
    out_path = sys.argv[1] if len(sys.argv) > 1 else "restored.db"
    if os.path.exists(out_path):
        sys.exit(f"⛔ فایل مقصد وجود دارد؛ اول حذفش کنید: {out_path}")
    con = sqlite3.connect(out_path)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA + "\n" + DB_INDEXES)
    con.execute("CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT)")
    have = {}
    for tbl, col, decl in MIGRATIONS:
        if tbl not in have:
            have[tbl] = {r["name"] for r in con.execute(f"PRAGMA table_info({tbl})")}
        if col not in have[tbl]:
            con.execute(f"ALTER TABLE {tbl} ADD COLUMN {col} {decl}")
            have[tbl].add(col)
    # بخیه‌های اسکیمای اپلیکیشن که داخل SCHEMA نیستند:
    con.execute(REFERRAL_CREDIT_TRIGGER)        # اعتبار ارجاع اتمیک + رسید credit_log (v6)
    bad = []
    async with httpx.AsyncClient() as client:
        for t in TABLES:
            rows = await fetch_all(client, t)
            if not rows:
                print(f"  ↳ {t}: 0"); continue
            cols = list(rows[0].keys())
            sql = f"INSERT OR REPLACE INTO {t}({','.join(cols)}) VALUES({','.join('?' * len(cols))})"
            with con:
                con.executemany(sql, [tuple(r.get(c) for c in cols) for r in rows])
            print(f"  ↳ {t}: {len(rows)} ردیف")
        dups = ensure_unique_indexes_local(con)
        if dups:
            print(f"  ⚠ ایندکس‌های یکتا رد شدند (ردیف تکراری): {', '.join(dups)}")
        con.commit()
        print("→ بررسی یکپارچگی مقصد بازگردانده‌شده …")
        bad = await verify(con, client)
    con.close()
    if bad:
        sys.exit(f"⛔ بازیابی ناقص است: {', '.join(bad)} — فایل {out_path} را استفاده نکنید.")
    print(f"✅ بازیابی کامل و verify شد: {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
