# -*- coding: utf-8 -*-
"""فاز ۴ — ابزارِ پذیرشِ زنده (workflow دستیِ qa-live) و نگهبان‌های فایل‌های آن.

این تست‌ها آفلاین‌اند: هیچ تماسِ شبکه‌ای و هیچ توکنی وجود ندارد. هدفشان قفل‌کردنِ
قراردادهای ابزار است (ورودی/خروجی، انتسابِ per-row، کدهای خروج) و شکلِ فایل‌های
workflow و رانبوک.
"""
import json
import os

from tools import qa_live_acceptance as acc

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOKEN = "123456789:AA" + "x" * 34


def _src(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


# ── Task 2: env، شکلِ توکن، پیش‌شرط‌ها ─────────────────────────────────────────────
def test_env_file_values_are_loaded_and_process_env_wins(tmp_path, monkeypatch):
    p = tmp_path / "run.env"
    p.write_text('BOT_TOKEN="from-file"\nSECRETS_KEY=from-file\n# comment\n\n', encoding="utf-8")
    monkeypatch.setenv("BOT_TOKEN", "from-process")
    env = acc.load_env(str(p))
    assert env["BOT_TOKEN"] == "from-process" and env["SECRETS_KEY"] == "from-file"


def test_the_token_shape_is_checked():
    assert acc.token_shape_ok(TOKEN)
    assert not acc.token_shape_ok("not-a-token")
    assert not acc.token_shape_ok("123456789:short")


def test_admin_ids_accept_commas_and_spaces():
    assert acc.admin_ids(" 1, 22 33 ") == {1, 22, 33}


def test_secret_presence_reports_names_only():
    names = acc.secret_values_present("x " + TOKEN + " y",
                                      {"BOT_TOKEN": TOKEN, "SECRETS_KEY": "k" * 64})
    assert names == ["BOT_TOKEN"]


def test_check_rejects_a_missing_token(tmp_path):
    env = {"BOT_TOKEN": "", "SUPER_ADMIN_IDS": "1", "DB_FILE": str(tmp_path / "db.sqlite")}
    code, out = acc.run_check(env, http_get=lambda url, timeout=10: {"ok": True})
    assert code == 2 and "BOT_TOKEN_MISSING" in out["problems"]


def test_check_requires_secrets_key_when_the_db_exists(tmp_path):
    db = tmp_path / "db.sqlite"
    db.write_bytes(b"")
    env = {"BOT_TOKEN": TOKEN, "SUPER_ADMIN_IDS": "1", "SECRETS_KEY": "", "DB_FILE": str(db)}
    code, out = acc.run_check(env, http_get=lambda url, timeout=10: {"ok": True})
    assert code == 2 and "SECRETS_KEY_REQUIRED" in out["problems"]


def test_check_requires_secrets_key_even_for_a_fresh_db(tmp_path):
    """کلیدِ خالی یعنی کلیدِ مدل به‌صورتِ متنِ ساده در DB کش‌شده می‌نشیند."""
    env = {"BOT_TOKEN": TOKEN, "SUPER_ADMIN_IDS": "1", "SECRETS_KEY": "",
           "DB_FILE": str(tmp_path / "db.sqlite")}
    code, out = acc.run_check(env, http_get=lambda url, timeout=10: {"ok": True})
    assert code == 2 and "SECRETS_KEY_REQUIRED" in out["problems"]


def test_check_calls_get_me_and_maps_failures_without_the_url(tmp_path):
    env = {"BOT_TOKEN": TOKEN, "SUPER_ADMIN_IDS": "1", "SECRETS_KEY": "k" * 64,
           "DB_FILE": str(tmp_path / "db.sqlite")}
    seen = {}

    def boom(url, timeout=10):
        seen["url"] = url
        raise RuntimeError("http 401")

    code, out = acc.run_check(env, http_get=boom)
    assert code == 2 and "GETME_FAILED" in out["problems"]
    assert TOKEN in seen["url"] and TOKEN not in json.dumps(out)


def test_check_passes_with_valid_inputs(tmp_path):
    env = {"BOT_TOKEN": TOKEN, "SUPER_ADMIN_IDS": "1", "SECRETS_KEY": "k" * 64,
           "DB_FILE": str(tmp_path / "db.sqlite")}
    code, out = acc.run_check(env, http_get=lambda url, timeout=10: {"ok": True, "result": {"id": 1}})
    assert code == 0 and out["ok"] is True and out["token_len"] == len(TOKEN)
