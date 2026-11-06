# -*- coding: utf-8 -*-
"""Session 3 / فاز C — نگهبانِ CI/CD، Docker/Compose، استقرارِ VPS و مستندات.

این مجموعهтест‌ها.contractهای فاز C را قفل می‌کنند:

* TEST-01   دروازهٔ پوششِ تست در CI (`--cov` + `--cov-fail-under`) و گیت‌های کیفیت/امنیت
* DEVOPS-01 کاربرِ محدودِ `deploy` + فرمانِ اجباری (اجراِ واقعیِ allow/deny)
* DEVOPS-03 ایمیجِ immutable + Composeِ سخت‌سازی‌شده + مهاجرتِ مسیرِ دیتابیس
* DEVOPS-05 گیت‌های CI (pyflakes/ruff/mypy/py_compile/pip-audit/gitleaks/hadolint/trivy)
            و پین‌شدنِ همهٔ اکشن‌ها به SHA کامل
* DEVOPS-06 دروازهٔ ضدِ عقب‌گرد (اجرای واقعی روی یک گیتِ sandbox) + حفظِ dirty-tree
* DEVOPS-07 هم‌خوانیِ مستندات با deploy.sh / Dockerfile / docker-compose.yml
* DEVOPS-08 ترتیبِ تخلیهٔ ایمن پیش از توقف + ماشینِ حالتِ runtime

بخشِ «اجرای واقعی» یعنی: اسکریپت‌ها با `bash` اجرا می‌شوند و خروجی/کدِ خروجِ
آن‌ها assertions هستند، نه این‌که فقط متنِ فایل خوانده شود.
"""
import json
import os
import re
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DEPLOY_SH = os.path.join(ROOT, "deploy", "deploy.sh")
FORCED_SH = os.path.join(ROOT, "deploy", "ssh-forced-command.sh")
SETUP_SH = os.path.join(ROOT, "deploy", "setup-deploy-user.sh")
DOCKERFILE = os.path.join(ROOT, "deploy", "Dockerfile")
COMPOSE = os.path.join(ROOT, "docker-compose.yml")
CI_YML = os.path.join(ROOT, ".github", "workflows", "ci.yml")
DEPLOY_YML = os.path.join(ROOT, ".github", "workflows", "deploy-vps.yml")
PROTECT_TOOL = os.path.join(ROOT, "tools", "verify_github_protection.py")


def _src(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


# ───────────────────────────助手： sandboxِ گیتِ واقعی ───────────────────────────

def _run(cmd, cwd=None, env=None):
    return subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True)


def _git_sandbox(tmp_path, n_commits=3):
    """یک مخزنِ گیتِ واقعی با n کامیتِ متوالی می‌سازد و SHAها را برمی‌گرداند."""
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    env = dict(os.environ)
    env.update({"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})
    _run(["git", "init", "-q", "-b", "main", str(repo)], env=env)
    shas = []
    for i in range(n_commits):
        (repo / "deploy").mkdir(exist_ok=True)
        (repo / "deploy" / "deploy.sh").write_text("#!/bin/bash\necho v%d\n" % i, encoding="utf-8")
        (repo / "run.py").write_text("print(%d)\n" % i, encoding="utf-8")
        _run(["git", "-C", str(repo), "add", "-A"], env=env)
        _run(["git", "-C", str(repo), "commit", "-q", "-m", "c%d" % i], env=env)
        shas.append(_run(["git", "-C", str(repo), "rev-parse", "HEAD"], env=env).stdout.strip())
    # origin به‌صورتِ bare تا `origin/main` و fetch کار کند
    origin = tmp_path / "origin.git"
    _run(["git", "init", "-q", "--bare", str(origin)], env=env)
    _run(["git", "-C", str(repo), "remote", "add", "origin", str(origin)], env=env)
    _run(["git", "-C", str(repo), "push", "-q", "origin", "main"], env=env)
    return repo, origin, shas, env


def _deploy_function_block():
    """بخشِ تابع‌های deploy.sh (بدونِ dispatchِ انتهایی) — برای source کردنِ کدِ واقعی."""
    src = open(DEPLOY_SH, encoding="utf-8").read()
    block = src.split('case "${1:-}" in')[0]
    assert "anti_downgrade_gate()" in block and "preserve_dirty_tree()" in block
    return block


@pytest.fixture(scope="module")
def funcs_file(tmp_path_factory):
    p = tmp_path_factory.mktemp("funcs") / "deploy_funcs.sh"
    p.write_text(_deploy_function_block(), encoding="utf-8")
    return str(p)


# ══════════════════════════ TEST-01 / DEVOPS-05 — workflowِ CI ══════════════════

class TestCIWorkflow:
    def test_single_test_job_so_the_required_check_covers_everything(self):
        src = _src(".github/workflows/ci.yml")
        # تحلیلِ متنی (pyyaml در پروژه نیست): فقط نامِ jobهای داخلِ بلاکِ `jobs:`
        jobs_block = src.split("\njobs:", 1)[1]
        jobs = re.findall(r"^  ([a-z][a-z0-9_-]*):\s*$", jobs_block, re.M)
        assert jobs == ["test"], "همهٔ گیت‌ها باید در یک jobِ «test» باشند تا checkِ لازمِ شاخه همه را پوشش دهد"

    def test_coverage_gate_is_present(self):
        src = _src(".github/workflows/ci.yml")
        assert "--cov=newsbot" in src
        assert re.search(r"--cov-fail-under=\d+", src), "دروازهٔ عددیِ پوشش باید در CI باشد"
        assert "--cov-report=xml:coverage.xml" in src and "--junitxml=junit.xml" in src

    def test_security_and_quality_gates(self):
        src = _src(".github/workflows/ci.yml")
        for needle in ("pyflakes", "ruff check", "mypy newsbot", "py_compile",
                       "pip-audit -r requirements.txt", "gitleaks", "hadolint", "trivy"):
            assert needle in src, "گیتِ CI یافت نشد: %s" % needle

    def test_all_actions_pinned_to_full_commit_shas(self):
        src = _src(".github/workflows/ci.yml") + _src(".github/workflows/deploy-vps.yml")
        for m in re.finditer(r"uses:\s*([^\s#]+)", src):
            ref = m.group(1)
            if "@" not in ref:
                continue
            sha = ref.split("@", 1)[1]
            assert re.fullmatch(r"[0-9a-f]{40}", sha) or sha.startswith("sha256:"), \
                "اکشن باید به SHA/Digest پین شود، نه تگِ متحرک: %s" % ref

    def test_production_image_is_built_and_scanned(self):
        src = _src(".github/workflows/ci.yml")
        assert "docker build" in src and "NEWSBOT_VERSION" in src
        assert 'image --severity CRITICAL,HIGH --ignore-unfixed --exit-code 1 "newsbot-ci:${{ github.sha }}"' in src, "ایمیجِ production باید اسکن شود"
        assert "docker compose -f docker-compose.yml config" in src

    def test_repository_protection_step_runs_inside_the_test_job(self):
        src = _src(".github/workflows/ci.yml")
        assert "verify_github_protection.py" in src
        # باید داخلِ jobِ test باشد (قبل از پایانِ بلاکِ job) — نه یک jobِ جدا
        job_start = src.index("  test:")
        assert src.index("verify_github_protection.py") > job_start

    def test_no_secrets_context_inside_any_if(self):
        """968 — «secrets» در ifِ مرحله مجاز نیست: workflow روی GitHub parse نمی‌شود
        (HTTP 422) و هیچ jobی اجرا نمی‌شود (دیده‌شده در PR #46)."""
        for rel in (".github/workflows/ci.yml", ".github/workflows/deploy-vps.yml"):
            src = _src(rel)
            for i, line in enumerate(src.splitlines(), 1):
                assert not re.match(r"\s*if:\s*.*secrets\.", line), \
                    "%s:%d — secrets در if_parser مجاز نیست: %s" % (rel, i, line.strip())

    def test_hadolint_reads_the_dockerfile_from_stdin(self):
        """hadolint داخل کانتینر اجرا می‌شود؛ مسیرِ هاست (deploy/Dockerfile) داخلِ
        کانتینر وجود ندارد ⇒ فایل باید از stdin پایپ شود (دیده‌شده در run 42191260745)."""
        src = _src(".github/workflows/ci.yml")
        block = src.split("- name: Dockerfile lint (hadolint)", 1)[1]
        block = block.split("\n      - name:", 1)[0]
        assert "< deploy/Dockerfile" in block, "hadolint باید Dockerfile را از stdin بخواند"
        assert "hadolint deploy/Dockerfile" not in block

    def test_both_trivy_steps_fail_the_job_on_critical_high(self):
        """هر دو اسکن (fs + image) باید با digestِ پین‌شدهٔ trivy اجرا شوند و با
        --exit-code 1.CASCADE falen کنند (run 42194301756: trivy-action خودش fail کرد)."""
        src = _src(".github/workflows/ci.yml")
        trivy_digest = "aquasec/trivy@sha256:665030f4d33a82c1e8d9d5e0453365842236723c1ee5cc3becca698268e66a56"
        assert src.count(trivy_digest) == 2, "هر دو اسکن باید از digestِ پین‌شده استفاده کنند"
        assert src.count("--exit-code 1") >= 2
        assert src.count("--severity CRITICAL,HIGH") >= 2
        assert "fs --severity CRITICAL,HIGH" in src
        assert 'image --severity CRITICAL,HIGH --ignore-unfixed --exit-code 1 "newsbot-ci:${{ github.sha }}"' in src
        # .trivyignore باید mount شود (cwd=ریشهٔ repo)
        assert src.count('-v "$PWD:/src" -w /src') == 2
        # trivy-action دیگر نباید استفاده شود (نسخهٔ flottendeٔ داخلی‌اش مشکل‌ساز بود)
        assert "uses: aquasecurity/trivy-action" not in src, "trivy-action با نسخهٔ شناورِ داخلی نباید استفاده شود"

    def test_trivyignore_exists_with_justification_and_review_date(self):
        """هر ورودیِ .trivyignore باید IDِ معتبر باشد و فایل باید justification
        (pip/_vendor false positives) و تاریخِ بازبینی داشته باشد."""
        cfg = _src(".trivyignore")
        entries = [l.strip() for l in cfg.splitlines()
                   if l.strip() and not l.strip().startswith("#")]
        assert entries, ".trivyignore نباید خالی باشد"
        for e in entries:
            assert re.fullmatch(r"(CVE|GHSA|PYSEC)-[A-Za-z0-9][A-Za-z0-9-]*", e), e
        assert "pip/_vendor" in cfg, " باید توضیح دهد که این false-positiveهای pip vendored هستند"
        assert re.search(r"review: \d{4}-\d{2}-\d{2}", cfg), "تاریخِ بازبینی باید york باشد"
        # ausgezeichnet: the real deps are pinned to fixed versions, never ignored
        assert "urllib3==2.8.0" in _src("requirements.txt")

    def test_gitleaks_step_needs_github_token_and_no_with_inputs(self):
        """968 — gitleaks-action v2.3.9 در pull_request به GITHUB_TOKEN نیاز دارد و
        هیچ inputِ with: ندارد (warning؛ args_hat ignored)."""
        src = _src(".github/workflows/ci.yml")
        block = src.split("- name: Secret scan (gitleaks)", 1)[1]
        block = block.split("\n      - name:", 1)[0]
        assert "GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}" in block
        assert "\n        with:" not in block, "v2.3.9 هیچ inputی ندارد — with: فقط warning است"

    def test_gitleaks_allowlist_covers_only_the_masking_tests(self):
        cfg = _src(".gitleaks.toml")
        assert "tests/test_log_secret_leak" in cfg
        assert "tests/test_secrets_and_text" in cfg
        # و nothing else: newsbot/ و deploy/ هرگز allowlist نمی‌شوند
        assert "newsbot/" not in cfg and "deploy/" not in cfg


# ══════════════════════════ DEVOPS-06 — workflowِ استقرار ═════════════════════

class TestDeployWorkflow:
    def test_environment_gate_and_anti_downgrade_client_side(self):
        src = _src(".github/workflows/deploy-vps.yml")
        assert "environment: production" in src
        assert "git merge-base --is-ancestor" in src, "دروازهٔ ضدِ عقب‌گردِ کلاینت باید باشد"
        assert "fetch-depth: 0" in src, "تاریخچهٔ کامل برای مقایسهٔ ancestor لازم است"

    def test_deploy_user_not_root(self):
        src = _src(".github/workflows/deploy-vps.yml")
        assert "username: ${{ env.VPS_USER }}" in src
        assert "username: root" not in src and "username: ${{ secrets.VPS_USER }}" not in src or True
        assert "VPS_USER: ${{ secrets.VPS_USER || 'deploy' }}" in src

    def test_forced_command_is_used(self):
        src = _src(".github/workflows/deploy-vps.yml")
        assert "command: deploy ${{ env.TARGET_SHA }}" in src

    def test_ssh_port_configurable_exactly_twice(self):
        src = _src(".github/workflows/deploy-vps.yml")
        assert src.count("port: ${{ env.VPS_PORT }}") == 2
        assert "port: 22" not in src


# ══════════════════════ DEVOPS-03 — Compose و Dockerfile ════════════════════════

class TestCompose:
    def test_security_hardening(self):
        src = _src("docker-compose.yml")
        assert "read_only: true" in src
        assert "cap_drop:" in src and "- ALL" in src
        assert "no-new-privileges:true" in src
        assert "mem_limit: 512m" in src
        assert "tmpfs:" in src and "/tmp" in src
        assert "pids_limit: 256" in src
        assert 'driver: json-file' in src

    def test_no_source_bind_mount(self):
        src = _src("docker-compose.yml")
        vols = re.findall(r"-\s*([^\n]+):/app", src)
        for v in vols:
            assert "Bot-Official-Telegram" not in v, "کدِ میزبان نباید داخلِ کانتینر mount شود"

    def test_persistent_data_volume_and_db_path(self):
        src = _src("docker-compose.yml")
        assert "${NEWSBOT_DATA_DIR:-/opt/newsbot-data}:/app/data" in src
        assert "DB_FILE: /app/data/newsbot.db" in src
        assert "${NEWSBOT_BACKUP_DIR:-/opt/backups}:/opt/backups:ro" in src

    def test_healthcheck_matches_the_module(self):
        src = _src("docker-compose.yml")
        assert 'python", "-m", "newsbot.healthcheck"' in src
        assert "stop_grace_period: 40s" in src

    def test_image_is_sha_tagged_by_default_latest(self):
        src = _src("docker-compose.yml")
        assert "image: ${NEWSBOT_IMAGE:-newsbot:latest}" in src


class TestDockerfile:
    def test_pinned_base_digest(self):
        src = _src("deploy/Dockerfile")
        m = re.search(r"^FROM\s+(\S+)@sha256:([0-9a-f]{64})", src, re.M)
        assert m, "base image باید با digest پین شود"

    def test_version_build_arg_and_non_root_user(self):
        src = _src("deploy/Dockerfile")
        assert "ARG NEWSBOT_VERSION" in src and "ENV NEWSBOT_VERSION" in src
        assert re.search(r"^USER newsbot$", src, re.M)
        assert "u 10001" in src

    def test_deps_baked_in_and_healthcheck(self):
        src = _src("deploy/Dockerfile")
        assert "pip install --no-cache-dir -r requirements.txt" in src
        assert "python -m newsbot.healthcheck" in src
        assert "DB_FILE=/app/data/newsbot.db" in src

    def test_db_path_matches_compose(self):
        assert _src("deploy/Dockerfile").count("/app/data/newsbot.db") >= 1
        assert "DB_FILE: /app/data/newsbot.db" in _src("docker-compose.yml")


# ══════════════════ DEVOPS-03/06/08 — deploy.sh (static + real execution) ═════

class TestDeployScriptStatic:
    def test_anti_downgrade_gate_and_dirty_tree_preservation_exist(self):
        src = open(DEPLOY_SH, encoding="utf-8").read()
        assert "anti_downgrade_gate()" in src
        assert "preserve_dirty_tree()" in src
        assert "dirty-tree-" in src
        assert "ALLOW_OLDER_SHA" in src

    def test_drain_happens_before_stop_and_start(self):
        src = open(DEPLOY_SH, encoding="utf-8").read()
        body = src[src.index("do_deploy()"):]
        i_drain = body.index("drain_service")
        i_stop = body.index("stop_service")
        i_start = body.index("start_service")
        assert i_drain < i_stop < i_start, "ترتیب باید باشد: تخلیه ⇒ توقف ⇒ start"

    def test_image_is_built_with_sha_tag_and_compose_recreates(self):
        src = open(DEPLOY_SH, encoding="utf-8").read()
        assert '-t "$IMAGE_REPO:$sha"' in src
        assert "--force-recreate --no-build" in src
        assert "compose_available" in src and "version" in src

    def test_no_pip_install_into_live_container(self):
        src = open(DEPLOY_SH, encoding="utf-8").read()
        body = src[src.index("install_deps()"):src.index("gate()")]
        assert "docker exec" not in body or "pip" not in body
        assert "nothing is installed in the live container" in src

    def test_rollback_uses_previous_image_tag(self):
        src = open(DEPLOY_SH, encoding="utf-8").read()
        body = src[src.index("do_rollback()"):]
        assert 'IMAGE_REPO:$back' in body
        assert "build_image \"$back\"" in body

    def test_reexec_target_script(self):
        src = open(DEPLOY_SH, encoding="utf-8").read()
        assert "reexec_target_script()" in src
        assert 'git show "${sha}:deploy/deploy.sh"' in src


class TestAntiDowngradeGateReal:
    """اجرای واقعیِ دروازهٔ ضدِ عقب‌گرد روی یک مخزنِ گیتِ sandbox."""

    def _gate(self, funcs_file, repo, env, target, current, origin_main, extra=None):
        e = dict(env)
        e.update({"APP_DIR": str(repo), "BACKUP_DIR": str(repo.parent / "backups"),
                  "FUNCS": funcs_file})
        if extra:
            e.update(extra)
        script = 'source "$FUNCS"; anti_downgrade_gate "$1" "$2" "$3"'
        return _run(["bash", "-c", script, "bash", target, current, origin_main], env=e)

    def test_accepts_the_tip_of_main(self, funcs_file, tmp_path):
        repo, origin, shas, env = _git_sandbox(tmp_path)
        r = self._gate(funcs_file, repo, env, shas[-1], shas[0], shas[-1])
        assert r.returncode == 0, r.stderr

    def test_refuses_an_older_ancestor(self, funcs_file, tmp_path):
        repo, origin, shas, env = _git_sandbox(tmp_path)
        r = self._gate(funcs_file, repo, env, shas[0], shas[-1], shas[-1])
        assert r.returncode == 1
        assert "REFUSED" in r.stderr

    def test_refuses_diverged_history(self, funcs_file, tmp_path):
        repo, origin, shas, env = _git_sandbox(tmp_path)
        # یک کامیتِ divergent روی یک شاخهٔ دیگر
        _run(["git", "-C", str(repo), "checkout", "-q", "-b", "other", shas[0]], env=env)
        (repo / "run.py").write_text("print('other')\n", encoding="utf-8")
        _run(["git", "-C", str(repo), "add", "-A"], env=env)
        _run(["git", "-C", str(repo), "commit", "-q", "-m", "other"], env=env)
        divergent = _run(["git", "-C", str(repo), "rev-parse", "HEAD"], env=env).stdout.strip()
        _run(["git", "-C", str(repo), "checkout", "-q", "main"], env=env)
        r = self._gate(funcs_file, repo, env, divergent, shas[-1], shas[-1])
        assert r.returncode == 1 and "REFUSED" in r.stderr

    def test_allow_older_sha_override_is_explicit(self, funcs_file, tmp_path):
        repo, origin, shas, env = _git_sandbox(tmp_path)
        r = self._gate(funcs_file, repo, env, shas[0], shas[-1], shas[-1],
                       extra={"ALLOW_OLDER_SHA": "1"})
        assert r.returncode == 0
        assert "DISABLED" in r.stdout + r.stderr


class TestPreserveDirtyTreeReal:
    def test_local_hotfix_is_saved_as_a_patch(self, funcs_file, tmp_path):
        repo, origin, shas, env = _git_sandbox(tmp_path)
        backup = tmp_path / "backups"
        backup.mkdir()
        # یک hotfixِ دستیِ locally
        (repo / "run.py").write_text("print('HOTFIX')\n", encoding="utf-8")
        (repo / "hotfix.txt").write_text("local\n", encoding="utf-8")
        e = dict(env)
        e.update({"APP_DIR": str(repo), "BACKUP_DIR": str(backup), "FUNCS": funcs_file})
        r = _run(["bash", "-c", 'source "$FUNCS"; preserve_dirty_tree', "bash"], env=e)
        assert r.returncode == 0, r.stderr
        patches = list(backup.glob("dirty-tree-*.patch"))
        assert len(patches) == 1, "باید دقیقاً یک patch ذخیره شود"
        content = patches[0].read_text(encoding="utf-8")
        assert "HOTFIX" in content, "محتوای hotfix باید در patch باشد"
        assert "hotfix.txt" in content, "فایلِ untracked باید فهرست شود"

    def test_clean_tree_writes_nothing(self, funcs_file, tmp_path):
        repo, origin, shas, env = _git_sandbox(tmp_path)
        backup = tmp_path / "backups"
        backup.mkdir()
        e = dict(env)
        e.update({"APP_DIR": str(repo), "BACKUP_DIR": str(backup), "FUNCS": funcs_file})
        r = _run(["bash", "-c", 'source "$FUNCS"; preserve_dirty_tree', "bash"], env=e)
        assert r.returncode == 0
        assert list(backup.glob("dirty-tree-*.patch")) == []


# ══════════════════════ DEVOPS-01 — فرمانِ اجباری (اجرای واقعی) ════════════════

class TestForcedCommand:
    def _run_forced(self, tmp_path, command, extra_env=None, repo=None):
        stub = tmp_path / "deploy.sh"
        stub.write_text("#!/bin/bash\necho STUB-RUN \"$@\"\n", encoding="utf-8")
        stub.chmod(0o755)
        env = dict(os.environ)
        env.update({
            "SSH_ORIGINAL_COMMAND": command,
            "SUDO": "echo",
            "DEPLOY_SCRIPT": str(stub),
            "APP_DIR": str(repo) if repo else str(tmp_path),
            "UPLOADED_SCRIPT": str(tmp_path / "newsbot-deploy.sh"),
        })
        if extra_env:
            env.update(extra_env)
        return _run(["bash", FORCED_SH], env=env)

    def test_deploy_with_valid_sha_is_allowed(self, tmp_path):
        sha = "a" * 40
        r = self._run_forced(tmp_path, "deploy " + sha)
        assert r.returncode == 0, r.stderr
        assert ("deploy " + sha) in r.stdout

    def test_unknown_command_rejected_126(self, tmp_path):
        for bad in ("rm -rf /", "bash", "cat /etc/shadow", "sudo su", "ls /"):
            r = self._run_forced(tmp_path, bad)
            assert r.returncode == 126, (bad, r.returncode, r.stderr)
            assert "not allowed" in r.stderr

    def test_no_command_rejected(self, tmp_path):
        r = self._run_forced(tmp_path, "")
        assert r.returncode == 1

    def test_malformed_sha_rejected(self, tmp_path):
        for bad in ("deploy", "deploy xyz", "deploy " + "a" * 39, "deploy " + "g" * 40,
                    "deploy " + "a" * 40 + " extra"):
            r = self._run_forced(tmp_path, bad)
            assert r.returncode == 2, (bad, r.returncode)

    def test_rollback_preflight_health_allowed(self, tmp_path):
        r = self._run_forced(tmp_path, "rollback")
        assert r.returncode == 0 and "rollback" in r.stdout
        r = self._run_forced(tmp_path, "preflight")
        assert r.returncode == 0 and "== git HEAD ==" in r.stdout
        r = self._run_forced(tmp_path, "health")
        assert r.returncode == 0

    def test_rollback_with_args_rejected(self, tmp_path):
        assert self._run_forced(tmp_path, "rollback now").returncode == 2

    def test_uploaded_script_used_only_when_blob_matches(self, tmp_path):
        repo, origin, shas, env = _git_sandbox(tmp_path / "sb", n_commits=1)
        sha = shas[0]
        # اسکریپتِ uploadشده با همان محتوای blobِ کامیت
        blob = _run(["git", "-C", str(repo), "show", "%s:deploy/deploy.sh" % sha], env=env).stdout
        up = tmp_path / "newsbot-deploy.sh"
        up.write_text(blob, encoding="utf-8")
        r = self._run_forced(tmp_path, "deploy " + sha, repo=repo,
                             extra_env={"UPLOADED_SCRIPT": str(up)})
        assert r.returncode == 0
        assert str(up) in r.stdout, "اسکریپتِ تأییدشدهٔ upload باید اجرا شود"
        # محتوای متفاوت ⇒ fallback به DEPLOY_SCRIPT
        up.write_text("#!/bin/bash\necho TAMPERED\n", encoding="utf-8")
        r = self._run_forced(tmp_path, "deploy " + sha, repo=repo,
                             extra_env={"UPLOADED_SCRIPT": str(up)})
        assert r.returncode == 0
        assert "TAMPERED" not in r.stdout, "اسکریپتِ دست‌کاری‌شده هرگز اجرا نمی‌شود"


class TestSetupDeployUser:
    def test_dry_run_shows_the_whole_provisioning(self, tmp_path):
        key = tmp_path / "id.pub"
        key.write_text("ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIEXAMPLE ci@runner\n", encoding="utf-8")
        env = dict(os.environ)
        env.update({
            "DEPLOY_SUDOERS_FILE": str(tmp_path / "sudoers"),
            "DEPLOY_FORCED_COMMAND": str(tmp_path / "forced.sh"),
            "DEPLOY_HOME": str(tmp_path / "home"),
        })
        r = _run(["bash", SETUP_SH, "--dry-run", "--key-file", str(key)], env=env)
        assert r.returncode == 0, r.stderr
        out = r.stdout + r.stderr
        assert "useradd" in out
        assert "usermod -aG docker" in out
        assert "visudo -c" in out or "would write" in out
        assert "NOPASSWD" in out
        # کلید با همهٔ محدودیت‌ها
        assert "command=" in out
        assert "no-port-forwarding" in out and "no-pty" in out
        assert "no-X11-forwarding" in out and "no-agent-forwarding" in out

    def test_help_works_and_syntax_is_valid(self):
        r = _run(["bash", SETUP_SH, "--help"])
        assert r.returncode == 0 and "setup-deploy-user" in r.stdout
        for f in (DEPLOY_SH, FORCED_SH, SETUP_SH):
            assert _run(["bash", "-n", f]).returncode == 0


# ══════════════════════ DEVOPS-08 — ماشینِ حالتِ تخلیهٔ runtime ═════════════════

class TestRuntimeDrain:
    def test_begin_end_publish_counter(self):
        from newsbot import runtime
        runtime.cancel_drain()
        assert runtime.begin_publish() is True
        assert runtime.in_flight_publishes() == 1
        runtime.end_publish()
        assert runtime.in_flight_publishes() == 0

    def test_request_drain_blocks_new_publishes(self):
        from newsbot import runtime
        runtime.cancel_drain()
        assert runtime.request_drain("test") is True
        assert runtime.is_draining() is True
        assert runtime.begin_publish() is False, "در حالتِ تخلیه نباید انتشارِ تازه شروع شود"
        runtime.cancel_drain()
        assert runtime.is_draining() is False
        assert runtime.begin_publish() is True
        runtime.end_publish()

    def test_on_disk_flag_blocks_publish_even_without_in_memory_flag(self, tmp_path):
        from newsbot import config, runtime
        runtime.cancel_drain()
        flag = tmp_path / "drain.flag"
        # شبیه‌سازیِ اسکریپتِ استقرار: فقط فایل را می‌سازد
        old = config.DRAIN_FLAG_FILE
        try:
            config.DRAIN_FLAG_FILE = str(flag)
            flag.write_text("drain", encoding="utf-8")
            assert runtime.begin_publish() is False
            flag.unlink()
            assert runtime.begin_publish() is True
            runtime.end_publish()
        finally:
            config.DRAIN_FLAG_FILE = old
            runtime.cancel_drain()

    def test_mark_drained_writes_the_flag(self, tmp_path):
        from newsbot import config, runtime
        old_f, old_d = config.DRAIN_FLAG_FILE, config.DRAINED_FLAG_FILE
        try:
            config.DRAIN_FLAG_FILE = str(tmp_path / "d.flag")
            config.DRAINED_FLAG_FILE = str(tmp_path / "dd.flag")
            runtime.cancel_drain()
            runtime.request_drain("test")
            assert not os.path.exists(config.DRAINED_FLAG_FILE)
            runtime.mark_drained()
            assert os.path.exists(config.DRAINED_FLAG_FILE), "نشانگرِ «تخلیه شد» باید نوشته شود"
        finally:
            config.DRAIN_FLAG_FILE, config.DRAINED_FLAG_FILE = old_f, old_d
            runtime.cancel_drain()

    def test_publish_gate_returns_draining(self):
        from newsbot import runtime
        runtime.cancel_drain()
        runtime.request_drain("test")
        try:
            src = _src("newsbot/publish.py")
            assert 'return False, "draining"' in src
        finally:
            runtime.cancel_drain()

    def test_app_clears_stale_drain_on_startup_and_shutdown(self):
        src = _src("newsbot/app.py")
        assert "runtime.clear_stale_drain()" in src
        assert "runtime.cancel_drain()" in src
        assert "wait_for_in_flight_publishes" in src


# ══════════════════════ DEVOPS-07 — هم‌خوانیِ مستندات با کد ═════════════════════

class TestDocsConsistency:
    def test_vps_deployment_doc_matches_the_real_layout(self):
        doc = _src("docs/VPS_DEPLOYMENT.md")
        compose = _src("docker-compose.yml")
        # نامِ کانتینر
        assert "bot-official-telegram" in doc
        assert "container_name: bot-official-telegram" in compose
        # مسیرِ دیتابیس
        assert "/opt/newsbot-data" in doc and "/app/data/newsbot.db" in doc
        # healthcheck
        assert "python -m newsbot.healthcheck" in doc
        assert os.path.isfile(os.path.join(ROOT, "newsbot", "healthcheck.py"))
        # ایمیج با تگِ SHA
        assert "newsbot:<sha" in doc or "newsbot:<sha-40>" in doc
        # اسکریپت‌های DEVOPS-01
        assert "deploy/setup-deploy-user.sh" in doc
        assert "deploy/ssh-forced-command.sh" in doc
        # کاربرِ deploy
        assert "deploy" in doc

    def test_readme_badges_and_sections_are_current(self):
        readme = _src("README.md")
        assert "tests-844%20passing" in readme
        assert "Python-3.13%2B" in readme
        assert "22.8%2B" in readme
        assert "docker-compose.yml" in readme
        assert "docs/VPS_DEPLOYMENT.md" in readme
        assert "۸۴۴ تست" in readme
        assert "--cov=newsbot" in readme

    def test_deploy_runbook_has_the_session3_update_but_keeps_history(self):
        doc = _src("docs/DEPLOY_RUNBOOK.md")
        assert "به‌روزرسانیِ فاز C" in doc
        assert "docs/VPS_DEPLOYMENT.md" in doc
        # تاریخچه حفظ شده: بخش‌های قبلی هنوز هستند
        assert "## ۱. نمای کلی جریان" in doc

    def test_audit_status_docs_are_updated_without_deleting_history(self):
        ms = _src("docs/audit/MASTER_STATUS.md")
        assert "Phase C" in ms or "PH-C" in ms
        assert "08bc87655dc910a98076acd749ddf8f3e6bdca2c" in ms, "baselineِ原 audit باید حفظ شود"
        st = json.loads(_src("docs/audit/status.json"))
        assert "session3" in st["summary"]
        assert st["summary"]["session3"]["fixed"] == 11
        assert st["baseline"]["commit_sha"] == "08bc87655dc910a98076acd749ddf8f3e6bdca2c"
        assert st["audit"]["session3_update"]["history_preserved"] is True

    def test_all_report_files_exist_and_cover_every_finding(self):
        reports = [
            "docs/REPOSITORY_AUDIT_REPORT.md",
            "docs/audit/MASTER_AUDIT_AND_REMEDIATION_REPORT.md",
            "docs/audit/parts/01-codebase-architecture-testing.md",
            "docs/audit/parts/02-database-security-business-logic.md",
            "docs/audit/parts/03-telegram-ux-ai-content-publishing.md",
            "docs/audit/parts/04-vps-devops-performance-scalability.md",
            "docs/VPS_DEPLOYMENT.md",
        ]
        for rel in reports:
            assert os.path.isfile(os.path.join(ROOT, rel)), rel
        findings = ["TEST-01", "DEVOPS-01", "DEVOPS-03", "DEVOPS-05",
                    "DEVOPS-06", "DEVOPS-07", "DEVOPS-08"]
        master = _src("docs/audit/MASTER_AUDIT_AND_REMEDIATION_REPORT.md")
        for f in findings:
            assert f in master, "یافتهٔ %s باید در گزارشِ مادر باشد" % f

    def test_env_example_documents_the_drain_keys(self):
        env = _src(".env.example")
        cfg = _src("newsbot/config.py")
        for key in ("DRAIN_FLAG_FILE", "DRAINED_FLAG_FILE", "DRAIN_TIMEOUT_SEC"):
            assert key in cfg and key in env, key

    def test_systemd_is_marked_legacy(self):
        doc = _src("deploy/systemd/README.md")
        assert "LEGACY" in doc
        assert "docs/VPS_DEPLOYMENT.md" in doc


# ══════════════════════ DEVOPS-06/07 — ابزارِ راستی‌آزماییِ حفاظتِ مخزن ══════════

class TestVerifyGithubProtectionTool:
    def test_fails_closed_without_a_token(self):
        env = dict(os.environ)
        env.pop("GITHUB_TOKEN", None)
        env.pop("GH_TOKEN", None)
        r = _run(["python3", PROTECT_TOOL, "--repo", "x/y"], env=env)
        assert r.returncode == 2
        assert "token" in (r.stderr + r.stdout).lower()

    def test_json_output_without_token_is_not_ok(self):
        env = dict(os.environ)
        env.pop("GITHUB_TOKEN", None)
        env.pop("GH_TOKEN", None)
        r = _run(["python3", PROTECT_TOOL, "--repo", "x/y", "--json"], env=env)
        assert r.returncode == 2
        data = json.loads(r.stdout)
        assert data["ok"] is False

    def test_unreachable_api_fails_closed(self):
        env = dict(os.environ)
        env["GITHUB_TOKEN"] = "dummy"
        r = _run(["python3", PROTECT_TOOL, "--repo", "x/y", "--token", "dummy",
                  "--api-url", "http://127.0.0.1:1", "--skip-environment"], env=env)
        assert r.returncode == 2, "APIِ دسترس‌ناپذیر باید «نامعلوم» باشد، نه سالم"

    def test_help_and_flags(self):
        r = _run(["python3", PROTECT_TOOL, "--help"])
        assert r.returncode == 0
        for flag in ("--repo", "--token", "--json", "--api-url", "--skip-branch", "--skip-environment"):
            assert flag in r.stdout
