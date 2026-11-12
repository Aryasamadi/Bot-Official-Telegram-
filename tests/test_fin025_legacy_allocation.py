# -*- coding: utf-8 -*-
"""FIN-025 / FIN-024: تخصیصِ صریح و اتمیکِ سطلِ منجمدِ legacy به واحدِ قابل‌خرج + تطبیقِ دفتر."""
import importlib.util
import os

import pytest

from newsbot.core_users import ensure_user
from newsbot.core_wallet import (
    CUR_LEGACY,
    CUR_TOMAN,
    CUR_USD,
    WalletError,
    wallet_allocate_legacy,
    wallet_balance,
    wallet_credit,
    wallet_get,
)
from newsbot.db import _migrate_legacy_credit, q
from newsbot.timeutils import now_iso

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location("allocate_legacy_wallet", os.path.join(ROOT, "tools", "allocate_legacy_wallet.py"))
alw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(alw)


def _seed_legacy(uid: int, amt: float):
    ensure_user(uid, f"u{uid}", f"User {uid}")
    q("INSERT OR REPLACE INTO wallet(user_id,cur,amount,updated_at) VALUES(?,?,?,?)",
      (uid, CUR_LEGACY, amt, now_iso()), commit=True)


def test_allocate_legacy_moves_balance_atomically_and_writes_receipts():
    uid = 930001
    _seed_legacy(uid, 250000.0)
    wallet_credit(uid, CUR_TOMAN, 50000.0, "seed", "r0")

    moved = wallet_allocate_legacy(uid, CUR_TOMAN, ref="ops-ticket-1")
    assert moved == 250000.0
    assert wallet_balance(uid, CUR_LEGACY) == 0.0
    assert wallet_get(uid, CUR_TOMAN) == 300000.0

    # اجرای دوباره idempotent است و هیچ رسیدی اضافه نمی‌کند
    assert wallet_allocate_legacy(uid, CUR_TOMAN, ref="ops-ticket-1") == 0.0
    logs = q("SELECT reason, delta, balance_after, cur FROM credit_log WHERE user_id=? ORDER BY id", (uid,))
    reasons = [(r["reason"], r["cur"], float(r["delta"]), float(r["balance_after"])) for r in logs]
    assert ("legacy_allocate_out", CUR_LEGACY, -250000.0, 0.0) in reasons
    assert ("legacy_allocate", CUR_TOMAN, 250000.0, 300000.0) in reasons


def test_allocate_legacy_refuses_non_spendable_currency():
    uid = 930002
    _seed_legacy(uid, 100.0)
    with pytest.raises(WalletError):
        wallet_allocate_legacy(uid, CUR_LEGACY)
    with pytest.raises(WalletError):
        wallet_allocate_legacy(uid, "eur")
    assert wallet_balance(uid, CUR_LEGACY) == 100.0


def test_cli_requires_confirm_and_supports_dry_run():
    uid = 930003
    _seed_legacy(uid, 42.5)
    # بدون --confirm رد می‌شود
    assert alw.main(["--user", str(uid), "--cur", "usd"]) == 2
    assert wallet_balance(uid, CUR_LEGACY) == 42.5

    # dry-run هیچ چیزی را تغییر نمی‌دهد
    assert alw.main(["--user", str(uid), "--cur", "usd", "--dry-run"]) == 0
    assert wallet_balance(uid, CUR_LEGACY) == 42.5
    assert wallet_get(uid, CUR_USD) == 0.0

    # اجرای واقعی با --confirm
    assert alw.main(["--user", str(uid), "--cur", "usd", "--confirm"]) == 0
    assert wallet_balance(uid, CUR_LEGACY) == 0.0
    assert wallet_get(uid, CUR_USD) == 42.5


def test_end_to_end_migration_then_authorized_allocation():
    uid = 930004
    ensure_user(uid, "u930004", "U4")
    q("UPDATE users SET credit=180000 WHERE id=?", (uid,), commit=True)
    q("DELETE FROM meta WHERE key='legacy_wallet_v1'", commit=True)
    from newsbot.db import _trx
    _trx(_migrate_legacy_credit)
    assert wallet_balance(uid, CUR_LEGACY) == 180000.0
    assert wallet_get(uid, CUR_TOMAN) == 0.0

    res = alw.run_allocation(uid=uid, cur=CUR_TOMAN, dry_run=False, ref="mig-e2e")
    assert res["count"] == 1 and res["items"][0]["amount"] == 180000.0
    assert wallet_get(uid, CUR_TOMAN) == 180000.0
    assert wallet_balance(uid, CUR_LEGACY) == 0.0
