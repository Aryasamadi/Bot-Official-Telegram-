# -*- coding: utf-8 -*-
"""PH-05 — یکپارچگیِ رابطِ کاربری و مسیرِ مشتری (UI/UX).

نُه تستِ خواسته‌شده در ``docs/audit/ROADMAP.md`` (بخش PH-05 → «Required tests»):

  ۱) هیچ متنِ نمایشی نباید placeholderِ حل‌نشده داشته باشد
  ۲) نامِ ۱۰۰کاراکتریِ پلن هم باید درست رندر شود
  ۳) ``split_html`` هیچ تکهٔ خالی و هیچ تکهٔ بیش‌ازحد نمی‌دهد
  ۴) هر ``tr(lang,"X")``ِ لفظی باید در ``TXT`` باشد
  ۵) مجموعهٔ placeholderهای fa/en برای هر کلید یکسان است
  ۶) هر مسیرِ اجراییِ ``*2`` یک ``log_event`` می‌نویسد
  ۷) ``s:auto`` و ``s:plan_free`` و ``s:uassign`` تأییدِ دومرحله‌ای می‌خواهند
  ۸) هر صفحهٔ قابل‌دسترس از مسیری، از صفحهٔ خانه هم قابل‌دسترس است
  ۹) بخشِ ۸ از ``docs/PERMISSIONS.md`` دقیقاً همان مسیرهای دومرحله‌ایِ واقعی را می‌گوید
"""
import inspect
import pathlib
import re
from html.parser import HTMLParser

import pytest

from newsbot import ui_i18n, ui_widgets
from newsbot.ui_i18n import TXT, tr

ROOT = pathlib.Path(__file__).resolve().parent.parent
VIEW_FILES = ("newsbot/ui_dispatch.py", "newsbot/ui_views_user.py", "newsbot/ui_views_admin.py",
              "newsbot/ui_views_super.py", "newsbot/ui_commands.py", "newsbot/ui_widgets.py",
              "newsbot/ui_inputs.py", "newsbot/cycle.py", "newsbot/scheduler.py",
              "newsbot/ui_support.py", "newsbot/notify.py", "newsbot/publish.py",
              "newsbot/core_payments.py", "newsbot/core_users.py", "newsbot/core_plans.py")


def _src(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def _placeholders(text):
    """placeholderهای واقعی — «{{name}}» (لفظی) placeholder نیست."""
    t = re.sub(r"\{\{", "\x00", text)
    return set(re.findall(r"\{([a-z_][a-z0-9_]*)\}", t))


def _tr_calls(rel):
    """(key, kwargs) برای هر فراخوانیِ tr(<lang>, "key", ...) در یک فایل."""
    src = _src(rel)
    out = []
    for m in re.finditer(r"\btr\(\s*", src):
        i = m.end()
        depth = 1
        j = i
        while j < len(src) and depth:
            ch = src[j]
            if ch in "([{":
                depth += 1
            elif ch in ")]}":
                depth -= 1
            j += 1
        call = src[i:j - 1]
        km = re.search(r"""["']([a-z_][a-z0-9_]*)["'](?!\s*\+)""", call)
        if not km:
            continue
        key = km.group(1)
        kwargs = set(re.findall(r"([a-z_][a-z0-9_]*)\s*=", call))
        out.append((key, kwargs, rel))
    return out


# ══════════════════════════════════════════════════════════════════════════════════════
# ۱) هیچ متنِ نمایشی نباید placeholderِ حل‌نشده داشته باشد
# ══════════════════════════════════════════════════════════════════════════════════════
def test_no_rendered_text_contains_an_unresolved_placeholder():
    """UI-002: هر فراخوانیِ ``tr`` باید همهٔ placeholderهای کلیدش را پر کند.

    (کلیدِی که با آرگومان رندر می‌شود، placeholderِ حل‌نشده ندارد؛ کلیدِی که *بدون*
    آرگومان رندر می‌شود نباید اصلاً placeholder داشته باشد.)"""
    bad = []
    for rel in VIEW_FILES:
        for key, kwargs, _f in _tr_calls(rel):
            if key not in TXT:
                continue
            fa, en = TXT[key]
            need = _placeholders(fa) | _placeholders(en)
            if not need:
                continue
            missing = need - kwargs
            if missing:
                bad.append("%s: %s → {%s}" % (rel, key, ", ".join(sorted(missing))))
    assert not bad, "فراخوانی‌هایی با placeholderِ حل‌نشده:\n" + "\n".join(bad[:15])


def test_the_intended_literal_markers_are_marked_as_such():
    """«متغیرها: {name}» باید آگاهانه لفظی باشد تا کلیدِ گم‌شده نشود."""
    out = tr("fa", "s_texts_title")
    assert "{name}" in out and "{plan}" in out and "{price}" in out
    out2 = tr("fa", "src_api_url")
    assert "{key}" in out2, "راهنمای API باید کلمهٔ key را لفظی نشان دهد"


# ══════════════════════════════════════════════════════════════════════════════════════
# ۲) نامِ ۱۰۰کاراکتریِ پلن هم باید درست رندر شود
# ══════════════════════════════════════════════════════════════════════════════════════
def test_a_100_character_plan_name_still_renders_a_valid_keyboard():
    """UI-008: برچسبِ دکمهٔ پلن تا ۱۰۰ کاراکتر بود ⇒ تلگرام کلِ صفحه را رد می‌کرد."""
    from newsbot.ui_widgets import B
    label = "پ" * 100
    btn = B(label, "u:plan:1")
    assert len(btn.text) <= 64, f"برچسبِ دکمه از سقفِ تلگرام رد شد: {len(btn.text)}"
    assert btn.text.endswith("…") and btn.text.startswith("پ")
    assert len(btn.callback_data) <= 64


def test_every_button_label_stays_within_the_telegram_limit():
    from newsbot.ui_widgets import B, BTN_LABEL_MAX
    for label in ("", "x" * 10, "ی" * 200, "a" * 64, "a" * 65):
        assert len(B(label, "c:cancel").text) <= BTN_LABEL_MAX


# ══════════════════════════════════════════════════════════════════════════════════════
# ۳) split_html: نه تکهٔ خالی، نه تکهٔ بیش‌ازحد
# ══════════════════════════════════════════════════════════════════════════════════════
def test_split_html_never_emits_an_empty_or_over_limit_chunk():
    """UI-005: نسخهٔ قبلی برای «<b>x</b> " + "A"*5000» تکه‌های [0, 5009] می‌داد."""
    from newsbot.text_format import split_html
    limit = 4096
    samples = [
        "<b>x</b> " + "A" * 5000,               # همان نمونهٔ یافته
        "A" * 20000,                            # یک پاراگرافِ خیلی بلند
        "\n".join("پ" * 900 for _ in range(30)),   # چند پاراگرافِ بلند
        "short",
        "a\n\nb",
        "<b>" + "متنِ باز " * 900 + "</b>",
    ]
    for text in samples:
        chunks = split_html(text, limit)
        assert chunks, "متنِ خالی نباید تکهٔ خالی بدهد"
        for c in chunks:
            assert c.strip(), "تکهٔ خالی تولید شد"
            assert len(c) <= limit, f"تکه {len(c)} > {limit}"


def test_split_html_property_over_many_lengths():
    """Property test: ورودی‌های ۰ تا ۲۰۰۰۰ کاراکتری."""
    from newsbot.text_format import split_html
    limit = 4096
    for n in list(range(0, 40)) + list(range(0, 20001, 97)):
        text = ("متنِ نمونه " * (n // 11 + 1))[:n]
        chunks = split_html(text, limit)
        for c in chunks:
            assert c.strip(), f"تکهٔ خالی برای طولِ {n}"
            assert len(c) <= limit, f"تکه {len(c)} > {limit} برای طولِ {n}"


class _TagBalance(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.stack, self.errors = [], []

    def handle_starttag(self, tag, attrs):
        self.stack.append(tag)

    def handle_endtag(self, tag):
        if self.stack and self.stack[-1] == tag:
            self.stack.pop()
        else:
            self.errors.append(tag)

    @property
    def balanced(self):
        return not self.errors and not self.stack


def _visible(source):
    from newsbot.text_format import sanitize_html, strip_tags
    return re.sub(r"\s+", "", strip_tags(sanitize_html(source)))


def _visible_chunks(chunks):
    from newsbot.text_format import strip_tags
    return re.sub(r"\s+", "", strip_tags("".join(chunks)))


def _assert_complete_and_valid(text, limit):
    from newsbot.text_format import split_html
    chunks = split_html(text, limit)
    assert chunks, f"هیچ تکه‌ای برای ورودیِ {len(text)} کاراکتری تولید نشد"
    for c in chunks:
        assert c.strip(), "تکهٔ خالی تولید شد"
        assert len(c) <= limit, f"تکه {len(c)} > {limit}"
        parser = _TagBalance()
        parser.feed(c)
        parser.close()
        assert parser.balanced, f"HTMLِ تکه نامعتبر است: {c[:80]!r}"
    assert _visible_chunks(chunks) == _visible(text), "محتوای ورودی در بازسازی گم شد"


def test_split_html_preserves_content_when_escaping_expands_past_the_limit():
    """UI-005: وقتی escape طول را از سقف رد می‌کرد، دنبالهٔ rest دور ریخته می‌شد."""
    from newsbot.text_format import _hard_split
    limit = 4096
    text = "&" * 3000
    chunks = _hard_split(text, limit)
    assert all(len(c) <= limit for c in chunks)
    assert _visible_chunks(chunks) == _visible(text)

    mixed = "A" * 4095 + "&" * 3000
    _assert_complete_and_valid(mixed, limit)


@pytest.mark.parametrize("limit", [50, 512, 4096])
def test_split_html_preserves_plain_escaped_html_and_boundary_content(limit):
    samples = [
        "A" * (limit - 1),
        "A" * limit,
        "A" * (limit + 1),
        "A" * (limit * 3),
        "<&>" * (limit * 2),
        "&amp;" * limit,
        "<b>" + "x" * (limit + 3) + "</b>",
        "<b><i>" + "متن " * limit + "</i></b>",
        "خطِ اول " * limit + "\n" + "خطِ دوم " * limit,
        '<a href="https://example.com/' + "p" * 300 + '">' + "y" * (limit * 2) + "</a>",
    ]
    for text in samples:
        _assert_complete_and_valid(text, limit)


# ══════════════════════════════════════════════════════════════════════════════════════
# ۴) هر tr(lang,"X")ِ لفظی باید در TXT باشد
# ══════════════════════════════════════════════════════════════════════════════════════
def test_every_literal_tr_key_exists_in_the_dictionary():
    """UI-001: شش کلیدِ گمشده (s_users_w، s_admins_w، s_prev، s_next، s_model_add،
    s_models_test) به کاربر نشان داده می‌شدند."""
    pattern = re.compile(
        r"""tr\(\s*(?:lang|sl|ul|dl|l|L\(update\)|user_lang\([^)]*\)|lang\w*)\s*,\s*["']([a-z_][a-z0-9_]*)["'](?!\s*\+)""")
    missing = {}
    for rel in VIEW_FILES:
        for m in pattern.finditer(_src(rel)):
            key = m.group(1)
            if key not in TXT:
                missing.setdefault(key, set()).add(rel)
    assert not missing, "کلیدهای i18nِ ناموجود: " + ", ".join(
        "%s (%s)" % (k, ", ".join(sorted(v))) for k, v in sorted(missing.items()))


# ══════════════════════════════════════════════════════════════════════════════════════
# ۵) مجموعهٔ placeholderهای fa/en برای هر کلید یکسان است
# ══════════════════════════════════════════════════════════════════════════════════════
def test_fa_and_en_have_identical_placeholder_sets():
    """UI-019: تنها ناهماهنگیِ دیکشنری، نبودِ {desc_en} در نسخهٔ انگلیسی بود."""
    bad = []
    for key, (fa, en) in TXT.items():
        pf, pe = _placeholders(fa), _placeholders(en)
        if pf != pe:
            bad.append("%s: fa=%s en=%s" % (key, sorted(pf), sorted(pe)))
    assert not bad, "کلیدهای ناهماهنگ: " + "; ".join(bad[:12])


# ══════════════════════════════════════════════════════════════════════════════════════
# ۶) هر مسیرِ اجراییِ *2 یک log_event می‌نویسد
# ══════════════════════════════════════════════════════════════════════════════════════
LOGGED_ELSEWHERE = {"pay_no2", "pay_refund2", "qreset2"}      # لاگ/تأیید در ماژولِ دیگر


def test_every_destructive_execute_route_writes_an_audit_event():
    """UI-011: فقط دو کنشِ مخرب لاگ می‌نوشتند ⇒ «چه کسی چه چیزی را حذف کرد؟» بی‌پاسخ."""
    src = _src("newsbot/ui_dispatch.py")
    branches = re.split(r"\n(?=\s*if b == )", src)
    offenders = []
    checked = 0
    for br in branches:
        m = re.search(r'if b == "([a-z_]+2)"', br)
        if not m:
            continue
        route = m.group(1)
        if route in LOGGED_ELSEWHERE:
            continue
        checked += 1
        if "log_event(" not in br:
            offenders.append(route)
    assert checked >= 15, f"فقط {checked} شاخه بررسی شد (انتظار: بیشتر)"
    assert not offenders, "شاخه‌های بدونِ لاگِ ممیزی: " + ", ".join(sorted(offenders))


# ══════════════════════════════════════════════════════════════════════════════════════
# ۷) کنش‌های پرخطرِ سوپرادمین تأییدِ دومرحله‌ای می‌خواهند
# ══════════════════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("action,execute_action", [("auto", "auto2"), ("plan_free", "plan_free2"),
                                                  ("uassign", "uassign2")])
def test_high_impact_super_admin_actions_require_confirmation(action, execute_action):
    """UI-012: این سه کنش با یک ضربه اجرا می‌شدند (خاموشیِ کلِ ربات / تغییرِ پلنِ
    رایگان / اعطای پلن) ⇒ حالا از مسیرِ دو‌مرحله‌ای می‌گذرند."""
    src = _src("newsbot/ui_dispatch.py")
    m = re.search(r'if b == "%s":(.*?)\n(\s*)if b == ' % re.escape(action), src, re.S)
    assert m, f"مسیرِ {action} پیدا نشد"
    body = m.group(1)
    assert "_confirm(" in body, f"{action} باید تأییدِ دومرحله‌ای داشته باشد"
    assert execute_action in body, f"{action} باید به {execute_action} برود"
    m2 = re.search(r'if b == "%s":(.*?)\n(\s*)if b == ' % re.escape(execute_action), src, re.S)
    assert m2, f"شاخهٔ اجراییِ {execute_action} پیدا نشد"
    assert "log_event(" in m2.group(1), f"{execute_action} باید لاگِ ممیزی بنویسد"


def test_plan_free_only_touches_the_targeted_plan():
    """FIN-020: قبلاً is_freeِ همهٔ پلن‌ها صفر می‌شد ⇒ ممکن بود هیچ پلنِ رایگانی نماند."""
    src = _src("newsbot/ui_dispatch.py")
    m = re.search(r'if b == "plan_free2":(.*?)\n(\s*)if b == ', src, re.S)
    assert m, "شاخهٔ plan_free2 پیدا نشد"
    body = m.group(1)
    assert "is_free=0 WHERE id<>?" in body, "باید فقط پلنِ دیگر را علامت‌زدایی کند"
    assert 'UPDATE plans SET is_free=0"' not in body, "نباید همهٔ پلن‌ها را بی‌تفاوت کند"


# ══════════════════════════════════════════════════════════════════════════════════════
# ۸) هر صفحهٔ قابل‌دسترس از مسیری، از صفحهٔ خانه هم قابل‌دسترس است
# ══════════════════════════════════════════════════════════════════════════════════════
def test_the_home_screen_exposes_support_invite_help_and_language():
    """UI-014: صفحهٔ خانه فقط یک دکمه داشت و ``u:ref`` از هیچ‌جا قابل‌دسترس نبود."""
    from newsbot.ui_views_user import view_user_home
    home = inspect.getsource(view_user_home)
    for route in ("u:plans", "u:man", "u:ref", "u:help", "u:lang"):
        assert route in home, f"مسیرِ {route} از صفحهٔ خانه در دسترس نیست"


def test_the_referral_route_is_reachable_from_the_home_screen():
    """مسیرِ دعوت باید واقعاً در روتر هم وجود داشته باشد (نه فقط در متنِ صفحه)."""
    disp = _src("newsbot/ui_dispatch.py")
    assert re.search(r'if b == "ref"', disp), "مسیرِ u:ref در روتر نیست"


# ══════════════════════════════════════════════════════════════════════════════════════
# ۹) بخشِ ۸ از PERMISSIONS.md دقیقاً همان مسیرهای دومرحله‌ایِ واقعی است
# ══════════════════════════════════════════════════════════════════════════════════════
def test_permissions_doc_lists_exactly_the_real_two_phase_routes():
    """UI-013: سند فهرستِ ناقص/نادرست می‌داد ⇒ بازبین، تصویرِ اشتباهی از مدلِ ایمنی داشت."""
    doc = _src("docs/PERMISSIONS.md")
    sec8 = doc[doc.index("## ۸."):]
    documented = set(re.findall(r"`([as]:[a-z_]+2)`", sec8))
    documented = set(d.split(":")[-1] for d in documented)      # بدونِ پیشوندِ بخش
    src = _src("newsbot/ui_dispatch.py")
    real = set(re.findall(r'if b == "([a-z_]+2)"', src))
    real |= set(re.findall(r'"([a-z_]+2)"', src))       # مسیرهایی که در یک تاپل آمده‌اند
    real |= {"pay_no2", "pay_refund2", "qreset2"}              # لاگ/تأیید در ماژولِ دیگر
    missing = sorted(r for r in real if r not in documented)
    extra = sorted(d for d in documented if d not in real)
    assert not missing, "مسیرهای دومرحله‌ایِ واقعی که در سند نیامده‌اند: " + ", ".join(missing)
    assert not extra, "مسیرهایی که در سند آمده ولی در کد نیستند: " + ", ".join(extra)


def test_the_audit_log_exists_and_is_used():
    """رگرسیونِ امنیتی: helperِ لاگ باید در دسترس باشد (نه یک نامِ تعریف‌نشده)."""
    from newsbot.logutils import log_event
    assert callable(log_event)
    assert ui_widgets.BTN_LABEL_MAX >= 64
    assert ui_i18n.TXT
