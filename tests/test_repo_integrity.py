# -*- coding: utf-8 -*-
"""نگهبانِ یکپارچگیِ زیرساخت مخزن.

این تست‌ها رفتار برنامه را نمی‌سنجند؛ ساختار و مراجع مخزن را می‌سنجند تا مرجع‌های شکسته
دوباره وارد مخزن نشوند:

• H2/OPS-028 — ``deploy/deploy.sh`` باید در ایندکس گیت اجرایی باشد (حالت 100755).
  ریشه: فایل با حالت 100644 commit شده بود و دستور مستندشده‌ی
  «./deploy/deploy.sh deploy <SHA>» با «Permission denied» شکست می‌خورد.

• H3/DOC-001 — اشاره‌های ``ROADMAP.md`` در اسناد و کد باید به فایلی برسد که واقعاً در
  مخزن هست. ریشه: شش ارجاع (``docs/PERMISSIONS.md``، ``docs/SECURITY.md``،
  ``newsbot/ratelimit.py``، ``tests/test_authz_matrix.py``) به سندی اشاره می‌کردند که
  هرگز commit نشده بود.

• H4 — هر مسیرِ داخل backtick که شکلِ مسیرِ مخزن دارد باید واقعاً وجود داشته باشد.
  (استثناها فقط مسیرهایی هستند که ممیزی صریحاً «برای افزوده‌شدن» پیشنهاد کرده است.)
"""
import os
import re
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# مسیرهایی که ممیزی به‌عنوان «فایلِ پیشنهادیِ هنوز-ساخته‌نشده» نام برده است.
# وقتی هرکدام ساخته شد، باید از این فهرست حذف شود.
PROPOSED_PATHS = {
    "deploy/systemd/newsbot.service",   # OPS-001 — پیشنهادِ فایل واحد سرویس
    "deploy/env.example",               # OPS-001 — پیشنهادِ تک‌منبعِ APP_DIR/SERVICE
    ".github/CODEOWNERS",               # OPS-023 — پیشنهادِ مالکیت مسیرهای حساس
    ".github/dependabot.yml",           # OPS-023 — پیشنهادِ به‌روزرسانی خودکار وابستگی
}

# فایل‌های سطح ریشه که مراجعه به آن‌ها باید معتبر باشد
ROOT_ARTIFACTS = (
    "run.py", "requirements.txt", "requirements-dev.txt", "README.md", "ROADMAP.md",
    "PHASE1-10-FINAL-REPORT.md", "DEPLOY-VPS-UPDATE.md", ".env.example",
    ".gitignore", ".dockerignore", "Dockerfile",
)

# پوشه‌هایی که مراجعِ مسیر در آن‌ها جست‌وجو می‌شود
SCAN_DIRS = ("newsbot", "tests", "tools", "deploy", "docs", ".github")

_BACKTICK = re.compile(r"`([^`\n]{1,160})`")
_HAS_SLASH = re.compile(r"^[A-Za-z0-9_.][A-Za-z0-9_./-]*/[A-Za-z0-9_./-]*$")


def _git_available():
    import shutil
    return shutil.which("git") is not None


def _git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)


def _all_repo_files():
    out = []
    for root, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in (".git", "__pycache__", ".venv")]
        for f in files:
            p = os.path.join(root, f)
            out.append(os.path.relpath(p, ROOT).replace("\\", "/"))
    return out


def _text_files():
    out = []
    for rel in _all_repo_files():
        if not rel.endswith((".md", ".py", ".sh", ".yml", ".yaml", ".conf", ".example", ".txt")):
            continue
        out.append(rel)
    return out


def _strip_line_suffix(ref):
    """«newsbot/db.py:99-104» → «newsbot/db.py» · «…:50,100,128» → «…» · «f.py::test_x» → «f.py»"""
    ref = re.sub(r"::\S+$", "", ref)                          # شناسهٔ گرهٔ pytest
    ref = re.sub(r"(:\d+(-\d+)?)(,\d+(-\d+)?)*$", "", ref)      # شمارهٔ خط یا فهرست شماره‌خط
    ref = re.sub(r":[A-Za-z_][A-Za-z0-9_]*$", "", ref)          # نامِ نماد («newsbot/opctx.py:_operations»)
    return ref.strip()


def _is_dir_reference(ref):
    return ref.endswith("/")


def test_deploy_script_is_executable_in_git_index():
    """OPS-028 — اسکریپت استقرار باید اجرایی commit شود (حالت 100755 در ایندکس گیت)."""
    import pytest
    if not _git_available():
        pytest.skip("git در دسترس نیست")
    r = _git("ls-files", "-s", "deploy/deploy.sh")
    if r.returncode != 0:
        pytest.skip("git در دسترس نیست")
    lines = [l for l in r.stdout.splitlines() if l.strip()]
    assert lines, "deploy/deploy.sh در ایندکس گیت نیست"
    mode = lines[0].split()[0]
    assert mode == "100755", (
        "deploy/deploy.sh اجرایی commit نشده است (حالت %s). "
        "دستور مستندشده‌ی «./deploy/deploy.sh deploy <SHA>» با Permission denied شکست می‌خورد. "
        "رفع: git update-index --chmod=+x deploy/deploy.sh" % mode
    )


def test_roadmap_document_is_tracked_and_contains_the_cited_sections():
    """DOC-001 — سندِ ارجاع‌شده باید در مخزن باشد و بخش‌های نقل‌شده در آن موجود باشند."""
    path = os.path.join(ROOT, "ROADMAP.md")
    assert os.path.isfile(path), (
        "ROADMAP.md در مخزن وجود ندارد، ولی اسناد و کد به آن ارجاع می‌دهند "
        "(docs/PERMISSIONS.md:3,98 · docs/SECURITY.md:3,78 · newsbot/ratelimit.py:20 · "
        "tests/test_authz_matrix.py:4)"
    )
    body = open(path, encoding="utf-8").read()
    # شماره‌گذاری سند با ارقام فارسی است («۶.۳» / «۶.۶»)
    for cited, what in (("۶.۳", "فاز ۳ — امنیت رازها/لاگ‌ها/ضد-injection"),
                        ("۶.۶", "فاز ۶ — مرز دسترسی")):
        assert cited in body, "بخش %s (%s) در ROADMAP.md یافت نشد" % (cited, what)


def test_referenced_repo_paths_exist():
    """H4 — هر مسیرِ داخل backtick که شکلِ مسیرِ مخزن دارد باید وجود داشته باشد."""
    repo = set(_all_repo_files())
    missing = {}
    for rel in _text_files():
        try:
            body = open(os.path.join(ROOT, rel), encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        for m in _BACKTICK.finditer(body):
            raw = _strip_line_suffix(m.group(1))
            if not raw or "*" in raw or " " in raw or _is_dir_reference(raw):
                continue
            if raw in repo or raw in PROPOSED_PATHS:
                continue
            if "/" in raw:
                if not raw.startswith(tuple(SCAN_DIRS)):
                    continue
            elif raw not in ROOT_ARTIFACTS:
                continue
            missing.setdefault(raw, []).append(rel)

    assert not missing, (
        "مسیرهای ارجاع‌شده‌ای که در مخزن وجود ندارند:\n"
        + "\n".join("  %s  ←  %s" % (k, ", ".join(sorted(set(v)))) for k, v in sorted(missing.items()))
    )
