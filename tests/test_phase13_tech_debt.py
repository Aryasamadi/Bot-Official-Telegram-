# -*- coding: utf-8 -*-
"""فاز ۱۳ — بدهی فنی بلندمدت (QA-05/QA-08/BIZ-09/ARCH-09).

* QA-05: اعتبارسنجی ورودی در مرزها — بازهٔ دقیق تنظیم ارجاع، تنظیمات خراب ⇒ fallback،
  تخمین ارتقای مرزی (پلن منقضی + موجودی کیف پول) و محافظ ستون‌های SQL.
* QA-08: اجرای واقعی بنچمارک بار روی دیتابیس موقت (ساب‌پروسس) و ساختار گزارش JSON.
* BIZ-09: رسید credit_log برای هر شارژ کیف پول (trigger ارجاع + تغییر پلن + نبودِ رسید برای دلتای صفر).
* ARCH-09: صف محدود + ری‌ترای اعلان سوپر (موفقیت پس از شکست، دراپ در سرریز، fallback بدون worker، سیم‌کشی اپ).
"""
import asyncio, json, os, subprocess, sys
from pathlib import Path

import pytest

from newsbot.db import q
from newsbot.timeutils import now_iso

ROOT = Path(__file__).resolve().parent.parent


def _src(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def _mk_user(uid):
    q("INSERT OR IGNORE INTO users(id,username,name,role,created_at,last_seen) VALUES(?,?,?,?,?,?)",
      (uid, f"u{uid}", f"u{uid}", "user", now_iso(), now_iso()), commit=True)


def _wallet(uid):
    """موجودیِ قابل‌خرجِ کیف پول (PH-01: جدولِ wallet به تفکیکِ واحدِ پول)."""
    from newsbot.core_wallet import wallet_total
    return wallet_total(uid)


# ════════════════════════════ QA-05 — مرزهای اعتبارسنجی ورودی ════════════════════════════

def test_referral_cfg_exact_bounds():
    """درصد پاداش دقیقاً در بازهٔ بستهٔ [۰، ۵۰] پذیرفته و بیرون آن رد می‌شود؛ ارقام فارسی و جداکنندهٔ هزارگان هم پذیرفته‌اند."""
    from newsbot.core_referrals import referral_cfg, set_referral_cfg
    p0, c0 = referral_cfg()
    try:
        assert set_referral_cfg(percent=0) == (0.0, c0), "مرز پایین باید پذیرفته شود"
        assert set_referral_cfg(percent=50)[0] == 50.0, "مرز بالا باید پذیرفته شود"
        assert set_referral_cfg(percent="۵۰")[0] == 50.0, "ارقام فارسی باید ترجمه شوند"
        for bad in ("50.01", "-0.01", "abc"):
            with pytest.raises(ValueError):
                set_referral_cfg(percent=bad)
        assert set_referral_cfg(cap_amount="1,000")[1] == 1000.0
        assert set_referral_cfg(cap_amount=0)[1] == 0.0, "سقف ۰ = نامحدود"
        with pytest.raises(ValueError):
            set_referral_cfg(cap_amount=-1)
    finally:
        set_referral_cfg(percent=p0, cap_amount=c0)


def test_referral_cfg_garbage_settings_falls_back_and_clamps():
    """مقدار خراب در settings نباید کرش کند (fallback به config) و مقدار بیرون‌بازه در خواندن کلمپ می‌شود."""
    from newsbot import config
    from newsbot.core_referrals import REF_SET_CAP, REF_SET_PERCENT, referral_cfg
    from newsbot.db import gget, gset

    def _restore(key, val):
        if val is None:
            q("DELETE FROM settings WHERE key=?", (key,), commit=True)
        else:
            gset(key, val)

    old_p, old_c = gget(REF_SET_PERCENT), gget(REF_SET_CAP)
    try:
        gset(REF_SET_PERCENT, "xx"); gset(REF_SET_CAP, "yy")
        pct, cap = referral_cfg()
        assert pct == float(config.REFERRAL_PERCENT), "مقدار خراب باید به پیش‌فرض config برگردد"
        assert cap == 0.0
        gset(REF_SET_PERCENT, 999); gset(REF_SET_CAP, -5)
        pct, cap = referral_cfg()
        assert pct == 50.0 and cap == 0.0, "خواندن هم باید مرزها را کلمپ کند"
    finally:
        _restore(REF_SET_PERCENT, old_p); _restore(REF_SET_CAP, old_c)


def test_upgrade_quote_expired_plan_is_fresh_with_wallet():
    """پلن منقضی ⇒ شاخهٔ fresh (بدون proration) و موجودی کیف پول از مبلغ کسر می‌شود."""
    from newsbot.core_plans import assign_plan, create_plan, get_plan, upgrade_quote
    uid = 88311; _mk_user(uid)
    pid_old = create_plan(name="q13-old", days=30, price_num=60, daily_posts=1, daily_tests=1, max_sources=1, max_channels=1)
    pid_new = create_plan(name="q13-new", days=30, price_num=100, daily_posts=1, daily_tests=1, max_sources=1, max_channels=1)
    assign_plan(uid, pid_old)
    q("UPDATE users SET plan_expires=? WHERE id=?", ("2020-01-01T00:00:00+00:00", uid), commit=True)
    from newsbot.core_wallet import wallet_credit; wallet_credit(uid, "usd", 10, "test", None)
    qt = upgrade_quote(uid, get_plan(pid_new), get_plan(pid_old), lang="en")
    assert qt["kind"] == "fresh" and qt["rem_days"] == 0.0
    assert qt["new_cost"] == 100.0 and qt["used_balance"] == 10.0 and qt["payable"] == 90.0
    assert qt["keep_expiry"] is False


def test_safe_fields_rejects_unknown_columns():
    """نام ستونِ SQL فقط از ستون‌های واقعی جدول پذیرفته می‌شود (ورودی کاربر هرگز به SQL خام نمی‌رسد)."""
    from newsbot.db import _safe_fields
    assert _safe_fields("users", {"id": 1, "credit": 2}) == {"id": 1, "credit": 2}
    with pytest.raises(ValueError):
        _safe_fields("users", {"id": 1, "credit; DROP TABLE users": 2})
    with pytest.raises(ValueError):
        _safe_fields("users", {"nope": 1})


# ════════════════════════════ QA-08 — بنچمارک بار ════════════════════════════

def test_benchmark_script_runs_and_reports_json(tmp_path):
    """بنچمارک روی دیتابیس موقت واقعاً اجرا می‌شود و گزارش JSON با ساختار کامل برمی‌گرداند."""
    out = tmp_path / "bench.json"
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "benchmark.py"), "--ops", "40", "--json", str(out)],
                       cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=180, env=env)
    assert r.returncode == 0, ((r.stdout or "")[-500:] + (r.stderr or "")[-800:])
    rep = json.loads(out.read_text(encoding="utf-8"))
    assert rep["ops"] == 40 and rep["python"] and rep["sqlite"]
    for name in ("users_insert", "users_select", "articles_insert", "referral_credit_trx"):
        b = rep["blocks"][name]
        assert b["per_sec"] and b["per_sec"] > 0, f"بلوک {name} باید نرخ مثبت داشته باشد"
    assert rep["blocks"]["users_insert"]["n"] == 40
    assert rep["blocks"]["referral_credit_trx"]["n"] == 8, "یک‌پنجم عملیات برای مسیر تراکنشی"


# ════════════════════════════ BIZ-09 — رسید credit_log ════════════════════════════

def test_referral_credit_writes_atomic_receipt():
    """شارژ کیف پول معرف (مسیر trigger) باید ردیف رسید با delta و موجودی پس از حرکت ثبت کند."""
    from newsbot.core_referrals import (credit_referral_on_payment, record_referral,
                                        referral_cfg, set_referral_cfg)
    r, b = 88301, 88302
    _mk_user(r); _mk_user(b)
    q("DELETE FROM referrals WHERE referred_id=?", (b,), commit=True)
    q("DELETE FROM credit_log WHERE user_id=?", (r,), commit=True)
    assert record_referral(r, b) is True
    p0, c0 = referral_cfg()
    try:
        set_referral_cfg(percent=10, cap_amount=0)
        assert credit_referral_on_payment(b, 100.0, ref_key="p13:recv:1") == (r, 10.0)
        row = q("SELECT * FROM credit_log WHERE user_id=? ORDER BY id DESC LIMIT 1", (r,), one=True)
        assert row is not None, "شارژ کیف پول باید رسید credit_log داشته باشد"
        assert (row["delta"], row["balance_after"], row["reason"], row["ref"]) == (10.0, _wallet(r), "referral", str(b))
    finally:
        set_referral_cfg(percent=p0, cap_amount=c0)


def test_plan_change_writes_receipt_and_skips_zero_delta():
    """مصرف/برگشت کیف پول در تغییر پلن رسید دارد؛ دلتای صفر رسید نمی‌سازد."""
    from newsbot.core_plans import apply_plan_change, create_plan
    uid = 88303; _mk_user(uid)
    pid = create_plan(name="recv13-p", days=30, daily_posts=1, daily_tests=1, max_sources=1, max_channels=1)
    from newsbot.core_wallet import wallet_credit; wallet_credit(uid, "usd", 20, "test", None)
    q("DELETE FROM credit_log WHERE user_id=?", (uid,), commit=True)

    apply_plan_change(uid, pid, {"kind": "upgrade", "from_plan": None, "used_balance": 5, "surplus": 0, "payable": 1, "rem_days": 1, "cur": "usd"})
    assert _wallet(uid) == 15.0
    row = q("SELECT * FROM credit_log WHERE user_id=? ORDER BY id DESC LIMIT 1", (uid,), one=True)
    assert (row["delta"], row["balance_after"], row["reason"], row["ref"]) == (-5.0, 15.0, "plan_change", str(pid))

    apply_plan_change(uid, pid, {"kind": "downgrade", "from_plan": None, "used_balance": 0, "surplus": 25, "payable": 0, "rem_days": 1, "cur": "usd"})
    assert _wallet(uid) == 40.0
    row2 = q("SELECT * FROM credit_log WHERE user_id=? ORDER BY id DESC LIMIT 1", (uid,), one=True)
    assert (row2["delta"], row2["balance_after"]) == (25.0, 40.0)

    n = q("SELECT COUNT(*) c FROM credit_log WHERE user_id=?", (uid,), one=True)["c"]
    apply_plan_change(uid, pid, {"kind": "noop", "from_plan": None, "used_balance": 0, "surplus": 0, "cur": "usd"})
    assert q("SELECT COUNT(*) c FROM credit_log WHERE user_id=?", (uid,), one=True)["c"] == n, "دلتای صفر نباید رسید بسازد"
    # FIN-007: سابقهٔ تغییر پلن همیشه ثبت می‌شود (حتی بدونِ حرکتِ کیف پول)
    assert q("SELECT COUNT(*) c FROM plan_changes WHERE user_id=?", (uid,), one=True)["c"] >= 3


def test_v6_schema_surface_and_migration():
    """v6: trigger سازگار با v5 + رسید داخل trigger + انتقال credit_log در مایگریشن D1.
    (فاز ۱۴: نسخهٔ کل به ۷ رفت — این تست سطح v6 را همچنان پین می‌کند.)"""
    import newsbot
    from newsbot.db import REFERRAL_CREDIT_TRIGGER
    from tools.migrate_sqlite_to_d1 import TABLES
    assert newsbot.SCHEMA_VERSION == 9, "PH-03: پایداریِ اعلان ⇒ نسخه ۹"
    assert "INSERT INTO credit_log" in REFERRAL_CREDIT_TRIGGER and "'referral'" in REFERRAL_CREDIT_TRIGGER
    assert "AFTER UPDATE OF credit_amount ON referrals" in REFERRAL_CREDIT_TRIGGER   # سازگاری v5⇒v6
    assert "DROP TRIGGER IF EXISTS trg_referral_credit_wallet" in _src("newsbot/db.py")
    assert "credit_log" in TABLES, "مایگریشن D1 باید جدول رسیدها را هم منتقل کند"


# ════════════════════════════ ARCH-09 — صف و ری‌ترای اعلان سوپر ════════════════════════════

def test_super_queue_retries_then_succeeds(monkeypatch):
    """دو شکست پیاپی سپس موفقیت: worker باید با فاصله‌های ری‌ترای دوباره تلاش کند و متن صف redact باشد."""
    from newsbot import notify, runtime
    from newsbot.logutils import redact
    calls, state = [], {"fail": 2}

    async def fake_super(text, kb=None):
        calls.append(text)
        if state["fail"] > 0:
            state["fail"] -= 1
            raise RuntimeError("boom")

    monkeypatch.setattr(runtime, "NOTIFY_SUPER", fake_super)
    monkeypatch.setattr(notify, "SUPER_RETRY_DELAYS", (0.0, 0.0))
    monkeypatch.setattr(notify, "_super_q", None)
    monkeypatch.setattr(notify, "_super_worker", None)
    text = "خطا: توکن 123456789:AAsecret0000000000000000 لو رفت"

    async def scenario():
        await notify.stop_super_worker()
        notify.ensure_super_worker()
        await notify.notify_super(text)
        await notify.drain_super_queue(timeout=5)
        await notify.stop_super_worker()

    asyncio.run(scenario())
    assert len(calls) == 3, "۲ شکست + ۱ موفقیت = ۳ تلاش"
    assert calls[-1] == redact(text) and ":AAsecret" not in calls[-1], "متن صف باید redact شده باشد"


def test_super_queue_drops_when_full(monkeypatch):
    """سرریز صف: پیام جدید دراپ می‌شود و مسیر فراخوانی هرگز بلاک نمی‌ماند."""
    from newsbot import notify, runtime

    async def fake_super(text, kb=None):
        raise AssertionError("با worker فعال نباید مستقیم ارسال شود")

    monkeypatch.setattr(runtime, "NOTIFY_SUPER", fake_super)
    monkeypatch.setattr(notify, "_super_q", None)
    monkeypatch.setattr(notify, "_super_worker", None)

    async def scenario():
        q_ = asyncio.Queue(maxsize=1)
        notify._super_q = q_
        notify._super_worker = asyncio.get_running_loop().create_task(asyncio.sleep(60))
        q_.put_nowait(("pre", None))
        await notify.notify_super("پیام جدید")
        assert q_.qsize() == 1, "در سرریز پیام جدید باید دراپ شود و صف بلاک نماند"
        assert q_.get_nowait()[0] == "pre"
        await notify.stop_super_worker()

    asyncio.run(scenario())


def test_super_worker_exhausts_retries_and_stays_alive(monkeypatch):
    """شکست دائمی: دقیقاً به تعداد تلاش‌ها تلاش می‌شود و worker برای پیام‌های بعدی زنده می‌ماند."""
    from newsbot import notify, runtime
    calls = []

    async def always_fail(text, kb=None):
        calls.append(text)
        raise RuntimeError("down")

    monkeypatch.setattr(runtime, "NOTIFY_SUPER", always_fail)
    monkeypatch.setattr(notify, "SUPER_RETRY_DELAYS", (0.0, 0.0))
    monkeypatch.setattr(notify, "_super_q", None)
    monkeypatch.setattr(notify, "_super_worker", None)

    async def scenario():
        notify.ensure_super_worker()
        await notify.notify_super("متن")
        await notify.drain_super_queue(timeout=5)
        t = notify._super_worker
        assert t is not None and not t.done(), "worker باید پس از شکست زنده بماند"
        await notify.stop_super_worker()

    asyncio.run(scenario())
    assert len(calls) == 3, "یک تلاش اول + دو ری‌ترای"


def test_notify_super_direct_fallback_without_worker(monkeypatch):
    """بدون worker (خارج از اپلیکیشن): رفتار قبلی تک‌شات مستقیم حفظ می‌شود و صفی ساخته نمی‌شود."""
    from newsbot import notify, runtime
    calls = []

    async def fake(text, kb=None):
        calls.append(text)

    monkeypatch.setattr(runtime, "NOTIFY_SUPER", fake)
    monkeypatch.setattr(notify, "_super_worker", None)
    monkeypatch.setattr(notify, "_super_q", None)

    async def scenario():
        await notify.notify_super("مستقیم")
        assert notify.super_queue_size() == 0, "بدون worker صفی ساخته نمی‌شود"

    asyncio.run(scenario())
    assert calls == ["مستقیم"]


def test_app_wires_super_queue_lifecycle():
    """چرخهٔ عمر صف باید در اپ سیم‌کشی شده باشد: استارت در post_init و توقف تمیز در post_shutdown."""
    src = _src("newsbot/app.py")
    assert "ensure_super_worker()" in src and "stop_super_worker()" in src
