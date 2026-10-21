# -*- coding: utf-8 -*-
"""PHASE-04 — محافظت در برابرِ حلقهٔ ری‌استارت (رخداد تولید 2026-10-21).

ریشهٔ محتملِ «۱۰۰٪ CPU و رباتِ کارنکرده»: کانُنیکال‌سازیِ زمان در استارتاپ، کلِ جدول را با
``fetchall()`` در حافظه می‌آورد، سطر‌به‌سطر در **یک تراکنشِ اسکیما** به‌روزرسانی می‌کند و پرچمِ
«انجام شد» را فقط در پایان می‌نویسد ⇒ روی دیتابیسِ بزرگ هرگز کامل نمی‌شود ⇒ systemd آن را
می‌کشد ⇒ ``Restart=always`` دوباره بلندش می‌کند ⇒ مصرفِ CPU دائمی و ربات بالا نمی‌آید.

این تست‌ها تضمین می‌کنند:
  H6-1  کانُنیکال‌سازی در **بسته‌های محدود** انجام می‌شود (نه یک ``fetchall`` بی‌حد).
  H6-2  برای هر ستون **checkpoint جداگانه** ثبت می‌شود ⇒ ری‌استارت ادامه می‌دهد، تکرار نمی‌کند.
  H6-3  checkpoint‌ها **بیرون از تراکنشِ اسکیما** commit می‌شوند (اگر داخل آن باشند، یک kill
        همه‌چیز را برمی‌گرداند و حلقه ادامه پیدا می‌کند).
  H6-4  drop-inِ systemd سقفِ ری‌استارت دارد ⇒ حلقهٔ بی‌پایانِ CPU متوقف می‌شود.
"""
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DROPIN = ROOT / "deploy" / "systemd" / "10-restart-always.conf"


def _src(path):
    return path.read_text(encoding="utf-8")


# ── H6-1 / H6-2 / H6-3 — رفتارِ کانُنیکال‌سازی ─────────────────────────────────────
def test_iso_normalisation_is_chunked_and_resumable():
    """کد باید بسته‌ای کار کند و برای هر ستون checkpoint بگذارد."""
    src = _src(ROOT / "newsbot" / "db.py")
    body = src.split("def normalize_iso_columns", 1)[1].split("\ndef ", 1)[0]
    code = "\n".join(l for l in body.splitlines() if not l.strip().startswith(("#", '"', "'''")))
    # بسته‌ای، نه خواندنِ بی‌حدِ کلِ جدول: فرمِ صفحه‌بندی‌شده باید باشد
    assert "IS NOT NULL AND rowid > ?" in code, "خواندن باید صفحه‌بندی‌شده باشد (rowid > ?)"
    assert 'IS NOT NULL"' not in code, "خواندنِ بی‌حدِ کلِ جدول نباید باقی بماند"
    assert "LIMIT ?" in code, "باید بسته‌ای (LIMIT) خوانده شود"
    assert "chunk=2000" in body, "اندازهٔ بسته باید صریح و محدود باشد"
    # checkpoint برای هر ستون
    assert "iso_norm_v1:" in code, "هر ستون باید checkpoint جداگانه داشته باشد"
    # ادامه‌دادن به‌جای تکرار
    assert "continue" in code
    # checkpoint باید commit شود (تا از kill جان سالم بماند)
    assert "conn.commit()" in code, "checkpoint باید commit شود"


def test_iso_normalisation_runs_outside_the_schema_transaction():
    """کانُنیکال‌سازی نباید داخلِ تراکنشِ اسکیما باشد (وگرنه kill همه‌چیز را برمی‌گرداند)."""
    src = _src(ROOT / "newsbot" / "db.py")
    body = src.split("def _ensure_schema", 1)[1].split("\ndef db(", 1)[0]
    trx_open = body.index("BEGIN IMMEDIATE")
    trx_close = body.index("if atomic: conn.commit()")
    call = body.index("normalize_iso_columns(conn)")
    assert call > trx_close, "کانُنیکال‌سازی باید بعد از commitِ اسکیما اجرا شود"
    assert call > trx_open


def test_iso_normalisation_actually_normalises_and_is_idempotent():
    """رفتارِ واقعی: مقدارهای legacy کانونیکال می‌شوند و اجرای دوم کاری نمی‌کند."""
    from newsbot import db as D
    from newsbot.timeutils import iso_fixed
    tmp = ROOT / "tests" / "_tmp_iso_probe.db"
    if tmp.exists():
        tmp.unlink()
    con = sqlite3.connect(str(tmp))
    con.row_factory = sqlite3.Row
    con.execute("CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT)")
    con.execute("CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, created_at TEXT, last_seen TEXT)")
    con.execute("INSERT INTO users(id,created_at,last_seen) VALUES(1,'2020-01-01T00:00:00+00:00','2020-01-01T00:00:00+00:00')")
    con.commit()
    n = D.normalize_iso_columns(con, chunk=1)      # chunk=1 ⇒ مسیرِ بسته‌ای واقعاً اجرا می‌شود
    row = con.execute("SELECT created_at FROM users WHERE id=1").fetchone()
    assert n == 2, "دو مقدار باید کانونیکال شوند"
    assert row[0] == iso_fixed(__import__("datetime").datetime.fromisoformat("2020-01-01T00:00:00+00:00"))
    marker = con.execute("SELECT value FROM meta WHERE key='iso_norm_v1:users.created_at'").fetchone()
    assert marker is not None, "برای هر ستون checkpoint ثبت شود"
    assert D.normalize_iso_columns(con, chunk=1) == 0, "اجرای دوم نباید کاری کند"
    con.close()
    tmp.unlink()


# ── H6-4 — سقفِ ری‌استارت در systemd ──────────────────────────────────────────────
def test_systemd_dropin_stops_the_restart_loop():
    """یونیت نباید بی‌نهایت ری‌استارت شود (حلقهٔ ۱۰۰٪ CPU)."""
    src = _src(DROPIN)
    assert "StartLimitIntervalSec=0" not in src, "سقفِ صفر یعنی حلقهٔ بی‌پایان"
    m = re.search(r"StartLimitIntervalSec=(\d+)", src)
    assert m, "StartLimitIntervalSec باید صریح باشد"
    assert int(m.group(1)) > 0
    assert re.search(r"StartLimitBurst=(\d+)", src), "StartLimitBurst باید صریح باشد"
    assert "Restart=always" in src, "سیاستمِ «هرگز خاموش نماند» حفظ می‌شود"


def test_deploy_health_gate_detects_a_crash_loopping_service():
    """گیتِ سلامتِ استقرار باید سرویسِ در حالِ crash-loop را تشخیص دهد."""
    src = _src(ROOT / "deploy" / "deploy.sh")
    assert "NRestarts" in src and "crash-looping" in src
