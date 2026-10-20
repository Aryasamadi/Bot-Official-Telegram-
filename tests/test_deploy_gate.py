# -*- coding: utf-8 -*-
"""نگهبانِ دروازهٔ تأیید دستیِ استقرار (H5).

ریشه: jobِ «deploy» در ``.github/workflows/deploy-vps.yml`` هیچ کلیدِ ``environment`` نداشت.
GitHub قاعدهٔ «Required reviewers» را فقط روی **محیط** اعمال می‌کند؛ jobی که محیط اعلام نکند
هرگز وارد محیط نمی‌شود و هیچ دروازه‌ای نمی‌تواند جلوی آن را بگیرد. نتیجه: یک push به
``main`` با CI سبز ⇒ استقرار **خودکار و بدون تأیید** به VPS.

این تست‌ها تضمین می‌کنند:
  H5-1  jobِ استقرار روی یک محیطِ قابل‌حفاظت اجرا می‌شود (نام محیط پایدار و یکسان بماند).
  H5-2  نامِ workflowِ CI در محرکِ ``workflow_run`` با ``name:`` واقعیِ ``ci.yml`` یکی است،
        وگرنه استقرار **بی‌صدا** دیگر اجرا نمی‌شود (و کسی متوجه نمی‌شود ربات دیگر منتشر نمی‌کند).

بدون وابستگی به PyYAML (این پروژه آن را ندارد) — تحلیل متنیِ محدود و دقیق.
"""
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# نامِ محیطی که jobِ استقرار روی آن اجرا می‌شود. باید با نامِ محیطی که در
# Settings → Environments ساخته می‌شود یکی باشد (پیش‌فرض: production).
DEPLOY_ENVIRONMENT = "production"

DEPLOY_WORKFLOW = os.path.join(ROOT, ".github", "workflows", "deploy-vps.yml")
CI_WORKFLOW = os.path.join(ROOT, ".github", "workflows", "ci.yml")


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _job_block(text, job):
    """بلوکِ یک job در YAML (سطح job = ۴ فاصله، زیرمجموعهٔ «jobs:» = ۲ فاصله)."""
    lines = text.splitlines()
    start = None
    for i, ln in enumerate(lines):
        if ln.rstrip() == "  %s:" % job:
            start = i + 1
            break
    if start is None:
        return None
    out = []
    for ln in lines[start:]:
        if ln.strip() and not ln.startswith("    "):
            break
        out.append(ln)
    return out


def test_deploy_job_declares_a_protectable_environment():
    """jobِ استقرار باید روی یک محیط اجرا شود تا دروازهٔ تأیید دستی ممکن باشد."""
    assert os.path.isfile(DEPLOY_WORKFLOW), "workflowِ استقرار یافت نشد"
    block = _job_block(_read(DEPLOY_WORKFLOW), "deploy")
    assert block is not None, "jobِ «deploy» در deploy-vps.yml یافت نشد"
    envs = [ln.split(":", 1)[1].strip() for ln in block
            if ln.strip().startswith("environment:")]
    assert envs, ("jobِ «deploy» کلیدِ environment ندارد ⇒ هیچ قاعدهٔ محیطی نمی‌تواند "
                  "استقرار خودکار را تا تأیید دستی متوقف کند")
    assert envs[0] == DEPLOY_ENVIRONMENT, (
        "نامِ محیطِ استقرار %r است ولی باید %r باشد (نام باید با تنظیمات مخزن یکی بماند)"
        % (envs[0], DEPLOY_ENVIRONMENT))


def test_deploy_workflow_trigger_matches_the_ci_workflow_name():
    """نامِ CI در محرکِ workflow_run باید با name: واقعیِ ci.yml یکی باشد."""
    ci = _read(CI_WORKFLOW)
    dep = _read(DEPLOY_WORKFLOW)
    m = re.search(r"^name:\s*(\S.*?)\s*$", ci, re.M)
    assert m, "نامِ workflowِ CI در ci.yml یافت نشد"
    ci_name = m.group(1).strip()
    m2 = re.search(r"workflows:\s*\[([^\]]*)\]", dep)
    assert m2, "محرکِ workflow_run در deploy-vps.yml یافت نشد"
    referenced = [x.strip().strip("'\"") for x in m2.group(1).split(",") if x.strip()]
    assert ci_name in referenced, (
        "deploy-vps.yml به workflowِ %r اشاره می‌کند ولی نامِ CI در ci.yml %r است ⇒ "
        "استقرار دیگر هرگز اجرا نمی‌شود" % (referenced, ci_name))
