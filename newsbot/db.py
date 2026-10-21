# -*- coding: utf-8 -*-
"""لایه‌ی دیتابیس با دو بک‌اند: sqlite (پیش‌فرض، سریع و اتمیک) و Cloudflare D1 (Source of Truth).
اینترفیس یکتا برای کل کد: q() / _trx() / gset() / gget() / _safe_fields() / arun().
هیچ ماژول دیگری مستقیم sqlite3 یا httpx صدا نمی‌زند.

ARCH-01 (فاز ۸): مسیرهای async هیچ کوئری‌ای را روی حلقهٔ رویداد اجرا نمی‌کنند؛
تابع همزمان با arun() به استخر تردهای اختصاصی دیتابیس سپرده می‌شود.
"""
import asyncio, contextvars, functools, json, os, sqlite3, threading
from concurrent.futures import ThreadPoolExecutor
import httpx
from newsbot import config, SCHEMA_VERSION   # DB-25: تک‌منبعِ نسخه — newsbot/__init__.py
from newsbot.logutils import log
from newsbot.timeutils import now_iso, iso_fixed, parse_dt

_lock = threading.RLock()      # فقط نوشتن‌ها را بین تردها سریال می‌کند
_tls = threading.local()       # هر ترد کانکشن مخصوص خودش را دارد
_SCHEMA_READY = False          # اسکیما/مایگریشن فقط یک‌بار در هر پروسه اجرا می‌شود

_DB_POOL_SIZE = 4              # ARCH-01: تعداد تردهای اختصاصی دیتابیس (WAL چند-خواننده/تک-نویسنده را تحمل می‌کند)
_db_pool = None
_db_pool_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, username TEXT, name TEXT, role TEXT DEFAULT 'user', plan_id INTEGER, plan_expires TEXT, next_plan_id INTEGER, next_plan_days INTEGER, free_used INTEGER DEFAULT 0, banned INTEGER DEFAULT 0, lang TEXT, remind_key TEXT, premium INTEGER DEFAULT 0, created_at TEXT, last_seen TEXT);
CREATE TABLE IF NOT EXISTS plans(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, name_en TEXT, days INTEGER, daily_posts INTEGER, max_sources INTEGER, max_channels INTEGER, daily_tests INTEGER, price TEXT DEFAULT '', price_en TEXT DEFAULT '', description TEXT DEFAULT '', description_en TEXT DEFAULT '', is_free INTEGER DEFAULT 0, active INTEGER DEFAULT 1, sort INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS discounts(code TEXT PRIMARY KEY, percent INTEGER, expires TEXT, max_uses INTEGER DEFAULT 0, used INTEGER DEFAULT 0, active INTEGER DEFAULT 1, created_at TEXT);
CREATE TABLE IF NOT EXISTS channels(id INTEGER PRIMARY KEY AUTOINCREMENT, admin_id INTEGER, chat_id INTEGER, title TEXT, username TEXT, verified_by INTEGER, created_at TEXT, settings TEXT, original_admin_id INTEGER);
CREATE TABLE IF NOT EXISTS sources(id INTEGER PRIMARY KEY AUTOINCREMENT, admin_id INTEGER, channel_id INTEGER, url TEXT, feed_url TEXT, title TEXT DEFAULT '', active INTEGER DEFAULT 1, bot_active INTEGER DEFAULT 1, api_url TEXT, api_key TEXT, api_note TEXT DEFAULT '', etag TEXT, last_modified TEXT, last_fetch TEXT, fail_count INTEGER DEFAULT 0, found_total INTEGER DEFAULT 0, last_error TEXT);
CREATE TABLE IF NOT EXISTS usage(admin_id INTEGER, day TEXT, posts INTEGER DEFAULT 0, tests INTEGER DEFAULT 0, ai INTEGER DEFAULT 0, PRIMARY KEY(admin_id, day));
CREATE TABLE IF NOT EXISTS articles(id INTEGER PRIMARY KEY AUTOINCREMENT, admin_id INTEGER, channel_id INTEGER, hash TEXT, url TEXT, title TEXT, source_id INTEGER, published_at TEXT, created_at TEXT, status TEXT, reason TEXT, score REAL, category TEXT, post_html TEXT, full_html TEXT, media TEXT, links TEXT, model TEXT, UNIQUE(channel_id, hash));
CREATE TABLE IF NOT EXISTS posted(channel_id INTEGER, hash TEXT, posted_at TEXT, PRIMARY KEY(channel_id, hash));
CREATE TABLE IF NOT EXISTS ch_tokens(token TEXT PRIMARY KEY, channel_id INTEGER, issued_to INTEGER, issued_at TEXT, expires_at TEXT, used_at TEXT);
CREATE TABLE IF NOT EXISTS ai_models(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, kind TEXT, base_url TEXT, api_key TEXT, model TEXT, priority INTEGER DEFAULT 10, active INTEGER DEFAULT 1, status TEXT DEFAULT 'ok', fail_count INTEGER DEFAULT 0, last_error TEXT, last_ok TEXT, last_fail TEXT, ok_count INTEGER DEFAULT 0, temperature REAL DEFAULT 0.5, max_tokens INTEGER DEFAULT 2500, owner_id INTEGER);
CREATE TABLE IF NOT EXISTS deeplinks(key TEXT PRIMARY KEY, admin_id INTEGER, created_at TEXT, local_json TEXT);
CREATE TABLE IF NOT EXISTS kv_cache(key TEXT PRIMARY KEY, value TEXT, cached_at TEXT);
CREATE TABLE IF NOT EXISTS pub_journal(article_id INTEGER PRIMARY KEY, journal TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS ai_cache(key TEXT PRIMARY KEY, value TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS ai_usage(day TEXT, model_id INTEGER, calls INTEGER DEFAULT 0, ptok INTEGER DEFAULT 0, ctok INTEGER DEFAULT 0, PRIMARY KEY(day, model_id));
CREATE TABLE IF NOT EXISTS dedup(channel_id INTEGER, fp TEXT, ts TEXT, PRIMARY KEY(channel_id, fp));
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS support(user_id INTEGER PRIMARY KEY, open INTEGER DEFAULT 0, opened_at TEXT);
CREATE TABLE IF NOT EXISTS support_map(msg_id INTEGER PRIMARY KEY, user_id INTEGER, ts TEXT);
CREATE TABLE IF NOT EXISTS plan_changes(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, plan_id INTEGER, from_plan INTEGER, kind TEXT, credit_used REAL DEFAULT 0, surplus REAL DEFAULT 0, paid REAL, rem_days REAL, ref TEXT, ts TEXT);
CREATE TABLE IF NOT EXISTS pay_requests(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, plan_id INTEGER, status TEXT DEFAULT 'pending', created_at TEXT, receipt_chat INTEGER, receipt_msg INTEGER, note TEXT, decided_at TEXT, discount TEXT, final_price TEXT, cur TEXT DEFAULT '', decided_by INTEGER, notified_at TEXT);
CREATE TABLE IF NOT EXISTS referrals(id INTEGER PRIMARY KEY AUTOINCREMENT, referrer_id INTEGER, referred_id INTEGER UNIQUE, created_at TEXT, credited INTEGER DEFAULT 0, credit_amount REAL DEFAULT 0, note TEXT DEFAULT '', credit_cur TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS credit_log(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, delta REAL, balance_after REAL, reason TEXT, ref TEXT, ts TEXT, cur TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS wallet(user_id INTEGER, cur TEXT, amount REAL DEFAULT 0, updated_at TEXT, PRIMARY KEY(user_id, cur));
CREATE TABLE IF NOT EXISTS rate_limits(key TEXT PRIMARY KEY, ts REAL);
CREATE INDEX IF NOT EXISTS idx_art_ch_status ON articles(channel_id, status);
CREATE INDEX IF NOT EXISTS idx_art_created ON articles(created_at);
CREATE INDEX IF NOT EXISTS idx_src_ch ON sources(channel_id);
"""
DB_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_pay_status ON pay_requests(status, id);
CREATE INDEX IF NOT EXISTS idx_usage_day ON usage(day);
CREATE INDEX IF NOT EXISTS idx_art_disco ON articles(channel_id, status, created_at);
CREATE INDEX IF NOT EXISTS idx_dedup_ts ON dedup(ts);
CREATE INDEX IF NOT EXISTS idx_users_plan_exp ON users(plan_expires);
CREATE INDEX IF NOT EXISTS idx_chtok_ch ON ch_tokens(channel_id, used_at);
CREATE INDEX IF NOT EXISTS idx_posted_ch_at ON posted(channel_id, posted_at);
CREATE INDEX IF NOT EXISTS idx_deeplinks_created ON deeplinks(created_at);
CREATE INDEX IF NOT EXISTS idx_kvcache_cached ON kv_cache(cached_at);
CREATE INDEX IF NOT EXISTS idx_ai_cache_created ON ai_cache(created_at);
CREATE INDEX IF NOT EXISTS idx_ref_referrer ON referrals(referrer_id);
CREATE INDEX IF NOT EXISTS idx_credit_log_user ON credit_log(user_id, id);
CREATE INDEX IF NOT EXISTS idx_rate_ts ON rate_limits(ts);
"""

# DB-12: ایندکس‌های یکتا — تعریف یکتا، مشترک بین _ensure_schema و ابزارهای migrate/restore.
# (label, کوئری تشخیص تکراری, CREATE). داخل اسکریپت SCHEMA نمی‌آیند چون دیتابیس‌های قدیمیِ دارای
# ردیف تکراری نباید در استارت fail شوند؛ اعمال با محافظ + هشدار ماندگار انجام می‌شود.
UNIQUE_INDEXES = [
    ("channels(chat_id)", "SELECT 1 FROM channels GROUP BY chat_id HAVING COUNT(*)>1 LIMIT 1",
     "CREATE UNIQUE INDEX IF NOT EXISTS idx_channels_chat_id ON channels(chat_id)"),
    ("sources(channel_id,url)", "SELECT 1 FROM sources GROUP BY channel_id,url HAVING COUNT(*)>1 LIMIT 1",
     "CREATE UNIQUE INDEX IF NOT EXISTS idx_sources_channel_url ON sources(channel_id,url)"),
]

# trigger اتمیک (v8 — PH-01/Q-1c): شارژ کیف پولِ معرف در *یک* دستور SQL همراه با به‌روزرسانی ردیف
# ارجاع؛ با «پاداش هر خرید» سازگار است (delta افزایش credit_amount، نه فقط گذار credited ۰→۱).
# علاوه بر آن، هر شارژ یک «رسید» در credit_log ثبت می‌کند (اتمیک و در همان تراکنش).
# v8: مقصدِ شارژ جدولِ wallet است (به‌ازای users.credit) و واحدِ پول در referrals.credit_cur
# نگه‌داری می‌شود ⇒ پاداشِ یک واحد با واحدِ دیگر قابل خرج نیست (FIN-001).
REFERRAL_CREDIT_TRIGGER = """
CREATE TRIGGER IF NOT EXISTS trg_referral_credit_wallet
AFTER UPDATE OF credit_amount ON referrals
FOR EACH ROW
WHEN NEW.credit_amount > COALESCE(OLD.credit_amount, 0)
BEGIN
    INSERT INTO wallet(user_id,cur,amount,updated_at)
        VALUES(NEW.referrer_id, NEW.credit_cur, 0, strftime('%Y-%m-%dT%H:%M:%f000+00:00','now'))
        ON CONFLICT(user_id,cur) DO NOTHING;
    UPDATE wallet SET amount=ROUND(COALESCE(amount,0)+(NEW.credit_amount-COALESCE(OLD.credit_amount,0)),2),
                     updated_at=strftime('%Y-%m-%dT%H:%M:%f000+00:00','now')
      WHERE user_id=NEW.referrer_id AND cur=NEW.credit_cur;
    INSERT INTO credit_log(user_id,delta,balance_after,reason,ref,cur,ts)
    VALUES(NEW.referrer_id, NEW.credit_amount-COALESCE(OLD.credit_amount,0),
           (SELECT amount FROM wallet WHERE user_id=NEW.referrer_id AND cur=NEW.credit_cur), 'referral',
           CAST(NEW.referred_id AS TEXT), NEW.credit_cur, strftime('%Y-%m-%dT%H:%M:%f000+00:00','now'));
END
"""

MIGRATIONS = [("users", "next_plan_id", "INTEGER"), ("users", "next_plan_days", "INTEGER"), ("users", "lang", "TEXT"), ("users", "remind_key", "TEXT"), ("users", "premium", "INTEGER DEFAULT 0"),
              ("plans", "name_en", "TEXT"), ("plans", "price_en", "TEXT DEFAULT ''"), ("plans", "description_en", "TEXT DEFAULT ''"),
              ("channels", "verified_by", "INTEGER"), ("channels", "created_at", "TEXT"), ("channels", "settings", "TEXT"), ("channels", "original_admin_id", "INTEGER"),
              ("sources", "channel_id", "INTEGER"), ("sources", "feed_url", "TEXT"), ("sources", "last_error", "TEXT"), ("articles", "channel_id", "INTEGER"),
              ("articles", "send_fails", "INTEGER DEFAULT 0"), ("sources", "bot_active", "INTEGER DEFAULT 1"), ("sources", "api_url", "TEXT"), ("sources", "api_key", "TEXT"), ("sources", "api_note", "TEXT DEFAULT ''"),
              ("pay_requests", "discount", "TEXT"), ("pay_requests", "final_price", "TEXT"), ("pay_requests", "pc", "TEXT"),
              ("users", "credit", "REAL DEFAULT 0"),
              ("ai_models", "temperature", "REAL DEFAULT 0.5"), ("ai_models", "max_tokens", "INTEGER DEFAULT 2500"), ("ai_models", "owner_id", "INTEGER"),
              ("ai_models", "fail_count", "INTEGER DEFAULT 0"), ("ai_models", "next_try_after", "TEXT DEFAULT ''"),
              ("plans", "daily_tests", "INTEGER DEFAULT 2"), ("plans", "price_num", "REAL DEFAULT 0"), ("sources", "found_total", "INTEGER DEFAULT 0"), ("sources", "title", "TEXT DEFAULT ''"),
              ("sources", "fail_count", "INTEGER DEFAULT 0"), ("sources", "next_try_after", "TEXT DEFAULT ''"),
              ("pay_requests", "claimed_at", "TEXT"),   # NEW v4: بازیابی پرداخت‌های گیرکرده در 'processing'
              ("pay_requests", "applied_at", "TEXT"),   # phase 4: لحظهٔ اعمال پلن/کیف‌پول — idempotency بازیابی (PAY-3)
              ("usage", "ai", "INTEGER DEFAULT 0"),     # phase 5: سقف روزانهٔ AI (BIZ-01)
              ("plans", "price_num_fa", "REAL DEFAULT 0"),  # phase 14/v7: تعرفهٔ عددی تومان (fa) — price_num برای en ($)
              # ── v8 / PH-01 (Q-1c): کیف پولِ واحد‌دار ──────────────────────────────────────────
              ("credit_log", "cur", "TEXT DEFAULT ''"),        # واحدِ حرکتِ کیف پول
              ("referrals", "credit_cur", "TEXT DEFAULT ''"),  # واحدِ پاداشِ ارجاع
              ("pay_requests", "cur", "TEXT DEFAULT ''"),      # واحدِ مبلغِ پرداخت
              ("pay_requests", "decided_by", "INTEGER"),       # چه کسی پرداخت را تأیید کرد
              ("pay_requests", "notified_at", "TEXT"),        # اعلامِ نتیجه به مشتری انجام شد؟
              ("plan_changes", "ref", "TEXT")]                  # شناسهٔ پرداختِ مرتبط (pay:<id>)

def _table_cols(conn, tbl):
    """نام ستون‌های جدول — با pragma_table_info (سازگار با sqlite و D1) و fallback به PRAGMA."""
    try: return {r["name"] for r in conn.execute("SELECT name FROM pragma_table_info(?)", (tbl,))}
    except Exception:
        try: return {r["name"] for r in conn.execute(f"PRAGMA table_info({tbl})")}
        except Exception: return set()

# ── شیم بک‌اند Cloudflare D1 (REST) — همان اینترفیس sqlite3 تا بقیه‌ی کد دست‌نخورده بماند ──
_d1_http = None
def _d1_client():
    global _d1_http
    if _d1_http is None:
        _d1_http = httpx.Client(timeout=httpx.Timeout(config.D1_TIMEOUT, connect=10),
                                limits=httpx.Limits(max_connections=8, max_keepalive_connections=4))
    return _d1_http
def _d1_url():
    return f"https://api.cloudflare.com/client/v4/accounts/{config.CF_ACCOUNT_ID}/d1/database/{config.CF_D1_DATABASE_ID}/query"
def _d1_headers(): return {"Authorization": f"Bearer {config.CF_D1_TOKEN}"}

def _d1_batch(stmts):
    """اجرای چند دستور در یک درخواست D1 به‌صورت *اتمیک* (batch API).
    D1 تراکنش تعاملی ندارد، ولی batch را «یک تراکنش» می‌بیند: اگر هر دستور شکست بخورد
    کل batch رول‌بک می‌شود. بنابراین این تابع معادل واقعی BEGIN IMMEDIATE…COMMIT روی D1 است.
    stmts: فهرست [(sql, params), …] — ترتیب دقیقاً حفظ می‌شود.
    خروجی: فهرست D1Cur هم‌ترتیب با ورودی (lastrowid برای INSERTها پر می‌شود)."""
    payload = [{"sql": s.strip(), "params": list(p or ())} for s, p in stmts]
    r = _d1_client().post(_d1_url(), json={"batch": payload}, headers=_d1_headers())
    if r.status_code >= 400: raise sqlite3.OperationalError(f"D1 HTTP {r.status_code}: {r.text[:200]}")
    j = r.json()
    if not j.get("success"): raise sqlite3.OperationalError(f"D1 batch error: {str(j.get('errors'))[:200]}")
    results = j.get("result") or []
    curs, inserts = [], []
    for i, res in enumerate(results):
        rows = [D1Row(x) for x in (res.get("results") or [])]
        changes = int((res.get("meta") or {}).get("changes") or 0)
        lid = (res.get("meta") or {}).get("last_row_id")
        curs.append(D1Cur(rows, rowcount=changes, lastrowid=lid))
        if str(payload[i]["sql"]).upper().startswith("INSERT"): inserts.append((i, i))
    # اگر D1 شناسهٔ درج را برنگرداند، برای هر INSERT جداگانه last_insert_rowid() خوانده می‌شود.
    for i, _ in inserts:
        if curs[i].lastrowid is None:
            try:
                r2 = _d1_client().post(_d1_url(), json={"sql": "SELECT last_insert_rowid() AS lid", "params": []}, headers=_d1_headers())
                curs[i].lastrowid = (r2.json().get("result") or [{}])[0].get("results", [{}])[0].get("lid")
            except Exception:
                pass
    return curs

class D1Row:
    """سطر شبه‌sqlite3.Row: نگاشت کلیدی + پشتیبانی dict() و keys()."""
    __slots__ = ("_d",)
    def __init__(self, d): self._d = d
    def keys(self): return list(self._d.keys())
    def __getitem__(self, k): return self._d[k] if not isinstance(k, int) else list(self._d.values())[k]
    def __contains__(self, k): return k in self._d
    def get(self, k, default=None): return self._d.get(k, default)

class D1Cur:
    """کرسر سازگار با sqlite3: fetchone پیشروی می‌کند (fetchall باقی‌مانده را برمی‌گرداند)."""
    def __init__(self, rows, rowcount=0, lastrowid=None):
        self._rows = rows; self._idx = 0
        self.rowcount = rowcount; self.lastrowid = lastrowid
    def fetchone(self):
        if self._idx >= len(self._rows): return None
        row = self._rows[self._idx]
        self._idx += 1
        return row
    def fetchall(self):
        rows = self._rows[self._idx:]
        self._idx = len(self._rows)
        return list(rows)

class D1Conn:
    """کانکشن D1: هر execute یک درخواست REST است؛ commit/rollback no-op (auto-commit).
    نکته‌ی تراکنشی: روی D1 تراکنش تعاملی وجود ندارد؛ _trx دستورات را به‌ترتیب اجرا می‌کند
    و عملیات حساس (رزرو سهمیه، CAS پرداخت) تک‌دستوره و اتمیک طراحی شده‌اند."""
    def execute(self, sql, params=()):
        s = sql.strip()
        u = s.upper()
        if u.startswith(("BEGIN", "COMMIT", "ROLLBACK")): return D1Cur([], 0, None)
        if u.startswith("PRAGMA"): return D1Cur([], 0, None)
        r = _d1_client().post(_d1_url(), json={"sql": s, "params": list(params)}, headers=_d1_headers())
        if r.status_code >= 400: raise sqlite3.OperationalError(f"D1 HTTP {r.status_code}: {r.text[:200]}")
        j = r.json()
        if not j.get("success"): raise sqlite3.OperationalError(f"D1 error: {str(j.get('errors'))[:200]}")
        res = (j.get("result") or [{}])[0]
        rows = [D1Row(x) for x in (res.get("results") or [])]
        meta = res.get("meta") or {}
        changes = int(meta.get("changes") or 0)
        # شناسهٔ درج از *همین* پاسخ (meta.last_row_id) خوانده می‌شود؛ درخواست جداگانهٔ
        # last_insert_rowid() روی REST بی‌ربط است (هر درخواست session جدید دارد) و مقدار خراب می‌دهد.
        is_insert = u.startswith("INSERT")
        lastrowid = meta.get("last_row_id") if is_insert else None
        if is_insert:
            rows = []   # رفتار sqlite3 برای INSERT بدون RETURNING: کرسر خالی است
        return D1Cur(rows, rowcount=changes, lastrowid=lastrowid)
    def executescript(self, script):
        for stmt in [x.strip() for x in script.split(";") if x.strip()]:
            self.execute(stmt)
        return self
    def commit(self): pass
    def rollback(self): pass
    def close(self): pass

class D1BatchConn:
    """کانکشن موقتِ داخل _trx روی D1: دستورات را جمع می‌کند و در پایان به‌صورت یک batch
    اتمیک به D1 می‌فرستد. خواندن (SELECT) همان‌جا و بلافاصله انجام می‌شود تا منطق
    «بخوان-تصمیم بگیر» فقط برای دادهٔ *پیش از* تراکنش باشد (D1 هم همین محدودیت را دارد).
    تا زمانی که batch ارسال نشده، rollback یعنی «هیچ‌چیز ارسال نشد» — پس اتمیک است."""
    def __init__(self, base): self._base = base; self._stmts = []
    def execute(self, sql, params=()):
        s = sql.strip(); u = s.upper()
        if u.startswith(("BEGIN", "COMMIT", "ROLLBACK")): return D1Cur([], 0, None)
        if u.startswith("PRAGMA"): return D1Cur([], 0, None)
        if u.startswith(("SELECT", "WITH")):
            return self._base.execute(sql, params)   # خواندن فوری (بدون اثر جانبی)
        self._stmts.append((s, params))
        return D1Cur([], 0, None)
    def flush(self):
        if not self._stmts: return
        _d1_batch(self._stmts); self._stmts = []
    def commit(self): pass
    def rollback(self): self._stmts = []
    def close(self): pass

def _init_conn():
    """کانکشن بک‌اند فعال برای ترد جاری."""
    if config.DB_BACKEND == "d1":
        if not (config.CF_ACCOUNT_ID and config.CF_D1_DATABASE_ID and config.CF_D1_TOKEN):
            raise SystemExit("⛔ DB_BACKEND=d1 ولی CF_ACCOUNT_ID / CF_D1_DATABASE_ID / CF_D1_TOKEN تنظیم نشده است.")
        return D1Conn()
    conn = sqlite3.connect(config.DB_FILE, timeout=30)
    conn.row_factory = sqlite3.Row
    for pr in ("PRAGMA journal_mode=WAL", "PRAGMA synchronous=NORMAL", "PRAGMA busy_timeout=15000", "PRAGMA cache_size=-16000", "PRAGMA temp_store=MEMORY"):
        try: conn.execute(pr)
        except sqlite3.OperationalError: pass
    return conn

def _ensure_unique_indexes(conn):
    """ایندکس‌های یکتا فقط اگر جدول ردیف تکراری نداشته باشد؛ وگرنه هشدار ماندگار بدون حذف داده.
    DB-12: این ایندکس‌ها *بخشی از اسکیما* محسوب می‌شوند و همان تعریفِ UNIQUE_INDEXES در ابزارهای
    migrate/restore هم استفاده می‌شود (قبلاً فقط این‌جا و به‌صورت پراکنده ساخته می‌شدند)."""
    dups = []
    for label, check_sql, create_sql in UNIQUE_INDEXES:
        try:
            if conn.execute(check_sql).fetchone():
                log.warning("unique index %s skipped: existing duplicate rows", label); dups.append(label)
            else:
                conn.execute(create_sql)
        except sqlite3.Error as e:
            log.warning("unique index %s creation skipped: %s", label, e); dups.append(f"{label}: {e}")
    if dups:
        conn.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('db_dup_warn',?)", (json.dumps(dups, ensure_ascii=False),))

def _exec_schema(conn, script):
    """اجرای اسکریپت DDL به‌صورت اتمیک (DB-24).
    SQLite: دستورها داخل تراکنشِ فراخوان اجرا می‌شوند (BEGIN IMMEDIATE در _ensure_schema).
    D1: کل اسکریپت در *یک* درخواست batch می‌رود — D1 هر batch را یک تراکنش می‌بیند؛ شکست هر دستور
    کل batch را رول‌بک می‌کند (قبلاً دستورها یکی‌یکی ارسال می‌شدند و شکست وسط راه نیمه‌اسکیما می‌گذاشت)."""
    stmts = [s.strip() for s in script.split(";") if s.strip()]
    if config.DB_BACKEND == "d1":
        _d1_batch([(s, ()) for s in stmts])
    else:
        for s in stmts: conn.execute(s)

# DB-14: ستون‌های زمانی‌ای که به‌صورت رشته در SQL مقایسه می‌شوند (کانُنیکال‌سازی یک‌باره)
_ISO_COLS = [("users", "created_at"), ("users", "last_seen"), ("users", "plan_expires"),
             ("channels", "created_at"), ("articles", "created_at"), ("posted", "posted_at"),
             ("ch_tokens", "issued_at"), ("ch_tokens", "expires_at"), ("deeplinks", "created_at"),
             ("kv_cache", "cached_at"), ("ai_cache", "created_at"), ("dedup", "ts"),
             ("support_map", "ts"), ("pay_requests", "created_at"), ("pay_requests", "decided_at"),
             ("pay_requests", "claimed_at"), ("referrals", "created_at"), ("pub_journal", "updated_at")]

def normalize_iso_columns(conn, chunk=2000):
    """DB-14: یکسان‌سازی یک‌بارهٔ قالب زمانی ستون‌های مقایسه‌ای (UTC + کسر ثانیهٔ ثابت).
    ردیف‌های legacy با آفست غیر-UTC یا بدون کسر ثانیه، مقایسهٔ رشته‌ای را غلط می‌کردند؛
    مثل channel_posts_today که پس از DB-13 مستقیماً با posted_at مقایسهٔ رشته‌ای می‌کند.

    PHASE-04 (رخداد تولید 2026-10-21) — این تابع منشأ «حلقهٔ ری‌استارت با ۱۰۰٪ CPU» بود:
      * نسخهٔ قبل کلِ جدول را با ``fetchall()`` در حافظه می‌آورد (OOM روی دیتابیسِ بزرگ)،
        سطر‌به‌سطر به‌روزرسانی می‌کرد، همه را **داخلِ تراکنشِ اسکیما** انجام می‌داد و پرچمِ
        «انجام شد» را **فقط در انتها** می‌نوشت ⇒ اگر پروسه کشته شود، کلِ کار از صفر تکرار می‌شود،
        استارتاپ هرگز کامل نمی‌شود، ``TimeoutStartSec`` آن را می‌کشد و ``Restart=always``
        دوباره بلندش می‌کند.

    حالا: کار در **بسته‌های با اندازهٔ محدود** (مصرف حافظهٔ کران‌دار)، **checkpoint برای هر
    ستون** (``meta.iso_norm_v1:<table>.<col>``) تا ری‌استارت **ادامه دهد** نه اینکه از صفر
    شروع کند، و پرچمِ نهایی در پایان همهٔ ستون‌ها نوشته می‌شود."""
    try:
        if conn.execute("SELECT value FROM meta WHERE key='iso_norm_v1'").fetchone(): return 0
    except sqlite3.Error:
        return 0
    changed = 0
    for tbl, col in _ISO_COLS:
        marker = f"iso_norm_v1:{tbl}.{col}"
        try:
            if conn.execute("SELECT value FROM meta WHERE key=?", (marker,)).fetchone():
                continue                      # این ستون قبلاً کامل شده ⇒ ادامه بده، تکرار نکن
            last = 0
            while True:
                rows = conn.execute(
                    f"SELECT rowid AS _rid, {col} AS _v FROM {tbl} "
                    f"WHERE {col} IS NOT NULL AND rowid > ? ORDER BY rowid LIMIT ?", (last, chunk)).fetchall()
                if not rows: break
                upd = []
                for r in rows:
                    try:
                        v = r["_v"]; d = parse_dt(v)
                        if not d: continue
                        nv = iso_fixed(d)
                        if nv != v: upd.append((nv, r["_rid"])); changed += 1
                    except (TypeError, ValueError):
                        continue
                if upd:
                    conn.executemany(f"UPDATE {tbl} SET {col}=? WHERE rowid=?", upd)
                last = rows[-1]["_rid"]
            # checkpoint این ستون ⇒ اگر پروسه کشته شود، همین ستون دوباره کار نمی‌شود
            conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)", (marker, "1"))
            try: conn.commit()
            except sqlite3.Error: pass
        except sqlite3.Error:
            continue
    conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('iso_norm_v1',?)", (str(changed),))
    try: conn.commit()
    except sqlite3.Error: pass
    if changed: log.info("ISO canonicalization: %d value(s) rewritten", changed)
    return changed

def _migrate_legacy_credit(conn):
    """PH-01/Q-1c — یک‌بار برای همیشه: موجودیِ قدیمیِ ``users.credit`` (بدون واحد) به سطلِ
    «legacy» منتقل می‌شود.

    * **افزایشی**: فقط یک سطر به ``wallet`` اضافه می‌شود؛ ستونِ ``users.credit`` حذف نمی‌شود
      (خواندن/نوشتنش متوقف می‌شود ولی دادهٔ خام برای بازگشت/ممیزی باقی می‌ماند).
    * **idempotent**: با کلیدِ ``meta.legacy_wallet_v1`` یک‌بار اجرا می‌شود.
    * **بدون تبدیل**: موجودی به «legacy» می‌رود؛ در برابرِ هیچ تعرفه‌ای قابل خرج نیست تا
      تخصیصِ دستیِ آن انجام شود (اسکریپتِ جداگانه و صریحاً مجاز).
    """
    try:
        if conn.execute("SELECT value FROM meta WHERE key='legacy_wallet_v1'").fetchone():
            return 0
    except sqlite3.Error:
        return 0
    n = 0
    try:
        rows = conn.execute("SELECT id, COALESCE(credit,0) AS c FROM users WHERE COALESCE(credit,0) <> 0").fetchall()
    except sqlite3.Error:
        rows = []
    for r in rows:
        try:
            amt = round(float(r["c"] or 0), 2)
        except (TypeError, ValueError):
            amt = 0.0
        if not amt:
            continue
        # «ست» می‌شود نه «افزوده» ⇒ اجرای دوباره هم بی‌اثر است (idempotent به‌خودِ‌خود،
        # نه فقط به‌خاطرِ کلیدِ meta). چون users.credit دیگر نوشته نمی‌شود، مقدارِ آن
        # هیچ‌وقت از مقدارِ منتقل‌شده تازه‌تر نمی‌شود.
        conn.execute("INSERT INTO wallet(user_id,cur,amount,updated_at) VALUES(?,?,?,?) "
                     "ON CONFLICT(user_id,cur) DO UPDATE SET amount=excluded.amount, updated_at=excluded.updated_at",
                     (r["id"], "legacy", amt, now_iso()))
        n += 1
    conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('legacy_wallet_v1',?)", (str(n),))
    if n:
        log.info("PH-01: %d legacy wallet balance(s) moved to the frozen 'legacy' bucket", n)
    return n


def _ensure_schema(conn):
    """اسکیمای پایه + مایگریشن idempotent + ثبت نسخهٔ اسکیما — یک‌بار در هر پروسه.
    DB-24: روی SQLite کل مراحل داخل یک تراکنش اجرا می‌شود؛ هر خطا ⇒ rollback کامل (بدون نیمه‌اسکیما)."""
    global _SCHEMA_READY
    with _lock:
        if _SCHEMA_READY: return
        if config.DB_BACKEND != "d1":
            try: os.chmod(config.DB_FILE, 0o600)   # کلیدهای API متن ساده در این فایل‌اند؛ دسترسی فقط برای مالک
            except Exception: pass
        atomic = config.DB_BACKEND != "d1"
        if atomic: conn.execute("BEGIN IMMEDIATE")
        try:
            _exec_schema(conn, SCHEMA + "\n" + DB_INDEXES)
            conn.execute("CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT)")
            _sv = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
            _sv = str(_sv["value"]).strip() if _sv else ""
            if _sv.isdigit() and int(_sv) > SCHEMA_VERSION:   # اجرای بیلد قدیمی روی دیتابیس جدیدتر ممنوع
                log.error("DB schema_version=%s newer than code schema_version=%s — downgrade refused", _sv, SCHEMA_VERSION)
                raise SystemExit(f"database schema_version {_sv} is newer than this build ({SCHEMA_VERSION}); run the newer build or restore a backup")
            have = {}
            for tbl, col, decl in MIGRATIONS:
                if tbl not in have: have[tbl] = _table_cols(conn, tbl)
                if col not in have[tbl]:
                    conn.execute(f"ALTER TABLE {tbl} ADD COLUMN {col} {decl}")
                    have[tbl].add(col)
            # PHASE-04: کانُنیکال‌سازیِ زمان بیرون از تراکنشِ اسکیما اجرا می‌شود (پایینِ تابع)
            # v8: trigger عوض شده (شارژ کیف پولِ واحد‌دار + رسید)؛ نسخه‌های قدیمی جایگزین می‌شوند
            conn.execute("DROP TRIGGER IF EXISTS trg_referral_credit_wallet")
            conn.execute(REFERRAL_CREDIT_TRIGGER)
            # v8/PH-01: موجودیِ قدیمیِ بدونِ واحد ⇒ سطلِ منجمدِ «legacy» (یک‌بار، idempotent)
            _migrate_legacy_credit(conn)
            conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('schema_version',?)", (str(SCHEMA_VERSION),))
            # ژورنال انتشار از جدول عمومی settings به جدول اختصاصی منتقل می‌شود (مایگریشن افزایشی و غیرمخرب)
            conn.execute("INSERT OR REPLACE INTO pub_journal(article_id,journal,updated_at) SELECT CAST(substr(key,6) AS INTEGER), value, ? FROM settings WHERE key LIKE 'pubj:%'", (now_iso(),))
            conn.execute("DELETE FROM settings WHERE key LIKE 'pubj:%'")
            conn.execute("UPDATE channels SET original_admin_id=admin_id WHERE original_admin_id IS NULL")
            _ensure_unique_indexes(conn)
            try: conn.execute("DROP TABLE IF EXISTS logs")   # جدول لاگ حذف‌شده؛ در دیتابیس‌های قدیمی هم پاک می‌شود
            except Exception: pass
            if atomic: conn.commit()
        except BaseException:
            if atomic:
                try: conn.rollback()
                except Exception: pass
            raise
        # PHASE-04 (رخداد تولید 2026-10-21): کانُنیکال‌سازیِ زمان **بیرون از تراکنشِ اسکیما**
        # اجرا می‌شود و checkpoint هر ستون را جداگانه commit می‌کند ⇒ اگر پروسه وسطِ کار کشته
        # شود، ری‌استارتِ بعدی **ادامه می‌دهد** نه اینکه کلِ اسکن را از صفر تکرار کند
        # (که با TimeoutStartSec/Restart=always به حلقهٔ ۱۰۰٪ CPU تبدیل می‌شد).
        try:
            normalize_iso_columns(conn)
        except Exception as e:
            log.error("ISO canonicalisation failed (continues; resumes on next start): %s", e)
        _SCHEMA_READY = True

def db():
    """کانکشن مخصوص همین ترد؛ هر ترد فقط با کانکشن خودش کار می‌کند."""
    conn = getattr(_tls, "conn", None)
    if conn is None:
        conn = _init_conn()
        _tls.conn = conn
        _ensure_schema(conn)
    return conn

def _db_executor():
    """استخر تردهای اختصاصی دیتابیس — جدا از استخر پیش‌فرض asyncio (که کارهای CPU سنگین روی آن است)."""
    global _db_pool
    if _db_pool is None:
        with _db_pool_lock:
            if _db_pool is None:
                _db_pool = ThreadPoolExecutor(max_workers=_DB_POOL_SIZE, thread_name_prefix="newsbot-db")
    return _db_pool

async def arun(fn, *args, **kwargs):
    """ARCH-01: اجرای یک تابع همزمانِ دیتابیس روی استخر تردهای اختصاصی تا حلقهٔ رویداد بلاک نشود.

    ContextVarها (زمینهٔ لاگ/عملیات/AI) مثل asyncio.to_thread منتقل می‌شوند تا لاگ‌ها و
    ردیابیِ عملیات داخل تابع هم درست بماند. هر ترد استخر کانکشن SQLite مخصوص خود را دارد (db()).
    """
    ctx = contextvars.copy_context()
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_db_executor(), ctx.run, functools.partial(fn, *args, **kwargs))

def close_db_pool():
    """خاموشی: استخر تردهای دیتابیس بسته می‌شود (کارهای در صف لغو، در حال اجرا تمام می‌شوند).
    بعد از این اگر فراخوانی arun برسد، استخر دوباره و به‌صورت lazy ساخته می‌شود."""
    global _db_pool
    if _db_pool is not None:
        _db_pool.shutdown(wait=False, cancel_futures=True)
        _db_pool = None

class _NullLock:
    def __enter__(self): return self
    def __exit__(self, *a): return False
_NUL = _NullLock()

def q(sql, params=(), one=False, commit=False):
    """خواندن آزاد روی کانکشن همان ترد؛ نوشتن/commit زیر قفل سراسری نوشتن؛ retry فقط روی locked/busy."""
    for attempt in range(5):
        try:
            conn = db()
            with (_lock if commit else _NUL):
                cur = conn.execute(sql, params)
                if commit: conn.commit(); return cur.lastrowid
                return cur.fetchone() if one else cur.fetchall()
        except sqlite3.OperationalError as e:
            msg = str(e).lower()
            try:
                _c = getattr(_tls, "conn", None)
                if _c is not None: _c.rollback()
            except Exception: pass
            if attempt == 4 or ("locked" not in msg and "busy" not in msg): raise
            time_sleep = 0.02 * (2 ** attempt)   # ۲۰/۴۰/۸۰/۱۶ms — کوتاه، چون q() داخل حلقهٔ رویداد هم اجرا می‌شود
            import time as _t; _t.sleep(time_sleep)
        except sqlite3.Error:
            try:
                _c = getattr(_tls, "conn", None)
                if _c is not None: _c.rollback()
            except Exception: pass
            raise

_COLS = {}
def _safe_fields(table, f):
    """در SQL پویا فقط ستون‌های واقعیِ همان جدول پذیرفته می‌شوند (نام ستون از ورودی کاربر می‌آید)."""
    if table not in _COLS: _COLS[table] = _table_cols(db(), table)
    bad = [k for k in f if k not in _COLS[table]]
    if bad: raise ValueError(f"unknown column: {bad[0]}")
    return f

def gset(k, v): q("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)", (k, json.dumps(v, ensure_ascii=False)), commit=True)
def gget(k, default=None):
    r = q("SELECT value FROM settings WHERE key=?", (k,), one=True)
    return json.loads(r["value"]) if r else default

def _trx(fn):
    """تراکنش اتمیک. خروجی fn بازگردانده می‌شود.

    sqlite: BEGIN IMMEDIATE + commit/rollback (اتمیسین حالت ممکن).
    d1    : دستورات نوشتاریِ fn تا پایان در حافظه جمع می‌شوند و یک‌جا به‌صورت یک batch
            اتمیک فرستاده می‌شوند؛ اگر fn وسط کار خطا بدهد هیچ دستوری ارسال نمی‌شود.
            (`BEGIN IMMEDIATE` روی D1 پشتیبانی نمی‌شود و قبلاً no-op بود ⇒ نیمه‌کاره‌ماندن.)
    """
    with _lock:
        if config.DB_BACKEND == "d1":
            conn = D1BatchConn(db())
            try:
                result = fn(conn)
                conn.flush()
                return result
            except BaseException:
                conn.rollback(); raise
        conn = db()
        try:
            conn.execute("BEGIN IMMEDIATE")
            result = fn(conn)
            conn.commit()
            return result
        except BaseException:
            conn.rollback(); raise

def readonly_check():
    """ARCH-04/C7 (تصمیم ۱۳): بازرسی فقط‌خواندنیِ دیتابیس برای `run.py --check`.

    هیچ فایل/پوشه‌ای نمی‌سازد و هیچ نوشتنی روی دیتابیس انجام نمی‌دهد: اگر دیتابیس نباشد فقط
    یادداشت می‌دهد (اولین اجرای عادی ربات آن را می‌سازد)؛ اگر باشد با URI فقط‌خواندنی باز می‌شود
    و نسخهٔ اسکیما + وجود جداول پایه بررسی می‌شود. خروجی: فهرست ایرادها (خالی = سالم)."""
    problems = []
    if config.DB_BACKEND == "d1":
        log.info("خودآزمایی: بک‌اند D1 — بازرسی محلی فقط‌خواندنی لازم نیست (اتصال در زمان اجرا اعتبارسنجی می‌شود).")
        return problems
    path = config.DB_FILE
    if not os.path.exists(path):
        log.info("خودآزمایی: دیتابیس موجود نیست (%s) — اولین اجرای ربات آن را می‌سازد؛ بازرسی فقط‌خواندنی چیزی برای بررسی نداشت.", path)
        return problems
    try:
        from pathlib import Path
        base = Path(os.path.abspath(path)).as_uri()
        # OPS/ARCH-04 نکته (Windows+WAL): اتصال mode=ro روی دیتابیس WALِ ساکن، فایل‌های -shm/-wal می‌سازد؛
        # برای «هیچ فایلی ساخته نشود» دیتابیسِ بدون -shm/-wal با immutable=1 باز می‌شود (فقط‌خواندنی قطعی،
        # بدون قفل و بدون فایل کمکی). اگر ربات همین حالا در حال اجرا باشد (-shm/-wal موجود است) اتصال ro
        # عادی می‌شود تا محتوای WAL هم دیده شود.
        suffix = "?mode=ro" if (os.path.exists(path + "-wal") or os.path.exists(path + "-shm")) else "?mode=ro&immutable=1"
        conn = sqlite3.connect(base + suffix, uri=True, timeout=10)
    except (sqlite3.Error, ValueError) as e:
        return [f"دیتابیس قابل بازکردن فقط‌خواندنی نیست: {path} ({e})"]
    try:
        conn.execute("PRAGMA busy_timeout=5000")   # فقط تنظیم کانکشن — نوشتنی روی فایل نیست
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "users" not in tables:
            problems.append("دیتابیس اسکیمای ربات را ندارد (جدول users یافت نشد).")
        else:
            try:
                meta = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
            except sqlite3.OperationalError:
                meta = None   # دیتابیس قدیمی بدون جدول meta — اجرای عادی آن را مهاجرت می‌دهد
            ver = str(meta[0]).strip() if meta else ""
            if ver.isdigit() and int(ver) > SCHEMA_VERSION:
                problems.append(f"schema_version دیتابیس ({ver}) از بیلد فعلی ({SCHEMA_VERSION}) جدیدتر است؛ بیلد جدید لازم است.")
            log.info("خودآزمایی: دیتابیس فقط‌خواندنی بررسی شد (schema_version=%s، %d جدول).", ver or "نامعلوم", len(tables))
    except sqlite3.Error as e:
        problems.append(f"خواندن اسکیما با کانکشن فقط‌خواندنی شکست خورد: {e}")
    finally:
        conn.close()
    return problems
