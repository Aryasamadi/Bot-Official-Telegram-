# -*- coding: utf-8 -*-
"""رگرسیون‌های PH-01 — یکپارچگی مالی (تصمیم‌های مالک Q-1/Q-2/Q-3/Q-4).

پوشش: FIN-001 · FIN-002 · FIN-003 · FIN-004 · FIN-005 · FIN-006 · FIN-007 · FIN-008 · FIN-010 ·
FIN-012 · FIN-013 · FIN-019 · FIN-021 · FIN-023 · UI-017 · TEST-005 · OBS-007
"""
import pytest

from newsbot.db import q
from newsbot.timeutils import now_iso, now_utc
from newsbot.core_plans import (create_plan, get_plan, upgrade_quote, assign_plan,
                                 apply_plan_change, free_claim)
from newsbot.core_payments import (pay_create, pay_get, pay_approve_atomic, refund_payment,
                                   PayQuoteStale, PayDiscountInvalid, PayError)
from newsbot.core_discounts import disc_create, disc_get
from newsbot.core_referrals import record_referral, credit_referral_on_payment
from newsbot.core_wallet import (wallet_get, wallet_total, wallet_rows, wallet_credit,
                                 CUR_TOMAN, CUR_USD, CUR_LEGACY, WalletInsufficient)


# ── ابزار ───────────────────────────────────────────────────────────────────────────
def _mk(uid, lang="fa", credit=0.0, cur=None):
    q("INSERT OR IGNORE INTO users(id,username,name,role,lang,created_at,last_seen) VALUES(?,?,?,?,?,?,?)",
      (uid, f"u{uid}", f"U{uid}", "user", lang, now_iso(), now_iso()), commit=True)
    if credit:
        wallet_credit(uid, cur or (CUR_USD if lang == "en" else CUR_TOMAN), credit, "test", None)
    return uid

def _plan(name, price_num=0.0, price_num_fa=0.0, days=30, is_free=0):
    return create_plan(name=name, name_en=name, days=days, price_num=price_num,
                       price_num_fa=price_num_fa, is_free=is_free, price="x")

def _quote(uid, pid, lang="fa", disc_pct=None):
    return upgrade_quote(uid, get_plan(pid), None, disc_pct, lang=lang)

def _request(uid, pid, lang="fa", disc=None, disc_pct=None):
    """درخواستِ پرداخت دقیقاً مثلِ مسیرِ محصول."""
    qt = _quote(uid, pid, lang, disc_pct)
    rid = pay_create(uid, pid, discount=disc, final_price=str(qt["payable"]), pc=qt, cur=qt.get("cur") or "")
    return rid, qt

def _ledger_sum(uid, cur):
    r = q("SELECT COALESCE(SUM(delta),0) s FROM credit_log WHERE user_id=? AND cur=?", (uid, cur), one=True)
    return round(float(r["s"] or 0), 2)


# ══════════════════════════════════════════════════════════════════════════════════════
# FIN-001 — کیف پولِ واحد‌دار: پاداشِ یک واحد با واحدِ دیگر خرج نمی‌شود
# ══════════════════════════════════════════════════════════════════════════════════════
def test_fin001_reward_in_one_currency_is_not_spendable_in_another():
    """پاداشِ تومانی نباید در برابرِ تعرفهٔ دلاری خرج شود (باگِ بحرانیِ تأییدشده)."""
    ref, payer = 90201, 90202
    _mk(ref, "en"); _mk(payer, "fa")
    # پرداخت‌کنندهٔ فارسی، ۶۰۰٬۰۰۰ تومان می‌پردازد ⇒ پاداشِ ۱۲۰٬۰۰۰ تومانی
    assert record_referral(ref, payer) is True
    assert credit_referral_on_payment(payer, 600000.0, cur=CUR_TOMAN, ref_key="ph01:1") == (ref, 120000.0)
    assert wallet_get(ref, CUR_TOMAN) == 120000.0
    # معرفِ انگلیسی یک پلنِ ۵ دلاری می‌خواهد
    usd = _plan("ph01-usd", price_num=5.0)
    qt = upgrade_quote(ref, get_plan(usd), None, lang="en")
    assert qt["cur"] == CUR_USD
    assert qt["balance"] == 0.0                      # موجودیِ دلاری صفر است
    assert qt["used_balance"] == 0.0                 # پاداشِ تومانی خرج نمی‌شود ⇒
    assert qt["payable"] == 5.0                     # و پلن رایگان به دست نمی‌آید
    # آیا موجودیِ تومانی برای پلنِ تومانی خرج می‌شود؟ بله.
    toman = _plan("ph01-toman", price_num_fa=600000.0)
    qt2 = upgrade_quote(ref, get_plan(toman), None, lang="fa")
    assert qt2["cur"] == CUR_TOMAN and qt2["used_balance"] == 120000.0 and qt2["payable"] == 480000.0


def test_fin001_legacy_bucket_is_frozen_and_not_spendable():
    """موجودیِ قدیمی (واحد نامعلوم) در سطلِ «legacy» منجمد می‌شود: دیده می‌شود، خرج نمی‌شود."""
    from newsbot.core_wallet import wallet_balance
    from newsbot.db import _migrate_legacy_credit, db
    uid = 90203
    _mk(uid, "en")
    q("UPDATE users SET credit=777 WHERE id=?", (uid,), commit=True)
    # شبیه‌سازیِ «پیش از مهاجرت»: کلیدِ یک‌بارمصرف پاک می‌شود
    q("DELETE FROM meta WHERE key='legacy_wallet_v1'", commit=True)
    _migrate_legacy_credit(db())
    assert wallet_balance(uid, CUR_LEGACY) == 777.0
    assert wallet_get(uid, CUR_USD) == 0.0 and wallet_get(uid, CUR_TOMAN) == 0.0
    pid = _plan("ph01-legacy-usd", price_num=5.0)
    qt = upgrade_quote(uid, get_plan(pid), None, lang="en")
    assert qt["used_balance"] == 0.0 and qt["payable"] == 5.0


def test_legacy_migration_is_idempotent():
    """اجرای دوبارهٔ مهاجرتِ موجودیِ قدیمی نباید چیزی را دوباره اضافه کند."""
    from newsbot.core_wallet import wallet_balance
    from newsbot.db import _migrate_legacy_credit, db
    uid = 90204
    _mk(uid, "en")
    q("UPDATE users SET credit=555 WHERE id=?", (uid,), commit=True)
    q("DELETE FROM meta WHERE key='legacy_wallet_v1'", commit=True)
    _migrate_legacy_credit(db())
    assert wallet_balance(uid, CUR_LEGACY) == 555.0
    q("DELETE FROM meta WHERE key='legacy_wallet_v1'", commit=True)     # شبیه‌سازیِ اجرای دوباره
    _migrate_legacy_credit(db())
    assert wallet_balance(uid, CUR_LEGACY) == 555.0, "نباید دوباره اضافه شود"


# ══════════════════════════════════════════════════════════════════════════════════════
# TEST-005 / FIN-006 — دفترِ کیف پول باید با موجودی برابر باشد
# ══════════════════════════════════════════════════════════════════════════════════════
def test_ledger_reconciles_with_wallet_after_many_movements():
    """جمعِ رسیدها همیشه برابرِ موجودی است (حرکت‌های پولیِ کوچک هم)."""
    uid = 90205
    _mk(uid, "en")
    wallet_credit(uid, CUR_USD, 5.0, "seed", None)
    pid = _plan("ph01-ledger", price_num=100.0)
    for i in range(12):
        used = round(0.01 * (i + 1), 2)
        apply_plan_change(uid, pid, {"kind": "fresh", "used_balance": used, "surplus": 0.0,
                                     "payable": 100.0, "rem_days": 1, "cur": CUR_USD})
    wallet_credit(uid, CUR_USD, 0.03, "test", None)     # حرکتِ کوچکِ دیگر
    assert wallet_get(uid, CUR_USD) == _ledger_sum(uid, CUR_USD)
    rows = q("SELECT delta, balance_after FROM credit_log WHERE user_id=? ORDER BY id", (uid,))
    prev = 0.0
    for r in rows:
        assert abs(round(prev + float(r["delta"]), 2) - float(r["balance_after"])) < 1e-9, \
            "balance_after باید دقیقاً موجودیِ ذخیره‌شده باشد (FIN-006)"
        prev = float(r["balance_after"])


def test_fin006_receipt_balance_always_equals_the_stored_wallet():
    """رسید و موجودیِ ذخیره‌شده همیشه یکی‌اند؛ حرکتی که گردشده‌اش صفر است اثری ندارد."""
    uid = 90206
    _mk(uid, "en")
    wallet_credit(uid, CUR_USD, 10.0, "seed", None)
    pid = _plan("ph01-zero", price_num=100.0)
    n0 = q("SELECT COUNT(*) c FROM credit_log WHERE user_id=?", (uid,), one=True)["c"]
    apply_plan_change(uid, pid, {"kind": "fresh", "used_balance": 0.0, "surplus": 0.0,
                                 "payable": 100.0, "rem_days": 1, "cur": CUR_USD})
    n1 = q("SELECT COUNT(*) c FROM credit_log WHERE user_id=?", (uid,), one=True)["c"]
    assert n1 == n0, "حرکتِ بدونِ تغییر نباید رسید بسازد"
    # حرکتِ کوچکی که گردشده‌اش صفر است ⇒ نه موجودی می‌جنبد، نه رسیدی ساخته می‌شود
    apply_plan_change(uid, pid, {"kind": "fresh", "used_balance": 0.001, "surplus": 0.0,
                                 "payable": 100.0, "rem_days": 1, "cur": CUR_USD})
    n2 = q("SELECT COUNT(*) c FROM credit_log WHERE user_id=?", (uid,), one=True)["c"]
    assert n2 == n0 and wallet_get(uid, CUR_USD) == 10.0
    # و هر حرکتِ واقعی دقیقاً یک رسید با موجودیِ درست می‌سازد
    apply_plan_change(uid, pid, {"kind": "fresh", "used_balance": 0.03, "surplus": 0.0,
                                 "payable": 100.0, "rem_days": 1, "cur": CUR_USD})
    row = q("SELECT delta, balance_after FROM credit_log WHERE user_id=? ORDER BY id DESC LIMIT 1", (uid,), one=True)
    assert (float(row["delta"]), float(row["balance_after"])) == (-0.03, 9.97)


# ══════════════════════════════════════════════════════════════════════════════════════
# FIN-002 / FIN-003 / FIN-019 — تأیید فقط وقتی مبلغِ نمایش‌داده‌شده معتبر است
# ══════════════════════════════════════════════════════════════════════════════════════
def test_fin002_material_wallet_change_refuses_approval():
    uid = 90210
    _mk(uid, "fa", credit=50.0)
    pid = _plan("ph01-refuse", price_num_fa=100.0)
    rid, qt = _request(uid, pid, "fa")
    assert qt["payable"] == 50.0
    from newsbot.core_wallet import wallet_debit
    wallet_debit(uid, CUR_TOMAN, 20.0, "test", None)     # موجودی کم شد ⇒ مبلغ عوض می‌شود
    with pytest.raises(PayQuoteStale):
        pay_approve_atomic(rid)
    assert pay_get(rid)["status"] == "pending"
    assert q("SELECT plan_id FROM users WHERE id=?", (uid,), one=True)["plan_id"] is None


def test_fin003_second_pending_request_is_refused_after_first_approved():
    """دو درخواستِ هم‌زمان: بعد از تأییدِ اول، دومی دیگر معتبر نیست ⇒ تأیید نمی‌شود."""
    uid = 90211
    _mk(uid, "fa")
    p1 = _plan("ph01-two-a", price_num_fa=100.0)
    p2 = _plan("ph01-two-b", price_num_fa=200.0)
    rid1, qt1 = _request(uid, p1, "fa")
    rid2, qt2 = _request(uid, p2, "fa")
    pay_approve_atomic(rid1)
    with pytest.raises(PayError):
        pay_approve_atomic(rid2)
    assert pay_get(rid2)["status"] == "pending"
    u = q("SELECT plan_id FROM users WHERE id=?", (uid,), one=True)
    assert u["plan_id"] == p1                     # پلنِ اول سرِ جایش مانده


def test_fin019_downgrade_is_rechecked_at_approval():
    """اگر تعرفه عوض شود و خرید به «نزول» تبدیل شود، تأیید باید متوقف شود."""
    uid = 90212
    _mk(uid, "fa")
    cheap = _plan("ph01-cheap", price_num_fa=100.0)
    pricey = _plan("ph01-pricey", price_num_fa=500.0)
    assign_plan(uid, pricey)
    # quote روی پلنِ گران گرفته می‌شود تا «ارتقا» باشد
    qt = upgrade_quote(uid, get_plan(pricey), get_plan(pricey), lang="fa")
    rid = pay_create(uid, pricey, final_price=str(qt["payable"]), pc=qt, cur=qt.get("cur") or "")
    # تعرفه‌ی پلنِ فعلی را پایین می‌آوریم ⇒ نرخِ روزانهٔ «پلنِ جدید» کمتر می‌شود ⇒ نزول
    q("UPDATE plans SET price_num_fa=1 WHERE id=?", (pricey,), commit=True)
    with pytest.raises(PayError):
        pay_approve_atomic(rid)
    assert pay_get(rid)["status"] == "pending"


# ══════════════════════════════════════════════════════════════════════════════════════
# FIN-004 — مبلغِ نمایش‌داده‌شده = مبلغِ دریافتی
# ══════════════════════════════════════════════════════════════════════════════════════
def test_fin004_displayed_price_equals_charged_price_with_wallet_and_discount():
    """با اعتبار و تخفیف هم، مبلغِ قیمت‌گیری = مبلغِ ذخیره‌شده در رسید."""
    uid = 90220
    _mk(uid, "fa", credit=500000.0)
    pid = _plan("ph01-disc", price_num_fa=3000000.0)
    code = disc_create("PH01D10", 10, (now_utc() + __import__("datetime").timedelta(days=1)).isoformat(), max_uses=5)
    d = disc_get(code); assert d and d["percent"] == 10
    rid, qt = _request(uid, pid, "fa", disc=code, disc_pct=10)
    gross = 3000000.0
    assert qt["new_cost"] == round(gross * 0.9, 2)          # تخفیف روی ناخالص
    assert qt["used_balance"] + qt["payable"] == qt["new_cost"]   # جمع = ناخالصِ تخفیف‌خورده
    assert pay_get(rid)["final_price"] == str(qt["payable"])     # همان عدد روی رسید
    pay_approve_atomic(rid)
    assert wallet_get(uid, CUR_TOMAN) == 0.0                   # اعتبار کامل مصرف شد
    assert pay_get(rid)["status"] == "approved"


# ══════════════════════════════════════════════════════════════════════════════════════
# FIN-005 — کاربرِ پولی نمی‌تواند پلنِ رایگان را بگیرد و اعتبارش را از دست بدهد
# ══════════════════════════════════════════════════════════════════════════════════════
def test_fin005_free_plan_cannot_replace_an_active_paid_plan():
    uid = 90230
    _mk(uid, "fa")
    paid = _plan("ph01-paid", price_num_fa=1000.0)
    free = _plan("ph01-free", is_free=1, days=7)
    assign_plan(uid, paid)
    before = q("SELECT plan_id, free_used FROM users WHERE id=?", (uid,), one=True)
    assert before["plan_id"] == paid
    # free_claim اجازه می‌دهد، ولی assign_plan نباید پلنِ پولیِ فعال را جابه‌جا کند
    from newsbot.core_plans import _assign_state
    st = _assign_state(uid, free)
    assert st is not None
    assert st["active"] is True, "پلنِ پولیِ فعال باید «فعال» تشخیص داده شود"


# ══════════════════════════════════════════════════════════════════════════════════════
# FIN-008 — کدِ تخفیفِ غیرفعال/منقضی در زمانِ تأیید پذیرفته نمی‌شود
# ══════════════════════════════════════════════════════════════════════════════════════
def test_fin008_deactivated_discount_is_not_honoured_at_approval():
    uid = 90240
    _mk(uid, "fa")
    pid = _plan("ph01-d8", price_num_fa=1000.0)
    code = disc_create("PH01OFF", 20, (now_utc() + __import__("datetime").timedelta(days=1)).isoformat(), max_uses=5)
    rid, qt = _request(uid, pid, "fa", disc=code, disc_pct=20)
    q("UPDATE discounts SET active=0 WHERE code=?", (code,), commit=True)   # لغوِ کد پیش از تأیید
    with pytest.raises(PayError) as ei:
        pay_approve_atomic(rid)
    assert ei.value.code in ("disc_invalid", "disc_exhausted")
    assert pay_get(rid)["status"] == "pending"
    assert disc_get(code)["used"] == 0


# ══════════════════════════════════════════════════════════════════════════════════════
# FIN-010 — سقفِ ارجاع به تفکیکِ واحد و اتمیک
# ══════════════════════════════════════════════════════════════════════════════════════
def test_fin010_referral_cap_is_per_currency():
    """سقفِ مبلغیِ معرف در هر واحد جداگانه اعمال می‌شود (تومان و دلار با هم جمع نمی‌شوند)."""
    from newsbot.core_referrals import referral_cfg, set_referral_cfg
    ref, p1, p2 = 90250, 90251, 90252
    _mk(ref, "fa"); _mk(p1, "fa"); _mk(p2, "en")
    assert record_referral(ref, p1) is True
    assert record_referral(ref, p2) is True
    p0, c0 = referral_cfg()
    try:
        set_referral_cfg(percent=10, cap_amount=10.0)     # سقف: ۱۰ واحد در هر واحدِ پول
        assert credit_referral_on_payment(p1, 100.0, cur=CUR_TOMAN, ref_key="ph01:cap1") == (ref, 10.0)
        # واحدِ دیگر ⇒ سقفِ مستقل ⇒ دوباره پاداش می‌دهد
        assert credit_referral_on_payment(p2, 100.0, cur=CUR_USD, ref_key="ph01:cap2") == (ref, 10.0)
        assert wallet_get(ref, CUR_TOMAN) == 10.0 and wallet_get(ref, CUR_USD) == 10.0
        # سقفِ همان واحد دوباره ⇒ دیگر پاداشی نمی‌دهد
        assert credit_referral_on_payment(p1, 100.0, cur=CUR_TOMAN, ref_key="ph01:cap3") is None
    finally:
        set_referral_cfg(percent=p0, cap_amount=c0)


# ══════════════════════════════════════════════════════════════════════════════════════
# FIN-012 — تعرفهٔ نامتناهی نباید کیف پول را آلوده کند
# ══════════════════════════════════════════════════════════════════════════════════════
def test_fin012_non_finite_tariff_is_ignored():
    """تعرفهٔ inf/نان ⇒ قیمتِ عددی وجود ندارد (مسیرِ متنی) و هرگز وارد کیف پول نمی‌شود."""
    from newsbot.core_plans import tariff
    pid = _plan("ph01-inf", price_num_fa=0.0)
    q("UPDATE plans SET price_num_fa=? WHERE id=?", (float("inf"), pid), commit=True)
    amt, cur = tariff(get_plan(pid), "fa")
    assert amt == 0.0 and cur == "", "تعرفهٔ نامتناهی باید نادیده گرفته شود (نه اینکه کیف پول را بسوزاند)"
    uid = 90260
    _mk(uid, "fa")
    qt = upgrade_quote(uid, get_plan(pid), None, lang="fa")
    assert qt["payable_num"] is False


# ══════════════════════════════════════════════════════════════════════════════════════
# FIN-013 / OBS-007 — بازگشتِ وجه + ردیابیِ پرداخت
# ══════════════════════════════════════════════════════════════════════════════════════
def test_fin013_refund_restores_wallet_and_writes_receipt():
    uid = 90270
    _mk(uid, "fa", credit=1000.0)
    pid = _plan("ph01-refund", price_num_fa=5000.0)
    rid, qt = _request(uid, pid, "fa")
    pay_approve_atomic(rid)
    assert wallet_get(uid, CUR_TOMAN) == 0.0
    assert refund_payment(rid) is True
    assert wallet_get(uid, CUR_TOMAN) == 1000.0
    assert _ledger_sum(uid, CUR_TOMAN) == wallet_get(uid, CUR_TOMAN)
    row = q("SELECT reason, ref FROM credit_log WHERE user_id=? ORDER BY id DESC LIMIT 1", (uid,), one=True)
    assert row["reason"] == "refund" and row["ref"] == f"pay:{rid}"


def test_obs007_wallet_receipt_is_joinable_to_the_payment():
    """هر حرکتِ کیف پول به پرداختِ خودش وصل است (credit_log.ref = pay:<id>)."""
    uid = 90271
    _mk(uid, "fa", credit=1000.0)
    pid = _plan("ph01-join", price_num_fa=5000.0)
    rid, qt = _request(uid, pid, "fa")
    pay_approve_atomic(rid)
    row = q("SELECT ref FROM credit_log WHERE user_id=? AND reason='plan_change' ORDER BY id DESC LIMIT 1", (uid,), one=True)
    assert row and row["ref"] == f"pay:{rid}"
    ch = q("SELECT ref FROM plan_changes WHERE user_id=? ORDER BY id DESC LIMIT 1", (uid,), one=True)
    assert ch and ch["ref"] == f"pay:{rid}"


# ══════════════════════════════════════════════════════════════════════════════════════
# FIN-007 — سابقهٔ خرید همیشه ثبت می‌شود
# ══════════════════════════════════════════════════════════════════════════════════════
def test_fin007_every_approved_payment_has_a_history_row():
    uid = 90280
    _mk(uid, "fa")
    pid = _plan("ph01-hist", price_num_fa=1000.0)
    rid, qt = _request(uid, pid, "fa")
    pay_approve_atomic(rid)
    n = q("SELECT COUNT(*) c FROM plan_changes WHERE user_id=?", (uid,), one=True)["c"]
    assert n == 1, "هر خریدِ تأییدشده باید یک ردیفِ سابقه داشته باشد (FIN-007)"


# ══════════════════════════════════════════════════════════════════════════════════════
# UI-017 / Q-4c — پلنِ بدونِ تعرفهٔ عددی آنلاین خریداری نمی‌شود
# ══════════════════════════════════════════════════════════════════════════════════════
def test_ui017_plan_without_numeric_tariff_has_no_numeric_quote():
    """پلنِ مذاکره‌ای قیمتِ عددی ندارد ⇒ quote عددی نمی‌دهد ⇒ مسیرِ خریدِ آنلاین بسته می‌ماند."""
    uid = 90290
    _mk(uid, "fa")
    negotiable = _plan("ph01-negotiable", price_num_fa=0.0, price_num=0.0)
    qt = upgrade_quote(uid, get_plan(negotiable), None, lang="fa")
    assert qt["payable_num"] is False and qt["payable"] == 0.0


# ══════════════════════════════════════════════════════════════════════════════════════
# FIN-021 — اعلامِ نتیجه به مشتری قابلِ ردیابی است
# ══════════════════════════════════════════════════════════════════════════════════════
def test_fin021_payment_notification_state_is_recorded():
    from newsbot.core_payments import pay_mark_notified, pay_unnotified
    uid = 90300
    _mk(uid, "fa")
    pid = _plan("ph01-notify", price_num_fa=1000.0)
    rid, qt = _request(uid, pid, "fa")
    pay_approve_atomic(rid)
    assert any(r["id"] == rid for r in pay_unnotified())
    pay_mark_notified(rid)
    assert not any(r["id"] == rid for r in pay_unnotified())
    assert pay_get(rid)["notified_at"]
