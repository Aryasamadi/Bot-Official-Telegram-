# -*- coding: utf-8 -*-
"""فاز ۱۴ — شفافیت پرداخت دوواحدی و بازچینش UI:
• تعرفهٔ عددیِ هم‌واحدِ زبان کاربر (fa=تومان price_num_fa · en=$ price_num) بدون fallback بین‌واحدی
• مدل ChatGPT-مانند: تمدید/ارتقای کسری/خرید؛ نزول پلن بلاک؛ بدون تعرفهٔ هم‌زبان ⇒ مسیر متنی
• کلیدهای i18n جدید (پلن‌ها/ارجاع) + دوزبانه‌بودن کامل SPHARSE/DIAG/STAGES/TXT
• رفع باگ‌های UI: شورتکات خانه حذف شده (تست در فاز ۹)، پیام سوپر به زبان خودش + قیمت پلن
"""
from pathlib import Path
from newsbot.db import q
from newsbot.timeutils import now_iso
from newsbot.core_plans import (create_plan, get_plan, assign_plan, upgrade_quote,
                                plan_num, fmt_amt, proration_remaining_days)

ROOT = Path(__file__).resolve().parent.parent


def _src(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def _mkuser(uid, lang="fa"):
    q("INSERT OR IGNORE INTO users(id,username,name,role,created_at,last_seen,lang) VALUES(?,?,?,?,?,?,?)",
      (uid, f"u{uid}", f"U{uid}", "admin", now_iso(), now_iso(), lang), commit=True)


# ═══════════════════ تعرفهٔ دوواحدی ═══════════════════

def test_plan_num_is_per_language_no_cross_unit_fallback():
    pid = create_plan(name="dual", days=30, price_num=5, price_num_fa=3000000)
    p = get_plan(pid)
    assert plan_num(p, "fa") == 3000000, "کاربر fa باید تعرفهٔ تومانی ببیند"
    assert plan_num(p, "en") == 5, "کاربر en باید تعرفهٔ دلاری ببیند"
    pid2 = create_plan(name="usd-only", days=30, price_num=7)
    p2 = get_plan(pid2)
    assert plan_num(p2, "fa") == 0, "تعرفهٔ دلاری هرگز برای fa جایگزین نمی‌شود"
    assert plan_num(p2, "en") == 7


def test_price_num_fa_column_exists_in_schema():
    cols = {r["name"] for r in q("SELECT name FROM pragma_table_info('plans')")}
    assert "price_num_fa" in cols, "اسکیمای v7 باید ستون تعرفهٔ تومانی را داشته باشد"


def test_fmt_amt_units():
    """PH-05/UI-018: مبلغ با نشانه‌های جداسازیِ جهت نمایش داده می‌شود (LRM در دو طرف)."""
    from newsbot.ui_i18n import ltr
    assert fmt_amt(3000000, "fa") == ltr("3000000 تومان")
    assert fmt_amt(3000000, "fa").strip("\u200e") == "3000000 تومان"
    assert fmt_amt(5, "en") == ltr("$5")
    assert fmt_amt(5, "en").strip("‎") == "$5"


# ═══════════════════ قواعد تمدید/ارتقا/خرید/نزول ═══════════════════

def test_quote_renew_upgrade_blocked_and_fresh_fa():
    p30 = create_plan(name="fa30", days=30, price_num_fa=3000000)
    p60 = create_plan(name="fa60", days=30, price_num_fa=6000000)
    p2y = create_plan(name="fa2y", days=730, price_num_fa=10000000)     # نرخ روزانهٔ ارزان‌تر ⇒ نزول
    uid = 88401; _mkuser(uid)
    assign_plan(uid, p30)
    assert proration_remaining_days(uid) > 29
    # تمدید همان پلن: مبلغ کامل دوره، بدون کسری
    renew = upgrade_quote(uid, get_plan(p30), get_plan(p30), lang="fa")
    assert renew["kind"] == "renew" and renew["payable_num"] and 2900000 < renew["payable"] <= 3000000
    # ارتقای کسری: تفاوت تعرفه برای روزهای باقی‌مانده، انقضا حفظ می‌شود
    up = upgrade_quote(uid, get_plan(p60), get_plan(p30), lang="fa")
    assert up["kind"] == "upgrade" and up["keep_expiry"] and 2900000 < up["payable"] <= 3000000
    # نزول: بلاک (فروش نداریم)
    dn = upgrade_quote(uid, get_plan(p2y), get_plan(p30), lang="fa")
    assert dn["kind"] == "blocked" and dn["blocked"] and dn["payable"] == 0
    # خرید بدون پلن فعال: مبلغ کامل
    uid2 = 88402; _mkuser(uid2)
    fresh = upgrade_quote(uid2, get_plan(p60), None, lang="fa")
    assert fresh["kind"] == "fresh" and fresh["payable"] == 6000000 and fresh["payable_num"]


def test_quote_en_tariff_and_wallet_deduction():
    p30 = create_plan(name="en30", days=30, price_num=30)
    p60 = create_plan(name="en60", days=30, price_num=60)
    uid = 88403; _mkuser(uid, "en")
    from newsbot.core_wallet import wallet_credit
    wallet_credit(uid, "usd", 10, "test", None)      # PH-01: کیف پولِ واحد‌دار
    assign_plan(uid, p30)
    up = upgrade_quote(uid, get_plan(p60), get_plan(p30), lang="en")
    assert up["kind"] == "upgrade" and up["used_balance"] == 10
    assert abs(up["payable"] - (up["new_cost"] - up["credit"] - 10)) < 0.01


def test_quote_missing_tariff_is_text_mode_not_guess():
    paid = create_plan(name="no-fa-tariff", days=30, price_num=50)      # فقط دلاری
    uid = 88404; _mkuser(uid, "fa")
    assign_plan(uid, paid)
    other = create_plan(name="other", days=30)
    uid2 = 88405; _mkuser(uid2, "fa"); assign_plan(uid2, other)
    # کاربر fa با پلن فعال و پلن جدیدِ بدون تعرفهٔ تومانی: quote نباید عدد حدسی بدهد
    qt = upgrade_quote(uid2, get_plan(paid), get_plan(other), lang="fa")
    assert qt["payable_num"] is False and qt["kind"] == "fresh"


def test_quote_renew_without_tariff_is_text_mode():
    p = create_plan(name="txt", days=30)
    uid = 88406; _mkuser(uid, "fa"); assign_plan(uid, p)
    qt = upgrade_quote(uid, get_plan(p), get_plan(p), lang="fa")
    assert qt["kind"] == "renew" and qt["payable_num"] is False


# ═══════════════════ شفافیت UI (کلیدها و متن‌ها) ═══════════════════

def test_i18n_dictionaries_are_complete_and_bilingual():
    from newsbot.ui_i18n import TXT, DIAG, STAGES, WIZ, DEFAULT_TEXTS
    for table, name in ((TXT, "TXT"), (DIAG, "DIAG"), (STAGES, "STAGES")):
        for k, v in table.items():
            assert isinstance(v, (tuple, list)) and len(v) == 2, f"{name}[{k}] باید (fa, en) باشد"
            assert str(v[0]).strip() and str(v[1]).strip(), f"{name}[{k}] نباید خالی باشد"
    for k, spec in WIZ.items():
        for field, prompt, typ in spec:
            assert isinstance(prompt, (tuple, list)) and len(prompt) == 2 and prompt[0] and prompt[1], f"WIZ[{k}][{field}]"
    for k, langs in DEFAULT_TEXTS.items():
        assert langs.get("fa") and langs.get("en"), f"DEFAULT_TEXTS[{k}]"


def test_spharse_is_bilingual_and_keys_match():
    from newsbot.ui_widgets import SPHARSE, SPHARSE_EN, _step_text
    assert set(SPHARSE.keys()) == set(SPHARSE_EN.keys()), "کلیدهای انگلیسی و فارسی مراحل زنده باید یکی باشند"
    for k, v in SPHARSE_EN.items():
        assert str(v).strip() and any("a" <= c.lower() <= "z" for c in v), f"ترجمهٔ انگلیسی ناقص: {k}"
    st = {"category": "post_gen", "status": "run", "variant": 0}
    en = _step_text(st, "en"); fa = _step_text(st, "fa")
    assert any("a" <= c.lower() <= "z" for c in en), "در lang=en متن باید انگلیسی باشد"
    assert any("\u0600" <= c <= "\u06ff" for c in fa), "در lang=fa متن باید فارسی باشد"


def test_plans_ref_line_and_referral_howto():
    from newsbot.ui_i18n import tr
    # متنِ خطِ دعوت عوض شد: به‌جای «رایگان» حالا اعتبارِ کیف پول از دعوتِ دوستان گفته می‌شود
    assert "/ref" in tr("fa", "plans_ref") and "دعوت" in tr("fa", "plans_ref")
    assert "/ref" in tr("en", "plans_ref") and "credit" in tr("en", "plans_ref").lower()
    how = tr("fa", "ref_how", p="10")
    assert "کیف پول" in how and "10" in how, "توضیح /ref باید مکانیزم هر خرید را بگوید"
    assert "wallet" in tr("en", "ref_how", p="10").lower()
    assert "سقف" in tr("fa", "s_ref_help"), "پنل سوپر باید درصد/سقف را توضیح دهد"
    assert "cap" in tr("en", "s_ref_help").lower()


def test_plan_page_notes_honest_and_no_proration_jargon():
    from newsbot.ui_i18n import tr, TXT
    for key in ("plan_renew_note", "plan_upgrade_note", "plan_nonum", "plan_downgrade"):
        for lg in ("fa", "en"):
            assert tr(lg, key).strip(), f"{key} ({lg}) خالی است"
    assert "plan_chain_note" not in TXT, "پیام قدیمیِ همیشه‌نمایش باید حذف شده باشد"
    blob = " ".join(tr(l, k) for k in TXT for l in ("fa", "en"))
    assert "proration" not in blob, "اصطلاح فنی proration نباید در متن کاربر دیده شود"


def test_super_receipt_message_has_plan_price_and_own_language():
    from newsbot.ui_i18n import tr
    for lg in ("fa", "en"):
        msg = tr(lg, "s_pay_new", id=1, who="u", uid=1, plan="P", plan_price="500", price="100", disc="", note="")
        assert "500" in msg, "قیمت پلن باید در پیام خرید دیده شود"
    src = _src("newsbot/ui_inputs.py")
    assert 'tr("fa", "s_pay_new"' not in src, "هدر رسید نباید همیشه fa باشد (باگ فاز ۱۳)"


def test_admin_home_has_no_shortcut_and_clean_titles():
    src = _src("newsbot/ui_views_admin.py")
    assert "len(chs) == 1" not in src, "میان‌بر کانال یکتا باید از خانه حذف شده باشد"
    assert "quota_bars_safe([" not in src.split("async def view_channel")[0], "نوارهای تکراری نباید در خانه باشند"
    from newsbot.ui_i18n import tr
    assert "امروز" in tr("fa", "today_line") and "Today" in tr("en", "today_line")
