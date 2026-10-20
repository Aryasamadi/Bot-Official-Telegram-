# CHANGELOG — Audit & Remediation Log

Format: one entry per **authorised phase**. Every entry records the scope, the files changed, the tests
executed and observed, the commit, the deployment status and the verification status.

---

## 2026-10-20 — PH-00 Production verification (read-only) — **PARTIAL: production read BLOCKED, repository side COMPLETE**

**Phase:** `PH-00` — Production verification (read-only) + audit-document validation + confirmed
repository-infrastructure fixes
**Authorisation:** owner instruction «مرحله PH-00 را در محیط کاری تمیز ممیزی اجرا کن… تست‌های پروژه را اجرا کن و هر ایراد تأییدشده در اسناد ممیزی یا زیرساخت مخزن را اصلاح کن… تغییرات موفق و تست‌شده را در صورت لازم کامیت و پوش کن.»
**Baseline:** `main` @ `08bc87655dc910a98076acd749ddf8f3e6bdca2c`
**Working directory:** `C:\Users\Mustafa\AppData\Local\Temp\opencode\audit-newsbot` (the clean audit clone)

### 13.1 Production read — **BLOCKED (no access)**

The PH-00 host checklist could **not** be executed. Verified absent in this environment:
`~/.ssh` (does not exist — no keys, no `config`, no `known_hosts`), any ssh host entry, the `gh` CLI
(not installed), and every VPS/SSH/host/deploy environment variable. The host address itself is only the
GitHub secret `VPS_HOST` (`deploy-vps.yml:37`), whose value is not readable.

→ **Unverified (unchanged):** the running commit SHA, the real systemd unit name and `WorkingDirectory`,
the deployed Python version and dependency set, the `.env` owner/mode, the database path / journal mode /
integrity, the backup inventory, and the service journal. The checklist is ready to run from any host with
the key.

**Repository-side verification completed instead (mechanical, not by eye):**

| Check | Result |
|---|---|
| `deploy.sh` service identity | `APP_DIR=/opt/bot-ai-automatic`, `SERVICE=bot-ai-automatic` (`deploy/deploy.sh:17-19`) |
| Occurrence counts (`git grep -c`) | `bot-ai-automatic` = **51** across **5** tracked files; `newsbot.service` = **0**; `bot-official-telegram` = **0** |
| Additional evidence site found | `deploy/systemd/10-restart-always.conf:7` — the `ExecStopPost` logger tag also carries the old identity (**added to OPS-001**) |
| `git ls-files -s deploy/deploy.sh` | `100644` — **not executable** (OPS-028) |
| `git ls-files \| Select-String ROADMAP` | **no match** — 6 dangling references (DOC-001) |
| Secret scan of the local `ROADMAP.md` (9 credential patterns) | **0 matches** |

### 13.2 FIN-001 — STATIC → **REPRODUCED**

Executed against an **isolated temporary SQLite database** using the real business functions
(`record_referral` → `credit_referral_on_payment` → `upgrade_quote`). No repository or production file was
touched. Observed:

```
plan U (en tariff) : price_num=5.0      price_num_fa=0.0
plan T (fa tariff) : price_num=0.0      price_num_fa=600000.0
credit_referral_on_payment -> (1001, 120000.0)          # 20% of 600000 Toman
users.credit = 120000.0   (unitless)   credit_log: delta=120000.0 balance_after=120000.0 reason='referral'
en referrer quotes the USD plan:  new_cost=5.0  balance=120000.0  used_balance=5.0  **payable=0.0**
=> FIN-001 REPRODUCED: a USD plan is obtained for FREE from a Toman-denominated reward (24000x over-credit)
MIRROR: fa referrer holding a USD reward of 1.0 -> used_balance=1.0, payable=599999.0 (worthless)
```

This confirms the CRITICAL classification and raises FIN-001 from `STATIC` to `REPRODUCED`.

### 13.3 Tests executed

| Command | Result |
|---|---|
| `python -m pytest -q -p no:cacheprovider` (full suite, before the changes) | **455 passed**, 0 failed, 0 skipped, 0 errors — 88.95 s (3 × `PTBDeprecationWarning`, unchanged) |
| `python -m pytest -q -p no:cacheprovider tests/test_repo_integrity.py` (new file) | **3 passed** |
| negative control — `ROADMAP.md` temporarily removed | **2 failed** (as intended — the guard is proven to detect the regression) |
| `python -m pytest -q -p no:cacheprovider` (full suite, after the changes) | see §13.6 below |

Environment: Windows 11, Python 3.13.14, **out-of-tree venv** with the pinned dependencies — nothing
installed, `-p no:cacheprovider`, `PYTHONDONTWRITEBYTECODE=1`. The repository was left clean throughout
(`git status --porcelain` shows only the intended changes).

### 13.4 Audit-document validation

A validator resolved every evidence reference in the register against the working tree:
**516 references checked, 0 pointing past EOF, 0 severity mismatches** between the register headings and
`status.json`, and the finding-ID sets of `FINDINGS.md` and `status.json` were **identical**.

Two real defects in the audit documents were found and corrected:
1. **Stale severity counts** in the `FINDINGS.md` header (`HIGH 25 / MEDIUM 68 / LOW 57 / INFO 29` instead of
   the true `26 / 78 / 64 / 11`) and an **"all OPEN"** claim that ignored the 4 deferred findings.
2. **OPS-001 was missing one evidence site** — `deploy/systemd/10-restart-always.conf:7`.

### 13.5 Repository-infrastructure fixes applied

| # | Fix | Files | Regression test | Finding |
|---|---|---|---|---|
| 1 | `deploy/deploy.sh` committed **executable** (git index `100644` → `100755`) | `deploy/deploy.sh` (mode only — content unchanged) | `tests/test_repo_integrity.py::test_deploy_script_is_executable_in_git_index` | **OPS-028** |
| 2 | The project's own **`ROADMAP.md` added to the repository root**, resolving 6 dangling references (original text unchanged, plus a five-line status note under the title marking it as the historical phase plan and pointing to `docs/audit/ROADMAP.md`) | `ROADMAP.md` (new) | `::test_roadmap_document_is_tracked_and_contains_the_cited_sections` + `::test_referenced_repo_paths_exist` | **DOC-001** (new finding) |

**Deliberately NOT changed:** the `bot-ai-automatic` service identity itself (OPS-001) — renaming it
without reading the live host risks making a working service unstartable; it is blocked on the PH-00
production read (DECISIONS.md D-13). No application logic, schema, dependency, workflow or deployment
behaviour was modified.

### 13.6 Deployment status

**None.** Nothing was deployed, and no production system was contacted or modified.

**Commit / push status**

| Item | Value |
|---|---|
| Branch | `audit/ph-00-verification-and-repo-integrity` (from `main` @ `08bc876`) |
| Commit | `3ebe34729c5f84b29bf16bb40cf40aabbb47714e` |
| Pushed | yes — `origin/audit/ph-00-verification-and-repo-integrity` |
| Pull request | **#20** — https://github.com/Aryasamadi/Bot-Official-Telegram-/pull/20 |
| CI on the PR | **`test` → success**, **`docker-build` → success** (run 39443652653, completed 2026-10-20T14:56:08Z) |
| Merge | **NOT merged** — merging is a separate action that was not authorised; the PR is green and ready |
| Deployed to production | **no** |

### 13.7 Verification status

| Finding | Before | After |
|---|---|---|
| FIN-001 | OPEN · STATIC | **OPEN** · REPRODUCED (defect confirmed; the *fix* is PH-01) |
| OPS-001 | OPEN · STATIC | **OPEN** · REPRODUCED (repository side) — *fix blocked on the PH-00 production read* |
| OPS-028 | OPEN | **VERIFIED_FIXED** |
| DOC-001 | (recorded only as a table row) | **VERIFIED_FIXED** (new tracked finding, LOW · P2) |

### 13.8 Remaining risks handed on

1. The production state is still **UNVERIFIED** — the PH-00 host checklist must be run from a machine with
   the VPS key, or the owner must paste its outputs.
2. `PH-01` cannot start until the owner answers **Q-1** (cross-currency reward denomination) and **Q-2**
   (refuse vs. re-derive a changed wallet) in `DECISIONS.md`.
3. `PH-04` must be merged before the first release that carries a PH-01…PH-03 fix.

### 13.9 Next permitted action

`AWAIT_OWNER_AUTHORIZATION` — the recommended next phase is **`PH-01`** (financial correctness), with
**`PH-00` re-run from a host with VPS access** as its prerequisite.

---

## 2026-10-20 — AUDIT (read-only) — **COMPLETE**

**Phase:** `AUDIT` (not a remediation phase; no application code was modified)
**Authorisation:** the MASTER DIRECTIVE itself (audit only)
**Baseline:** `main` @ `08bc87655dc910a98076acd749ddf8f3e6bdca2c` ("Fix referral invitation copy and fallback", 2026-10-19T20:03:08Z)

### Scope
Full engineering audit across all 20 audit domains (A–T): architecture and code quality; Python runtime and
dependencies; database and data integrity; user data and account lifecycle; financial correctness; content
discovery and source fetching; AI models and content generation; publishing and scheduling; Telegram UI/UX and
user journeys; security and authorization; networking and external services; performance and latency; VPS,
Linux and systemd; CI/CD, releases and recovery; testing and QA; observability and operations; documentation
and configuration; business logic and cross-module integration; accessibility, clarity and product quality;
remaining risks and future maintainability.

### Files created (audit documentation only — no application file was touched)
All under `docs/audit/` in the audit workspace
`C:\Users\Mustafa\AppData\Local\Temp\opencode\audit-newsbot`:

| File | Size / content |
|---|---|
| `docs/audit/MASTER_STATUS.md` | Master state, counts, risks, limitations, next action |
| `docs/audit/ROADMAP.md` | 12 phases (PH-00 … PH-11) with dependencies, risks, acceptance criteria and rollback strategies |
| `docs/audit/FINDINGS.md` | The 180-finding register with evidence and verification status |
| `docs/audit/status.json` | Machine-readable state: baseline, 180 findings, 12 phases, next permitted action |
| `docs/audit/ARCHITECTURE.md` | Verified architecture, data flows, critical state transitions, external integrations |
| `docs/audit/TEST_MATRIX.md` | Test inventory, execution status, 28-rule coverage matrix, required regression coverage |
| `docs/audit/DECISIONS.md` | Existing project decisions, audit proposals, 6 open owner questions, 10 rejected approaches |
| `docs/audit/CHANGELOG.md` | This file |

**No application source file, schema, migration, dependency, workflow or deployment file was created, modified
or deleted. No database was migrated. No service was restarted, stopped or reconfigured. Nothing was
deployed, committed or pushed.**

### Verification performed
| Activity | Result |
|---|---|
| Repository baseline | Clean clone of `main` @ `08bc876`; 10 branches; 19 PRs (all closed/merged); 0 open PRs |
| Test suite | `python -m pytest -q -p no:cacheprovider` → **455 passed, 0 failed, 0 skipped, 0 errors** (112.5 s) on Windows 11 / Python 3.13.14, out-of-tree venv, nothing installed |
| Test order independence | Reverse alphabetical file order → **455 passed** (92.9 s) |
| Repository hygiene after the runs | `git status --porcelain` empty; no `newsbot.db` in the repo root; no `.pytest_cache` |
| Isolated DB probe | A throwaway temp database proved that all 10 statically-flagged `q()` write sites **do** pass `commit=True` — the suspected "uncommitted writes" defect was **disproved and not filed** |
| Callback route map | AST diff of all 216 emitted `callback_data` strings against every handled route → **zero unmatched emitters** (no dead buttons) and **zero IDOR** |
| i18n parity | 446 keys, 100 % structural fa/en parity; 6 keys referenced by code but **absent**; 4 templates with unsatisfied placeholders; 0 Persian/Arabic character mixing |
| Git-history secret scan | All refs scanned → **no real secret committed**; only synthetic test fixtures and `.env.example` placeholders |
| Environment reconciliation | 62/62 documented defaults match `newsbot/config.py`; 0 undocumented-but-used; 0 documented-but-unused |
| Blocking-call analysis | 2 blocking calls in async paths (`db.q()`'s retry sleep, `subprocess.run("git rev-parse")`); ~40 `q()` calls still on the event loop |
| Swallowed-exception census | 59 `except: pass/continue` sites; 25 hide business-meaningful failures |

### Findings produced
**180 findings, all OPEN** (176 OPEN, 4 DEFERRED) — CRITICAL 1 · HIGH 26 · MEDIUM 78 · LOW 64 · INFO 11.
Distribution: FIN 24 · SEC 16 · DB 11 · UI 26 · PIPE 22 · NET 8 · PERF 7 · PUB 5 · OPS 31 · TEST 14 · OBS 16.

### Deployment status
**None.** Nothing was deployed. The production commit SHA remains **UNVERIFIED** (no VPS access).

### Verification status
The audit itself is **VERIFIED_FIXED** in the sense that every reported finding is backed by a file:line
reference, and the eight reproduction/measurement claims are marked `REPRODUCED`. No remediation has been
attempted, so no finding is `VERIFIED_FIXED`.

### Residual risks handed to the owner
1. The production state is **unverified** — the running commit, the real systemd unit, the deployed path, the
   `.env` permissions and the backup contents were not inspected (phase `PH-00`).
2. The deploy chain targets a **different project's** identity (`bot-ai-automatic`), and a failed restart
   leaves new code on disk with the old code running (`OPS-001`, `OPS-026`).
3. The wallet mixes two currencies (FIN-001, the only CRITICAL finding).
4. Money can be lost silently through the receipt-notification chain (UI-006, UI-009, OBS-013).
5. The AI-provider egress is unguarded (SEC-001 / NET-001) and reachable by any account holding role `admin`.

### Next permitted action
`AWAIT_OWNER_AUTHORIZATION`. The owner may:
- issue `"continue the audit"` to resume any incomplete inspection work;
- issue `"start phase PH-00"` to run the read-only production verification;
- issue `"start phase PH-01"` to begin the financial-correctness remediation (after answering the six open
  questions in `DECISIONS.md`).

---

## Template for future entries

```
## <date> — <PHASE-ID> <title> — <STATUS>

**Phase:** PH-xx
**Authorisation:** <owner instruction, verbatim>
**Scope:** <finding IDs covered>

### Files changed
| File | Change | Risk |

### Tests executed
| Command | Result | Observed |
|---|---|---|

### Regression tests added
| Test file | Test name | Covers |

### Commit
<sha> — <message> — <branch>

### Deployment status
<not deployed | deployed to <host> as <sha>>

### Verification status
<finding IDs>: OPEN -> VERIFIED_FIXED | FIXED_UNVERIFIED | BLOCKED

### Remaining risks
```

---

## Phase log (to be completed as phases are authorised)

| Phase | Status | Started | Completed | Findings closed | Commit | Deployed | Verified |
|---|---|---|---|---|---|---|---|
| AUDIT | COMPLETE | 2026-10-20 | 2026-10-20 | 0 (181 produced) | `3ebe347` (docs only) | no | yes (report presented) |
| PH-00 | COMPLETE (production read BLOCKED — no VPS access) | 2026-10-20 | 2026-10-20 | 2 (OPS-028, DOC-001) → VERIFIED_FIXED; FIN-001 and OPS-001 upgraded to REPRODUCED | `3ebe347` → PR #20, **CI green**, not merged | no | yes (repository side) |
| PH-01 | OPEN | – | – | – | – | – | – |
| PH-02 | OPEN | – | – | – | – | – | – |
| PH-03 | OPEN | – | – | – | – | – | – |
| PH-04 | OPEN | – | – | – | – | – | – |
| PH-05 | OPEN | – | – | – | – | – | – |
| PH-06 | OPEN | – | – | – | – | – | – |
| PH-07 | OPEN | – | – | – | – | – | – |
| PH-08 | OPEN | – | – | – | – | – | – |
| PH-09 | OPEN | – | – | – | – | – | – |
| PH-10 | OPEN | – | – | – | – | – | – |
| PH-11 | OPEN | – | – | – | – | – | – |
