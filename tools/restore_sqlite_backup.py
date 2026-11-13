#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""بازیابی امن دیتابیس SQLite از بکاپ (OPS-011 / OPS-012).

چرا ابزار جدا؟ بازیابی با `cp` یا بازنویسی مستقیم روی دیتابیس زنده، فایل‌های جانبی WAL
(`-wal` / `-shm`) دیتابیس قبلی را سر جایشان می‌گذارد و SQLite ممکن است آن‌ها را روی
فایل تازه «replay» کند ⇒ دیتابیس ترکیبی/خراب. این ابزار:

  1. بکاپ را فقط‌خواندنی باز می‌کند و `PRAGMA integrity_check` را روی آن اجرا می‌کند؛
  2. از دیتابیس فعلی (اگر هست) یک نسخهٔ `<db>.pre-restore-<stamp>` می‌گیرد؛
  3. بکاپ را با `sqlite3.backup` در فایل موقتِ کنار مقصد می‌سازد و دوباره راستی‌آزمایی می‌کند؛
  4. `-wal` / `-shm` مقصد را پاک می‌کند و فایل موقت را اتمیک (`os.replace`) جایگزین می‌کند؛
  5. مجوز/مالک فایل قبلی را حفظ می‌کند.

مصرف:
  python tools/restore_sqlite_backup.py BACKUP.db TARGET.db --confirm-service-stopped [--dry-run]

سرویس باید قبلاً متوقف شده باشد (`systemctl stop newsbot` یا `docker compose stop`).
"""
from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import sys
import time

SIDECARS = ("-wal", "-shm")


class RestoreError(Exception):
    pass


def _integrity(path: str) -> None:
    """راستی‌آزمایی فقط‌خواندنی؛ در صورت مشکل RestoreError."""
    uri = "file:" + path.replace("\\", "/") + "?mode=ro"
    try:
        con = sqlite3.connect(uri, uri=True)
    except sqlite3.Error as e:
        raise RestoreError(f"cannot open {path}: {e}") from e
    try:
        rows = [r[0] for r in con.execute("PRAGMA integrity_check")]
        if rows != ["ok"]:
            raise RestoreError(f"integrity_check failed for {path}: {rows[:3]}")
        n = con.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
        if n == 0:
            raise RestoreError(f"{path} contains no tables — refusing to restore an empty database")
    except sqlite3.Error as e:
        raise RestoreError(f"{path} is not a usable SQLite database: {e}") from e
    finally:
        con.close()


def _snapshot(src: str, dst: str) -> None:
    """نسخهٔ سازگار با WAL (sqlite3.backup)؛ اگر مبدأ خراب است، کپی فایل‌ها."""
    try:
        s = sqlite3.connect(src)
        try:
            d = sqlite3.connect(dst)
            try:
                s.backup(d)
            finally:
                d.close()
        finally:
            s.close()
    except sqlite3.Error:
        shutil.copy2(src, dst)
        for suf in SIDECARS:
            if os.path.exists(src + suf):
                shutil.copy2(src + suf, dst + suf)


def restore(backup: str, target: str, *, dry_run: bool = False, stamp: str | None = None) -> dict:
    backup, target = os.path.abspath(backup), os.path.abspath(target)
    if not os.path.isfile(backup):
        raise RestoreError(f"backup not found: {backup}")
    if os.path.abspath(backup) == target:
        raise RestoreError("backup and target are the same file")
    _integrity(backup)
    stamp = stamp or time.strftime("%Y%m%d-%H%M%S")
    pre = f"{target}.pre-restore-{stamp}"
    plan = {"backup": backup, "target": target, "pre_restore_copy": pre if os.path.exists(target) else None,
            "sidecars_removed": [target + s for s in SIDECARS if os.path.exists(target + s)],
            "dry_run": dry_run}
    if dry_run:
        return plan
    tdir = os.path.dirname(target) or "."
    os.makedirs(tdir, exist_ok=True)
    st = os.stat(target) if os.path.exists(target) else None
    if st is not None:
        _snapshot(target, pre)
    tmp = f"{target}.restore-tmp-{os.getpid()}"
    try:
        for p in (tmp, tmp + "-wal", tmp + "-shm"):
            if os.path.exists(p):
                os.remove(p)
        _snapshot(backup, tmp)
        # backup() یک دیتابیس WAL را WAL نگه می‌دارد؛ اطمینان از تمام‌شدن همهٔ نوشتن‌ها در خود فایل.
        con = sqlite3.connect(tmp)
        try:
            con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            con.execute("PRAGMA journal_mode=DELETE")
        finally:
            con.close()
        for suf in SIDECARS:
            if os.path.exists(tmp + suf):
                os.remove(tmp + suf)
        _integrity(tmp)
        for suf in SIDECARS:   # sidecarهای دیتابیس قبلی نباید روی فایل تازه replay شوند
            if os.path.exists(target + suf):
                os.remove(target + suf)
        if st is not None:
            os.chmod(tmp, st.st_mode & 0o777)
            if hasattr(os, "chown"):
                try:
                    os.chown(tmp, st.st_uid, st.st_gid)
                except OSError:
                    pass
        else:
            os.chmod(tmp, 0o600)
        os.replace(tmp, target)
    finally:
        for p in (tmp, tmp + "-wal", tmp + "-shm"):
            if os.path.exists(p):
                os.remove(p)
    _integrity(target)
    return plan


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("backup")
    ap.add_argument("target")
    ap.add_argument("--dry-run", action="store_true", help="فقط بکاپ را بررسی و برنامه را چاپ کن")
    ap.add_argument("--confirm-service-stopped", action="store_true",
                    help="تأیید می‌کنم سرویس ربات متوقف است (الزامی برای اجرای واقعی)")
    a = ap.parse_args(argv)
    if not a.dry_run and not a.confirm_service_stopped:
        print("refused: stop the bot first, then pass --confirm-service-stopped (or use --dry-run)", file=sys.stderr)
        return 2
    try:
        plan = restore(a.backup, a.target, dry_run=a.dry_run)
    except RestoreError as e:
        print(f"restore failed: {e}", file=sys.stderr)
        return 1
    print(("DRY-RUN " if a.dry_run else "RESTORED ") + str(plan))
    if not a.dry_run:
        print("next: start the service and run `python run.py --check`")
    return 0


if __name__ == "__main__":
    sys.exit(main())
