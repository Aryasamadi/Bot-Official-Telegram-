# -*- coding: utf-8 -*-
"""OPS-011: بازیابی امن SQLite — sidecarهای WAL قدیمی نباید روی دیتابیس بازیابی‌شده replay شوند."""
import importlib.util
import os
import sqlite3

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location("restore_sqlite_backup", os.path.join(ROOT, "tools", "restore_sqlite_backup.py"))
rsb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rsb)


def _make_db(path, rows, wal=True):
    con = sqlite3.connect(path)
    if wal:
        con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE t (v TEXT)")
    con.executemany("INSERT INTO t VALUES (?)", [(r,) for r in rows])
    con.commit()
    return con


def _values(path):
    con = sqlite3.connect(path)
    try:
        return sorted(r[0] for r in con.execute("SELECT v FROM t"))
    finally:
        con.close()


def test_restore_replaces_data_and_clears_stale_sidecars(tmp_path):
    backup = str(tmp_path / "backup.db")
    c = _make_db(backup, ["old1", "old2"], wal=False); c.close()

    target = str(tmp_path / "live.db")
    live = _make_db(target, ["NEW-should-vanish"])
    live.close()
    # بعد از crash، sidecarهای باقی‌مانده کنار دیتابیس می‌مانند (سرویس متوقف است)
    with open(target + "-wal", "wb") as f:
        f.write(b"\x37\x7f\x06\x82" + b"stale-wal-frames" * 50)
    with open(target + "-shm", "wb") as f:
        f.write(b"stale-shm" * 100)

    rsb.restore(backup, target, stamp="T")

    assert _values(target) == ["old1", "old2"]
    assert not os.path.exists(target + "-wal")
    assert not os.path.exists(target + "-shm")
    # نسخهٔ قبل از بازیابی نگه داشته شده و داده‌ی قدیمیِ live را دارد
    assert _values(str(tmp_path / "live.db.pre-restore-T")) == ["NEW-should-vanish"]

def test_restore_refuses_corrupt_backup_and_leaves_target_untouched(tmp_path):
    bad = tmp_path / "bad.db"
    bad.write_bytes(b"this is not sqlite" * 100)
    target = str(tmp_path / "live.db")
    c = _make_db(target, ["keep"], wal=False); c.close()
    with pytest.raises(rsb.RestoreError):
        rsb.restore(str(bad), target)
    assert _values(target) == ["keep"]


def test_restore_refuses_empty_database(tmp_path):
    empty = str(tmp_path / "empty.db")
    sqlite3.connect(empty).close()
    target = str(tmp_path / "live.db")
    c = _make_db(target, ["keep"], wal=False); c.close()
    with pytest.raises(rsb.RestoreError):
        rsb.restore(empty, target)
    assert _values(target) == ["keep"]


def test_dry_run_changes_nothing(tmp_path):
    backup = str(tmp_path / "b.db")
    c = _make_db(backup, ["b"], wal=False); c.close()
    target = str(tmp_path / "live.db")
    c = _make_db(target, ["live"], wal=False); c.close()
    plan = rsb.restore(backup, target, dry_run=True)
    assert plan["dry_run"] is True
    assert _values(target) == ["live"]
    assert not [p for p in os.listdir(tmp_path) if "pre-restore" in p]


def test_restore_into_missing_target_works(tmp_path):
    backup = str(tmp_path / "b.db")
    c = _make_db(backup, ["x"], wal=False); c.close()
    target = str(tmp_path / "sub" / "new.db")
    rsb.restore(backup, target)
    assert _values(target) == ["x"]


def test_cli_requires_service_stopped_confirmation(tmp_path):
    backup = str(tmp_path / "b.db")
    c = _make_db(backup, ["b"], wal=False); c.close()
    target = str(tmp_path / "live.db")
    assert rsb.main([backup, target]) == 2
    assert not os.path.exists(target)
    assert rsb.main([backup, target, "--confirm-service-stopped"]) == 0
    assert _values(target) == ["b"]
