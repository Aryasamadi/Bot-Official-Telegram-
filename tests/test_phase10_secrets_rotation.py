# -*- coding: utf-8 -*-
"""فاز ۱۰ — نگهبانِ چرخشِ SECRETS_KEY (W2 · AUD-001).

این فایل قراردادِ ابزارِ ``tools/rotate_secrets_key.py`` را قفل می‌کند:

1. بازرمزگذاری با کلیدِ نو برای هر دو جدول (``ai_models`` و ``ai_providers``).
2. اجرای دوباره بی‌اثر است (idempotent) و ردیف‌های ازپیش‌جدیدشده دست‌نخورده می‌مانند.
3. ردیفی که با هیچ‌کدام از کلیدها باز نمی‌شود ⇒ خروج 3 و **هیچ نوشتنی**.
4. ردیفِ متنیِ ساده هم با کلیدِ نو رمز می‌شود.
5. بکاپِ ناموجود/همان‌فایل/کهنه/با شمارشِ متفاوت ⇒ خروج 2.
6. مقدارِ ``enc:v1:`` بیرون از دو جدولِ هدف ⇒ خروج 2 (fail-closed).
7. هیچ مقدارِ رازی هرگز در stdout/stderr نمی‌آید.
8. ``fernet_for`` با رمزگذاری/رمزگشاییِ موجود هم‌ارز است و مهاجرتِ استارت هر دو جدول را می‌پوشاند.
"""
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from cryptography.fernet import InvalidToken

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "rotate_secrets_key.py"

OLD_KEY = "old-secret-for-tests-only"
NEW_KEY = "new-secret-for-tests-only"
THIRD_KEY = "third-secret-for-tests-only"
PLAIN_VALUE = "plain-api-key-for-tests-only"


# ── کمکی ────────────────────────────────────────────────────────────────────────
def _enc(key, value):
    from newsbot.secret_store import PREFIX, fernet_for

    fernet = fernet_for(key)
    assert fernet is not None
    return PREFIX + fernet.encrypt(value.encode("utf-8")).decode("ascii")


def _dec(key, value):
    from newsbot.secret_store import PREFIX, fernet_for

    fernet = fernet_for(key)
    assert fernet is not None
    return fernet.decrypt(value[len(PREFIX):].encode("ascii")).decode("utf-8")


@pytest.fixture()
def db_file(tmp_path):
    path = tmp_path / "newsbot.db"
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE ai_models(id INTEGER PRIMARY KEY, name TEXT, api_key TEXT)")
    conn.execute("CREATE TABLE ai_providers(id INTEGER PRIMARY KEY, name TEXT, api_key TEXT)")
    conn.execute("CREATE TABLE notes(id INTEGER PRIMARY KEY, body TEXT)")
    conn.commit()
    conn.close()
    return path


def _insert(db_file, table, value, row_id=1):
    conn = sqlite3.connect(str(db_file))
    conn.execute("INSERT INTO %s(id, name, api_key) VALUES(?, ?, ?)" % table,
                 (row_id, "n%s" % row_id, value))
    conn.commit()
    conn.close()


def _values(db_file):
    conn = sqlite3.connect(str(db_file))
    try:
        models = [r[0] for r in conn.execute("SELECT api_key FROM ai_models ORDER BY id")]
        providers = [r[0] for r in conn.execute("SELECT api_key FROM ai_providers ORDER BY id")]
    finally:
        conn.close()
    return models, providers


def _backup(db_file, tmp_path, name="backup.db"):
    path = tmp_path / name
    shutil.copy2(str(db_file), str(path))
    return path


def _run(args, db_file, env_extra=None):
    env = dict(os.environ)
    env.pop("OLD_SECRETS_KEY", None)
    env.pop("SECRETS_KEY", None)
    env.update({"OLD_SECRETS_KEY": OLD_KEY, "SECRETS_KEY": NEW_KEY, "PYTHONPATH": str(ROOT)})
    if env_extra:
        env.update(env_extra)
    cmd = [sys.executable, str(TOOL), "--db", str(db_file)] + list(args)
    return subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=str(ROOT))


def _counts(result):
    import json

    return json.loads(result.stdout)


# ── ۱) چرخشِ واقعی ──────────────────────────────────────────────────────────────
def test_apply_rotates_both_tables_with_the_new_key(db_file, tmp_path):
    _insert(db_file, "ai_models", _enc(OLD_KEY, "model-key"), 1)
    _insert(db_file, "ai_providers", _enc(OLD_KEY, "provider-key"), 1)
    backup = _backup(db_file, tmp_path)

    r = _run(["--apply", "--backup", str(backup)], db_file)
    assert r.returncode == 0, r.stderr

    models, providers = _values(db_file)
    assert _dec(NEW_KEY, models[0]) == "model-key"
    assert _dec(NEW_KEY, providers[0]) == "provider-key"
    # کلیدِ قدیمی دیگر باز نمی‌کند ⇒ InvalidToken (نه «استثنای کور»)
    with pytest.raises(InvalidToken):
        _dec(OLD_KEY, models[0])

    counts = _counts(r)
    assert counts["rows_total"] == 2
    assert counts["rows_reencrypted"] == 2
    assert counts["rows_failed"] == 0
    assert counts["other_enc_v1_tables"] == []


def test_check_reports_without_writing(db_file, tmp_path):
    original = _enc(OLD_KEY, "model-key")
    _insert(db_file, "ai_models", original, 1)
    backup = _backup(db_file, tmp_path)

    r = _run(["--check", "--backup", str(backup)], db_file)
    assert r.returncode == 0, r.stderr
    models, _ = _values(db_file)
    assert models == [original], "--check نباید چیزی بنویسد"
    counts = _counts(r)
    assert counts["mode"] == "check"
    assert counts["rows_reencrypted"] == 1


def test_a_second_apply_is_idempotent(db_file, tmp_path):
    _insert(db_file, "ai_models", _enc(OLD_KEY, "model-key"), 1)
    backup = _backup(db_file, tmp_path)
    assert _run(["--apply", "--backup", str(backup)], db_file).returncode == 0
    after_first, _ = _values(db_file)

    fresh_backup = _backup(db_file, tmp_path, name="backup2.db")
    r = _run(["--apply", "--backup", str(fresh_backup)], db_file)
    assert r.returncode == 0, r.stderr
    after_second, _ = _values(db_file)
    assert after_second == after_first, "اجرای دوباره نباید چیزی را عوض کند"
    counts = _counts(r)
    assert counts["rows_already_new"] == 1
    assert counts["rows_reencrypted"] == 0


def test_plaintext_rows_are_encrypted_with_the_new_key(db_file, tmp_path):
    _insert(db_file, "ai_models", PLAIN_VALUE, 1)
    _insert(db_file, "ai_providers", PLAIN_VALUE, 2)
    backup = _backup(db_file, tmp_path)

    r = _run(["--apply", "--backup", str(backup)], db_file)
    assert r.returncode == 0, r.stderr
    models, providers = _values(db_file)
    assert _dec(NEW_KEY, models[0]) == PLAIN_VALUE
    assert _dec(NEW_KEY, providers[0]) == PLAIN_VALUE
    counts = _counts(r)
    assert counts["rows_encrypted_plain"] == 2


# ── ۲) خطاها و پیش‌شرط‌ها ───────────────────────────────────────────────────────
def test_a_row_with_another_key_fails_without_writing(db_file, tmp_path):
    good = _enc(OLD_KEY, "model-key")
    bad = _enc(THIRD_KEY, "other-key")
    _insert(db_file, "ai_models", good, 1)
    _insert(db_file, "ai_models", bad, 2)
    backup = _backup(db_file, tmp_path)
    before, _ = _values(db_file)

    r = _run(["--apply", "--backup", str(backup)], db_file)
    assert r.returncode == 3, r.stdout
    after, _ = _values(db_file)
    assert after == before, "هیچ ردیفی نباید تغییر کند"


def test_apply_without_a_backup_is_refused(db_file):
    r = _run(["--apply"], db_file)
    assert r.returncode == 2, r.stdout


def test_the_backup_may_not_be_the_live_database(db_file):
    r = _run(["--apply", "--backup", str(db_file)], db_file)
    assert r.returncode == 2, r.stdout


def test_a_missing_backup_is_refused(db_file, tmp_path):
    r = _run(["--apply", "--backup", str(tmp_path / "nope.db")], db_file)
    assert r.returncode == 2, r.stdout


def test_a_stale_backup_is_refused(db_file, tmp_path):
    _insert(db_file, "ai_models", _enc(OLD_KEY, "model-key"), 1)
    backup = _backup(db_file, tmp_path)
    # ردیفِ تازه ⇒ شمارشِ ردیف‌ها فرق می‌کند و بکاپ کهنه می‌شود
    _insert(db_file, "ai_models", _enc(OLD_KEY, "model-key-2"), 2)

    r = _run(["--apply", "--backup", str(backup)], db_file)
    assert r.returncode == 2, r.stdout
    models, _ = _values(db_file)
    assert len(models) == 2, "هیچ تغییری نباید انجام شود"


def test_an_older_backup_timestamp_is_refused(db_file, tmp_path):
    _insert(db_file, "ai_models", _enc(OLD_KEY, "model-key"), 1)
    backup = _backup(db_file, tmp_path)
    os.utime(str(backup), (1_600_000_000, 1_600_000_000))

    r = _run(["--apply", "--backup", str(backup)], db_file)
    assert r.returncode == 2, r.stdout


def test_an_encrypted_value_outside_the_target_tables_aborts(db_file, tmp_path):
    _insert(db_file, "ai_models", _enc(OLD_KEY, "model-key"), 1)
    conn = sqlite3.connect(str(db_file))
    conn.execute("INSERT INTO notes(id, body) VALUES(?, ?)", (1, _enc(OLD_KEY, "foreign")))
    conn.commit()
    conn.close()
    backup = _backup(db_file, tmp_path)

    r = _run(["--apply", "--backup", str(backup)], db_file)
    assert r.returncode == 2, r.stdout
    models, _ = _values(db_file)
    assert _dec(OLD_KEY, models[0]) == "model-key", "هیچ تغییری نباید انجام شود"


def test_a_missing_environment_key_is_refused(db_file):
    r = _run(["--check"], db_file, env_extra={"OLD_SECRETS_KEY": ""})
    assert r.returncode == 2, r.stdout


def test_identical_old_and_new_keys_are_refused(db_file):
    r = _run(["--check"], db_file, env_extra={"SECRETS_KEY": OLD_KEY})
    assert r.returncode == 2, r.stdout


def test_a_missing_database_is_refused(tmp_path):
    r = _run(["--check"], tmp_path / "missing.db")
    assert r.returncode == 2, r.stdout


# ── ۳) بی‌صداییِ راز ─────────────────────────────────────────────────────────────
def test_no_secret_value_appears_in_the_output(db_file, tmp_path):
    _insert(db_file, "ai_models", _enc(OLD_KEY, PLAIN_VALUE), 1)
    _insert(db_file, "ai_providers", PLAIN_VALUE, 2)
    backup = _backup(db_file, tmp_path)

    r = _run(["--apply", "--backup", str(backup)], db_file)
    output = r.stdout + r.stderr
    for secret in (OLD_KEY, NEW_KEY, THIRD_KEY, PLAIN_VALUE):
        assert secret not in output, "مقدارِ راز نباید در خروجی بیاید"
    assert "enc:v1:" not in output, "مقدارِ رمزشده نباید در خروجی بیاید"


def test_a_failing_run_does_not_print_secret_values(db_file, tmp_path):
    _insert(db_file, "ai_models", _enc(THIRD_KEY, PLAIN_VALUE), 1)
    backup = _backup(db_file, tmp_path)

    r = _run(["--apply", "--backup", str(backup)], db_file)
    assert r.returncode == 3
    output = r.stdout + r.stderr
    for secret in (OLD_KEY, NEW_KEY, THIRD_KEY, PLAIN_VALUE):
        assert secret not in output


# ── ۴) هم‌ارزیِ secret_store ────────────────────────────────────────────────────
def test_fernet_for_matches_encrypt_and_decrypt(monkeypatch):
    from newsbot import config, secret_store

    monkeypatch.setattr(config, "SECRETS_KEY", "equivalence-key")
    encrypted = secret_store.encrypt("payload")
    assert secret_store.is_encrypted(encrypted)
    assert secret_store.decrypt(encrypted) == "payload"
    assert secret_store.fernet_for("equivalence-key") is not None
    assert secret_store.fernet_for("") is None
    assert secret_store.fernet_for(None) is None


def test_migrate_ai_keys_encrypts_ai_providers_too(isolated_db, monkeypatch):
    from newsbot import config, db, secret_store

    monkeypatch.setattr(config, "SECRETS_KEY", "phase10-migrate-key")
    db.q("INSERT INTO ai_models(name, api_key) VALUES(?, ?)", ("m", "plain-model"))
    db.q("INSERT INTO ai_providers(name, api_key) VALUES(?, ?)", ("p", "plain-provider"))

    assert secret_store.migrate_ai_keys() >= 2
    for table in ("ai_models", "ai_providers"):
        row = db.q("SELECT api_key FROM %s WHERE api_key LIKE 'enc:v1:%%'" % table, one=True)
        assert row, "کلیدِ متنی در %s باید رمز شود" % table
    assert secret_store.migrate_ai_keys() == 0, "مهاجرت باید idempotent باشد"
