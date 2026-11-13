# -*- coding: utf-8 -*-
"""یک شکستِ «جبران» در مسیر تأیید پرداخت نباید بی‌صدا بماند (silent except)."""
import pytest

from newsbot import core_payments
from newsbot.core_payments import pay_approve_atomic
from tests import test_d1_payment_path as _d1

d1_pay = _d1.d1_pay   # fixture اشتراکی (D1 ساختگی روی SQLite واقعی)


def test_failed_discount_refund_during_compensation_is_logged(d1_pay, monkeypatch):
    d1_pay.conn.execute("INSERT INTO discounts(code,percent,expires,max_uses,used,active) VALUES('SAVE10',10,NULL,5,0,1)")
    d1_pay.conn.execute("UPDATE pay_requests SET discount='SAVE10' WHERE id=1")
    import newsbot.core_discounts as discmod
    import newsbot.core_plans as plansmod

    def boom(*a, **k):
        raise RuntimeError("crash after the wallet debit")

    def refund_boom(*a, **k):
        raise RuntimeError("refund store unavailable")

    logged = []
    monkeypatch.setattr(plansmod, "_assign_writes", boom)
    monkeypatch.setattr(discmod, "disc_refund", refund_boom)
    monkeypatch.setattr(core_payments, "log_event", lambda level, msg, *a, **k: logged.append((level, msg)))

    with pytest.raises(RuntimeError, match="crash after the wallet debit"):
        pay_approve_atomic(1, by_uid=1)

    assert any(lv == "ERROR" and "discount refund" in m and "rid=1" in m for lv, m in logged), logged
