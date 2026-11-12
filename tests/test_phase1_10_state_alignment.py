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
    """فایل مرجع وضعیت باید موجود باشد و فاز جاری را اعلام کند."""
    path = os.path.join(ROOT, "docs", "PROJECT_ROADMAP_AND_EXECUTION_STATE.md")
    assert os.path.isfile(path), (
        "فایل مرجع وضعیت وجود ندارد — بدون آن، چت بعدی نمی‌تواند نقطهٔ شروع را پیدا کند"
    )
    body = _src("docs/PROJECT_ROADMAP_AND_EXECUTION_STATE.md")
    assert "| 1 |" in body, "رودمپ ۱۰ فازی باید فاز ۱ را داشته باشد"
    assert "IN_PROGRESS" in body, "وضعیت جاری باید یکی از مقادیر مجاز باشد"
    assert "NOT_STARTED" in body and "COMPLETED" in body, "حالت‌های وضعیت باید تعریف شوند"


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
        [sys.executable, "-m", "pytest", "-q", "--collect-only"],
        cwd=ROOT, capture_output=True, text=True,
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
    """اسناد ممیزی باید کهنه‌نبودن خود را اعلام کنند تا وضعیت فعلی خوانده نشوند."""
    master = _src("docs/audit/MASTER_STATUS.md")
    assert "HISTORICAL DOCUMENT" in master, "MASTER_STATUS.md باید کهنه‌بودن خود را اعلام کند"
    assert "docs/PROJECT_ROADMAP_AND_EXECUTION_STATE.md" in master, (
        "MASTER_STATUS.md باید به فایل مرجع وضعیت اشاره کند"
    )

    data = json.loads(_src("docs/audit/status.json"))
    assert data.get("reconciliation", {}).get("status_values_are_historical") is True, (
        "status.json باید صریحاً اعلام کند که مقادیر وضعیت تاریخی‌اند"
    )
    assert data["reconciliation"]["current_main_sha"], "current_main_sha باید ثبت شود"