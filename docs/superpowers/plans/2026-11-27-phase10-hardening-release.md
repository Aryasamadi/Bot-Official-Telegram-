# Phase 10 — Hardening, Cleanup and Release Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the three open Phase 10 findings honestly — portable Windows tests (AUD-012), a tested `SECRETS_KEY` rotation tool plus runbook for the owner-gated rotation (AUD-001, owner scope = `BOT_TOKEN` + `SECRETS_KEY` only), and proof-based branch/document hygiene (AUD-014) — then prove the suite is green, record live acceptance (D-7), and deliver a release/rollback runbook and final report.

**Architecture:** Four small workstreams on top of the existing codebase: (W1) a shared Git-Bash test helper and an MSYS path-conversion fix in the deploy test sandbox; (W2) a pure `fernet_for(key)` in `newsbot/secret_store.py`, an idempotent atomic `tools/rotate_secrets_key.py`, and `migrate_ai_keys()` extended to `ai_providers`; (W3) server-side deletion of branches whose PR is **MERGED** and whose tip still equals the merged head, plus a zero-or-proven dead-code pass; (W4) runbooks, final report, state-file evidence and the release gate. The live `qa-live` run `46013627630` is already in progress with the **old** key and `db_mode=fresh`; it is adopted as D-7 if it succeeds. Rotation happens only after that run, and any future D-7 run is `fresh`.

**Tech Stack:** Python 3.13, pytest 9, SQLite, `cryptography` (Fernet), GitHub Actions (`qa-live`, `ci`), `gh` CLI, PowerShell on Windows for local commands, Git Bash for shell-script tests.

**Spec:** `docs/superpowers/specs/2026-11-26-phase10-hardening-release-design.md`

## Global Constraints

- **One phase at a time.** Do not start any phase beyond Phase 10 (the roadmap has exactly ten phases); Phase 10 stays `IN_PROGRESS` until D-7 and owner rotation evidence are recorded.
- **Evidence before claims.** Never write “green”, “rotated”, “scanned” or “released” without the command output / run URL / artifact that proves it.
- **Secrets:** read only from environment. Never pass a secret in argv, never print it, never write it to a report, state file, PR body, commit or artifact. Counts and variable names only.
- **Two live runs must not overlap.** The current run `46013627630` is in progress. Do not rotate `BOT_TOKEN`/`SECRETS_KEY`, do not delete the v1 cache, and do not touch `newsbot-deploy-pkg\newsbot.env` until it finishes.
- **Branch names in committed docs are bold, never backticks.** This is enforced by `tests/test_repo_integrity.py`.
- **PR protocol:** confirm `mergeable` / `mergeStateStatus`, CI `test` SUCCESS, run `gate.py <PR> lift`, `gh pr merge --squash --delete-branch`, `gate.py <PR> restore`, then `verify_protection.py` shows **15/15 IDENTICAL**.
- **Windows baseline:** exactly **19** known failures before this work (4 `test_deploy_script.py` + 15 `test_session3_cicd_docker_deploy_docs.py`). Target after W1: **0 failed**; Linux CI remains the final authority.
- **No product behaviour / schema change** except the small, tested `secret_store` refactor and `migrate_ai_keys()` extension. `sources.api_key` stays plaintext and is recorded as **D-12**.
- **No Dependabot merges in this phase** (owner decision: after release). **D-4** is recorded as open.
- **No force-push, no tag/release deletion, no production DB/VPS/provider-key work.**
- Run `glyph_scan8.py` on every new or edited Markdown file and PR body before committing.
- Bump the README collection count **after** the last test file is added and **before** the full-suite run; if the count changes, kill the run and restart at the final HEAD.

## Current Measured State (2026-11-27)

| Item | Fact | Evidence |
|---|---|---|
| `main` | `967afde` (PR #106), clean except the untracked design doc | `git status --short --branch` |
| Design | approved; untracked on `main` | `docs/superpowers/specs/2026-11-26-phase10-hardening-release-design.md` |
| Live run | `46013627630`, job `140555102637`, started `2026-11-27T14:11:14Z`, `db_mode=fresh`, “Run the live acceptance” running | `gh run view 46013627630 --json ...` |
| 15 Windows failures | `bash` resolves to `C:\WINDOWS\system32\bash.exe` (WSL) and fails with exit 127; Git Bash runs the same scripts correctly | direct run + `Get-Command bash` |
| 4 docker failures | Git Bash converts the container argument `/app` to `C:/Program Files/Git/app`; the stub log shows `docker exec ... -w C:/Program Files/Git/app ...`; the DB path becomes `C:\Program Files\Git\app\newsbot.db` | `pytest ... -q --tb=long`, sandbox `stub.log` |
| W1 result | after the fix: **97 passed** for `test_deploy_script.py` + `test_session3_cicd_docker_deploy_docs.py` (0 failed; the 19 known failures are gone) | `phase10_w1_full.txt` |
| F-2 | CI image scan red: **HIGH — CVE-2026-124851 (libzstd1)** in the pinned base image; fixed by PR0 | CI run `46016985685` |
| `pip-audit` local | `No known vulnerabilities found`, exit 0 | `python -m pip_audit -r requirements.txt --no-deps` |
| docker/trivy/hadolint local | docker is not installed; these scanners are CI-only evidence | `docker --version` (missing) |
| Branches | 50 remote heads: **36** with a MERGED PR still on origin, **13** without (11 Dependabot + 2 stale), **55** stale local tracking refs | `branch_audit10.py`, `gh pr list --state merged` |
| Collection | **1183**; README badge + Persian line are in sync | `pytest --collect-only -q` |
| V1 cache | `.github/workflows/qa-live.yml` uses `qa-live-db-v1-` | workflow lines 50–51, 110 |

## Delivery Sequence

1. **PR0 — security (F-2):** **fix/ci-libzstd-cve-2026-124851** (`libzstd1` upgrade + guard test). Merges first: the image scan runs on every PR, so no PR can be green before it.
2. **PR1 — docs:** **docs/phase10-design-plan** (design + plan + allowlist). Merge second.
2. **PR2 — code:** **phase10/hardening** (W1 + W2 + cache prefix v2 + `deploy/env.example` + README count). Merge second.
3. **Server-side branch cleanup:** after PR2 merge, delete only the 36 proven-merged branches and prune local refs; record the list for PR3.
4. **PR3 — records:** **docs/phase10-records** (runbooks, final report, state file, remaining-branch list, D-7/F-1 evidence if available). Merge third.
5. **Owner gate:** live D-7 result and secret rotation; if they complete after PR3, a small follow-up docs PR records the evidence and sets Phase 10 `COMPLETED`. No next phase starts automatically.

---

## Task 0: PR0 — close the HIGH image vulnerability (F-2)

**Files**
- Modify: `deploy/Dockerfile`
- Modify: `tests/test_session3_cicd_docker_deploy_docs.py`

- [ ] **Step 1: Reproduce from CI evidence.** `test` job of run `46016985685` fails in the “Image scan (trivy)” step: `HIGH — CVE-2026-124851 (libzstd1)`, installed `1.5.7+dfsg-1`, fixed `1.5.7+dfsg-1+deb13u1`. This affects every PR, so it must land first.

- [ ] **Step 2: Upgrade `libzstd1` in the image, minimal and in the existing style.**

```dockerfile
RUN apt-get update \
    && apt-get install -y --no-install-recommends --only-upgrade zlib1g libzstd1 \
    && rm -rf /var/lib/apt/lists/*
```

Keep the digest pinned; no full `apt-get upgrade`. Document CVE-2026-124851 in the comment block so a later digest bump can drop the block.

- [ ] **Step 3: Add the guard test.**

```python
def test_known_debian_cves_are_upgraded_in_the_image(self):
    src = _src("deploy/Dockerfile")
    assert re.search(r"--only-upgrade[^\n]*zlib1g", src), "zlib1g باید ارتقا یابد"
    assert re.search(r"--only-upgrade[^\n]*libzstd1", src), \
        "libzstd1 باید ارتقا یابد (CVE-2026-124851)"
    assert "CVE-2026-124851" in src, "دلیلِ ارتقا باید در Dockerfile مستند باشد"
```

- [ ] **Step 4: Run, commit, PR, gate.**

```powershell
& ".\.venv\Scripts\python.exe" -m pytest tests/test_session3_cicd_docker_deploy_docs.py -q -k "Dockerfile or trivy or hadolint"
git add deploy/Dockerfile tests/test_session3_cicd_docker_deploy_docs.py
git commit -m "fix(ci): upgrade libzstd1 for CVE-2026-124851"
```

Acceptance: the CI “Image scan (trivy)” step is SUCCESS (no CRITICAL/HIGH with `--ignore-unfixed`); merge via the gate protocol; protection 15/15.

---

## Task 1: PR1 — approve and commit the design + plan

**Files**
- Modify: `docs/superpowers/specs/2026-11-26-phase10-hardening-release-design.md`
- Create: `docs/superpowers/plans/2026-11-27-phase10-hardening-release.md`
- Modify: `tests/test_repo_integrity.py` (`PROPOSED_PATHS`, lines 27–32)

- [ ] **Step 1: Create the docs branch from the known-good tip.**

```powershell
git switch -c docs/phase10-design-plan 967afde
```

- [ ] **Step 2: Amend the design for the live-run ordering and the new observation.**

In §6.5, add:

- The live run `46013627630` started on `2026-11-27T14:11:14Z` with `db_mode=fresh` **before** rotation. Because `SECRETS_KEY` has not changed, this run is valid D-7 evidence; no v1 cache is involved.
- If this run fails, the next D-7 run happens **after** rotation, after deleting the v1 caches, and with `db_mode=fresh`.
- The owner-reported “504 / ~60 s connection test” is recorded as live observation **F-1**; it is not fixed in Phase 10 and needs a separate design amendment. Code evidence: `newsbot/ai_models.py:310-311` (30–60 s provider timeout), `newsbot/net_http.py:334`, and `ai_models.py:607` (`504` → `ai_timeout`).

In §3 non-goals, add: “fixing F-1 (504 / slow connection test)”.
In §11, keep the four owner answers and add **D-13** for the residual provider-key risk and **D-12** for `sources.api_key`.

- [ ] **Step 3: Add every not-yet-created path to the integrity allowlist.**

`tests/test_repo_integrity.py` must contain (inside `PROPOSED_PATHS`):

```python
"tests/_shell.py",                      # Phase 10 W1
"tests/test_phase10_secrets_rotation.py",  # Phase 10 W2
"tools/rotate_secrets_key.py",          # Phase 10 W2
"docs/OPS_SECRET_ROTATION.md",          # Phase 10 W4
"docs/RELEASE_RUNBOOK.md",              # Phase 10 W4
"docs/PHASE10_FINAL_REPORT.md",         # Phase 10 W4
```

`deploy/env.example` is already present; do not duplicate it.

- [ ] **Step 4: Scan glyphs and run the two guardians.**

```powershell
& ".\.venv\Scripts\python.exe" "C:\Users\Mustafa\AppData\Local\Temp\opencode\glyph_scan8.py" "docs\superpowers\specs\2026-11-26-phase10-hardening-release-design.md" "docs\superpowers\plans\2026-11-27-phase10-hardening-release.md"
& ".\.venv\Scripts\python.exe" -m pytest tests/test_repo_integrity.py tests/test_phase1_10_state_alignment.py -q
```

Expected: `suspicious lines: 0`; pytest has no failure.

- [ ] **Step 5: Commit, push and open PR1.**

```powershell
git add docs/superpowers/specs/2026-11-26-phase10-hardening-release-design.md docs/superpowers/plans/2026-11-27-phase10-hardening-release.md tests/test_repo_integrity.py
git commit -m "docs(phase10): approved design and implementation plan"
git push -u origin docs/phase10-design-plan
```

Open PR1 with a Persian body, include `Spec: ...`, `Plan: ...`, the four owner decisions, and “no code change”. Use `--body-file`; never put secrets in it. Merge through the gate protocol (Global Constraints).

- [ ] **Step 6: Record PR1.**

Expected evidence: PR number, merge commit, CI `test` green, protection 15/15. Keep `main` clean before starting PR2.

---

## Task 2: W1 — make the 19 Windows failures green at the root

**Files**
- Create: `tests/_shell.py`
- Modify: `tests/test_deploy_script.py`
- Modify: `tests/test_session3_cicd_docker_deploy_docs.py`

### 2A: Shared Git-Bash helper (fixes the 15 WSL failures)

- [ ] **Step 1: Create `tests/_shell.py`.**

```python
# -*- coding: utf-8 -*-
"""Shared shell discovery for tests that execute real POSIX scripts."""
import os
import shutil
from pathlib import Path

import pytest


def find_bash():
    """Return Git Bash on Windows, a POSIX bash elsewhere; never WSL's system32 shim."""
    candidates = []
    if os.name == "nt":
        candidates.extend((
            r"C:\Program Files\Git\bin\bash.exe",
            r"C:\Program Files\Git\usr\bin\bash.exe",
        ))
    found = shutil.which("bash")
    if found:
        candidates.append(found)
    for candidate in candidates:
        if not candidate:
            continue
        if "system32" in candidate.lower():
            continue
        if Path(candidate).exists():
            return candidate
    return None


BASH = find_bash()
needs_bash = pytest.mark.skipif(
    BASH is None,
    reason="bash (Git Bash on Windows) is required; Linux CI always has it",
)
```

- [ ] **Step 2: Use it in `tests/test_deploy_script.py`.**

Delete the local `_find_bash()` and `BASH = _find_bash()`; add:

```python
from _shell import BASH
```

Keep the existing `pytestmark` (`BASH is None or shutil.which("git") is None`) unchanged. Keep `shutil` import because the fixture still uses `shutil.copytree`.

- [ ] **Step 3: Add the helper guard test in `tests/test_deploy_script.py`.**

```python
def test_find_bash_never_returns_a_wsl_path(monkeypatch):
    import _shell

    monkeypatch.setattr(
        _shell.shutil, "which",
        lambda _name: r"C:\WINDOWS\system32\bash.exe",
    )
    found = _shell.find_bash()
    assert not found or "system32" not in found.lower()
```

- [ ] **Step 4: Use it in `tests/test_session3_cicd_docker_deploy_docs.py`.**

Add near the imports:

```python
from _shell import BASH

pytestmark = pytest.mark.skipif(
    BASH is None,
    reason="real deploy scripts need bash (Git Bash on Windows); Linux CI always has it",
)
```

Replace **only the executable token** at the 8 real call sites (keep the trailing `"bash"` that is the script’s `$0`):

```python
_run([BASH, "-c", script, "bash", target, current, origin_main], env=e)
_run([BASH, "-c", 'source "$FUNCS"; preserve_dirty_tree', "bash"], env=e)
_run([BASH, FORCED_SH], env=env)
_run([BASH, SETUP_SH, "--dry-run", "--key-file", str(key)], env=env)
_run([BASH, SETUP_SH, "--help"])
_run([BASH, "-n", f])
```

Do **not** replace the `"bash"` string in `test_unknown_command_rejected_126`; it is a rejected *command argument*, not an executable.

- [ ] **Step 5: Run the 15 tests.**

```powershell
& ".\.venv\Scripts\python.exe" -m pytest tests/test_session3_cicd_docker_deploy_docs.py -q
```

Expected: `15 passed` (or `15 passed` with only explicitly documented skips). If a failure is a Windows path issue, normalize the script path with `.replace("\\", "/")` in `_run([BASH, ...])`; do not change assertions.

- [ ] **Step 6: Commit 2A.**

```powershell
git add tests/_shell.py tests/test_deploy_script.py tests/test_session3_cicd_docker_deploy_docs.py
git commit -m "test(phase10): share a portable git-bash helper on windows"
```

### 2B: Stop MSYS from translating container paths (fixes the 4 docker failures)

- [ ] **Step 7: Reproduce and keep the evidence.**

The sandbox log proves the root cause:

```text
docker exec -i -w C:/Program Files/Git/app bot-official-telegram python - C:/Program Files/Git/app ...
```

Git Bash converts the argument `/app` before the native `docker` stub sees it. This is a test-harness problem, not a defect in `deploy.sh`: the real Docker CLI receives `/app` inside the container.

- [ ] **Step 8: Keep the container paths POSIX inside the sandbox (harness only).**

In `tests/test_deploy_script.py`:

1. In the `sandbox` fixture environment add **only** a scoped exclusion:

```python
"MSYS2_ARG_CONV_EXCL": "/app",
```

Do **not** set `MSYS_NO_PATHCONV=1`: it disables every conversion and breaks the stub itself (its `#!/usr/bin/env python3` shebang then receives an untranslated `/tmp/...` path and fails to start).

2. Make the Docker stub emit LF only — Windows text-mode stdout adds `\r`, and Git Bash’s `read` keeps it, so the mount table never matches `/app`:

```python
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(newline="\n")
```

3. Make the stub’s in-container path handling POSIX-like (Windows `os.path.normpath("/app/x")` yields `\app\x`):

```python
helper = ("import os\n"
          "import posixpath\n\n"
          "def _host_path(p):\n"
          "    p = p.replace(os.sep, '/')\n"
          "    return p.replace('/app', APP, 1) if p.startswith('/app') else p\n"
          "def _posix_normpath(p):\n"
          "    return posixpath.normpath(p.replace(os.sep, '/'))\n")
code = code.replace("import os\n", helper, 1)
code = code.replace("os.path.normpath", "_posix_normpath")
```

(Use the real `MOUNT`/`APP` values in the generated code, as the existing stub already does.)

Do not change `deploy/deploy.sh`: the real Docker CLI receives `/app` inside the container; only the Windows test harness mangles it.

- [ ] **Step 9: Run the 4 docker-runtime tests.**

```powershell
& ".\.venv\Scripts\python.exe" -m pytest tests/test_deploy_script.py -q -k docker_runtime
```

Expected: `4 passed`; the missing-deploy test remains green. If a path is still wrong, inspect the sandbox `stub.log`; fix only the harness.

- [ ] **Step 10: Run both files together and commit 2B.**

```powershell
& ".\.venv\Scripts\python.exe" -m pytest tests/test_deploy_script.py tests/test_session3_cicd_docker_deploy_docs.py -q
git add tests/test_deploy_script.py
git commit -m "test(phase10): disable msys path conversion in the docker sandbox"
```

Expected: **0 failed**. Record the skip count in the commit/PR notes; at most the 4 docker tests may be skipped, and only if the harness fix is impossible — it is not expected here.

---

## Task 3: W2 — `fernet_for()` and `migrate_ai_keys()` for both tables

**Files**
- Modify: `newsbot/secret_store.py`
- Create/append: `tests/test_phase10_secrets_rotation.py`

- [ ] **Step 1: Add the pure key helper.**

In `newsbot/secret_store.py`, replace the body of `_fernet()` with a call to a new function:

```python
def fernet_for(key):
    """Build a Fernet for an arbitrary key string; None when empty/unavailable."""
    key = (key or "").strip()
    if not key:
        return None
    try:
        from cryptography.fernet import Fernet
        return Fernet(base64.urlsafe_b64encode(hashlib.sha256(key.encode("utf-8")).digest()))
    except ImportError:
        log.warning("رمزنگاری رازها: بستهٔ cryptography نصب نیست (pip install -r requirements.txt)")
        return None


def _fernet():
    return fernet_for(config.SECRETS_KEY)
```

Keep `PREFIX`, `encrypt`, `decrypt`, `is_encrypted` and their behaviour unchanged.

- [ ] **Step 2: Extend the startup migration to providers.**

Replace the query/update bodies so `migrate_ai_keys()` loops over `(("ai_models", "ai_models"), ("ai_providers", "ai_providers"))` (or an equivalent tuple), selects `id, api_key` from each, and encrypts plaintext rows in both. The no-key warning must count both tables. Keep the return value = number of rows encrypted and the docstring updated. Do not touch `sources`.

- [ ] **Step 3: Add the provider-migration test.**

In `tests/test_phase10_secrets_rotation.py`, add:

```python
def test_migrate_ai_keys_encrypts_ai_providers_too(isolated_db, monkeypatch):
    from newsbot import config, db, secret_store

    monkeypatch.setattr(config, "SECRETS_KEY", "phase10-migrate-key")
    db.q("INSERT INTO ai_models(name, api_key) VALUES(?, ?)", ("m", "plain-model"))
    db.q("INSERT INTO ai_providers(name, api_key) VALUES(?, ?)", ("p", "plain-provider"))
    assert secret_store.migrate_ai_keys() >= 2
    for table in ("ai_models", "ai_providers"):
        row = db.q("SELECT api_key FROM %s WHERE api_key LIKE 'enc:v1:%%'" % table, one=True)
        assert row, table
    assert secret_store.migrate_ai_keys() == 0
```

- [ ] **Step 4: Run the touched tests.**

```powershell
& ".\.venv\Scripts\python.exe" -m pytest tests/test_api_key_entry.py tests/test_phase12_config_docs.py tests/test_phase2_provider_registry.py -q
```

Expected: all pass. If the existing `test_migrate_ai_keys_encrypts_plaintext_once` depends on the old single-table count, update only that assertion to the new return value; do not weaken it.

- [ ] **Step 5: Commit.**

```powershell
git add newsbot/secret_store.py tests/test_phase10_secrets_rotation.py
git commit -m "feat(phase10): add fernet_for and migrate provider keys"
```

---

## Task 4: W2 — rotate the secrets key safely

**Files**
- Create: `tools/rotate_secrets_key.py`
- Modify: `tests/test_phase10_secrets_rotation.py`

- [ ] **Step 1: Define the CLI contract.**

```text
usage: rotate_secrets_key.py (--check | --apply --backup PATH) [--db PATH]

env:
  OLD_SECRETS_KEY   required, current key
  SECRETS_KEY       required, new key
  DB_FILE           optional when --db is given

tables:
  ai_models.api_key
  ai_providers.api_key
```

Exit codes: `0` success, `2` precondition failure, `3` any row cannot be decrypted with the old key (no writes). Output is a JSON object with counts only.

- [ ] **Step 2: Implement preconditions before any write.**

Required checks, each returning exit 2 with a short value-free message:

- `OLD_SECRETS_KEY` and `SECRETS_KEY` are present and non-empty after `.strip()`.
- `OLD != NEW` (use `secrets.compare_digest`).
- `--apply` requires `--backup`.
- Target DB exists and is a file; backup exists, is non-empty, is not the target (`samefile`/`abspath`).
- `fernet_for(old)` and `fernet_for(new)` are not `None` (cryptography).
- Backup: `PRAGMA integrity_check` is `ok`.
- Backup row counts for `ai_models` and `ai_providers` equal the target’s counts.
- Backup mtime is not older than the target DB mtime (`<` only).
- No `enc:v1:` value exists in any table outside `ai_models`/`ai_providers`; scan `sqlite_master` tables, `PRAGMA table_info`, and text columns with `LIKE 'enc:v1:%'`. Report only table names.

- [ ] **Step 3: Validate every target row before writing.**

For each row in both tables with a non-empty `api_key`:

- If it starts with `PREFIX`: try `old.decrypt(token)`.
  - Success → candidate for re-encryption with `new`; count `rows_reencrypted`.
  - Failure → try `new.decrypt(token)`.
    - Success → already rotated; count `rows_already_new`, no write.
    - Failure → record `rows_failed`; after scanning all rows, exit 3 **without writing anything**.
- If it does not start with `PREFIX` (plaintext) → candidate for encryption with `new`; count `rows_encrypted_plain`.
- `rows_total` = all non-empty `api_key` rows in both tables.

- [ ] **Step 4: Apply atomically.**

After validation, open the target with `sqlite3`, set `isolation_level=None`, run `BEGIN IMMEDIATE`, execute all `UPDATE ... SET api_key=? WHERE id=?` statements, `COMMIT`, and roll back on any exception (exit 3). `--check` performs the same validation and prints the counts but performs no `BEGIN`/`UPDATE`.

- [ ] **Step 5: Print only counts and exit.**

```python
{
  "mode": "check" | "apply",
  "rows_total": int,
  "rows_reencrypted": int,
  "rows_encrypted_plain": int,
  "rows_already_new": int,
  "rows_failed": int,
  "other_enc_v1_tables": [table, ...]
}
```

Never print `OLD_SECRETS_KEY`, `SECRETS_KEY`, an `api_key`, a token, or an exception message that could contain one. Use `json.dumps(..., sort_keys=True)`.

- [ ] **Step 6: Add the rotation tests.**

In `tests/test_phase10_secrets_rotation.py`, build a temporary SQLite DB with `ai_models(id, api_key)`, `ai_providers(id, api_key)`, and `other(id, value)`. Use `secret_store.fernet_for` to make values. Cover:

1. Round-trip: old-key rows in both tables become `enc:v1:` readable by the new key and not by the old key.
2. Idempotency: a second `--apply` with the same env reports `rows_already_new` and changes no value.
3. Third-key row: exit 3; compare every `api_key` before/after (nothing changed).
4. Plaintext row in each table: exit 0; both become encrypted with the new key.
5. Missing `--backup`: exit 2.
6. `--backup` equal to `--db`: exit 2.
7. Invalid/stale backup or row-count mismatch: exit 2.
8. `enc:v1:` in `other`: exit 2, fail-closed, no write.
9. No secret value appears in stdout/stderr (assert the old key, new key and plaintext row values are absent).
10. `fernet_for(key)` decrypts what `secret_store.encrypt` wrote when `config.SECRETS_KEY` is set to the same key, and `fernet_for("") is None`.
11. `--check` on a valid DB reports counts and leaves the DB byte-for-byte unchanged (hash/compare values).

Use `subprocess.run([sys.executable, TOOL, ...], env=env, capture_output=True, text=True, cwd=ROOT)` so the test exercises the real CLI. Set `PYTHONPATH=ROOT`; never put a key in argv.

- [ ] **Step 7: Run the new tests and the existing secret tests.**

```powershell
& ".\.venv\Scripts\python.exe" -m pytest tests/test_phase10_secrets_rotation.py tests/test_api_key_entry.py tests/test_phase12_config_docs.py -q
```

Expected: all pass.

- [ ] **Step 8: Commit.**

```powershell
git add tools/rotate_secrets_key.py tests/test_phase10_secrets_rotation.py
git commit -m "feat(phase10): add an atomic secrets-key rotation tool"
```

---

## Task 5: D-6 cache prefix, `deploy/env.example`, and PR2 verification

**Files**
- Modify: `.github/workflows/qa-live.yml`
- Modify: `tests/test_phase4_live_acceptance.py` (only assertions that name the v1 prefix)
- Create: `deploy/env.example`
- Modify: `README.md` (collection count)
- Modify: `tests/test_repo_integrity.py` (remove created paths from `PROPOSED_PATHS`)

- [ ] **Step 1: Roll the cache prefix.**

Change both occurrences of `qa-live-db-v1-` in `.github/workflows/qa-live.yml` to `qa-live-db-v2-` (the per-run key and the `restore-keys` prefix). Update any test assertion that hard-codes v1. Do not change the secret names, permissions, timeout or inputs.

- [ ] **Step 2: Create `deploy/env.example` with placeholders only.**

```dotenv
# Copy to the deployment location and inject real values from the owner's secret store.
# Never commit the filled file.
BOT_TOKEN=
SUPER_ADMIN_IDS=
SECRETS_KEY=
DB_BACKEND=sqlite
DB_FILE=/app/data/newsbot.db
```

No real token, key, chat id or password. Keep the file ASCII.

- [ ] **Step 3: Remove only the paths created in this PR from `PROPOSED_PATHS`.**

Remove `tests/_shell.py`, `tests/test_phase10_secrets_rotation.py`, `tools/rotate_secrets_key.py`, and `deploy/env.example`. Leave the three docs paths in the allowlist for PR3.

- [ ] **Step 4: Bump the collection count exactly once.**

```powershell
& ".\.venv\Scripts\python.exe" -m pytest --collect-only -q
```

Read the final `N tests collected`. Update both places in `README.md`:
- badge `tests-N%20collected`
- Persian line `# ۱۱۸۳ تست` → the new Persian numeral count

Do this after all new tests exist and before the full-suite run. If the count changes later, update both again and restart the suite at the final HEAD.

- [ ] **Step 5: Run the full local suite.**

```powershell
& ".\.venv\Scripts\python.exe" -m pytest -q 2>&1 | Tee-Object -FilePath "C:\Users\Mustafa\AppData\Local\Temp\opencode\phase10_full_suite.txt"
```

Expected: **0 failed**, 0 unexplained errors; any skip is one of the documented docker/bash cases. Record the passed/skipped counts and the exact HEAD.

- [ ] **Step 6: Run the independent verifier and the startup check.**

```powershell
& ".\.venv\Scripts\python.exe" tools/final_verification.py
$env:BOT_TOKEN="123456789:AAtest_only_dummy_token_0000000000000"; $env:SUPER_ADMIN_IDS="1"; $env:DB_FILE="C:\Users\Mustafa\AppData\Local\Temp\opencode\check.db"; & ".\.venv\Scripts\python.exe" run.py --check
```

Expected: verifier prints **17/17** (it may exit 1 on its final print; record the 17/17 output), `run.py --check` exit 0. Do not claim 17/17 without the output.

- [ ] **Step 7: Run lint/type gates.**

```powershell
& ".\.venv\Scripts\python.exe" -m ruff check newsbot tests tools run.py
& ".\.venv\Scripts\python.exe" -m mypy newsbot
& ".\.venv\Scripts\python.exe" -m pyflakes newsbot tests tools run.py
& ".\.venv\Scripts\python.exe" -m py_compile run.py newsbot\*.py tools\rotate_secrets_key.py
```

Expected: clean. If mypy reports a pre-existing unrelated error, record it and compare with `main` before changing anything.

- [ ] **Step 8: Commit, push and open PR2.**

```powershell
git add .github/workflows/qa-live.yml tests/test_phase4_live_acceptance.py deploy/env.example README.md tests/test_repo_integrity.py
git add tests/_shell.py tests/test_deploy_script.py tests/test_session3_cicd_docker_deploy_docs.py newsbot/secret_store.py tools/rotate_secrets_key.py tests/test_phase10_secrets_rotation.py
git commit -m "feat(phase10): harden windows tests and secrets rotation"
git push -u origin phase10/hardening
```

PR2 body: list the 19→0 Windows result, the rotation tool contract, the v2 cache change, the README count, and `F-1` as out of scope. Merge through the gate protocol.

- [ ] **Step 9: Record PR2 evidence.**

PR number, merge commit, CI `test` green, protection 15/15, suite counts, verifier 17/17, `run.py --check` exit 0.

---

## Task 6: W3 — branch cleanup and dead-code proof (after PR2 merge)

**Files**
- No repo file changed by the deletion itself.
- Evidence saved to `C:\Users\Mustafa\AppData\Local\Temp\opencode\phase10_branch_cleanup.txt` for PR3.

- [ ] **Step 1: Prune local tracking refs and re-audit.**

```powershell
git fetch --prune origin
python "C:\Users\Mustafa\AppData\Local\Temp\opencode\branch_audit10.py"
```

Expected: **36** delete candidates, **13** kept, **55** stale tracking refs pruned (the number may move slightly if Dependabot changed; record the real number).

- [ ] **Step 2: Guard every deletion with the merged PR head SHA.**

For each candidate branch:
- Get its remote tip: `git ls-remote origin "refs/heads/<branch>"`.
- Get the merged PR’s last head: `gh pr view <number> --json headRefOid,mergedAt`.
- Delete only if `mergedAt` is non-null **and** the two SHAs are equal. Otherwise keep it and list it as “PR merged but branch moved; not deleted”.
- Never delete `main` or a branch whose PR is not MERGED.

```powershell
git push origin --delete <branch>
```

Record each deleted branch and PR number. Do not use `--force`.

- [ ] **Step 3: Verify the kept set.**

Expected kept: 11 Dependabot branches (`#68–#78` heads) + `fix/deploy-and-tests-oct-2026` + `fix/phase-a-safety-net`, plus any branch that failed the SHA guard. List them in PR3 with one line of why.

- [ ] **Step 4: Dead-code pass (code proof, zero is valid).**

Run a bounded scan and record the result:
- `git grep -n "def <symbol>\|class <symbol>"` and references outside the definition, in `newsbot/`, `tests/`, `tools/`, `run.py`, `deploy/`, `.github/`.
- Ignore entrypoints (`run.py`, `tools/*` `main`, pytest fixtures, public UI handlers referenced dynamically).
- Delete only with triple proof: no code reference, no test reference, no workflow/doc command. Use a separate commit. If nothing qualifies, write “0 removed” in the PR3 report.

- [ ] **Step 5: Save the evidence.**

Include in `phase10_branch_cleanup.txt`: command, timestamp, 36 deleted names + PR numbers, 13 kept names + reason, prune count, dead-code result. No secrets.

---

## Task 7: PR3 — runbooks, final report, state file

**Files**
- Create: `docs/OPS_SECRET_ROTATION.md`
- Create: `docs/RELEASE_RUNBOOK.md`
- Create: `docs/PHASE10_FINAL_REPORT.md`
- Modify: `docs/QA_LIVE_ACCEPTANCE.md` (v2 cache + `fresh` after rotation)
- Modify: `docs/PROJECT_ROADMAP_AND_EXECUTION_STATE.md`
- Modify: `tests/test_repo_integrity.py` (remove the three docs paths)

- [ ] **Step 1: Write `docs/OPS_SECRET_ROTATION.md` (Persian).**

Must contain, in order:
1. Scope: owner approved only `BOT_TOKEN` + `SECRETS_KEY`; provider keys are **not** reissued; residual risk **D-13**.
2. Preconditions: maintenance window, full DB backup, service venv at `/opt/newsbot/.venv`, `OLD_SECRETS_KEY`/`SECRETS_KEY` exported without echo, `cryptography` installed.
3. Stop the service; back up DB (`sqlite3 ... ".backup ..."` or the repo’s backup tool) including `-wal`/`-shm`; verify `PRAGMA integrity_check`.
4. Run `--check`, then `--apply --backup` from the deploy checkout with `/opt/newsbot/.venv/bin/python tools/rotate_secrets_key.py ...` (not inside the image; the image lacks `tools/`).
5. Update the production env file without printing it; restart; verify `run.py --check` and one provider connection test.
6. Rotate `BOT_TOKEN` in BotFather (`/revoke`), update env, restart, confirm the bot responds; document the short downtime.
7. Purge `qa-live-db-v1-*` caches and run any later D-7 with `db_mode=fresh`.
8. Only after a successful rotation, delete `newsbot-deploy-pkg\newsbot.env`; if anything fails, restore the old env + backup and stop.
9. Rollback: restore env and DB backup, restart, verify.
10. Explicit “never print/paste/commit the values” rule.

- [ ] **Step 2: Write `docs/RELEASE_RUNBOOK.md` (Persian).**

Sections: prerequisites and evidence URLs; CI gates (`pip-audit`, `gitleaks`, `trivy` FS+image, `hadolint`) with the real run URL; local `pip-audit`; full suite; `final_verification` 17/17; `run.py --check`; protection 15/15; owner gates (D-7 artifact, rotation done); release steps referencing `docs/DEPLOY_RUNBOOK.md`; post-release checks; rollback; stop criteria. State that `trivy`/`hadolint` are CI-only because docker is not installed locally.

- [ ] **Step 3: Write `docs/PHASE10_FINAL_REPORT.md` (Persian).**

Include a findings table:

| Finding | Status | Evidence |
|---|---|---|
| AUD-001 | Partially closed — rotation tool + runbook delivered; actual rotation is the owner gate; provider keys not reissued (D-13) | tool tests, runbook, owner gate |
| AUD-012 | Closed — 19 Windows failures root-caused; 15 by Git Bash helper, 4 by MSYS path conversion; 0 failed locally | exact test counts + commit |
| AUD-014 | Closed for proof-based cleanup — 36 merged branches deleted, 13 kept and listed, 55 refs pruned; dead code 0/N removed with proof | cleanup evidence |
| D-4 | Deferred after release (owner decision) | 11 PRs listed |
| D-12 | Open — `sources.api_key` plaintext, out of scope | code reference |
| D-13 | Open — provider keys not reissued; old DB copies remain decryptable with the leaked key | owner decision |
| F-1 | Open observation — 504 / ~60 s connection test; code timeout evidence; no fix in Phase 10 | owner report + code lines |

Also include: suite counts, collection, verifier, `run.py --check`, scans, D-7 run URL/result, and an honest release recommendation (“ready only if all gates are closed”).

- [ ] **Step 4: Record D-7 and F-1 honestly.**

If run `46013627630` is complete:
```powershell
gh run view 46013627630 --json status,conclusion,createdAt,updatedAt
gh run download 46013627630 -n qa-live-46013627630 -D "C:\Users\Mustafa\AppData\Local\Temp\opencode\d7"
```
Read `report.json` and `bot.log`; record the conclusion, active duration, scenario, `db_mode=fresh`, and artifact name. Do not upload DB. If the run is still in progress or failed, record exactly that and keep Phases 4–9 and 10 `IN_PROGRESS`; do not claim live success. Search `bot.log` for `504`, `ai_timeout`, `timeout` and report counts only (F-1).

- [ ] **Step 5: Update `docs/PROJECT_ROADMAP_AND_EXECUTION_STATE.md`.**

- §2 row 10: Phase 10 status `IN_PROGRESS` until D-7 + rotation evidence; do not write `COMPLETED` early.
- §4.9: Phase 10 result, PRs, suite counts, W1/W2/W3/W4 outcomes.
- §5: D-7 evidence row with run URL, conclusion, artifact, date; F-1 evidence.
- §7: delivery status for Phase 10.
- §8: **D-12**, **D-13**, **F-1**, D-4 deferral, D-6 v2 cache.
- Update any stale `qa-live` text that says `350`/`v1` to the actual `340`/`v2` values.

- [ ] **Step 6: Update `docs/QA_LIVE_ACCEPTANCE.md`.**

Add the v2 cache key, the “rotation before a future run + `fresh`” rule, and a pointer to `docs/OPS_SECRET_ROTATION.md`. Do not claim a completed rotation.

- [ ] **Step 7: Remove the three docs paths from `PROPOSED_PATHS`.**

Remove `docs/OPS_SECRET_ROTATION.md`, `docs/RELEASE_RUNBOOK.md`, `docs/PHASE10_FINAL_REPORT.md`. The set should be empty or contain only genuinely future files.

- [ ] **Step 8: Scan and verify.**

```powershell
& ".\.venv\Scripts\python.exe" "C:\Users\Mustafa\AppData\Local\Temp\opencode\glyph_scan8.py" docs/OPS_SECRET_ROTATION.md docs/RELEASE_RUNBOOK.md docs/PHASE10_FINAL_REPORT.md docs/QA_LIVE_ACCEPTANCE.md docs/PROJECT_ROADMAP_AND_EXECUTION_STATE.md
& ".\.venv\Scripts\python.exe" -m pytest tests/test_repo_integrity.py tests/test_phase1_10_state_alignment.py -q
```

Expected: 0 suspicious lines; guardians green.

- [ ] **Step 9: Commit, push and open PR3.**

```powershell
git add docs/OPS_SECRET_ROTATION.md docs/RELEASE_RUNBOOK.md docs/PHASE10_FINAL_REPORT.md docs/QA_LIVE_ACCEPTANCE.md docs/PROJECT_ROADMAP_AND_EXECUTION_STATE.md tests/test_repo_integrity.py
git commit -m "docs(phase10): record hardening, rotation and release gate"
git push -u origin docs/phase10-records
```

PR3 body: findings table, exact evidence, open owner gates, branch-cleanup numbers, D-7 status, and an explicit “Phase 10 is not COMPLETED until …”. Merge through the gate protocol.

---

## Task 8: Conditional close-out PR (only after both owner gates have evidence)

**Files**
- Modify: `docs/PHASE10_FINAL_REPORT.md`
- Modify: `docs/PROJECT_ROADMAP_AND_EXECUTION_STATE.md`

- [ ] **Step 1: Confirm both gates with evidence.**

- D-7: a completed `qa-live` run with the artifact and `report.json` recorded in §5.
- Rotation: the owner reports a successful `--apply`, env update, restart, provider test, and `BOT_TOKEN` revoke; record only counts/date/runbook steps, never values. `newsbot.env` deletion is recorded with date.

If either is missing, stop; do not open this PR and do not mark Phase 10 complete.

- [ ] **Step 2: Update status and report.**

Set §2 row 10 and §4.9 to `COMPLETED`; add the final evidence rows; update the release recommendation. Mark Phases 4–9 `COMPLETED` only if the D-7 artifact proves their live scenarios.

- [ ] **Step 3: Verify, commit, PR, gate.**

Run the integrity/state tests + glyph scan; open **docs/phase10-closeout**; merge via the gate protocol; confirm protection 15/15. Then stop — the roadmap ends at Phase 10.

---

## Acceptance Checklist

- [ ] `tests/_shell.py` exists; `find_bash()` never returns `system32`; both test files use it.
- [ ] `test_session3_cicd_docker_deploy_docs.py`: 15 previously failing tests pass.
- [ ] `test_deploy_script.py`: 4 docker-runtime tests pass with `MSYS2_ARG_CONV_EXCL=/app` + LF-only stub output + POSIX path normalization; no `deploy.sh` change.
- [ ] F-2: `libzstd1` is upgraded in `deploy/Dockerfile`; the CI image scan is green.
- [ ] Full local suite: **0 failed**; skip count documented; Linux CI green.
- [ ] `fernet_for()` tested; `migrate_ai_keys()` encrypts both tables; second call returns 0.
- [ ] Rotation tool: round-trip, idempotency, third-key exit 3 with no writes, plaintext encryption, missing/equal/stale backup exit 2, foreign `enc:v1:` exit 2, no secret values in output, `--check` no writes.
- [ ] `qa-live` cache prefix is v2; v1 caches are purged in the owner window; any future D-7 is `fresh`.
- [ ] 36 merged branches deleted only after SHA equality proof; 13 kept and listed; 55 refs pruned.
- [ ] Runbooks and final report exist in Persian; no secret values anywhere.
- [ ] `final_verification` 17/17; `run.py --check` exit 0; ruff/mypy/pyflakes/py_compile clean.
- [ ] README collection count matches the final suite; both places updated together.
- [ ] State file never says `COMPLETED` before D-7 + rotation evidence; D-4/D-12/D-13/F-1 explicit.
- [ ] Every PR merged through lift/merge/restore; protection 15/15 IDENTICAL after each.

## Stop Conditions

Stop and report to the owner if:
- the in-progress D-7 run is still running when a step would rotate, purge caches, or delete `newsbot.env`;
- any secret value appears in output (rotate/stop immediately, do not paste it);
- the full suite has a new failure outside the known 19 before W1;
- a branch’s merged PR head SHA differs from the current remote tip;
- `--apply` reports `rows_failed > 0` (exit 3) — do not retry with a different key; restore the backup and investigate;
- CI `test` is not SUCCESS or protection is not 15/15 after a merge.
