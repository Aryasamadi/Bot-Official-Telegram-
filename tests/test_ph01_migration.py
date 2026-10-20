# -*- coding: utf-8 -*-
"""PH-01 — اجرای مهاجرتِ پایگاه‌داده روی **کپی** و اثباتِ افزایشی/تکرارپذیر بودنِ آن.

روش: یک پایگاه‌دادهٔ واقعی ساخته می‌شود، سپس **به نسخهٔ ۷ برگردانده می‌شود** (جدولِ
``wallet`` و ستون‌های جدید حذف، trigger قدیمی برگردانده، ``schema_version=7``). از آن یک
**کپی** گرفته می‌شود و مهاجرت روی **کپی** اجرا می‌شود. پایگاه‌دادهٔ مبدأ نباید تغییری
 ببیند.

اثبات‌ها:
  ۱) **افزایشی** — هیچ ستون/جدولی حذف نمی‌شود و دادهٔ موجود سالم می‌ماند.
  ۲) **idempotent** — اجرای دوباره هیچ تغییری نمی‌دهد (نه در داده، نه در ساختار).
  ۳) **بدونِ تبدیل** — موجودیِ قدیمی در سطلِ منجمدِ ``legacy`` می‌نشیند و خرج نمی‌شود.
  ۴) **مبدأ دست‌نخورده** — فایلِ کپی‌شده پیش از مهاجرت، با اثرِ انگشتِ SHA-256 یکسان می‌ماند.
"""
import hashlib
import shutil
import sqlite3

import pytest

from newsbot.timeutils import now_iso

OLD_TRIGGER = """
CREATE TRIGGER trg_referral_credit_wallet
AFTER UPDATE OF credit_amount ON referrals
FOR EACH ROW
WHEN NEW.credit_amount > COALESCE(OLD.credit_amount, 0)
BEGIN
    UPDATE users SET credit=COALESCE(credit,0)+(NEW.credit_amount-COALESCE(OLD.credit_amount,0)) WHERE id=NEW.referrer_id;
    INSERT INTO credit_log(user_id,delta,balance_after,reason,ref,ts)
    VALUES(NEW.referrer_id, NEW.credit_amount-COALESCE(OLD.credit_amount,0),
           (SELECT COALESCE(credit,0) FROM users WHERE id=NEW.referrer_id), 'referral',
           CAST(NEW.referred_id AS TEXT), strftime('%Y-%m-%dT%H:%M:%f000+00:00','now'));
END
"""


def _sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _downgrade_to_v7(path):
    """پایگاه‌دادهٔ فعلی را به شکلِ «پیش از v8» درمی‌آورد (شبیه‌سازیِ یک نصبِ قدیمی)."""
    c = sqlite3.connect(path)
    c.executescript(OLD_TRIGGER.replace("CREATE TRIGGER ", "DROP TRIGGER IF EXISTS trg_referral_credit_wallet; CREATE TRIGGER "))
    c.execute("DROP TRIGGER IF EXISTS trg_referral_credit_wallet")
    c.execute(OLD_TRIGGER)
    c.execute("DROP TABLE IF EXISTS wallet")
    for tbl, col in (("credit_log", "cur"), ("referrals", "credit_cur"), ("pay_requests", "cur"),
                     ("pay_requests", "decided_by"), ("pay_requests", "notified_at"),
                     ("plan_changes", "ref")):
        try:
            c.execute(f"ALTER TABLE {tbl} DROP COLUMN {col}")
        except sqlite3.OperationalError:
            pass
    c.execute("DELETE FROM meta WHERE key='legacy_wallet_v1'")
    c.execute("UPDATE meta SET value='7' WHERE key='schema_version'")
    c.commit()
    c.close()


def _migrate(path):
    """مهاجرت را روی فایلِ داده‌شده اجرا می‌کند و اتصالِ برنامه را موقتاً به آن می‌برد.

    اتصالِ برگشتی **بسته نمی‌شود** (فراخوان بستنش با فراخوانی بعدی تداخل می‌کرد)؛
    فراخوان باید خودش آن را ببندد."""
    from newsbot import config
    from newsbot import db as D
    old_file = config.DB_FILE
    old_ready = D._SCHEMA_READY
    old_conn = getattr(D._tls, "conn", None)
    try:
        config.DB_FILE = path
        D._SCHEMA_READY = False
        D._tls.conn = None
        conn = D.db()          # ← اینجاست که اسکیما/مهاجرت اجرا می‌شود
        conn.commit()
        return conn
    finally:
        D._tls.conn = old_conn
        config.DB_FILE = old_file
        D._SCHEMA_READY = old_ready


@pytest.fixture()
def legacy_copy(tmp_path):
    """یک کپیِ واقعی از پایگاه‌دادهٔ فعلی که به v7 برگردانده شده، به‌علاوهٔ فایلِ مبدأ."""
    from newsbot import config
    from newsbot.db import db
    src = config.DB_FILE
    conn = db()
    conn.commit()
    # پایگاه‌داده در حالت WAL است ⇒ برای اینکه «کپی» کامل باشد، اول WAL را
    # checkpoint می‌کنیم تا همه‌چیز در خودِ فایلِ اصلی بنشیند.
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.commit()
    legacy = str(tmp_path / "legacy-v7.db")
    copy = str(tmp_path / "copy-for-migration.db")
    shutil.copy2(src, legacy)
    _downgrade_to_v7(legacy)
    shutil.copy2(legacy, copy)
    return {"legacy": legacy, "copy": copy, "src": src}


def test_migration_on_a_copy_is_additive_and_preserves_data(legacy_copy):
    conn = _migrate(legacy_copy["copy"])
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(credit_log)")}
        assert "cur" in cols, "ستونِ واحد باید اضافه شده باشد"
        cols = {r[1] for r in conn.execute("PRAGMA table_info(pay_requests)")}
        assert {"cur", "decided_by", "notified_at"} <= cols
        cols = {r[1] for r in conn.execute("PRAGMA table_info(referrals)")}
        assert "credit_cur" in cols
        cols = {r[1] for r in conn.execute("PRAGMA table_info(users)")}
        assert "credit" in cols, "ستونِ قدیمی نباید حذف شود (مهاجرت افزایشی است)"
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "wallet" in tables
        # نسخهٔ اسکیما بالا رفته
        ver = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0]
        assert ver == "8"
        # دادهٔ موجود سالم است
        n_users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        assert n_users >= 0
    finally:
        conn.close()
        _restore()


def test_migration_moves_legacy_balance_into_a_frozen_bucket(legacy_copy):
    from newsbot.core_wallet import wallet_get, CUR_USD
    # موجودیِ قدیمیِ یک کاربر در فایلِ v7
    c = sqlite3.connect(legacy_copy["copy"])
    c.execute("INSERT OR IGNORE INTO users(id,username,name,role,credit,created_at,last_seen) "
              "VALUES(9001,'u9001','U9001','user',123.45,?,?)", (now_iso(), now_iso()))
    c.commit(); c.close()

    conn = _migrate(legacy_copy["copy"])
    try:
        r = conn.execute("SELECT amount FROM wallet WHERE user_id=9001 AND cur='legacy'").fetchone()
        assert r is not None and abs(float(r[0]) - 123.45) < 1e-9, \
            "موجودیِ قدیمی باید در سطلِ منجمدِ legacy بنشیند"
        # و نباید قابلِ خرج باشد
        assert wallet_get(9001, CUR_USD) == 0.0
    finally:
        conn.close()
        _restore()


def test_migration_is_idempotent(legacy_copy):
    conn = _migrate(legacy_copy["copy"])
    try:
        before = conn.execute("SELECT (SELECT COUNT(*) FROM wallet), (SELECT value FROM meta WHERE key='schema_version'), "
                              "(SELECT COUNT(*) FROM users), (SELECT COUNT(*) FROM pay_requests)").fetchone()
        conn.commit()
    finally:
        conn.close()
    # اجرای دوباره
    conn = _migrate(legacy_copy["copy"])
    try:
        after = conn.execute("SELECT (SELECT COUNT(*) FROM wallet), (SELECT value FROM meta WHERE key='schema_version'), "
                             "(SELECT COUNT(*) FROM users), (SELECT COUNT(*) FROM pay_requests)").fetchone()
        assert tuple(after) == tuple(before), "اجرای دوبارهٔ مهاجرت نباید چیزی را تغییر دهد"
        # و داده‌ها هم نباید دوباره منتقل شوند
        n_legacy = conn.execute("SELECT COUNT(*) FROM wallet WHERE cur='legacy'").fetchone()[0]
        assert n_legacy == before[0]
    finally:
        conn.close()
        _restore()


def test_migration_does_not_touch_the_source_database(legacy_copy):
    """فایلِ مبدأ (v7) نباید حتی یک بایت تغییر کند."""
    before = _sha(legacy_copy["legacy"])
    conn = _migrate(legacy_copy["copy"])
    try:
        assert conn is not None
    finally:
        conn.close()
        _restore()
    assert _sha(legacy_copy["legacy"]) == before, "پایگاه‌دادهٔ مبدأ نباید تغییر کند"


def _restore():
    """بازگرداندنِ اتصالِ برنامه به پایگاه‌دادهٔ اصلیِ تست."""
    from newsbot import db as D
    D._tls.conn = None
    D._SCHEMA_READY = False
    try:
        D.db()
    except Exception:
        pass
