#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ابزارِ چرخشِ SECRETS_KEY (فاز ۱۰ · AUD-001).

قرارداد:

* کلیدها **فقط** از محیط خوانده می‌شوند: ``OLD_SECRETS_KEY`` (کلیدِ کنونی) و
  ``SECRETS_KEY`` (کلیدِ نو). هرگز argv، هرگز چاپ، هرگز لاگ، هرگز در گزارش.
* مسیرِ دیتابیس: ``--db`` یا متغیرِ محیطی ``DB_FILE``.
* حالت‌ها: ``--check`` (فقط اعتبارسنجی، بدونِ نوشتن) یا ``--apply --backup PATH``.
* جدول‌های هدف: ``ai_models.api_key`` و ``ai_providers.api_key``.
* خروجی: یک JSON با **شمارش‌ها** فقط (بدونِ هیچ مقدار).

کدهای خروج: ``0`` موفق، ``2`` پیش‌شرط نامعتبر (هیچ نوشتنی)، ``3`` خطای داده
(هیچ نوشتنی). همهٔ به‌روزرسانی‌ها در یک تراکنشِ ``BEGIN IMMEDIATE`` انجام می‌شود:
یا همه با کلیدِ نو، یا هیچ.
"""
import argparse
import json
import os
import secrets
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from newsbot.secret_store import PREFIX, fernet_for  # noqa: E402

TARGET_TABLES = ("ai_models", "ai_providers")


class PreconditionError(Exception):
    """پیش‌شرط برقرار نیست ⇒ exit 2 و هیچ نوشتنی."""


class DataError(Exception):
    """داده با کلیدِ قدیم باز نمی‌شود ⇒ exit 3 و هیچ نوشتنی."""


# ── کمکی‌ها ──────────────────────────────────────────────────────────────────────
def _read_env(name):
    value = (os.environ.get(name) or "").strip()
    if not value:
        raise PreconditionError("missing environment variable %s" % name)
    return value


def _quoted(name):
    return '"%s"' % str(name).replace('"', '""')


def _connect(path):
    conn = sqlite3.connect(str(path), isolation_level=None)
    conn.row_factory = sqlite3.Row
    return conn


def _table_exists(conn, name):
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def _key_rows(conn, table):
    if not _table_exists(conn, table):
        return 0
    row = conn.execute(
        "SELECT COUNT(*) c FROM %s WHERE api_key IS NOT NULL AND api_key != ''" % _quoted(table)
    ).fetchone()
    return int(row["c"])


def _all_tables(conn):
    return [
        r["name"]
        for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        if not str(r["name"]).startswith("sqlite_")
    ]


def _foreign_encrypted_tables(conn):
    """جدول‌هایی بیرون از دو جدولِ هدف که مقدارِ ``enc:v1:`` دارند (fail-closed)."""
    found = []
    for table in _all_tables(conn):
        if table in TARGET_TABLES:
            continue
        for col in conn.execute("PRAGMA table_info(%s)" % _quoted(table)).fetchall():
            ctype = (col["type"] or "").upper()
            if ctype and not any(k in ctype for k in ("CHAR", "TEXT", "CLOB", "STRING")):
                continue
            hit = conn.execute(
                "SELECT 1 FROM %s WHERE %s LIKE ? LIMIT 1" % (_quoted(table), _quoted(col["name"])),
                (PREFIX + "%",),
            ).fetchone()
            if hit:
                found.append(table)
                break
    return sorted(set(found))


def _decrypt(fernet, value):
    """متنِ اصلی یا None اگر باز نشود."""
    try:
        return fernet.decrypt(value[len(PREFIX):].encode("ascii")).decode("utf-8")
    except Exception:
        return None


def _encrypt(fernet, plain):
    return PREFIX + fernet.encrypt(plain.encode("utf-8")).decode("ascii")


def _validate_backup(backup_path, db_path, target_conn):
    """بکاپ باید یک اسنپ‌شاتِ معتبر و تازه از همین دیتابیس باشد."""
    if not backup_path.exists():
        raise PreconditionError("backup file does not exist")
    if backup_path.stat().st_size == 0:
        raise PreconditionError("backup file is empty")
    if db_path.exists() and os.path.samefile(str(backup_path), str(db_path)):
        raise PreconditionError("backup must not be the live database")
    if backup_path.stat().st_mtime < db_path.stat().st_mtime:
        raise PreconditionError("backup is older than the live database")
    conn = _connect(backup_path)
    try:
        row = conn.execute("PRAGMA integrity_check").fetchone()
        if not row or str(row[0]).strip().lower() != "ok":
            raise PreconditionError("backup integrity_check did not pass")
        for table in TARGET_TABLES:
            if _key_rows(conn, table) != _key_rows(target_conn, table):
                raise PreconditionError("backup row counts differ from the live database")
    finally:
        conn.close()


def _collect_rows(conn):
    rows = []
    for table in TARGET_TABLES:
        if not _table_exists(conn, table):
            continue
        for row in conn.execute(
            "SELECT id, api_key FROM %s WHERE api_key IS NOT NULL AND api_key != ''" % _quoted(table)
        ):
            rows.append((table, row["id"], row["api_key"]))
    return rows


def run(args):
    old_key = _read_env("OLD_SECRETS_KEY")
    new_key = _read_env("SECRETS_KEY")
    if secrets.compare_digest(old_key, new_key):
        raise PreconditionError("OLD_SECRETS_KEY and SECRETS_KEY must differ")

    raw_db = args.db or os.environ.get("DB_FILE") or ""
    if not raw_db.strip():
        raise PreconditionError("database path is required (--db or DB_FILE)")
    db_path = Path(raw_db).expanduser()
    if not db_path.is_file():
        raise PreconditionError("database file not found")
    db_path = db_path.resolve()

    old_fernet = fernet_for(old_key)
    new_fernet = fernet_for(new_key)
    if old_fernet is None or new_fernet is None:
        raise PreconditionError("cryptography is required to rotate secrets")

    backup_path = None
    if args.backup:
        backup_path = Path(args.backup).expanduser().resolve()
    if args.apply and backup_path is None:
        raise PreconditionError("--apply requires --backup")

    counts = {
        "mode": "apply" if args.apply else "check",
        "rows_total": 0,
        "rows_reencrypted": 0,
        "rows_encrypted_plain": 0,
        "rows_already_new": 0,
        "rows_failed": 0,
        "other_enc_v1_tables": [],
    }

    conn = _connect(db_path)
    try:
        foreign = _foreign_encrypted_tables(conn)
        counts["other_enc_v1_tables"] = foreign
        if foreign:
            raise PreconditionError(
                "enc:v1 values exist outside %s: %s" % (", ".join(TARGET_TABLES), ", ".join(foreign))
            )
        if backup_path is not None:
            _validate_backup(backup_path, db_path, conn)

        updates = []
        failed = []
        for table, row_id, value in _collect_rows(conn):
            counts["rows_total"] += 1
            if isinstance(value, str) and value.startswith(PREFIX):
                plain = _decrypt(old_fernet, value)
                if plain is not None:
                    updates.append((table, row_id, _encrypt(new_fernet, plain)))
                    counts["rows_reencrypted"] += 1
                    continue
                if _decrypt(new_fernet, value) is not None:
                    counts["rows_already_new"] += 1
                    continue
                failed.append(table)
                continue
            updates.append((table, row_id, _encrypt(new_fernet, value)))
            counts["rows_encrypted_plain"] += 1

        counts["rows_failed"] = len(failed)
        if failed:
            raise DataError(
                "%d row(s) could not be decrypted with the old key; nothing was written" % len(failed)
            )

        if args.apply and updates:
            conn.execute("BEGIN IMMEDIATE")
            try:
                for table, row_id, value in updates:
                    conn.execute(
                        "UPDATE %s SET api_key=? WHERE id=?" % _quoted(table),
                        (value, row_id),
                    )
                conn.execute("COMMIT")
            except Exception:
                try:
                    conn.execute("ROLLBACK")
                except Exception:
                    pass
                raise DataError("the transaction failed; nothing was written")
    finally:
        conn.close()
    return counts


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Rotate SECRETS_KEY: re-encrypt ai_models/ai_providers keys atomically.",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="validate only; write nothing")
    mode.add_argument("--apply", action="store_true", help="rotate the keys (requires --backup)")
    parser.add_argument("--backup", help="path of a verified backup of the live database")
    parser.add_argument("--db", help="path of the live database (default: $DB_FILE)")
    args = parser.parse_args(argv)

    try:
        counts = run(args)
    except PreconditionError as exc:
        print(json.dumps({"error": "precondition", "detail": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    except DataError as exc:
        print(json.dumps({"error": "data", "detail": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 3
    print(json.dumps(counts, sort_keys=True, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
