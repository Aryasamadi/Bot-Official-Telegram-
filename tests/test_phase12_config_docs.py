# -*- coding: utf-8 -*-
"""فاز ۱۲ — پیکربندی، رازها و صداقت مستندات (CFG / SEC-09 / ARCH / REF-3 / D-items).

پوشش:
  * CFG-01/02/03/09: env نامعتبر ⇒ پیش‌فرض + CONFIG_ERRORS · SUPER_ADMIN_IDS ناسالم ثبت می‌شود ·
    کلمپ بازه‌ای (۹۹ ⇒ ۵۰) · مسیر صریح .env نسبت به ریشهٔ پروژه
  * SEC-09 (تصمیم ۱۰-الف): رمزنگاری کلیدهای AI در حالت سکون (roundtrip · idempotent ·
    fail-closed بدون کلید · مهاجرت یک‌بارهٔ استارت)
  * REF-3 (تصمیم ۸): پاداش هر خرید با قفل idempotency هر پرداخت · سقف مبلغی از پنل · تنظیم زنده
  * ARCH-05/D4/D9: جدایی state زنده از config · سطح پنل ارجاع · هم‌راستایی README/.env.example
"""
import importlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from newsbot.db import q
from newsbot.timeutils import now_iso

ROOT = Path(__file__).resolve().parent.parent
NEW_I18N_KEYS = ("ref_join_ok", "s_ref", "s_ref_title", "s_ref_line_p", "s_ref_line_c",
                 "s_ref_unlimited", "s_ref_setp", "s_ref_setc", "s_ref_prompt_p", "s_ref_prompt_c")


def _src(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


# ══════════════════ CFG-01/02/03/09 — اعتبارسنجی env ══════════════════

def _fresh_config(sub_env):
    """config را در پروسه‌ی جدا با env سفارشی لود می‌کند (بدون آلودگی به دیتابیس تست‌ها)."""
    code = ("import json, newsbot.config as c;"
            "print(json.dumps({'pct': c.REFERRAL_PERCENT, 'tick': c.TICK_SECONDS,"
            "'admins': sorted(c.SUPER_ADMIN_IDS), 'errors': c.CONFIG_ERRORS}, ensure_ascii=False))")
    env = {**os.environ, **sub_env}
    env["PYTHONIOENCODING"] = "utf-8"
    for k in ("TICK_SECONDS", "REFERRAL_PERCENT", "D1_TIMEOUT"):
        if k not in sub_env:
            env.pop(k, None)   # پاکسازی آلودگی احتمالی شل
    out = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT), env=env,
                         capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_invalid_int_falls_back_and_is_recorded():
    data = _fresh_config({"TICK_SECONDS": "abc"})
    assert data["tick"] == 120
    assert any("TICK_SECONDS" in e for e in data["errors"]), "خطای env نامعتبر باید در CONFIG_ERRORS ثبت شود"


def test_out_of_range_float_is_clamped_and_recorded():
    data = _fresh_config({"REFERRAL_PERCENT": "99"})
    assert data["pct"] == 50.0
    assert any("REFERRAL_PERCENT" in e for e in data["errors"])


def test_empty_env_uses_default_without_error():
    data = _fresh_config({"TICK_SECONDS": "", "REFERRAL_PERCENT": ""})
    assert data["tick"] == 120 and data["pct"] == 10.0
    assert not any(("TICK_SECONDS" in e or "REFERRAL_PERCENT" in e) for e in data["errors"])


def test_super_admin_ids_invalid_tokens_recorded_not_silent():
    data = _fresh_config({"SUPER_ADMIN_IDS": "1,abc,-5"})
    assert data["admins"] == [1]
    assert any("abc" in e for e in data["errors"]), "توکن نامعتبر نباید بی‌صدا حذف شود (CFG-02)"


def test_dotenv_uses_explicit_project_root_path(monkeypatch):
    """CFG-09: load_dotenv باید با مسیر مطلق ریشهٔ پروژه صدا زده شود، نه وابسته به CWD."""
    import dotenv
    import newsbot.config as cfg
    calls = []
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **k: calls.append(a[0] if a else k.get("dotenv_path")))
    importlib.reload(cfg)
    assert calls and Path(calls[0]) == ROOT / ".env"
    monkeypatch.undo()
    importlib.reload(cfg)   # بازگردانی ماژول به حالت واقعی برای تست‌های بعدی


# ══════════════════ SEC-09 — رمزنگاری کلیدهای AI (تصمیم ۱۰-الف) ══════════════════

def test_secret_store_roundtrip_idempotent(monkeypatch):
    from newsbot import config as cfg
    from newsbot import secret_store as ss
    monkeypatch.setattr(cfg, "SECRETS_KEY", "test-key-123")
    enc = ss.encrypt("sk-abc-xyz")
    assert ss.is_encrypted(enc) and enc.startswith("enc:v1:")
    assert ss.encrypt(enc) == enc                      # idempotent: دوباره رمز نمی‌شود
    assert ss.decrypt(enc) == "sk-abc-xyz"
    assert ss.decrypt("plain-text") == "plain-text"    # متن ساده سالم برمی‌گردد


def test_secret_store_fail_closed(monkeypatch):
    from newsbot import config as cfg
    from newsbot import secret_store as ss
    monkeypatch.setattr(cfg, "SECRETS_KEY", "key-A")
    enc = ss.encrypt("sk-secret")
    monkeypatch.setattr(cfg, "SECRETS_KEY", "key-B")   # کلید اشتباه
    assert ss.decrypt(enc) == "", "با کلید اشتباه نباید مقدار نادرست برگردد (fail-closed)"
    monkeypatch.setattr(cfg, "SECRETS_KEY", "")        # بدون کلید
    assert ss.decrypt(enc) == ""
    assert ss.encrypt("sk-raw") == "sk-raw"            # بدون کلید: عبور بدون تغییر (سازگاری)


def test_migrate_ai_keys_encrypts_plaintext_once(monkeypatch):
    from newsbot import config as cfg
    from newsbot import secret_store as ss
    monkeypatch.setattr(cfg, "SECRETS_KEY", "test-migrate-key")
    uid = 987000111
    before = q("SELECT id, api_key FROM ai_models WHERE api_key IS NOT NULL AND api_key != ''")
    try:
        q("INSERT INTO ai_models(name,kind,base_url,api_key,model,priority,owner_id) VALUES(?,?,?,?,?,?,?)",
          ("t-sec12", "openai", "https://example.com/v1", "sk-plain-legacy", "m-x", 10, uid), commit=True)
        mid = q("SELECT id FROM ai_models WHERE owner_id=?", (uid,), one=True)["id"]
        assert ss.migrate_ai_keys() >= 1
        row = q("SELECT api_key FROM ai_models WHERE id=?", (mid,), one=True)
        assert ss.is_encrypted(row["api_key"]) and ss.decrypt(row["api_key"]) == "sk-plain-legacy"
        assert ss.migrate_ai_keys() == 0               # idempotent: بار دوم چیزی برای مهاجرت نیست
    finally:
        for r in before:                               # بازگردانی ردیف‌های سایر تست‌ها به متن اصلی
            q("UPDATE ai_models SET api_key=? WHERE id=?", (r["api_key"], r["id"]), commit=True)
        q("DELETE FROM ai_models WHERE owner_id=?", (uid,), commit=True)


def test_add_and_update_model_store_keys_encrypted(monkeypatch):
    from newsbot import config as cfg
    from newsbot import secret_store as ss
    from newsbot.ai_models import add_model, get_model, update_model
    monkeypatch.setattr(cfg, "SECRETS_KEY", "test-model-key")
    uid = 987000222
    try:
        mid = add_model("https://api.example.com/v1", "  sk-live-abc  ", "model-x", name="t-sec12", owner_id=uid)
        row = get_model(mid)
        assert ss.is_encrypted(row["api_key"]) and ss.decrypt(row["api_key"]) == "sk-live-abc"
        update_model(mid, api_key="sk-new-xyz")
        row = get_model(mid)
        assert ss.is_encrypted(row["api_key"]) and ss.decrypt(row["api_key"]) == "sk-new-xyz"
    finally:
        q("DELETE FROM ai_models WHERE owner_id=?", (uid,), commit=True)


# ══════════════════ REF-3 — پاداش هر خرید + سقف پنل (تصمیم ۸) ══════════════════

def _mk_user(uid):
    q("INSERT OR IGNORE INTO users(id,username,name,role,created_at,last_seen) VALUES(?,?,?,?,?,?)",
      (uid, f"u{uid}", f"U{uid}", "user", now_iso(), now_iso()), commit=True)


def _wallet(uid):
    return round(float(q("SELECT credit FROM users WHERE id=?", (uid,), one=True)["credit"] or 0), 2)


def test_referral_cfg_panel_validation_and_roundtrip():
    from newsbot.core_referrals import referral_cfg, set_referral_cfg
    p0, c0 = referral_cfg()
    try:
        pct, cap = set_referral_cfg(percent="۲۵", cap_amount="۱۲.۵")
        assert (pct, cap) == (25.0, 12.5)
        for bad in ({"percent": 51}, {"percent": -1}, {"cap_amount": -2}, {"percent": "abc"}):
            with pytest.raises(ValueError):
                set_referral_cfg(**bad)
    finally:
        set_referral_cfg(percent=p0, cap_amount=c0)


def test_credit_per_purchase_dedupe_and_amount_cap():
    from newsbot.core_referrals import (credit_referral_on_payment, record_referral,
                                        referral_stats, referral_cfg, set_referral_cfg)
    referrer, buyer = 88201, 88202
    ref2, buyer2 = 88203, 88204
    for uid in (referrer, buyer, ref2, buyer2):
        _mk_user(uid)
    q("DELETE FROM referrals WHERE referred_id IN (?,?)", (buyer, buyer2), commit=True)
    assert record_referral(referrer, buyer) is True
    assert record_referral(ref2, buyer2) is True
    p0, c0 = referral_cfg()
    try:
        set_referral_cfg(percent=10, cap_amount=0)
        q("UPDATE users SET credit=0 WHERE id IN (?,?)", (referrer, ref2), commit=True)
        assert credit_referral_on_payment(buyer, 200.0, ref_key="pay:11") == (referrer, 20.0)
        assert credit_referral_on_payment(buyer, 200.0, ref_key="pay:11") is None      # همان پرداخت ⇒ idempotent
        assert credit_referral_on_payment(buyer, 100.0, ref_key="pay:12") == (referrer, 10.0)  # هر خرید پاداش دارد
        assert _wallet(referrer) == 30.0
        set_referral_cfg(cap_amount=15.0)                                              # سقف مبلغی ۱۵ (کمتر از پاداش انبارشده)
        assert credit_referral_on_payment(buyer, 100.0, ref_key="pay:13") is None
        assert _wallet(referrer) == 30.0                                               # سقف‌شده‌ها اضافه نمی‌شوند
        assert round(referral_stats(referrer)[1], 2) == 30.0

        set_referral_cfg(percent=10, cap_amount=15.0)                                  # خریدار دوم: پاداش جزئی تا سقف
        assert credit_referral_on_payment(buyer2, 100.0, ref_key="pay:21") == (ref2, 10.0)
        assert credit_referral_on_payment(buyer2, 200.0, ref_key="pay:22") == (ref2, 5.0)   # cap_partial
        assert credit_referral_on_payment(buyer2, 50.0, ref_key="pay:23") is None      # سقف پر شد
        assert _wallet(ref2) == 15.0
        assert round(referral_stats(ref2)[1], 2) == 15.0
    finally:
        set_referral_cfg(percent=p0, cap_amount=c0)


def test_referral_max_per_user_deprecated_not_used_in_modules():
    for rel in ("newsbot/core_referrals.py", "newsbot/ui_views_user.py",
                "newsbot/ui_views_super.py", "newsbot/ui_inputs.py"):
        assert "REFERRAL_MAX_PER_USER" not in _src(rel), f"{rel} نباید دیگر سقف «تعداد» را بخواند"
    assert "REFERRAL_MAX_PER_USER" in _src("newsbot/config.py")   # فقط برای سازگاری import نگه داشته شده


def test_trigger_v5_delta_and_drop_before_create():
    from newsbot.db import REFERRAL_CREDIT_TRIGGER
    assert "AFTER UPDATE OF credit_amount ON referrals" in REFERRAL_CREDIT_TRIGGER
    assert "WHEN NEW.credit_amount > COALESCE(OLD.credit_amount, 0)" in REFERRAL_CREDIT_TRIGGER
    assert "(NEW.credit_amount-COALESCE(OLD.credit_amount,0))" in REFERRAL_CREDIT_TRIGGER
    assert "DROP TRIGGER IF EXISTS trg_referral_credit_wallet" in _src("newsbot/db.py")


def test_ref_panel_surface_wired():
    sup, disp, inp = _src("newsbot/ui_views_super.py"), _src("newsbot/ui_dispatch.py"), _src("newsbot/ui_inputs.py")
    assert "def view_s_ref" in sup and '"s:ref_p"' in sup and '"s:ref_c"' in sup
    assert "view_s_ref" in disp and '"ref_p"' in disp
    assert '"refpct"' in inp and '"refcap"' in inp and "set_referral_cfg" in inp


def test_new_referral_i18n_keys_in_both_langs():
    from newsbot.ui_i18n import TXT, tr
    for key in NEW_I18N_KEYS:
        assert key in TXT, f"کلید i18n غایب: {key}"
        assert tr("fa", key) != key and tr("en", key) != key


# ══════════════════ ARCH-05 / D4 / D9 / D1-D2 — ادعاها ══════════════════

def test_runtime_state_separated_from_config():
    cfg_src = _src("newsbot/config.py")
    assert "NOTIFY_SUPER = " not in cfg_src and "BOT_USERNAME = " not in cfg_src
    notify_src = _src("newsbot/notify.py")
    assert "runtime.NOTIFY_SUPER" in notify_src and "runtime.NOTIFY_USER" in notify_src
    assert "config.NOTIFY_SUPER" not in notify_src and "config.NOTIFY_USER" not in notify_src
    pub_src = _src("newsbot/publish.py")
    assert "runtime.BOT_USERNAME" in pub_src and "config.BOT_USERNAME" not in pub_src
    app_src = _src("newsbot/app.py")
    assert "runtime.BOT_USERNAME = me.username" in app_src
    assert "migrate_ai_keys()" in app_src and "bad.extend(config.CONFIG_ERRORS)" in app_src


def test_env_example_is_in_sync_with_config():
    example = set(re.findall(r"^#?([A-Z][A-Z0-9_]*)=", _src(".env.example"), flags=re.M))
    reads = set(re.findall(r'(?:os\.getenv|_env_int|_env_float)\(\s*"([A-Z0-9_]+)"', _src("newsbot/config.py")))
    aliases = {"TELEGRAM_BOT_TOKEN", "CF_KV_ID"}
    assert example <= reads, f".env.example کلید بدون مصرف در config دارد: {sorted(example - reads)}"
    missing = reads - aliases - example
    assert not missing, f"متغیرهای خوانده‌شدهٔ config که در .env.example مستند نشده‌اند: {sorted(missing)}"


def test_readme_reflects_phase12_claims():
    readme = _src("README.md")
    for needle in ("runtime.py", "secret_store.py", "SECRETS_KEY", "net_http.py",
                   "REFERRAL_MAX_PER_USER", "منسوخ", "CF_ACCOUNT_ID"):
        assert needle in readme, f"README فاقد: {needle}"
    assert re.search(r"AI_MAX_MODEL_ATTEMPTS` \| `6`", readme), "ردیف AI_MAX_MODEL_ATTEMPTS باید ۶ باشد (D1)"


def test_schema_version_bumped_to_v5():
    import newsbot
    assert newsbot.SCHEMA_VERSION == 5
