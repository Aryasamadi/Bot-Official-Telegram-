# -*- coding: utf-8 -*-
"""فاز ۶.۴ / F2 — مستندات Rollback باید ایمن و هم‌راستا با `deploy/deploy.sh` باشند.

این تست‌ها روی فایل‌های واقعی مخزن اجرا می‌شوند و سه چیز را نگهبانی می‌کنند:

1. هیچ سند عملیاتی، دستور اجراییِ `git reset --hard` را پیشنهاد نمی‌کند
   (حتی اگر داخل بلوک کد فقط یک «توضیح» باشد). اشارهٔ توضیحی در متن فقط
   وقتی مجاز است که همان پاراگراف صریحاً هشدار «هرگز / اجرا نکنید» بدهد.
2. مسیر رسمی Rollback همان `sudo ./deploy/deploy.sh rollback` است و هر
   راهنمای آپدیت به بخش ۵ سند `docs/VPS_DEPLOYMENT.md` ارجاع می‌دهد؛
   root، restart سرویس، دروازهٔ سلامت و شکست با کد خروج غیرصفر مستند شده‌اند.
3. توضیحات مستندات با رفتار واقعی اسکریپت هم‌خوان است: بررسی درخت کاری،
   `git reset --keep`، نشانگر `.deployed_sha.prev` و مسیر محافظه‌کارانهٔ
   جایگزین که تغییرات محلی را حفظ می‌کند.

هیچ اتصالی به VPS، SSH یا سرویس واقعی وجود ندارد؛ فقط فایل‌ها خوانده می‌شوند.
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DEPLOY_SH = ROOT / "deploy" / "deploy.sh"
CANONICAL_DOC = "docs/VPS_DEPLOYMENT.md"
ROLLBACK_GUIDES = (
    "README.md",
    "DEPLOY-VPS-UPDATE.md",
    "docs/DEPLOY_RUNBOOK.md",
)
# گزارش‌های تاریخی/آدیت، «سند عملیاتی» نیستند و ممکن است آسیب قدیمی را توصیف کنند.
NON_OPERATIONAL_REPORTS = {
    "REPOSITORY_AUDIT_REPORT.md",
    "PHASE1-10-FINAL-REPORT.md",
}

OFFICIAL_ROLLBACK = "sudo ./deploy/deploy.sh rollback"
HARD_RESET = re.compile(r"\bgit\s+reset\b[^\n]*?--hard\b", re.IGNORECASE)
FENCE_OPEN = re.compile(r"^\s*(`{3,}|~{3,})")
WARNING_MARKERS = tuple(
    marker.casefold()
    for marker in (
        "هرگز",
        "هیچ‌گاه",
        "هیچگاه",
        "هیچ‌وقت",
        "هیچوقت",
        "اجرا نکنید",
        "استفاده نکنید",
        "never",
        "do not",
        "don't",
        "avoid",
    )
)


def _operational_docs():
    """همهٔ راهنماهای عملیاتی ریشه و docs، بدون گزارش‌های تاریخی."""
    rels = ["README.md", "DEPLOY-VPS-UPDATE.md"]
    for path in sorted((ROOT / "docs").glob("*.md")):
        if path.name not in NON_OPERATIONAL_REPORTS:
            rels.append(path.relative_to(ROOT).as_posix())
    return tuple(rels)


OPERATIONAL_DOCS = _operational_docs()


def _read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def _normalized(text):
    """حذف نشانگر نقل‌قول و جمع‌کردن خطوط برای تطبیق عبارت‌های چندخطی."""
    text = re.sub(r"(?m)^\s*>\s?", "", text)
    return re.sub(r"\s+", " ", text)


def _fenced_line_numbers(lines):
    """شمارهٔ خطوط داخل بلوک‌های کد (شامل خطوط حصار)."""
    inside = set()
    fence = None
    for number, line in enumerate(lines, 1):
        stripped = line.strip()
        if fence is None:
            match = FENCE_OPEN.match(stripped)
            if match:
                fence = match.group(1)
                inside.add(number)
        else:
            closing = re.match(
                r"^" + re.escape(fence[0]) + "{" + str(len(fence)) + r",}\s*$",
                stripped,
            )
            inside.add(number)
            if closing:
                fence = None
    return inside


def _paragraph_text(lines, index, fenced):
    """پاراگراف پیرامون یک خط (برای تشخیص هشدار توضیحی)."""
    if index + 1 in fenced:
        return lines[index]
    start = index
    while (
        start > 0
        and lines[start - 1].strip()
        and start not in fenced
    ):
        start -= 1
    end = index
    while (
        end + 1 < len(lines)
        and lines[end + 1].strip()
        and (end + 2) not in fenced
    ):
        end += 1
    return "\n".join(lines[start:end + 1])


def _scan_destructive_resets(text):
    """برمی‌گرداند: [(line, mode, context)] برای `git reset --hard` غیرمجاز.

    mode = "fenced" یعنی دستور داخل بلوک کد است و همیشه ممنوع است.
    mode = "prose" یعنی اشارهٔ متنی بدون هشدار صریح در همان پاراگراف.
    """
    lines = text.splitlines()
    fenced = _fenced_line_numbers(lines)
    violations = []
    for index, line in enumerate(lines):
        if not HARD_RESET.search(line):
            continue
        number = index + 1
        if number in fenced:
            violations.append((number, "fenced", line.strip()))
            continue
        paragraph = _paragraph_text(lines, index, fenced).casefold()
        if not any(marker in paragraph for marker in WARNING_MARKERS):
            violations.append((number, "prose", line.strip()))
    return violations


def _section(text, start, end):
    begin = text.index(start)
    finish = text.index(end, begin)
    return text[begin:finish]


# ── خودِ اسکنر: تفکیک صریح دستور اجرایی از هشدار توضیحی ───────────────────────
def test_scanner_rejects_fenced_hard_reset_even_with_warning_comment():
    text = "```bash\ngit reset --hard origin/main  # هرگز اجرا نکنید\n```\n"
    violations = _scan_destructive_resets(text)
    assert violations, "دستور داخل بلوک کد باید همیشه ممنوع باشد"
    assert violations[0][1] == "fenced"


def test_scanner_allows_prose_warning_and_inline_code():
    texts = (
        "> هرگز `git reset --hard` را اجرا نکنید.\n",
        "Never run `git reset --hard` on the VPS.\n",
    )
    for text in texts:
        assert _scan_destructive_resets(text) == [], text


def test_scanner_flags_prose_recommendation_without_warning():
    text = "برای آپدیت، `git reset --hard origin/main` را اجرا کنید.\n"
    violations = _scan_destructive_resets(text)
    assert violations, "پیشنهاد متنیِ بدون هشدار باید ممنوع باشد"
    assert violations[0][1] == "prose"


def test_safe_keep_reset_is_not_flagged():
    text = "```bash\ngit reset --keep <COMMIT_SHA>\n```\n"
    assert _scan_destructive_resets(text) == []


# ── سندهای واقعی ─────────────────────────────────────────────────────────────
@pytest.mark.parametrize("rel", OPERATIONAL_DOCS)
def test_operational_docs_contain_no_executable_hard_reset(rel):
    violations = _scan_destructive_resets(_read(rel))
    assert violations == [], (
        f"{rel} دستور اجرایی git reset --hard دارد: {violations!r}"
    )


@pytest.mark.parametrize("rel", ROLLBACK_GUIDES)
def test_each_guide_uses_official_rollback_and_links_to_canonical_section(rel):
    text = _read(rel)
    assert OFFICIAL_ROLLBACK in text, (
        f"{rel} باید مسیر رسمی Rollback را با sudo نشان دهد"
    )
    assert CANONICAL_DOC in text, (
        f"{rel} باید به سند رسمی {CANONICAL_DOC} ارجاع دهد"
    )
    assert re.search(r"(§\s*۵|بخش\s*۵|section\s*5)", text), (
        f"{rel} باید به بخش ۵ سند رسمی Rollback ارجاع دهد"
    )


@pytest.mark.parametrize("rel", ROLLBACK_GUIDES)
def test_rollback_guides_document_root_restart_health_and_nonzero_failure(rel):
    text = _read(rel)
    assert "root" in _normalized(text).casefold(), (
        f"{rel} باید نیاز به دسترسی root را صریح بگوید"
    )
    assert "systemctl restart newsbot" in _normalized(text), (
        f"{rel} باید restart سرویس را مستند کند"
    )
    assert re.search(r"سلامت|health", text, re.IGNORECASE), (
        f"{rel} باید دروازهٔ سلامت را مستند کند"
    )
    assert re.search(r"غیرصفر|non[- ]?zero", text, re.IGNORECASE), (
        f"{rel} باید شکست restart/health را با کد خروج غیرصفر مستند کند"
    )
    assert ".deployed_sha.prev" in text, (
        f"{rel} باید مبنای Rollback یعنی .deployed_sha.prev را ذکر کند"
    )


def test_canonical_rollback_section_is_complete_and_safe():
    text = _read(CANONICAL_DOC)
    section = _section(text, "## ۵. بازگشت", "## ۶. پایش")

    assert OFFICIAL_ROLLBACK in section
    assert ".deployed_sha.prev" in section
    assert "systemctl restart newsbot" in section
    assert "سلامت" in section
    assert "غیرصفر" in section
    assert "root" in section.casefold()
    assert _scan_destructive_resets(section) == []


def test_deploy_script_and_docs_share_safe_reset_contract():
    script = DEPLOY_SH.read_text(encoding="utf-8")
    assert re.search(r"git\s+reset\s+-q\s+--keep", script), (
        "deploy.sh باید از git reset --keep استفاده کند"
    )
    assert not HARD_RESET.search(script), (
        "deploy.sh نباید git reset --hard داشته باشد"
    )
    assert "require_clean_worktree" in script
    assert "git status --porcelain=v1 --untracked-files=normal" in script
    assert ".deployed_sha.prev" in script

    for rel in ROLLBACK_GUIDES:
        text = _normalized(_read(rel))
        assert "git reset --keep" in text, (
            f"{rel} باید همگام‌سازی ایمن را با git reset --keep توضیح دهد"
        )
        assert re.search(r"working\s+tree", text, re.IGNORECASE), (
            f"{rel} باید بررسی working tree را ذکر کند"
        )

    for rel in ("DEPLOY-VPS-UPDATE.md", "docs/DEPLOY_RUNBOOK.md"):
        text = _read(rel)
        assert "git status --porcelain" in text, (
            f"{rel} باید بررسی درخت کاری با git status --porcelain را نشان دهد"
        )


@pytest.mark.parametrize("rel", ("README.md", "DEPLOY-VPS-UPDATE.md"))
def test_manual_update_paths_use_official_deploy_script(rel):
    text = _read(rel)
    assert "sudo ./deploy/deploy.sh deploy" in text, (
        f"{rel} باید مسیر رسمی Deploy را با sudo نشان دهد"
    )
    assert not re.search(
        r"git\s+fetch\s+origin\s+main\s*&&\s*git\s+reset",
        text,
        re.IGNORECASE,
    ), f"{rel} نباید دستور fetch+reset دستی را به‌عنوان مسیر عادی تجویز کند"


@pytest.mark.parametrize("rel", ("DEPLOY-VPS-UPDATE.md", "docs/DEPLOY_RUNBOOK.md"))
def test_conservative_manual_fallback_preserves_local_changes(rel):
    text = _read(rel)
    assert "git status --porcelain" in text
    assert "git reset --keep" in text
    assert re.search(r"(commit|stash)", text, re.IGNORECASE), (
        f"{rel} باید حفظ تغییرات محلی را قبل از جابه‌جایی توضیح دهد"
    )
    assert "run.py --check" in text
    assert "systemctl restart newsbot" in text
