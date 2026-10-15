# -*- coding: utf-8 -*-
"""لایه‌ی دیتابیس با دو بک‌اند: sqlite (پیش‌فرض، سریع و اتمیک) و Cloudflare D1 (Source of Truth).
اینترفیس یکتا برای کل کد: q() / _trx() / gset() / gget() / _safe_fields().
هیچ модуول دیگری مستقیم sqlite3 یا httpx صدا نمی‌زند.
"""
import json, os, sqlite3, threading
import httpx
from newsbot import config
from newsbot.logutils import log
from newsbot.timeutils import now_iso

_lock = threading.RLock()      # فقط نوشتن‌ها را بین تردها سریال می‌کند
_tls = threading.local()       # هر ترد کانکشن مخصوص خودش را دارد
_SCHEMA_READY = False          # اسکیما/مایگریشن فقط یک‌بار در هر پروسه اجرا می‌شود
SCHEMA_VERSION = 3             # v3: جدول‌های referrals و rate_limits + ستون claimed_at + trigger اتمیک اعتبار ارجاع

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
CREATE TABLE IF NOT EXISTS dedup(channel_id INTEGER, fp TEXT, ts TEXT, PRIMARY KEY(channel_id, fp));
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS support(user_id INTEGER PRIMARY KEY, open INTEGER DEFAULT 0, opened_at TEXT);
CREATE TABLE IF NOT EXISTS support_map(msg_id INTEGER PRIMARY KEY, user_id INTEGER, ts TEXT);
CREATE TABLE IF NOT EXISTS plan_changes(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, plan_id INTEGER, from_plan INTEGER, kind TEXT, credit_used REAL DEFAULT 0, surplus REAL DEFAULT 0, paid REAL, rem_days REAL, ts TEXT);
CREATE TABLE IF NOT EXISTS pay_requests(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, plan_id INTEGER, status TEXT DEFAULT 'pending', created_at TEXT, receipt_chat INTEGER, receipt_msg INTEGER, note TEXT, decided_at TEXT, discount TEXT, final_price TEXT);
CREATE TABLE IF NOT EXISTS referrals(id INTEGER PRIMARY KEY AUTOINCREMENT, referrer_id INTEGER, referred_id INTEGER UNIQUE, created_at TEXT, credited INTEGER DEFAULT 0, credit_amount REAL DEFAULT 0, note TEXT DEFAULT '');
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
CREATE INDEX IF NOT EXISTS idx_rate_ts ON rate_limits(ts);
"""

# trigger اتمیک: claim شرطیِ اعتبار ارجاع و شارژ کیف پول در *یک* دستور SQL (sqlite و D1 هر دو اجرا می‌کنند)
REFERRAL_CREDIT_TRIGGER = """
CREATE TRIGGER IF NOT EXISTS trg_referral_credit_wallet
AFTER UPDATE OF credited ON referrals
FOR EACH ROW
WHEN OLD.credited = 0 AND NEW.credited = 1 AND NEW.credit_amount > 0
BEGIN
    UPDATE users SET credit=COALESCE(credit,0)+NEW.credit_amount WHERE id=NEW.referrer_id;
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
              ("usage", "ai", "INTEGER DEFAULT 0")]     # phase 5: سقف روزانهٔ AI (BIZ-01)

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
    """ایندکس‌های یکتا فقط اگر جدول ردیف تکراری نداشته باشد؛ وگرنه هشدار ماندگار بدون حذف داده."""
    dups = []
    try:
        if conn.execute("SELECT 1 FROM channels GROUP BY chat_id HAVING COUNT(*) >1 LIMIT 1").fetchone():
            log.warning("unique index channels(chat_id) skipped: existing duplicate rows"); dups.append("channels(chat_id)")
        else:
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_channels_chat_id ON channels(chat_id)")
        if conn.execute("SELECT 1 FROM sources GROUP BY channel_id,url HAVING COUNT(*) >1 LIMIT 1").fetchone():
            log.warning("unique index sources(channel_id,url) skipped: existing duplicate rows"); dups.append("sources(channel_id,url)")
        else:
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_sources_channel_url ON sources(channel_id,url)")
    except sqlite3.Error as e:
        log.warning("unique index creation skipped: %s", e); dups.append(f"sqlite error: {e}")
    if dups:
        conn.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('db_dup_warn',?)", (json.dumps(dups, ensure_ascii=False),))

def _ensure_schema(conn):
    """اسکیمای پایه + مایگریشن idempotent + ثبت نسخهٔ اسکیما — یک‌بار در هر پروسه."""
    global _SCHEMA_READY
    with _lock:
        if _SCHEMA_READY: return
        if config.DB_BACKEND != "d1":
            try: os.chmod(config.DB_FILE, 0o600)   # کلیدهای API متن ساده در این فایل‌اند؛ دسترسی فقط برای مالک
            except Exception: pass
        conn.executescript(SCHEMA + "\n" + DB_INDEXES)
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
        # claim اعتبار ارجاع + شارژ کیف پول باید یک statement اتمیک باشد؛ trigger همان UPDATE را اجرا می‌کند
        conn.execute(REFERRAL_CREDIT_TRIGGER)
        conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('schema_version',?)", (str(SCHEMA_VERSION),))
        # ژورنال انتشار از جدول عمومی settings به جدول اختصاصی منتقل می‌شود (مایگریشن افزایشی و غیرمخرب)
        conn.execute("INSERT OR REPLACE INTO pub_journal(article_id,journal,updated_at) SELECT CAST(substr(key,6) AS INTEGER), value, ? FROM settings WHERE key LIKE 'pubj:%'", (now_iso(),))
        conn.execute("DELETE FROM settings WHERE key LIKE 'pubj:%'")
        conn.execute("UPDATE channels SET original_admin_id=admin_id WHERE original_admin_id IS NULL")
        _ensure_unique_indexes(conn)
        try: conn.execute("DROP TABLE IF EXISTS logs")   # جدول لاگ حذف‌شده؛ در دیتابیس‌های قدیمی هم پاک می‌شود
        except Exception: pass
        conn.commit()
        _SCHEMA_READY = True

def db():
    """کانکشن مخصوص همین ترد؛ هر ترد فقط با کانکشن خودش کار می‌کند."""
    conn = getattr(_tls, "conn", None)
    if conn is None:
        conn = _init_conn()
        _tls.conn = conn
        _ensure_schema(conn)
    return conn

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
