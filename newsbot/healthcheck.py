# -*- coding: utf-8 -*-
"""Healthcheck سبک برای Docker/Compose (DEVOPS-03).

هدف: یک بررسیِ *سریع* و *بی‌خطر* که بگوید کانتینر می‌تواند کار کند، بدونِ
نیاز به توکنِ ربات و بدونِ نوشتن در دیتابیس.

سه چیز بررسی می‌شود:
  1. بستهٔ ``newsbot`` و ماژول ``config`` بدونِ خطا import می‌شوند (تصویر سالم است).
  2. پوشهٔ داده (``DB_FILE``) وجود دارد و نوشتنی است (وگرنه ربات در لحظهٔ اول
     نوشتن crash می‌کند).
  3. اگر فایلِ دیتابیس هست، باز می‌شود و یک کوئریِ بی‌خطر (``SELECT 1``) اجرا
     می‌شود ⇒ فایل قفل‌شده یا خراب نیست.

خروجی: exit code ‎0 = سالم · ‎1 = ناسالم (و یک خطِ توضیح روی stdout).
این اسکریپت هرگز چیزی در دیتابیس نمی‌نویسد.
"""
import os
import sys


def _check() -> list:
    """فهرستِ مشکلات (خالی = سالم)."""
    problems = []
    try:
        from newsbot import config
    except Exception as exc:                      # pragma: no cover — تصویرِ خراب
        return [f"cannot import newsbot.config: {type(exc).__name__}: {exc}"]

    db_file = config.DB_FILE
    folder = os.path.dirname(os.path.abspath(db_file)) or "."
    if not os.path.isdir(folder):
        problems.append(f"the data folder does not exist: {folder}")
    elif not os.access(folder, os.W_OK):
        problems.append(f"the data folder is not writable: {folder}")

    if os.path.exists(db_file):
        if not os.access(db_file, os.W_OK):
            problems.append(f"the database file is not writable: {db_file}")
        else:
            try:
                import sqlite3
                con = sqlite3.connect(f"file:{db_file}?mode=ro", uri=True, timeout=5)
                try:
                    con.execute("SELECT 1").fetchone()
                finally:
                    con.close()
            except Exception as exc:
                problems.append(f"the database cannot be read: {type(exc).__name__}: {exc}")
    return problems


def main() -> int:
    problems = _check()
    if problems:
        print("healthcheck: FAIL — " + "; ".join(problems))
        return 1
    print("healthcheck: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
