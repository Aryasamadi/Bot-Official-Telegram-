#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""مهاجرت یک‌باره‌ی داده‌ها: SQLite محلی → Cloudflare D1 (Source of Truth).
Idempotent: همه‌ی درج‌ها INSERT OR REPLACE هستند؛ اجرای دوباره داده‌ای را تکراری/خراب نمی‌کند.
در پایان محتوای هر جدول (fingerprint هر ردیف) بین مبدأ و مقصد verify می‌شود — نه فقط شمارش؛
در صورت عدم تطابق exit code = 1.

پروتکل هماهنگی با ربات زنده (DB-21) و محافظت مقصد (DB-03):
  ۱) از دیتابیس مبدأ یک snapshot سازگار با پروتکل رسمی sqlite3.backup گرفته می‌شود؛ پس خواندنِ
     وسط کار حتی وقتی ربات در حال اجراست، دادهٔ نیمه-نوشته نمی‌بیند (WAL-safe).
  ۲) اگر heartbeat ربات تازه باشد (< ۵ دقیقه)، بدون --force متوقف می‌شود — اول ربات را ببندید.
  ۳) اگر schema_version مبدأ با این ابزار یکی نباشد، بدون --force متوقف می‌شود.
  ۴) مقصد D1 اگر داده داشته باشد، فقط با --force بازنویسی می‌شود؛ نسخهٔ جدیدتر مقصد هرگز.
  ۵) اسکیمای D1 در یک batch اتمیک ساخته می‌شود (DB-24) و در پایان last_migrate_at ثبت می‌گردد.
استفاده:
    export CF_ACCOUNT_ID=... CF_D1_DATABASE_ID=... CF_D1_TOKEN=... DB_FILE=newsbot.db
    python tools/migrate_sqlite_to_d1.py [--force]
"""
import asyncio, hashlib, json, os, sqlite3, sys, time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import httpx
from newsbot import config
from newsbot.db import SCHEMA, DB_INDEXES, MIGRATIONS, REFERRAL_CREDIT_TRIGGER, UNIQUE_INDEXES, SCHEMA_VERSION
from newsbot.timeutils import now_iso, parse_dt

TABLES = ["users", "plans", "discounts", "channels", "sources", "usage", "articles", "posted",
          "ch_tokens", "ai_models", "deeplinks", "kv_cache", "pub_journal", "ai_cache", "dedup",
          "settings", "support", "support_map", "plan_changes", "pay_requests", "referrals",
          "rate_limits", "meta"]
SEM = asyncio.Semaphore(8)
FORCE = "--force" in sys.argv[1:]
LIVE_HEARTBEAT_SEC = 300


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


async def d1_batch(client, stmts):
    """DB-24: اجرای چند دستور در یک درخواست batch — D1 هر batch را یک تراکنش می‌بیند؛
    شکست هر دستور، کل batch را رول‌بک می‌کند (بدون نیمه‌اسکیما)."""
    payload = [{"sql": s.strip(), "params": list(p or ())} for s, p in stmts]
    r = await client.post(
        f"https://api.cloudflare.com/client/v4/accounts/{config.CF_ACCOUNT_ID}/d1/database/{config.CF_D1_DATABASE_ID}/query",
        json={"batch": payload},
        headers={"Authorization": f"Bearer {config.CF_D1_TOKEN}"}, timeout=90)
    r.raise_for_status()
    j = r.json()
    if not j.get("success"):
        raise RuntimeError(str(j.get("errors"))[:300])
    return j.get("result") or []


async def ensure_schema(client):
    stmts = [s.strip() for s in (SCHEMA + "\n" + DB_INDEXES).split(";") if s.strip()]
    # PR1-08: meta در SCHEMA نیست (db._ensure_schema می‌سازدش)؛ بدون این، copy جدول meta شکست می‌خورد
    stmts.append("CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT)")
    # همگام‌سازی trigger اتمیک اعتبار ارجاع با اسکیمای اپلیکیشن (داخل همان batch اتمیک)
    stmts.append(REFERRAL_CREDIT_TRIGGER)
    await d1_batch(client, [(s, ()) for s in stmts])
    for tbl, col, decl in MIGRATIONS:
        res = await d1(client, "SELECT name FROM pragma_table_info(?)", (tbl,))
        cols = {row["name"] for row in (res.get("results") or [])}
        if col not in cols:
            await d1(client, f"ALTER TABLE {tbl} ADD COLUMN {col} {decl}")


async def ensure_unique_indexes(client):
    """DB-12: همان تعریف مشترک db.UNIQUE_INDEXES — با محافظ ردیف تکراری (بدون حذف داده)."""
    dups = []
    for label, check_sql, create_sql in UNIQUE_INDEXES:
        res = await d1(client, check_sql)
        if res.get("results"):
            print(f"  ⚠ unique index {label} skipped: existing duplicate rows")
            dups.append(label)
        else:
            await d1(client, create_sql)
    return dups


def snapshot_source(path):
    """DB-21: snapshot سازگار از مبدأ (پروتکل رسمی sqlite3.backup؛ WAL-safe حتی با ربات زنده)."""
    tmp = f"{path}.migrate-snap-{os.getpid()}.db"
    src = sqlite3.connect(path, timeout=30)
    src.row_factory = sqlite3.Row
    dst = sqlite3.connect(tmp)
    try:
        src.backup(dst)
    finally:
        dst.close(); src.close()
    return tmp


def heartbeat_age_sec(con):
    """سن heartbeat ربات از settings در snapshot؛ None اگر موجود/معتبر نباشد."""
    try:
        r = con.execute("SELECT value FROM settings WHERE key='heartbeat'").fetchone()
        hb = json.loads(r["value"]) if r else None
        d = parse_dt(hb) if isinstance(hb, str) else None
        return (time.time() - d.timestamp()) if d else None
    except Exception:
        return None


def source_schema_version(con):
    try:
        r = con.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        return str(r["value"]).strip() if r else ""
    except Exception:
        return ""


async def check_target(client, force):
    """DB-03: مقصدِ دارای داده فقط با --force بازنویسی می‌شود؛ نسخهٔ جدیدتر مقصد هرگز."""
    res = await d1(client, "SELECT (SELECT COUNT(*) FROM users) u, (SELECT COUNT(*) FROM channels) c, "
                           "(SELECT COUNT(*) FROM posted) p, (SELECT COUNT(*) FROM pay_requests) r")
    row = (res.get("results") or [{}])[0]
    n = {k: int(row.get(k) or 0) for k in ("u", "c", "p", "r")}
    if sum(n.values()) and not force:
        raise SystemExit("⛔ مقصد D1 داده دارد (users=%(u)d, channels=%(c)d, posted=%(p)d, pay=%(r)d). "
                         "INSERT OR REPLACE مقصد را بازنویسی می‌کند؛ اگر مطمئنید --force بدهید." % n)
    tsv = ""
    try:
        res = await d1(client, "SELECT value FROM meta WHERE key='schema_version'")
        tsv = str(((res.get("results") or [{}])[0]).get("value") or "").strip()
    except Exception:
        pass
    if tsv.isdigit() and int(tsv) > SCHEMA_VERSION:
        raise SystemExit(f"⛔ نسخهٔ اسکیمای مقصد ({tsv}) از این ابزار ({SCHEMA_VERSION}) جدیدتر است — "
                         "بیلد جدید را اجرا کنید؛ بازنویسی انجام نشد.")


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
    """hash قانونی از همهٔ ستون‌های سطر؛ شمارشِ صرف نمی‌تواند فسادِ «همان‌اندازه» را بگیرد (PR1-09)."""
    value = dict(row)
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


async def verify(local_con, client, tables=TABLES):
    """مقایسهٔ fingerprintِ چند-مجموعه‌ای مبدأ/مقصد.
    DB-11: صفحه‌بندی مقصد با ORDER BY rowid — بدون آن، ترتیب ردیف‌ها بین صفحه‌ها تضمین‌نشده است
    و صفحه‌گردی می‌تواند ردیف را تکرار/جا بیندازد (MISMATCH کاذب).
    قابلیت استفادهٔ مشترک: restore_d1_to_sqlite هم همین را روی SQLiteِ بازگردانده‌شده صدا می‌زند."""
    bad = []
    for t in tables:
        local_rows = local_con.execute(f"SELECT * FROM {t}").fetchall()
        local_hashes = sorted(_row_fingerprint(row) for row in local_rows)
        remote_hashes, offset = [], 0
        while True:
            res = await d1(client, f"SELECT * FROM {t} ORDER BY rowid LIMIT 500 OFFSET {offset}")
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
    snap = snapshot_source(config.DB_FILE)
    con = sqlite3.connect(snap)
    con.row_factory = sqlite3.Row
    bad = []
    try:
        hb = heartbeat_age_sec(con)
        if hb is not None and hb < LIVE_HEARTBEAT_SEC and not FORCE:
            sys.exit(f"⛔ heartbeat ربات تازه است ({hb:.0f} ثانیه پیش) — احتمالاً ربات در حال اجراست. "
                     "اول ربات را متوقف کنید یا --force بدهید (پروتکل DB-21).")
        sv = source_schema_version(con)
        if sv.isdigit() and int(sv) != SCHEMA_VERSION and not FORCE:
            sys.exit(f"⛔ schema_version مبدأ ({sv}) با این ابزار ({SCHEMA_VERSION}) یکی نیست؛ "
                     "بیلد درست را استفاده کنید یا --force بدهید.")
        async with httpx.AsyncClient() as client:
            print("→ ساخت/به‌روزرسانی اسکیمای D1 (batch اتمیک) …")
            await ensure_schema(client)
            await check_target(client, FORCE)
            total = 0
            for t in TABLES:
                n = await copy_table(client, con, t)
                total += n
                print(f"  ↳ {t}: {n} ردیف")
            print(f"→ مجموع {total} ردیف کپی شد. ایندکس‌های یکتا …")
            dups = await ensure_unique_indexes(client)
            if dups:
                print(f"  ⚠ ایندکس‌های یکتا رد شدند (ردیف تکراری): {', '.join(dups)}")
            print("→ بررسی یکپارچگی …")
            bad = await verify(con, client)
            if not bad:
                await d1(client, "INSERT OR REPLACE INTO meta(key,value) VALUES('last_migrate_at',?)", (now_iso(),))
    finally:
        con.close()
        try:
            os.remove(snap)
        except OSError:
            pass
    if bad:
        sys.exit(f"⛔ داده‌های SQLite و D1 یکسان نیستند: {', '.join(bad)}. هیچ داده‌ای از مقصد حذف نشده؛ DB_BACKEND را تغییر ندهید و اختلاف‌ها را جداگانه بررسی کنید.")
    print("✅ مهاجرت کامل و verify شد.")


if __name__ == "__main__":
    asyncio.run(main())
