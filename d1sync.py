# -*- coding: utf-8 -*-
"""
d1sync — Cloudflare D1 به‌عنوان «منبع حقیقتِ دائمی»؛ SQLite محلی فقط کپی کاری (working copy).

چرا این معماری؟  ربات منطق سهمیه/پرداخت/ژورنال انتشار را با تراکنش‌های تعاملی SQLite (BEGIN IMMEDIATE +
rowcount) پیاده کرده و q() همزمان داخل حلقهٔ رویداد صدا زده می‌شود. D1 از راه REST تراکنش تعاملی ندارد و هر
درخواستش ~۱۰۰–۳۰۰ms است؛ پس جایگزینی مستقیم q() ربات را کند و ناصحیح می‌کند. راه درست:

  • هر نوشتنِ محلی با TRIGGER در جدول _changes ثبت می‌شود (هیچ مسیر نوشتنی جا نمی‌ماند)
  • یک ترد پس‌زمینه «تصویر فعلیِ ردیف» را idempotent (INSERT OR REPLACE / DELETE) به D1 می‌فرستد
  • برای نقاط حساس (ژورنال انتشار، پرداخت) barrier() تا تأیید D1 صبر می‌کند
  • روی VPS جدید: اگر D1 دارای داده باشد، دیتابیس محلی از D1 بازسازی می‌شود (restore)
  • دیتابیس موجودِ فعلی در اولین اجرا به D1 seed می‌شود (با بکاپ قبلش)
  • lease در D1 جلوی اجرای هم‌زمان دو نمونه (split-brain) را می‌گیرد

فقط کتابخانهٔ استاندارد پایتون. توکن هرگز لاگ نمی‌شود.
"""
import os, sys, json, time, math, random, socket, signal, sqlite3, logging, threading, uuid, re, http.client
from urllib.parse import urlparse

log = logging.getLogger("d1sync")

# جدول‌های دائمی (D1 مرجع). kv_cache/ai_cache/dedup عمداً محلی‌اند: TTLدار و بازسازی‌پذیرند.
REPLICATED = ("users", "plans", "discounts", "channels", "sources", "usage", "articles", "posted", "ch_tokens",
              "ai_models", "deeplinks", "pub_journal", "settings", "support", "support_map", "plan_changes", "pay_requests")
PAGE_ROWS = {"articles": 25, "deeplinks": 50, "pub_journal": 100}  # جدول‌های سنگین: صفحه‌های کوچک‌تر
PAGE_DEFAULT = 200
MAX_SAFE_INT = 2 ** 53  # D1 REST اعداد را JSON می‌فرستد؛ بالاتر از این دقت از دست می‌رود


class D1Error(Exception):
    """kind: transient | auth | config | sql | other"""
    def __init__(self, msg, kind="other", status=None, retry_after=None):
        super().__init__(msg); self.kind, self.status, self.retry_after = kind, status, retry_after


class D1Unavailable(Exception):
    pass


def _q(name): return '"' + str(name).replace('"', '""') + '"'


# ----------------------------------------------------------------------------- پیکربندی
class Config:
    def __init__(self, env=None):
        e = env if env is not None else os.environ
        g = lambda k, d="": (e.get(k) or d).strip()
        self.account = g("CF_ACCOUNT_ID"); self.database = g("CF_D1_DATABASE_ID")
        self.token = g("CF_D1_API_TOKEN") or g("CF_API_TOKEN")
        self.base = g("D1_API_BASE", "https://api.cloudflare.com").rstrip("/")
        self.required = g("D1_REQUIRED", "1").lower() not in ("0", "false", "no", "off")
        self.interval = float(g("D1_SYNC_INTERVAL", "3")); self.lease_ttl = int(g("D1_LEASE_TTL", "90"))
        self.lease_wait = int(g("D1_LEASE_WAIT", "150")); self.boot_wait = int(g("D1_BOOT_WAIT", "90"))
        self.batch = g("D1_BATCH", "auto").lower()  # auto | 1 | 0
        self.force_restore = g("D1_FORCE_RESTORE") == "1"; self.force_seed = g("D1_FORCE_SEED") == "1"
        self.instance = g("D1_INSTANCE_ID")

    @property
    def enabled(self): return bool(self.account and self.database and self.token)


# ----------------------------------------------------------------------------- کلاینت HTTP/D1
class _Transport:
    """اتصال keep-alive با http.client؛ هر خطای شبکه ⇒ بستن و یک بار اتصال تازه."""
    MAX_BODY = 32 * 1024 * 1024

    def __init__(self, base, timeout):
        u = urlparse(base); self.https = u.scheme == "https"; self.host = u.hostname
        self.port = u.port or (443 if self.https else 80); self.timeout = timeout; self.c = None

    def close(self):
        try:
            if self.c: self.c.close()
        except Exception: pass
        self.c = None

    def request(self, path, headers, body):
        for attempt in (0, 1):
            try:
                if self.c is None:
                    cls = http.client.HTTPSConnection if self.https else http.client.HTTPConnection
                    self.c = cls(self.host, self.port, timeout=self.timeout)
                self.c.request("POST", path, body, headers)
                r = self.c.getresponse(); data = r.read(self.MAX_BODY + 1)
                if len(data) > self.MAX_BODY: self.close(); raise D1Error("response too large", "other")
                if (r.getheader("Connection") or "").lower() == "close": self.close()
                return r.status, r, data
            except (OSError, http.client.HTTPException):
                self.close()
                if attempt: raise


class D1Client:
    def __init__(self, cfg, timeout=30, max_attempts=5, max_total=120):
        self.cfg, self.max_attempts, self.max_total = cfg, max_attempts, max_total
        self.path = f"/client/v4/accounts/{cfg.account}/d1/database/{cfg.database}/query"
        self._tr = _Transport(cfg.base, timeout); self._lock = threading.Lock()
        self.batch_mode = {"1": "on", "0": "off"}.get(cfg.batch, "unknown")
        self.requests = 0

    def redact(self, s):
        s = str(s)
        for secret in (self.cfg.token,):
            if secret: s = s.replace(secret, "***")
        return s

    def _call(self, body):
        payload = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        headers = {"Authorization": f"Bearer {self.cfg.token}", "Content-Type": "application/json", "Accept": "application/json"}
        t0 = time.monotonic()
        for attempt in range(1, self.max_attempts + 1):
            err = None
            try:
                with self._lock:
                    self.requests += 1
                    status, resp, data = self._tr.request(self.path, headers, payload)
            except (OSError, http.client.HTTPException) as e:
                err = D1Error(f"network error: {type(e).__name__}", "transient")
            else:
                try: obj = json.loads(data.decode("utf-8", "replace")) if data else {}
                except ValueError: obj = None
                if 200 <= status < 300 and isinstance(obj, dict) and obj.get("success", True) is not False:
                    return obj
                msg = ""
                if isinstance(obj, dict) and obj.get("errors"):
                    msg = "; ".join(str((x or {}).get("message", x)) for x in obj["errors"][:3])
                msg = self.redact(msg or f"HTTP {status}")[:300]
                ra = resp.getheader("Retry-After")
                try: ra = min(float(ra), 60.0) if ra else None
                except ValueError: ra = None
                if status in (401, 403): err = D1Error(f"D1 auth failed ({status}): {msg}", "auth", status)
                elif status == 404: err = D1Error(f"D1 not found ({status}): {msg}", "config", status)
                elif status == 429 or status >= 500: err = D1Error(f"D1 transient ({status}): {msg}", "transient", status, ra)
                else: err = D1Error(f"D1 rejected ({status}): {msg}", "sql", status)
            if err.kind != "transient" or attempt == self.max_attempts or time.monotonic() - t0 > self.max_total:
                raise err
            delay = err.retry_after if err.retry_after is not None else min(30.0, 0.5 * 2 ** (attempt - 1))
            delay *= 0.75 + random.random() * 0.5  # jitter
            log.warning("D1 request retry %d/%d in %.1fs: %s", attempt, self.max_attempts, delay, err)
            time.sleep(delay)

    @staticmethod
    def _results(obj):
        res = obj.get("result")
        if isinstance(res, dict): res = [res]
        return res if isinstance(res, list) else []

    def query(self, sql, params=()):
        res = self._results(self._call({"sql": sql, "params": list(params)}))
        return (res[0].get("results") or []) if res else []

    def batch(self, stmts):
        """stmts: [(sql, params)] ⇒ [rows]. بچ REST اگر پشتیبانی نشود، به ارسال تکی برمی‌گردد (همه‌ی دستورها idempotent‌اند)."""
        if not stmts: return []
        if self.batch_mode != "off":
            try:
                obj = self._call({"batch": [{"sql": s, "params": list(p)} for s, p in stmts]})
                res = self._results(obj)
                if len(res) != len(stmts): raise D1Error("batch result count mismatch", "other")
                self.batch_mode = "on"
                return [(r.get("results") or []) for r in res]
            except D1Error as e:
                if e.kind == "sql" and self.batch_mode == "unknown":
                    self.batch_mode = "off"
                    log.warning("D1 REST batch not accepted (%s) — falling back to single statements", e)
                else: raise
        return [self.query(s, p) for s, p in stmts]

    def close(self): self._tr.close()


# ----------------------------------------------------------------------------- تکثیر محلی → D1
REMOTE_INTERNAL_DDL = (
    "CREATE TABLE IF NOT EXISTS _d1_lease(id INTEGER PRIMARY KEY CHECK(id=1), owner TEXT NOT NULL, epoch INTEGER NOT NULL, lease_until INTEGER NOT NULL, host TEXT, updated_at TEXT)",
    "CREATE TABLE IF NOT EXISTS _d1_state(k TEXT PRIMARY KEY, v TEXT)",
    "CREATE TABLE IF NOT EXISTS _d1_seq(name TEXT PRIMARY KEY, seq INTEGER NOT NULL)",
)
_SRV_NOW = "CAST(strftime('%s','now') AS INTEGER)"


def _inject_if_not_exists(sql):
    return re.sub(r"^\s*CREATE\s+(UNIQUE\s+)?(TABLE|INDEX)\s+(?!IF\s+NOT\s+EXISTS)", lambda m: f"CREATE {m.group(1) or ''}{m.group(2)} IF NOT EXISTS ", sql, count=1, flags=re.I)


def _val(v, table, col):
    if v is None or isinstance(v, str): return v
    if isinstance(v, float):
        if not math.isfinite(v): raise ValueError(f"non-finite number in {table}.{col}")
        return v
    if isinstance(v, int):
        if abs(v) >= MAX_SAFE_INT: raise ValueError(f"integer too large for D1 REST in {table}.{col}")
        return v
    raise ValueError(f"unsupported value type {type(v).__name__} in {table}.{col}")


class Replicator:
    def __init__(self, db_path, client, interval=3.0, tables=REPLICATED, batch_size=50, max_fetch=500):
        self.db_path, self.client, self.interval, self.tables = db_path, client, interval, tuple(tables)
        self.batch_size, self.max_fetch = batch_size, max_fetch
        self.shipped_seq = 0; self.fatal = None; self.last_ok = 0.0
        self._stop = threading.Event(); self._wake = threading.Event(); self._cv = threading.Condition()
        self._thread = None; self._conn_ = None; self._seq_sent = {}; self._colcache = {}; self._pkcache = {}
        self._last_info = 0.0; self._shipped_since = 0

    # ---- اتصال/اسکیما
    def connect(self):
        c = sqlite3.connect(self.db_path, timeout=30, check_same_thread=False); c.row_factory = sqlite3.Row  # هر کانکشن هم‌زمان فقط یک ترد (flush نهایی بعد از join)
        c.execute("PRAGMA busy_timeout=15000"); return c

    def _conn(self):
        if self._conn_ is None: self._conn_ = self.connect()
        return self._conn_

    def pk_cols(self, conn, t):
        if t not in self._pkcache:
            self._pkcache[t] = [r[0] for r in conn.execute("SELECT name FROM pragma_table_info(?) WHERE pk>0 ORDER BY pk", (t,))]
            if not self._pkcache[t]: raise RuntimeError(f"table {t} has no primary key")
        return self._pkcache[t]

    def columns(self, conn, t):
        if t not in self._colcache:
            self._colcache[t] = [r[0] for r in conn.execute("SELECT name FROM pragma_table_info(?)", (t,))]
        return self._colcache[t]

    def install(self):
        """ایجاد _changes/_repl_ctl و تریگرها (هر بار drop+create تا با اسکیمای فعلی همگام بماند)."""
        c = self.connect()
        try:
            c.execute("SELECT json_array(1)")  # JSON1 لازم است
            self.ensure_ctl(c)
            w = "WHEN (SELECT v FROM _repl_ctl WHERE k='paused')=0"
            for t in self.tables:
                pk = self.pk_cols(c, t)
                new = ",".join(f"NEW.{_q(x)}" for x in pk); old = ",".join(f"OLD.{_q(x)}" for x in pk)
                for suf in ("ai", "au", "ad"): c.execute(f"DROP TRIGGER IF EXISTS {_q('_rpl_' + t + '_' + suf)}")
                c.execute(f"CREATE TRIGGER {_q('_rpl_' + t + '_ai')} AFTER INSERT ON {_q(t)} {w} BEGIN INSERT INTO _changes(tbl,pk) VALUES('{t}', json_array({new})); END")
                c.execute(f"CREATE TRIGGER {_q('_rpl_' + t + '_au')} AFTER UPDATE ON {_q(t)} {w} BEGIN "
                          f"INSERT INTO _changes(tbl,pk) VALUES('{t}', json_array({new})); "
                          f"INSERT INTO _changes(tbl,pk) SELECT '{t}', json_array({old}) WHERE json_array({old}) IS NOT json_array({new}); END")
                c.execute(f"CREATE TRIGGER {_q('_rpl_' + t + '_ad')} AFTER DELETE ON {_q(t)} {w} BEGIN INSERT INTO _changes(tbl,pk) VALUES('{t}', json_array({old})); END")
            c.commit()
        finally: c.close()

    @staticmethod
    def ensure_ctl(conn):
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS _changes(seq INTEGER PRIMARY KEY AUTOINCREMENT, tbl TEXT NOT NULL, pk TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS _changes_dead(seq INTEGER PRIMARY KEY AUTOINCREMENT, tbl TEXT, pk TEXT, err TEXT, ts TEXT);
            CREATE TABLE IF NOT EXISTS _repl_ctl(k TEXT PRIMARY KEY, v INTEGER);
            INSERT OR IGNORE INTO _repl_ctl(k,v) VALUES('paused',0);""")

    def set_paused(self, conn, paused):
        self.ensure_ctl(conn)
        conn.execute("INSERT OR REPLACE INTO _repl_ctl(k,v) VALUES('paused',?)", (1 if paused else 0,))

    # ---- ساخت دستور برای یک ردیف
    def upsert_stmt(self, conn, t, row):
        cols = self.columns(conn, t)
        return (f"INSERT OR REPLACE INTO {_q(t)}({','.join(_q(c) for c in cols)}) VALUES({','.join('?' * len(cols))})",
                [_val(row[c], t, c) for c in cols])

    def stmt_for(self, conn, t, pk_vals):
        pk = self.pk_cols(conn, t); where = " AND ".join(f"{_q(c)}=?" for c in pk)
        row = conn.execute(f"SELECT * FROM {_q(t)} WHERE {where}", pk_vals).fetchone()
        if row is None: return (f"DELETE FROM {_q(t)} WHERE {where}", [_val(v, t, c) for v, c in zip(pk_vals, pk)])
        return self.upsert_stmt(conn, t, row)

    # ---- یک دور ارسال
    def ship_once(self):
        conn = self._conn()
        rows = conn.execute("SELECT seq,tbl,pk FROM _changes ORDER BY seq LIMIT ?", (self.max_fetch,)).fetchall()
        if not rows:
            self._ship_sequences(conn); return 0
        last = rows[-1]["seq"]; keys = {}
        for r in rows: keys[(r["tbl"], r["pk"])] = r["seq"]  # ادغام: چند تغییرِ یک ردیف ⇒ یک دستور
        stmts, meta = [], []
        for (t, pk) in keys:
            if t not in self.tables: continue
            try: stmts.append(self.stmt_for(conn, t, json.loads(pk))); meta.append((t, pk))
            except ValueError as e: self._dead(conn, t, pk, str(e))
        for i in range(0, len(stmts), self.batch_size): self._send(conn, stmts[i:i + self.batch_size], meta[i:i + self.batch_size])
        self._ship_sequences(conn)
        conn.execute("DELETE FROM _changes WHERE seq<=?", (last,)); conn.commit()
        with self._cv: self.shipped_seq = max(self.shipped_seq, last); self._cv.notify_all()
        self._shipped_since += len(stmts)
        return len(rows)

    def _send(self, conn, stmts, meta):
        try: self.client.batch(stmts)
        except D1Error as e:
            if e.kind != "sql": raise
            if len(stmts) == 1: self._dead(conn, meta[0][0], meta[0][1], str(e)); return
            mid = len(stmts) // 2  # دوبخشی: فقط ردیف مسموم قرنطینه می‌شود، بقیه می‌روند
            self._send(conn, stmts[:mid], meta[:mid]); self._send(conn, stmts[mid:], meta[mid:])

    def _dead(self, conn, t, pk, err):
        conn.execute("INSERT INTO _changes_dead(tbl,pk,err,ts) VALUES(?,?,?,?)", (t, pk, str(err)[:300], time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())))
        conn.commit(); log.error("D1 replication quarantined a row: table=%s pk=%s reason=%s", t, pk, str(err)[:200])

    def _ship_sequences(self, conn):
        """sqlite_sequence را هم می‌فرستیم تا بعد از restore شناسه‌های حذف‌شده دوباره استفاده نشوند (IDOR/اشتباه callback)."""
        try: cur = {r[0]: r[1] for r in conn.execute("SELECT name,seq FROM sqlite_sequence")}
        except sqlite3.Error: return
        diff = [(n, s) for n, s in cur.items() if n in self.tables and self._seq_sent.get(n) != s]
        if not diff: return
        self.client.batch([("INSERT OR REPLACE INTO _d1_seq(name,seq) VALUES(?,?)", [n, s]) for n, s in diff])
        self._seq_sent.update(dict(diff))

    # ---- ترد پس‌زمینه
    def start(self):
        self._stop.clear(); self._thread = threading.Thread(target=self._run, name="d1-replicator", daemon=True); self._thread.start()

    def _run(self):
        backoff = 1.0; last_fatal_log = 0.0
        while not self._stop.is_set():
            wait = self.interval
            try:
                n = self.ship_once(); self.last_ok = time.time(); self.fatal = None; backoff = 1.0
                if n >= self.max_fetch: wait = 0
                self._info_tick()
            except D1Error as e:
                if e.kind in ("auth", "config"):
                    self.fatal = str(e); wait = 60.0
                    if time.time() - last_fatal_log > 300: log.error("D1 replication blocked (%s) — data is safe locally and in _changes", e); last_fatal_log = time.time()
                else:
                    wait = min(60.0, backoff) * (0.75 + random.random() * 0.5); backoff *= 2
                    log.warning("D1 replication will retry in %.0fs: %s", wait, e)
            except Exception:
                log.exception("D1 replication loop error"); wait = 5.0
            self._wake.wait(wait); self._wake.clear()

    def _info_tick(self):
        if self._shipped_since and time.time() - self._last_info > 60:
            log.info("D1 replication: %d row-images shipped (last 60s) · pending=%d", self._shipped_since, self.pending()); self._shipped_since = 0; self._last_info = time.time()

    def stop(self, flush_seconds=10.0):
        deadline = time.monotonic() + flush_seconds
        self._stop.set(); self._wake.set()
        if self._thread: self._thread.join(timeout=5)
        try:
            while time.monotonic() < deadline and self.pending() and not self.fatal:
                if self.ship_once() == 0: break
        except Exception as e: log.warning("D1 final flush incomplete: %s", self.client.redact(e))

    # ---- barrier / وضعیت
    def pending(self):
        c = self.connect()
        try: return c.execute("SELECT COUNT(*) FROM _changes").fetchone()[0]
        finally: c.close()

    def dead(self):
        c = self.connect()
        try: return c.execute("SELECT COUNT(*) FROM _changes_dead").fetchone()[0]
        finally: c.close()

    def barrier(self, timeout=10.0):
        """تا زمانی که همهٔ تغییرات تا این لحظه در D1 تأیید نشده‌اند صبر می‌کند. False = تأیید نشد (timeout/خطای دائمی)."""
        c = self.connect()
        try: target = c.execute("SELECT MAX(seq) FROM _changes").fetchone()[0]
        finally: c.close()
        if target is None: return True
        deadline = time.monotonic() + timeout
        with self._cv:
            while self.shipped_seq < target:
                if self.fatal: return False
                rem = deadline - time.monotonic()
                if rem <= 0: return False
                self._wake.set(); self._cv.wait(min(rem, 0.5))
        return True


# ----------------------------------------------------------------------------- هماهنگ‌کنندهٔ کل
class D1Sync:
    def __init__(self, db_path, cfg=None, schema_version=1, on_lost=None):
        self.db_path, self.cfg, self.schema_version = db_path, cfg or Config(), schema_version
        self.client = D1Client(self.cfg); self.rep = Replicator(db_path, self.client, self.cfg.interval)
        self.on_lost = on_lost or self._default_on_lost
        self.instance = self._instance_id(); self.epoch = None; self.mode = None; self._lease_ok_at = 0.0
        self._hb = None; self._hb_stop = threading.Event(); self.lost = False

    # ---- شناسهٔ نمونه (پایدار بین ری‌استارت‌ها تا lease بلافاصله دوباره گرفته شود)
    def _instance_id(self):
        if self.cfg.instance: return self.cfg.instance[:64]
        p = self.db_path + ".instance"
        try:
            with open(p, "r", encoding="utf-8") as f: v = f.read().strip()
            if v: return v
        except OSError: pass
        v = f"{socket.gethostname()[:30]}-{uuid.uuid4().hex[:10]}"
        try:
            with open(p, "w", encoding="utf-8") as f: f.write(v)
            os.chmod(p, 0o600)
        except OSError as e: log.warning("cannot persist instance id: %s", e)
        return v

    @staticmethod
    def _default_on_lost():
        log.critical("D1 lease lost — another instance owns the bot. Shutting down to avoid split-brain.")
        os.kill(os.getpid(), signal.SIGTERM)

    # ---- state در D1
    def _state_get(self, k):
        r = self.client.query("SELECT v FROM _d1_state WHERE k=?", [k]); return r[0]["v"] if r else None

    def _state_set(self, k, v): self.client.query("INSERT OR REPLACE INTO _d1_state(k,v) VALUES(?,?)", [k, v])

    def _local_meta(self, conn, k):
        r = conn.execute("SELECT value FROM meta WHERE key=?", (k,)).fetchone(); return r[0] if r else None

    def _local_meta_set(self, conn, k, v): conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)", (k, str(v)))

    # ---- اسکیمای راه‌دور
    def ensure_remote_schema(self):
        for ddl in REMOTE_INTERNAL_DDL: self.client.query(ddl)
        c = self.rep.connect()
        try:
            for t in REPLICATED:
                sql = c.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (t,)).fetchone()
                if not sql or not sql[0]: raise RuntimeError(f"local table {t} missing — run the bot schema first")
                self.client.query(_inject_if_not_exists(sql[0]))
                remote_cols = {r["name"] for r in self.client.query(f"PRAGMA table_info({_q(t)})")}
                for col in c.execute("SELECT name,type,dflt_value FROM pragma_table_info(?)", (t,)):
                    if col["name"] not in remote_cols:
                        dflt = f" DEFAULT {col['dflt_value']}" if col["dflt_value"] is not None else ""
                        self.client.query(f"ALTER TABLE {_q(t)} ADD COLUMN {_q(col['name'])} {col['type'] or ''}{dflt}")
                        log.info("D1 schema: added column %s.%s", t, col["name"])
            for r in c.execute("SELECT name,sql FROM sqlite_master WHERE type='index' AND sql IS NOT NULL AND tbl_name IN (%s)" % ",".join("?" * len(REPLICATED)), REPLICATED):
                try: self.client.query(_inject_if_not_exists(r["sql"]))
                except D1Error as e:
                    if e.kind != "sql": raise
                    log.error("D1 index %s not created: %s", r["name"], e)
        finally: c.close()
        rv = self._state_get("schema_version")
        if rv and rv.isdigit() and int(rv) > self.schema_version:
            raise SystemExit(f"D1 schema_version {rv} is newer than this build ({self.schema_version}); deploy the newer build")
        self._state_set("schema_version", str(max(self.schema_version, int(rv or 0))))

    # ---- lease
    def _lease_sql(self):
        return (f"INSERT INTO _d1_lease(id,owner,epoch,lease_until,host,updated_at) VALUES(1,?,1,{_SRV_NOW}+?,?,datetime('now')) "
                f"ON CONFLICT(id) DO UPDATE SET epoch=CASE WHEN owner=? THEN epoch ELSE epoch+1 END, owner=?, lease_until={_SRV_NOW}+?, host=?, updated_at=datetime('now') "
                f"WHERE owner=? OR lease_until<{_SRV_NOW}")

    def lease_try(self):
        me, ttl, host = self.instance, self.cfg.lease_ttl, socket.gethostname()[:60]
        self.client.query(self._lease_sql(), [me, ttl, host, me, me, ttl, host, me])
        r = self.client.query(f"SELECT owner,epoch,lease_until,{_SRV_NOW} AS now FROM _d1_lease WHERE id=1")[0]
        if r["owner"] == me:
            self.epoch = int(r["epoch"]); self._lease_ok_at = time.monotonic(); return True, r
        return False, r

    def lease_ok(self):
        """پیش از کارهای حساس (انتشار) چک شود: تازه‌ترین تمدید موفق کمتر از ۸۰٪ TTL پیش بوده؟"""
        return (not self.lost) and (time.monotonic() - self._lease_ok_at) < self.cfg.lease_ttl * 0.8

    def _acquire_blocking(self):
        deadline = time.monotonic() + self.cfg.lease_wait; warned = False
        while True:
            ok, r = self.lease_try()
            if ok: return
            left = max(0, int(r["lease_until"]) - int(r["now"]))
            if not warned: log.warning("D1 lease held by '%s' for ~%ss more — waiting (graceful stop of the other instance releases it at once)", r["owner"], left); warned = True
            if time.monotonic() > deadline:
                raise SystemExit(f"another bot instance ('{r['owner']}') owns this D1 database; refusing to start (split-brain protection)")
            time.sleep(min(5.0, max(0.2, self.cfg.lease_ttl / 10.0)))

    def _heartbeat(self):
        every = max(0.5, self.cfg.lease_ttl / 3.0)
        while not self._hb_stop.wait(every):
            try:
                ok, r = self.lease_try()
                if not ok: self.lost = True; self.on_lost(); return
            except D1Error as e:
                # قطعی/خطای موقت D1 ⇒ ربات را نمی‌کشیم (قطعی Cloudflare نباید ربات را از کار بیندازد). فقط lease_ok() بعد از ۸۰٪ TTL
                # False می‌شود تا مسیرهای حساس (انتشار) متوقف بمانند؛ «از دست رفتن» فقط وقتی است که مالکِ دیگری را واقعاً ببینیم.
                log.warning("D1 lease renew failed (lease_ok=%s): %s", self.lease_ok(), self.client.redact(e))
            except Exception: log.exception("lease heartbeat error")

    def release(self):
        try: self.client.query("UPDATE _d1_lease SET lease_until=0 WHERE id=1 AND owner=?", [self.instance])
        except Exception as e: log.warning("lease release failed: %s", self.client.redact(e))

    # ---- بکاپ / seed / restore
    def backup_local(self, tag):
        dst = f"{self.db_path}.{tag}-{time.strftime('%Y%m%d-%H%M%S')}.bak"
        src = sqlite3.connect(self.db_path); out = sqlite3.connect(dst)
        try: src.backup(out)
        finally: out.close(); src.close()
        try: os.chmod(dst, 0o600)
        except OSError: pass
        log.info("local DB backup written: %s", dst); return dst

    def _local_has_data(self, conn):
        for t in ("users", "plans", "channels", "sources", "articles", "posted", "pay_requests", "ai_models", "discounts", "usage"):
            if conn.execute(f"SELECT 1 FROM {_q(t)} LIMIT 1").fetchone(): return True
        return False

    def _page(self, t): return PAGE_ROWS.get(t, PAGE_DEFAULT)

    def seed_up(self, conn):
        """بارگذاری کامل محلی → D1 (اولین مهاجرت). ابتدا D1 خالی می‌شود تا آینهٔ دقیق باشد، سپس شمارش‌ها verify می‌شوند."""
        self.client.query("DELETE FROM _d1_state WHERE k IN ('lineage','seeded_at')")
        for t in REPLICATED: self.client.query(f"DELETE FROM {_q(t)}")
        for t in REPLICATED:
            last, total = -1, 0; page = self._page(t)
            while True:
                rows = conn.execute(f"SELECT rowid AS _rid,* FROM {_q(t)} WHERE rowid>? ORDER BY rowid LIMIT ?", (last, page)).fetchall()
                if not rows: break
                stmts = [self.rep.upsert_stmt(conn, t, r) for r in rows]
                for i in range(0, len(stmts), 25): self.client.batch(stmts[i:i + 25])
                last = rows[-1]["_rid"]; total += len(rows)
            log.info("seed D1: %s → %d rows", t, total)
        self._verify_counts(conn)
        seqs = [(r[0], r[1]) for r in conn.execute("SELECT name,seq FROM sqlite_sequence") if r[0] in REPLICATED] if self._has_seq(conn) else []
        for n, s in seqs: self.client.query("INSERT OR REPLACE INTO _d1_seq(name,seq) VALUES(?,?)", [n, s])

    @staticmethod
    def _has_seq(conn): return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE name='sqlite_sequence'").fetchone())

    def _verify_counts(self, conn):
        bad = []
        for t in REPLICATED:
            l = conn.execute(f"SELECT COUNT(*) FROM {_q(t)}").fetchone()[0]
            r = int(self.client.query(f"SELECT COUNT(*) AS c FROM {_q(t)}")[0]["c"])
            if l != r: bad.append(f"{t}: local={l} d1={r}")
        if bad: raise RuntimeError("D1 verification failed — " + "; ".join(bad))

    def restore_down(self, conn):
        """D1 → محلی داخل «یک» تراکنش محلی (کرش وسط کار = برگشت کامل). تریگرها در طول restore خاموش‌اند."""
        conn.execute("BEGIN IMMEDIATE")
        try:
            self.rep.set_paused(conn, True)
            conn.execute("DELETE FROM _changes")
            for t in REPLICATED:
                conn.execute(f"DELETE FROM {_q(t)}"); cols = self.rep.columns(conn, t); last, total = -1, 0
                ins = f"INSERT OR REPLACE INTO {_q(t)}({','.join(_q(c) for c in cols)}) VALUES({','.join('?' * len(cols))})"
                while True:
                    rows = self.client.query(f"SELECT rowid AS _rid,* FROM {_q(t)} WHERE rowid>? ORDER BY rowid LIMIT ?", [last, self._page(t)])
                    if not rows: break
                    conn.executemany(ins, [[r.get(c) for c in cols] for r in rows]); last = rows[-1]["_rid"]; total += len(rows)
                log.info("restore from D1: %s ← %d rows", t, total)
            for r in self.client.query("SELECT name,seq FROM _d1_seq"):
                if r["name"] in REPLICATED and self._has_seq(conn):
                    cur = conn.execute("SELECT seq FROM sqlite_sequence WHERE name=?", (r["name"],)).fetchone()
                    if cur is None: conn.execute("INSERT INTO sqlite_sequence(name,seq) VALUES(?,?)", (r["name"], r["seq"]))
                    elif cur[0] < r["seq"]: conn.execute("UPDATE sqlite_sequence SET seq=? WHERE name=?", (r["seq"], r["name"]))
            self._verify_counts(conn)
            self.rep.set_paused(conn, False)
            conn.commit()
        except BaseException:
            conn.rollback(); raise

    # ---- bootstrap
    def _wait_reachable(self):
        deadline = time.monotonic() + self.cfg.boot_wait; delay = 1.0
        while True:
            try: self.client.query("SELECT 1"); return
            except D1Error as e:
                if e.kind in ("auth", "config"): raise SystemExit(f"D1 configuration error: {e}")
                if time.monotonic() > deadline: raise SystemExit(f"D1 unreachable at startup: {e}")
                log.warning("waiting for D1: %s", e); time.sleep(delay); delay = min(delay * 2, 15)

    def bootstrap(self):
        """پیش از seed_plans()/recover_publications() صدا زده شود. خروجی: mode ∈ fresh|seed|restore|resume"""
        self._wait_reachable(); self.ensure_remote_schema(); self._acquire_blocking()
        conn = self.rep.connect()
        try:
            self.client_state = {k: self._state_get(k) for k in ("lineage", "seeded_at")}
            remote_lineage = self.client_state["lineage"]; has_data = self._local_has_data(conn)
            l_lin, l_ep = self._local_meta(conn, "d1_lineage"), self._local_meta(conn, "d1_epoch")
            if self.cfg.force_restore and remote_lineage: mode = "restore"
            elif self.cfg.force_seed and has_data: mode = "seed"
            elif not remote_lineage: mode = "seed" if has_data else "fresh"
            elif l_lin == remote_lineage and l_ep is not None and int(l_ep) >= self.epoch: mode = "resume"
            else: mode = "restore"
            log.info("D1 bootstrap: mode=%s local_data=%s local_lineage=%s epoch(local/remote)=%s/%s", mode, has_data, (l_lin or "-")[:8], l_ep, self.epoch)
            lineage = remote_lineage
            if mode in ("seed", "restore") and has_data: self.backup_local("pre-d1-" + mode)
            if mode == "seed":
                self.rep.install(); c2 = self.rep.connect()
                try: self.rep.set_paused(c2, True); c2.commit(); self.seed_up(c2)
                finally: c2.close()
                lineage = uuid.uuid4().hex
            elif mode == "restore": self.restore_down(conn)
            elif mode == "fresh": lineage = uuid.uuid4().hex
            if mode in ("seed", "fresh"):
                self._state_set("lineage", lineage); self._state_set("seeded_at", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
            self._local_meta_set(conn, "d1_lineage", lineage); self._local_meta_set(conn, "d1_epoch", self.epoch); conn.commit()
        finally: conn.close()
        c3 = self.rep.connect()
        try: self.rep.set_paused(c3, False); c3.commit()
        finally: c3.close()
        self.rep.install(); self.mode = mode; return mode

    def start(self):
        self.rep.start(); self._hb_stop.clear()
        self._hb = threading.Thread(target=self._heartbeat, name="d1-lease", daemon=True); self._hb.start()

    def stop(self):
        self._hb_stop.set(); self.rep.stop(); self.release(); self.client.close()

    def barrier(self, timeout=10.0, strict=True):
        ok = self.rep.barrier(timeout)
        if not ok and strict: raise D1Unavailable("D1 did not confirm the write in time")
        return ok

    def status(self):
        return {"mode": self.mode, "epoch": self.epoch, "instance": self.instance, "pending": self.rep.pending(), "dead": self.rep.dead(),
                "fatal": self.rep.fatal, "last_ok_age_s": (time.time() - self.rep.last_ok) if self.rep.last_ok else None,
                "lease_ok": self.lease_ok(), "batch_mode": self.client.batch_mode}


# ----------------------------------------------------------------------------- CLI: selftest با D1 واقعی
def _selftest():
    cfg = Config()
    if not cfg.enabled: print("FAIL: set CF_ACCOUNT_ID, CF_D1_DATABASE_ID and CF_D1_API_TOKEN (or CF_API_TOKEN)"); return 2
    cl = D1Client(cfg)
    try:
        cl.query("CREATE TABLE IF NOT EXISTS _d1_selftest(k TEXT PRIMARY KEY, v INTEGER)")
        cl.query("INSERT OR REPLACE INTO _d1_selftest(k,v) VALUES(?,?)", ["a", 1])
        cl.batch([("INSERT OR REPLACE INTO _d1_selftest(k,v) VALUES(?,?)", ["b", 2]), ("INSERT OR REPLACE INTO _d1_selftest(k,v) VALUES(?,?)", ["c", 3])])
        rows = cl.query("SELECT k,v FROM _d1_selftest ORDER BY k")
        ok = [(r["k"], r["v"]) for r in rows] == [("a", 1), ("b", 2), ("c", 3)]
        pr = {r["name"] for r in cl.query('PRAGMA table_info("_d1_selftest")')}
        cl.query("DROP TABLE _d1_selftest")
        print(f"read/write: {'OK' if ok else 'MISMATCH ' + str(rows)} · batch mode: {cl.batch_mode} · PRAGMA table_info: {'OK' if pr == {'k', 'v'} else 'UNEXPECTED ' + str(pr)}")
        return 0 if ok and pr == {"k", "v"} else 1
    except D1Error as e:
        print(f"FAIL [{e.kind}]: {cl.redact(e)}"); return 1
    finally: cl.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    sys.exit(_selftest() if cmd == "selftest" else (print("usage: python d1sync.py selftest") or 2))
