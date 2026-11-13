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
    # فازِ جاری = اولین فازی که در جدولِ رودمپ COMPLETED نیست؛ این نگهبان با پیشرویِ
    # فازها سخت‌کد نمی‌شود (قبلاً «Phase 1» هاردکد بود و با بستنِ فاز ۱ دروغ می‌شد).
    phases = []
    for line in body.splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 4 or not cells[0].isdigit():
            continue
        status = cells[3].replace("*", "").replace("`", "").strip().split()[0] if cells[3].strip() else ""
        phases.append((int(cells[0]), status))
    assert phases, "جدول رودمپ ۱۰ فازی با ستونِ وضعیت پیدا نشد"
    open_phases = [(n, s) for n, s in phases if s != "COMPLETED"]
    if open_phases:
        first = open_phases[0][0]
        assert re.search(r"Phase\s*%s\b" % first, m.group(1)), (
            "فاز جاری باید Phase %s باشد (اولین فازِ نا تمامِ رودمپ)، نه «%s»" % (first, m.group(1).strip())
        )
    else:
        assert re.search(r"COMPLETED", m.group(1)), (
            "همهٔ فازها COMPLETED هستند؛ فیلد «Current phase» باید این را اعلام کند، نه «%s»" % m.group(1).strip()
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


def test_every_open_pr_has_a_disposition_in_the_roadmap():
    """هر PR باز باید تعیین‌تکلیفِ صریح داشته باشد — داخل فازِ خودش.

    بدون این نگهبان، یک PR می‌تواند بی‌صدا از قلم بیفتد و تا آخر باز بماند؛
    در حالی که قاعدهٔ «هر فاز با PRهای خودش merge می‌شود» نقض شده است.
    """
    body = _src("docs/PROJECT_ROADMAP_AND_EXECUTION_STATE.md")
    assert "Disposition" in body or "تعیین‌تکلیف" in body, (
        "فایل وضعیت باید بخشِ تعیین‌تکلیفِ PRها را داشته باشد"
    )
    # هر PR زنجیره باید با تصمیم صریح (MERGE یا MERGED) مشخص شده باشد
    for n in range(46, 57):
        row = re.search(r"^\|\s*\*\*#%d\*\*\s*\|(.*)$" % n, body, re.M)
        assert row, "PR #%d در جدول تعیین‌تکلیف نیست" % n
        cells = [c.strip() for c in row.group(1).split("|")]
        # ستون‌ها: عنوان | فازِ مالک | تصمیم | ترتیبِ merge
        assert len(cells) >= 3, "ردیف PR #%d ستون‌های کافی ندارد" % n
        assert "MERGE" in cells[2], (
            "PR #%d تصمیم صریح ندارد (باید MERGE یا MERGED باشد)" % n
        )
        phase_cell = cells[1]
        assert re.search(r"Phase\s*(10|[1-9])\b", phase_cell), (
            "PR #%d فازِ مالک را ندارد (ستون: %r)" % (n, phase_cell)
        )
    # قاعدهٔ «merge در پایان فاز» باید صریحاً ثبت شده باشد
    assert re.search(r"COMPLETED.*PR", body, re.S) or "still open" in body, (
        "قاعدهٔ «فاز با PRهای بازِ خودش COMPLETED نیست» ثبت نشده است"
    )


def test_merge_gate_workaround_is_documented_and_verifiable():
    """راه‌حلِ موقتِ برداشتن گیتِ تأیید باید مستند باشد و راه بازگرداندنش هم باشد."""
    body = _src("docs/PROJECT_ROADMAP_AND_EXECUTION_STATE.md")
    assert "updateBranchProtectionRule" in body, (
        "راهِ بازگرداندنِ قاعدهٔ تأیید (GraphQL) باید مستند باشد"
    )
    assert "required_approving_review_count" in body, (
        "نامِ فیلدِ قاعدهٔ تأیید باید ثبت شود"
    )
    # قاعدهٔ نگهبانی: درِ امنیتی نباید باز بماند
    assert re.search(r"Never leave a security gate disabled", body), (
        "قاعدهٔ «گیت امنیتی نباید باز بماند» باید در توافق کاری باشد"
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
    ):
        body = _src(rel)
        assert marker in body, "%s باید به‌عنوان سند تاریخی علامت‌گذاری شود" % rel

    # #47 دو سندِ ریشه را بازنشسته کرد؛ بنرِ تاریخیِ آن‌ها نباید گم شود: متنش در
    # §3.1 فایلِ وضعیت حفظ شده و خودِ فایل‌ها دیگر نباید وجود داشته باشند.
    state = _src("docs/PROJECT_ROADMAP_AND_EXECUTION_STATE.md")
    assert "3.1 Retired documents" in state and "سند تاریخی" in state, (
        "بنرِ PHASE1-10-FINAL-REPORT.md باید در §3.1 فایلِ وضعیت حفظ شود"
    )
    for retired in ("PHASE1-10-FINAL-REPORT.md", "DEPLOY-VPS-UPDATE.md"):
        assert not os.path.exists(os.path.join(ROOT, retired)), (
            "%s باید بازنشسته (حذف) شده باشد؛ محتوایش در §3.1 و تاریخِ گیت است" % retired
        )

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
