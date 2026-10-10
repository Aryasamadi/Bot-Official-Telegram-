# -*- coding: utf-8 -*-
import ast, glob, json, logging, os, shutil, sqlite3, sys, tempfile, threading, time, unittest
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT); sys.path.insert(0, HERE)
import d1sync
from d1sync import Config, D1Sync, D1Error, D1Unavailable, REPLICATED
from fake_d1 import FakeD1

BOT = os.path.join(ROOT, "bot.py") if os.path.exists(os.path.join(ROOT, "bot.py")) else os.path.join(ROOT, "bot.py.orig")


def _bot_schema():
    """اسکیمای واقعی ربات را بدون import (telegram لازم نیست) از AST بیرون می‌کشد."""
    tree = ast.parse(open(BOT, encoding="utf-8").read()); want = {"SCHEMA", "DB_INDEXES", "MIGRATIONS"}; out = {}
    for n in tree.body:
        if isinstance(n, ast.Assign) and len(n.targets) == 1 and getattr(n.targets[0], "id", None) in want: out[n.targets[0].id] = ast.literal_eval(n.value)
    return out


S = _bot_schema()


def make_local(path):
    c = sqlite3.connect(path); c.execute("PRAGMA journal_mode=WAL")
    c.executescript(S["SCHEMA"] + "\n" + S["DB_INDEXES"]); c.execute("CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT)")
    have = {}
    for tbl, col, decl in S["MIGRATIONS"]:
        have.setdefault(tbl, {r[1] for r in c.execute(f"PRAGMA table_info({tbl})")})
        if col not in have[tbl]: c.execute(f"ALTER TABLE {tbl} ADD COLUMN {col} {decl}")
    c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_channels_chat_id ON channels(chat_id)")
    c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_sources_channel_url ON sources(channel_id,url)")
    c.commit(); return c


def populate(c):
    c.execute("INSERT INTO users(id,username,name,role,created_at) VALUES(111,'ali','علی','super','t')")
    c.execute("INSERT INTO users(id,username,name,role,created_at) VALUES(222,'sara','سارا','admin','t')")
    c.execute("INSERT INTO plans(name,name_en,days,daily_posts,price,is_free) VALUES('رایگان','Free',7,1,'رایگان',1)")
    c.execute("INSERT INTO channels(admin_id,chat_id,title,settings) VALUES(222,-1001234567890,'کانال تست','{\"a\":1}')")
    c.execute("INSERT INTO sources(admin_id,channel_id,url) VALUES(222,1,'https://example.com/rss')")
    for i in range(5): c.execute("INSERT INTO articles(admin_id,channel_id,hash,url,title,status,post_html) VALUES(222,1,?,?,?,?,?)", (f"h{i}", f"https://e.com/{i}", f"عنوان {i} 🙂", "ready", "<b>متن</b>" * 50))
    c.execute("INSERT INTO usage(admin_id,day,posts,tests) VALUES(222,'2026-10-07',3,1)")
    c.execute("INSERT INTO posted VALUES(-1001234567890,'h0','2026-10-07T00:00:00+00:00')")
    c.execute("INSERT INTO settings VALUES('automation_enabled','true')")
    c.execute("INSERT INTO pub_journal VALUES(1,'{\"phase\":\"prepared\"}','t')")
    c.execute("INSERT INTO kv_cache VALUES('k','v','t')"); c.execute("INSERT INTO dedup VALUES(-1,'fp','t')")
    c.commit()


def dump(conn, tables=REPLICATED):
    out = {}
    for t in tables:
        conn.row_factory = None
        out[t] = sorted(json.dumps(r, ensure_ascii=False, default=str) for r in conn.execute(f"SELECT * FROM \"{t}\""))
    return out


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(); self.fake = FakeD1(os.path.join(self.tmp, "d1.db")); self.syncs = []
        self.local_path = os.path.join(self.tmp, "a.db")

    def tearDown(self):
        for s in self.syncs:
            try: s.rep.stop(0.1); s.client.close()
            except Exception: pass
        self.fake.close(); shutil.rmtree(self.tmp, ignore_errors=True)

    def cfg(self, instance="A", **extra):
        env = dict(CF_ACCOUNT_ID="acc", CF_D1_DATABASE_ID="db", CF_D1_API_TOKEN="tok-SECRET", D1_API_BASE=self.fake.base, D1_SYNC_INTERVAL="0.2",
                   D1_LEASE_TTL="6", D1_LEASE_WAIT="3", D1_BOOT_WAIT="3", D1_INSTANCE_ID=instance); env.update(extra)
        return Config(env)

    def mk(self, path=None, instance="A", **extra):
        s = D1Sync(path or self.local_path, self.cfg(instance, **extra), schema_version=1, on_lost=lambda: None)
        self.syncs.append(s); return s

    def remote(self): return sqlite3.connect(os.path.join(self.tmp, "d1.db"))


class TestBootstrap(Base):
    def test_fresh_install(self):
        c = make_local(self.local_path); s = self.mk(); self.assertEqual(s.bootstrap(), "fresh")
        c.execute("INSERT INTO users(id,username,name,role,created_at) VALUES(5,'u','n','user','t')"); c.commit()
        self.assertEqual(s.rep.ship_once(), 1)
        self.assertEqual(dump(c), dump(self.remote()))

    def test_seed_existing_data_with_backup_and_verify(self):
        c = make_local(self.local_path); populate(c); s = self.mk()
        self.assertEqual(s.bootstrap(), "seed")
        self.assertEqual(dump(c), dump(self.remote()))
        self.assertTrue(glob.glob(self.local_path + ".pre-d1-seed-*.bak"), "backup must exist before seeding")
        names = {r[0] for r in self.remote().execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertNotIn("kv_cache", names); self.assertNotIn("dedup", names)  # داده‌های موقت محلی می‌مانند
        self.assertEqual(s.rep.pending(), 0)  # seed نباید از طریق تریگر دوباره صف شود

    def test_restore_on_new_vps_and_sequences_not_reused(self):
        c = make_local(self.local_path); populate(c); a = self.mk(instance="A"); a.bootstrap()
        c.execute("DELETE FROM articles WHERE id>=4"); c.commit(); a.rep.ship_once()  # شناسه‌های ۴ و ۵ حذف شدند؛ sequence باید ۵ بماند
        want = dump(c); a.release()
        new = os.path.join(self.tmp, "newvps.db"); c2 = make_local(new); b = self.mk(new, instance="B")
        self.assertEqual(b.bootstrap(), "restore"); self.assertEqual(want, dump(c2))
        nid = c2.execute("INSERT INTO articles(admin_id,channel_id,hash,url,status) VALUES(222,1,'x','u','ready')").lastrowid; c2.commit()
        self.assertGreater(nid, 5, "AUTOINCREMENT ids must not be reused after restore")
        self.assertEqual(b.rep.pending(), 1)  # فقط همین نوشتن جدید؛ restore echo نداشته

    def test_split_brain_refused_while_other_instance_alive(self):
        make_local(self.local_path); a = self.mk(instance="A"); a.bootstrap(); a.start()  # heartbeat زنده = نمونهٔ A هنوز در حال اجراست
        new = os.path.join(self.tmp, "b.db"); make_local(new); b = self.mk(new, instance="B")
        with self.assertRaises(SystemExit): b.bootstrap()

    def test_stale_old_instance_restores_and_keeps_backup(self):
        c = make_local(self.local_path); populate(c); a = self.mk(instance="A"); a.bootstrap(); a.release()
        pb = os.path.join(self.tmp, "b.db"); cb = make_local(pb); b = self.mk(pb, instance="B"); self.assertEqual(b.bootstrap(), "restore")
        cb.execute("INSERT INTO users(id,username,name,role,created_at) VALUES(999,'new','n','user','t')"); cb.commit(); b.rep.ship_once(); b.release()
        a2 = self.mk(instance="A")  # VPS قدیمی دوباره روشن شد
        self.assertEqual(a2.bootstrap(), "restore")
        c3 = sqlite3.connect(self.local_path)
        self.assertEqual(c3.execute("SELECT COUNT(*) FROM users WHERE id=999").fetchone()[0], 1)
        self.assertTrue(glob.glob(self.local_path + ".pre-d1-restore-*.bak"))

    def test_resume_same_instance_ships_backlog_after_crash(self):
        c = make_local(self.local_path); a = self.mk(); a.bootstrap()
        c.execute("INSERT INTO users(id,username,name,role,created_at) VALUES(7,'x','n','user','t')"); c.commit()  # «کرش» قبل از ارسال
        a2 = self.mk(); self.assertEqual(a2.bootstrap(), "resume"); a2.rep.ship_once()
        self.assertEqual(self.remote().execute("SELECT COUNT(*) FROM users WHERE id=7").fetchone()[0], 1)


class TestReplication(Base):
    def setUp(self):
        super().setUp(); self.c = make_local(self.local_path); populate(self.c); self.s = self.mk(); self.s.bootstrap()

    def test_coalescing_update_delete_composite_and_pk_change(self):
        c, s = self.c, self.s
        for i in range(100): c.execute("UPDATE users SET name=? WHERE id=222", (f"n{i}",))
        c.commit(); self.fake.stmts.clear(); s.rep.ship_once()
        self.assertEqual(len([x for x in self.fake.stmts if "users" in x[0]]), 1)
        c.execute("DELETE FROM sources WHERE id=1"); c.execute("INSERT INTO usage(admin_id,day,posts,tests) VALUES(222,'2026-10-07',1,0) ON CONFLICT(admin_id,day) DO UPDATE SET posts=posts+1")
        c.execute("UPDATE settings SET key='renamed' WHERE key='automation_enabled'"); c.execute("INSERT OR REPLACE INTO posted VALUES(-1001234567890,'h0','later')")
        c.commit(); s.rep.ship_once(); self.assertEqual(dump(c), dump(self.remote()))

    def test_transient_faults_then_converges(self):
        self.c.execute("INSERT INTO users(id,username,name,role,created_at) VALUES(8,'a','b','user','t')"); self.c.commit()
        self.fake.faults += [("status", 500), ("status", 429, {"Retry-After": "0"}), ("drop",)]
        self.s.rep.ship_once(); self.assertEqual(dump(self.c), dump(self.remote()))

    def test_partial_application_then_resend_is_idempotent(self):
        self.s.client.max_attempts = 1
        for i in range(6): self.c.execute("INSERT INTO users(id,username,name,role,created_at) VALUES(?,?,?,?,?)", (300 + i, "u", "n", "user", "t"))
        self.c.commit(); self.fake.faults.append(("partial", 2))
        with self.assertRaises(D1Error): self.s.rep.ship_once()
        self.assertGreater(self.s.rep.pending(), 0)  # چیزی از صف پاک نشده
        self.s.rep.ship_once(); self.assertEqual(dump(self.c), dump(self.remote())); self.assertEqual(self.s.rep.pending(), 0)

    def test_poison_rows_quarantined_others_ship(self):
        self.fake.reject_marker = "POISON"
        self.c.execute("INSERT INTO articles(admin_id,channel_id,hash,url,title,status) VALUES(222,1,'p','u','POISON','ready')")
        self.c.execute("INSERT INTO users(id,username,name,role,created_at) VALUES(2**60,'big','n','user','t')".replace("2**60", str(2 ** 60)))
        self.c.execute("INSERT INTO users(id,username,name,role,created_at) VALUES(9,'ok','n','user','t')"); self.c.commit()
        self.s.rep.ship_once()
        self.assertEqual(self.s.rep.dead(), 2); self.assertEqual(self.s.rep.pending(), 0)
        r = self.remote()
        self.assertEqual(r.execute("SELECT COUNT(*) FROM users WHERE id=9").fetchone()[0], 1)
        self.assertEqual(r.execute("SELECT COUNT(*) FROM articles WHERE hash='p'").fetchone()[0], 0)

    def test_auth_failure_never_quarantines(self):
        self.c.execute("INSERT INTO users(id,username,name,role,created_at) VALUES(10,'a','b','user','t')"); self.c.commit()
        self.fake.token = "rotated"
        with self.assertLogs("d1sync", level="WARNING") as cm, self.assertRaises(D1Error) as ex:
            logging.getLogger("d1sync").warning("probe"); self.s.rep.ship_once()
        self.assertEqual(ex.exception.kind, "auth"); self.assertEqual(self.s.rep.dead(), 0); self.assertEqual(self.s.rep.pending(), 1)
        self.assertNotIn("tok-SECRET", str(ex.exception)); self.assertFalse(any("tok-SECRET" in m for m in cm.output))
        self.fake.token = "tok-SECRET"; self.s.rep.ship_once(); self.assertEqual(self.s.rep.pending(), 0)

    def test_barrier_true_after_flush_false_when_down_and_strict_raises(self):
        self.s.start(); self.c.execute("INSERT INTO users(id,username,name,role,created_at) VALUES(11,'a','b','user','t')"); self.c.commit()
        self.assertTrue(self.s.barrier(5.0)); self.assertEqual(self.remote().execute("SELECT COUNT(*) FROM users WHERE id=11").fetchone()[0], 1)
        self.s.client.max_attempts = 1; self.fake.faults += [("status", 500)] * 50
        self.c.execute("INSERT INTO users(id,username,name,role,created_at) VALUES(12,'a','b','user','t')"); self.c.commit()
        with self.assertRaises(D1Unavailable): self.s.barrier(1.0)
        self.assertFalse(self.s.barrier(0.5, strict=False))
        self.fake.faults.clear(); self.assertTrue(self.s.barrier(8.0))

    def test_volatile_settings_not_replicated_but_real_settings_are(self):
        c = self.c
        for i in range(20):
            c.execute("INSERT OR REPLACE INTO settings VALUES('heartbeat',?)", (f'"{i}"',)); c.execute("INSERT OR REPLACE INTO settings VALUES('load_due',?)", (str(i),))
        c.execute("INSERT OR REPLACE INTO settings VALUES('automation_enabled','false')"); c.commit()
        self.fake.stmts.clear(); self.s.rep.ship_once()
        shipped = [x for x in self.fake.stmts if "settings" in x[0]]
        self.assertEqual(len(shipped), 1); self.assertEqual(shipped[0][1][0], "automation_enabled")
        self.assertEqual(self.s.rep.pending(), 0)

    def test_write_burst_from_many_threads_replicates_exactly_without_lock_errors(self):
        self.s.start(); errs = []
        def writer(n):
            try:
                c = sqlite3.connect(self.local_path, timeout=30)
                for i in range(100):
                    c.execute("INSERT INTO articles(admin_id,channel_id,hash,url,title,status) VALUES(222,1,?,?,?,?)", (f"w{n}-{i}", f"https://e/{n}/{i}", f"t{n}-{i}", "ready")); c.commit()
                c.close()
            except Exception as e: errs.append(repr(e))
        ts = [threading.Thread(target=writer, args=(n,)) for n in range(20)]; t0 = time.time()
        [t.start() for t in ts]; [t.join() for t in ts]
        self.assertEqual(errs, []); self.assertTrue(self.s.barrier(60.0), "all 2000 rows must be confirmed by D1")
        self.assertEqual(dump(self.c), dump(self.remote())); self.assertEqual(self.s.rep.pending(), 0); self.assertEqual(self.s.rep.dead(), 0)
        n_art = len([x for x in self.fake.stmts if "articles" in x[0]]); self.assertLessEqual(n_art, 2100)

    def test_graceful_stop_flushes_pending(self):
        s = self.mk(instance="A2", D1_SYNC_INTERVAL="30"); s.cfg.interval = 30; s.rep.interval = 30
        s.ensure_remote_schema(); s.start()  # ترد در خواب طولانی است؛ فقط flush نهایی می‌تواند بفرستد
        self.c.execute("INSERT INTO users(id,username,name,role,created_at) VALUES(77,'z','z','user','t')"); self.c.commit()
        s.rep.stop(5.0); self.assertEqual(self.remote().execute("SELECT COUNT(*) FROM users WHERE id=77").fetchone()[0], 1)

    def test_schema_drift_adds_remote_column(self):
        self.c.execute("ALTER TABLE users ADD COLUMN vip INTEGER DEFAULT 0"); self.c.commit(); self.s.rep._colcache.clear()
        self.s.ensure_remote_schema(); self.c.execute("UPDATE users SET vip=1 WHERE id=111"); self.c.commit(); self.s.rep.ship_once()
        self.assertEqual(self.remote().execute("SELECT vip FROM users WHERE id=111").fetchone()[0], 1)

    def test_batch_unsupported_falls_back_to_single(self):
        self.fake.batch_supported = False; self.c.execute("INSERT INTO users(id,username,name,role,created_at) VALUES(13,'a','b','user','t')"); self.c.commit()
        self.s.client.batch_mode = "unknown"; self.s.rep.ship_once()
        self.assertEqual(self.s.client.batch_mode, "off"); self.assertEqual(dump(self.c), dump(self.remote()))


class TestLease(Base):
    def test_cas_epoch_and_release(self):
        make_local(self.local_path); a = self.mk(instance="A"); b = self.mk(instance="B")
        a.ensure_remote_schema(); self.assertTrue(a.lease_try()[0]); e = a.epoch; self.assertTrue(a.lease_try()[0]); self.assertEqual(a.epoch, e)
        self.assertFalse(b.lease_try()[0]); a.release(); self.assertTrue(b.lease_try()[0]); self.assertEqual(b.epoch, e + 1); self.assertTrue(b.lease_ok())

    def test_outage_does_not_kill_bot_but_pauses_lease_ok(self):
        lost = []; make_local(self.local_path)
        a = D1Sync(self.local_path, self.cfg("A", D1_LEASE_TTL="3"), schema_version=1, on_lost=lambda: lost.append(1)); self.syncs.append(a)
        a.bootstrap(); a.start(); a.client.max_attempts = 1; self.assertTrue(a.lease_ok())
        self.fake.faults += [("status", 500)] * 400; time.sleep(3.8)
        self.assertFalse(lost, "a D1 outage alone must not shut the bot down"); self.assertFalse(a.lease_ok(), "sensitive paths must pause during outage")
        self.fake.faults.clear(); time.sleep(2.0); self.assertTrue(a.lease_ok()); self.assertFalse(lost)

    def test_real_loss_triggers_on_lost(self):
        lost = []; make_local(self.local_path)
        a = D1Sync(self.local_path, self.cfg("A", D1_LEASE_TTL="3"), schema_version=1, on_lost=lambda: lost.append(1)); self.syncs.append(a)
        a.bootstrap(); a.start()
        r = self.remote(); r.execute("UPDATE _d1_lease SET owner='B', lease_until=strftime('%s','now')+1000"); r.commit(); r.close()
        deadline = time.time() + 4
        while not lost and time.time() < deadline: time.sleep(0.1)
        self.assertEqual(lost, [1]); self.assertFalse(a.lease_ok())

    def test_inject_if_not_exists(self):
        f = d1sync._inject_if_not_exists
        self.assertEqual(f("CREATE TABLE t(a)"), "CREATE TABLE IF NOT EXISTS t(a)")
        self.assertEqual(f("CREATE UNIQUE INDEX i ON t(a)"), "CREATE UNIQUE INDEX IF NOT EXISTS i ON t(a)")
        self.assertEqual(f("CREATE TABLE IF NOT EXISTS t(a)"), "CREATE TABLE IF NOT EXISTS t(a)")


if __name__ == "__main__":
    logging.disable(logging.CRITICAL) if "-v" not in sys.argv else None
    unittest.main()
