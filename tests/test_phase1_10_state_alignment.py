# -*- coding: utf-8 -*-
"""نگهبان «همگامی حقیقت» (Phase 1 · P1-1).

این فایل رفتار برنامه را نمی‌سنجد؛ انحرافِ مستندات از واقعیتِ مخزن را قفل می‌کند تا
وضعیت پروژه دوباره «بر پایهٔ حافظهٔ چت» یا اعداد کهنه توصیف نشود:

• نسخهٔ Python در README باید با `deploy/Dockerfile` و `.github/workflows/ci.yml` یکی باشد.
• فایل مرجع وضعیت باید وجود داشته باشد و فاز جاری را به‌درستی اعلام کند.
• `docs/audit/MASTER_STATUS.md` و `docs/audit/status.json` باید کهنه‌نبودن خود را
  اعلام کنند (وگرنه خوانندهٔ بعدی آن‌ها را وضعیت فعلی می‌خواند).
"""
import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _src(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


def test_state_file_exists_and_names_the_current_phase():
    """فایل مرجع وضعیت باید موجود باشد و فاز جاری را به‌درستی اعلام کند (نه صرفاً یک رشته)."""
    path = os.path.join(ROOT, "docs", "PROJECT_ROADMAP_AND_EXECUTION_STATE.md")
    assert os.path.isfile(path), (
        "فایل مرجع وضعیت وجود ندارد — بدون آن، چت بعدی نمی‌تواند نقطهٔ شروع را پیدا کند"
    )
    body = _src("docs/PROJECT_ROADMAP_AND_EXECUTION_STATE.md")
    assert re.search(r"\|\s*10\s*phases?\s*\|", body, re.I) or "| 10 |" in body, (
        "رودمپ ۱۰ فازی باید فاز ۱۰ را داشته باشد"
    )
    # فیلد واقعی «Current phase» را می‌سنجیم، نه وجودِ رشتهٔ IN_PROGRESS در جدول وضعیت
    m = re.search(r"\|\s*\*\*Current phase\*\*\s*\|\s*([^\n|]+)", body)
    assert m, "فیلد «Current phase» در فایل وضعیت پیدا نشد"
    assert re.search(r"Phase\s*1\b", m.group(1)), (
        "فاز جاری باید Phase 1 باشد، نه «%s»" % m.group(1).strip()
    )
    # مقادیر وضعیت باید در بخش راهنما تعریف شده باشند
    for token in ("NOT_STARTED", "IN_PROGRESS", "BLOCKED", "COMPLETED"):
        assert token in body, "حالت وضعیت «%s» تعریف نشده است" % token


def test_readme_python_matches_runtime_files():
    """نسخهٔ Python در README نباید با Dockerfile/CI/requirements در تضاد باشد."""
    readme = _src("README.md")
    m = re.search(r"Python (\d+\.\d+)\+", readme)
    assert m, "README باید نسخهٔ Python را به‌صورت «Python X.Y+» اعلام کند"
    claimed = m.group(1)

    dockerfile = _src("deploy/Dockerfile")
    ci = _src(".github/workflows/ci.yml")
    runtime = set(re.findall(r"python:(\d+\.\d+)", dockerfile))
    runtime |= set(re.findall(r'python-version:\s*"?(\d+\.\d+)"?', ci))
    assert runtime, "نسخهٔ Python در Dockerfile/CI پیدا نشد"
    assert claimed in runtime, (
        "README مدعی Python %s+ است ولی فایل‌های اجرایی %s را به کار می‌برند" % (claimed, sorted(runtime))
    )


def test_readme_test_count_matches_collection():
    """عدد تست در README باید با تعداد واقعیِ جمع‌آوری‌شده یکی باشد (نه عددِ ثابتِ کهنه)."""
    import subprocess
    import sys

    r = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--collect-only", "-p", "no:cacheprovider"],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert r.returncode == 0, (
        "pytest --collect-only شکست خورد (exit %s):\n%s"
        % (r.returncode, (r.stdout[-400:] + r.stderr[-400:]))
    )
    m = re.search(r"(\d+) tests? collected", r.stdout + r.stderr)
    assert m, "خروجی pytest قابل تفسیر نبود:\n" + (r.stdout[-400:] + r.stderr[-400:])
    real = int(m.group(1))

    readme = _src("README.md")
    claimed = {int(x) for x in re.findall(r"tests?-(\d+)", readme)}
    claimed |= {int(x) for x in re.findall(r"#\s*(\d{3,})\s*تست", readme)}
    assert claimed, "README هیچ عددِ تستی ندارد"
    assert claimed == {real}, (
        "README مدعی %s تست است ولی واقعاً %d تست جمع‌آوری می‌شود" % (sorted(claimed), real)
    )


def test_audit_status_files_declare_their_age():
    """اسناد ممیزی باید کهنه‌نبودن خود را اعلام کنند و به فایل مرجع اشاره کنند.

    این نگهبان عمداً فراتر از هدر می‌رود: یک «Reminder» در انتها یا یک فیلد
    `current_phase` در JSON می‌تواند همانقدر گمراه‌کننده باشد که یک هدر.
    """
    master = _src("docs/audit/MASTER_STATUS.md")
    assert "HISTORICAL DOCUMENT" in master, "MASTER_STATUS.md باید کهنه‌بودن خود را اعلام کند"
    assert "docs/PROJECT_ROADMAP_AND_EXECUTION_STATE.md" in master, (
        "MASTER_STATUS.md باید به فایل مرجع وضعیت اشاره کند"
    )
    # هیچ دستورِ «اول این فایل را بخوان» نباید باقی بماند
    assert "read docs/audit/MASTER_STATUS.md" not in master, (
        "MASTER_STATUS.md هنوز خواندنِ خودش را به‌عنوان مرجع دستور می‌دهد"
    )
    # اسناد تاریخی دیگر هم باید بنر داشته باشند
    for rel, marker in (
        ("docs/audit/ROADMAP.md", "HISTORICAL DOCUMENT"),
        ("ROADMAP.md", "مرجعِ واحد و زندهٔ وضعیت"),
        ("PHASE1-10-FINAL-REPORT.md", "سند تاریخی"),
    ):
        body = _src(rel)
        assert marker in body, "%s باید به‌عنوان سند تاریخی علامت‌گذاری شود" % rel

    data = json.loads(_src("docs/audit/status.json"))
    rec = data.get("reconciliation", {})
    assert rec.get("status_values_are_historical") is True, (
        "status.json باید صریحاً اعلام کند که مقادیر وضعیت تاریخی‌اند"
    )
    assert rec.get("current_main_sha"), "current_main_sha باید ثبت شود"
    # فیلدهای سطح‌بالا نباید فازِ کهنه را «جاری» اعلام کنند
    assert "PH-" not in str(data.get("current_phase", "")), (
        "status.json فیلد current_phase را به فازِ کهنه («%s») اشاره می‌دهد" % data.get("current_phase")
    )
    assert "PROJECT_ROADMAP_AND_EXECUTION_STATE" in str(data.get("superseded_by", "")), (
        "status.json باید superseded_by را به فایل مرجع وضعیت اشاره دهد"
    )