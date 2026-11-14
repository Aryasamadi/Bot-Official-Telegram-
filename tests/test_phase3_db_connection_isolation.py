# -*- coding: utf-8 -*-
"""کانکشنِ دیتابیس باید مسیرِ فایل را عوض کند وقتی ``DB_FILE`` عوض می‌شود (فاز ۳).

هنگامِ ساختِ موتورِ چرخش، تست‌ها شکستند چون ``db()`` کانکشنِ هر ترد را کش می‌کرد و بعد از
تغییرِ ``config.DB_FILE`` (فیکسچرِ ``isolated_db``) تردهای استخر همچنان روی فایلِ قبلی
می‌نوشتند: مکان‌نمای چرخش در دیتابیسِ تستِ قبلی جلو می‌رفت و در دیتابیسِ فعلی ردیفی نبود.
این یک باگِ واقعی است، نه فقط مسئلهٔ تست: هر تغییرِ مسیر در زمانِ اجرا همان اثر را دارد.
"""
from newsbot import config, db


def test_a_thread_connection_follows_a_changed_db_file(tmp_path, monkeypatch):
    old = config.DB_FILE
    first = tmp_path / "first.db"
    second = tmp_path / "second.db"

    # همان ترتیبی که در واقعیت رخ داد: WRITE در یک تردِ استخر، سپس تغییرِ مسیر، سپس READ.
    monkeypatch.setattr(config, "DB_FILE", str(first))
    db._tls.conn = None
    db.q("CREATE TABLE IF NOT EXISTS probe(v INTEGER)", commit=True)
    db.q("INSERT INTO probe(v) VALUES(1)", commit=True)

    monkeypatch.setattr(config, "DB_FILE", str(second))
    db.q("CREATE TABLE IF NOT EXISTS probe(v INTEGER)", commit=True)
    assert db.q("SELECT COUNT(*) AS n FROM probe", one=True)["n"] == 0, "نباید ردیف‌های فایلِ قبلی را ببیند"

    db.q("INSERT INTO probe(v) VALUES(7)", commit=True)
    monkeypatch.setattr(config, "DB_FILE", str(first))
    assert db.q("SELECT COUNT(*) AS n FROM probe", one=True)["n"] == 1, "بازگشت به فایلِ اول باید دیده شود"

    c = getattr(db._tls, "conn", None)
    if c is not None:
        c.close()
    db._tls.conn = None
    monkeypatch.setattr(config, "DB_FILE", old)


def test_a_transient_d1_backend_does_not_drop_the_cached_connection(tmp_path, monkeypatch):
    """یک تماسِ گذرا با ``DB_BACKEND=d1`` نباید کانکشنِ جاری را دور بریزد.

    باگِ واقعیِ همین فاز: نسخه‌های اولِ نگهبان، هنگامِ سنجشِ سلامتِ D1 کانکشن را می‌بست و
    ``_init_conn`` بدونِ اعتبارنامه SystemExit می‌گرفت (۵ تستِ D1 و ۱ تستِ سلامت شکستند).
    """
    monkeypatch.setattr(config, "DB_FILE", str(tmp_path / "d1-guard.db"))
    db._tls.conn = None
    db.q("CREATE TABLE IF NOT EXISTS probe(v INTEGER)", commit=True)
    conn_before = db.db()

    monkeypatch.setattr(config, "DB_BACKEND", "d1")
    assert db.db() is conn_before, "نباید کانکشن عوض شود"

    monkeypatch.setattr(config, "DB_BACKEND", "sqlite")
    assert db.db() is conn_before, "بازگشت به sqlite هم نباید کانکشن را عوض کند"

    c = getattr(db._tls, "conn", None)
    if c is not None:
        c.close()
    db._tls.conn = None
