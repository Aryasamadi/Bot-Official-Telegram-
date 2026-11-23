# -*- coding: utf-8 -*-
"""فاز ۷ — ستونِ افزایشیِ onboarding_state و کمککنندههای حالتِ نخستین ورود."""
import pytest

from newsbot.db import q


def test_schema_is_thirteen_and_the_column_is_additive(isolated_db):
    import newsbot
    from newsbot.core_users import ensure_user
    assert newsbot.SCHEMA_VERSION == 13
    ensure_user(4242, "u", "U")   # جدولِ ایزوله خالی است؛ یک ردیف برای بررسیِ پیشفرضِ ستون لازم است
    row = q("SELECT onboarding_state FROM users LIMIT 1", one=True)
    assert row is not None and row["onboarding_state"] in (None, "")


def test_onboarding_state_roundtrip_and_validation(isolated_db):
    from newsbot.core_users import ensure_user, get_onboarding_state, set_onboarding_state
    ensure_user(4242, "u", "U")
    assert get_onboarding_state(4242) == ""
    set_onboarding_state(4242, "welcome")
    assert get_onboarding_state(4242) == "welcome"
    set_onboarding_state(4242, "bogus")
    assert get_onboarding_state(4242) == "welcome"


def test_v13_db_is_refused_by_a_v12_build(isolated_db, monkeypatch):
    import newsbot.db as dbmod
    from newsbot.app import init_core
    q("UPDATE meta SET value='13' WHERE key='schema_version'", commit=True)
    monkeypatch.setattr(dbmod, "SCHEMA_VERSION", 12)
    conn = getattr(dbmod._tls, "conn", None)
    if conn is not None:
        conn.close()
        dbmod._tls.conn = None
    dbmod._SCHEMA_READY = False
    try:
        with pytest.raises(SystemExit) as exc:
            init_core()
        assert "newer than this build" in str(exc.value)
    finally:
        dbmod._SCHEMA_READY = False
