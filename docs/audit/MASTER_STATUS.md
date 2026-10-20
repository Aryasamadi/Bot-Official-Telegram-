# MASTER STATUS — Bot-Official-Telegram- Comprehensive Engineering Audit

> This file is the single entry point for audit state. It always points to exactly one current
> phase and one next permitted action. Read it first in every session.

| Field | Value |
|---|---|
| **Last audit timestamp** | 2026-10-20 (UTC) · **verification pass:** 2026-10-20 (phase `PH-00`) |
| **Audited repository** | `Aryasamadi/Bot-Official-Telegram-` |
| **Remote** | `https://github.com/Aryasamadi/Bot-Official-Telegram-.git` |
| **Audited branch** | `main` |
| **Audited commit SHA** | `08bc87655dc910a98076acd749ddf8f3e6bdca2c` ("Fix referral invitation copy and fallback", 2026-10-19T20:03:08Z) |
| **Production commit SHA** | **UNKNOWN — NOT VERIFIED.** No VPS/host access was available: this environment has **no `~/.ssh`, no ssh config, no `gh` CLI and no VPS environment variables**. The repository contains no deployment ledger; `.deployed_sha` is written on the host and never committed. The only in-repo evidence of the running SHA is the super-admin `/health` panel (`newsbot/ui_views_super.py:36-60`). See `FINDINGS.md` §13.1. |
| **Audit workspace** | `C:\Users\Mustafa\AppData\Local\Temp\opencode\audit-newsbot` (clean clone of `main` @ `08bc876`; audit docs written here) |
| **Overall project status** | **AMBER / DEGRADED-RISK** — core engine is sound (exactly-once publishing, atomic payment approval, layered SSRF/prompt-injection defences, WAL-safe backups, CI gate, real rollback). However **1 CRITICAL** financial defect (now **reproduced**), **26 HIGH** defects, and an **unverified production state** mean the system must not be considered production-hardened. |
| **Audit completion** | **~94 %** (all 20 audit domains inspected; a second verification pass executed the FIN-001 reproduction and re-ran the suite; production runtime verification unavailable) |
| **Current phase** | `PH-00` (executed) → `PH-01` (awaiting authorization) |
| **Current phase status** | `PH-00 COMPLETE — production read BLOCKED (no access), repository-side verification COMPLETE, 2 infrastructure defects FIXED` |
| **Next permitted action** | `AWAIT_OWNER_AUTHORIZATION` |
| **Owner approval required** | `true` |

---

## 1. Findings summary

| Severity | Count |
|---|---|
| CRITICAL | **1** |
| HIGH | **26** |
| MEDIUM | **78** |
| LOW | **65** |
| INFO | **11** |
| **TOTAL** | **181** |

| Status | Count |
|---|---|
| OPEN | 175 |
| FIXED_UNVERIFIED | 0 |
| VERIFIED_FIXED | 2 (OPS-028, DOC-001) |
| BLOCKED | 0 (OPS-001's *fix* is blocked on the PH-00 production read; the finding itself is OPEN) |
| DEFERRED | 4 (SEC-003, SEC-004, SEC-005, SEC-006 — secrets-at-rest, awaiting decision D-08) |
| WONT_FIX | 0 |

### Findings by ID prefix

| Prefix | Domain | Count | Worst severity |
|---|---|---|---|
| `SEC-001…016` | Security & authorization | 16 | HIGH |
| `FIN-001…024` | Financial correctness & business rules | 24 | **CRITICAL** |
| `UI-001…026` | Telegram UI/UX & user journeys | 26 | HIGH |
| `DB-001…011` | Database & data integrity | 11 | HIGH |
| `PIPE-001…022` | Content pipeline & AI generation | 22 | HIGH |
| `NET-001…008` | Networking, HTTP clients, SSRF transport | 8 | HIGH |
| `PERF-001…007` | Performance & latency | 7 | MEDIUM |
| `PUB-001…005` | Publishing, scheduling, exactly-once | 5 | MEDIUM |
| `OPS-001…031` | CI/CD, deployment, systemd, ops docs | 31 | HIGH |
| `TEST-001…014` | Testing & QA | 14 | HIGH |
| `OBS-001…016` | Observability & operations | 16 | HIGH |
| `DOC-001` | Documentation integrity | 1 | LOW |

---

## 2. Most critical risks (owner-facing, ranked)

1. **FIN-001 (CRITICAL) — the wallet has no currency dimension.** A referral reward computed in the
   *payer's* unit (Toman for `fa`, USD for `en`) is credited 1:1 to the *referrer's* unitless
   `users.credit` and later spent against the referrer's own tariff. An `en` referrer earning a
   Toman reward gets a USD plan for free (≈100 000× over-credit). Mechanism is confirmed statically;
   exposure is data-dependent (requires a referrer/payer language split).
2. **OPS-001 (HIGH) — the entire deployment chain points at a different project.**
   `deploy/deploy.sh:17-19` uses `/opt/bot-ai-automatic` + service `bot-ai-automatic`; the intended
   identity is `/opt/bot-official-telegram` + `newsbot.service`. The strings `newsbot.service` and
   `/opt/bot-official-telegram` appear **nowhere** in the repository. `systemctl restart` therefore
   fails, and `deploy.sh:90-93` calls `die` instead of rolling back — leaving new code on disk and
   old code running. **Production state is UNVERIFIED and may be affected.**
3. **FIN-002 / FIN-003 (HIGH) — payment approval is not re-quoted and not exclusive.**
   A frozen wallet share is applied to a changed wallet (overcharge, and `plan_changes` disagrees with
   `credit_log`); two pending requests for one user let the later approval silently overwrite the
   earlier paid plan with no refund path.
4. **FIN-004 (HIGH) — advertised price ≠ charged price.** The plan page discounts the *gross* tariff
   while the receipt charges the *net* payable; the user consents to 2 700 000 and is asked for
   2 750 000.
5. **UI-006 / UI-009 / OBS-013 (HIGH) — money-path notification loss.** A receipt whose caption
   exceeds 1000 chars is rejected by Telegram and the failure is swallowed (no administrator ever sees
   the receipt, yet the customer is told "receipt received"); any other button press wipes the
   in-flight receipt form so the customer's photo is silently ignored.
6. **NET-001 / SEC-001 (HIGH) — unguarded SSRF egress via the AI-provider path.** `newsbot/ai_models.py:93`
   posts to a user-supplied `base_url` with no host validation and `follow_redirects=True`; any account
   holding role `admin` (granted automatically by the free plan) can reach internal/metadata hosts.
7. **OPS-002 / OPS-003 (HIGH) — the deploy health gate is both false-positive and false-negative
   prone** (10 s budget vs `Type=notify`/`TimeoutStartSec=180`; a rolling 120 s journal grep for the
   word "traceback" that matches the app's own handled-exception logs). A correct release can be rolled
   back; a crash-looping bot can be reported healthy.
8. **OBS-011 / OBS-013 (HIGH) — alerting is lossy and its only channel is Telegram DMs.** A full queue
   drops messages; a failed receipt notification orphans a paid request; the DB may say `approved`
   while the customer is never told.
9. **FIN-009 / OBS-005 (HIGH) — referral rewards are granted outside the payment transaction with no
   reconciler.** A crash between the payment commit and the referral transaction loses the reward
   permanently, and the idempotency markers are never purged.

---

## 3. What was actually inspected

**Executed / observed**
- Full clone of `main` @ `08bc876`; branch list (10 branches), all 19 PRs (1–19, all closed/merged),
  commit history, per-commit diffs.
- **Test suite executed** on Windows 11 / Python 3.13.14 (out-of-tree venv, pinned deps, nothing
  installed): `python -m pytest -q -p no:cacheprovider` → **455 passed, 0 failed, 0 skipped** (112.5 s).
  Re-run in reverse alphabetical file order → **455 passed** (no order sensitivity). Repo left clean
  (`git status --porcelain` empty; no `newsbot.db` created in the repo).
- **Isolated DB probe** (temp DB only, outside the repo) to test the commit semantics of `db.q()`.
  Result: all 10 write sites flagged by static heuristics **do** pass `commit=True` (on a continuation
  line) — the suspected "uncommitted writes" defect was **disproved** and is *not* reported.
- Every `newsbot/*.py` module read in full or by targeted extraction: `db.py`, `config.py`, `app.py`,
  `net_http.py`, `publish.py`, `cycle.py`, `scheduler.py`, `core_*.py`, `ui_*.py`, `ai_models.py`,
  `generate.py`, `discover.py`, `extract.py`, `text_format.py`, `timeutils.py`, `concurrency.py`,
  `watchdog.py`, `notify.py`, `ratelimit.py`, `logutils.py`, `secret_store.py`, `opctx.py`,
  `runtime.py`, `adfilter.py`, `core_discounts.py`.
- `deploy/deploy.sh`, `deploy/Dockerfile`, `deploy/systemd/*.conf`, `.github/workflows/ci.yml`,
  `.github/workflows/deploy-vps.yml`, `.env.example`, `requirements*.txt`, `run.py`,
  `tools/*.py`, and all seven `docs/*.md` files.
- All 34 test files (6 473 lines) inventoried; ~22 source-asserting tests identified.
- AST-level analysis: every `callback_data` emitter vs. every handled route (216 emitters, **zero
  unmatched**); i18n `fa`/`en` key and placeholder parity; every `q()` call inside an `async def`;
  every `except ... pass`; every `time.sleep` / `subprocess` in async paths.
- Git-history secret scan (all refs): **no real secrets committed**; only synthetic test fixtures and
  `.env.example` placeholders.
- Environment-variable reconciliation: `.env.example` ↔ `newsbot/config.py` ↔ `README.md` —
  **62/62 documented defaults match; zero undocumented-but-used; zero documented-but-unused**
  (the cleanest area of the repository).

**NOT inspected / limitations (recorded, not guessed)**
- **No VPS access.** No systemd unit, no `journalctl`, no deployed SHA, no real `.env` permissions,
  no real database, no installed-dependency list, no scheduled-job state, no `sshd` configuration.
  → "Is production running `08bc876`?" is **UNVERIFIED**.
- **No CI run observed.** "Is CI green on `08bc876`?" is **UNVERIFIED**.
- **GitHub branch protection / required reviews / environment protection / secret-scanning settings
  could not be read** with the available tooling. **UNVERIFIED.**
- **No live Telegram client, no visual rendering check, no Bot API call.** All Telegram UX findings are
  static; no screen was seen on a device.
- **No external network calls.** Cloudflare D1 was never executed (all D1 assertions use a fake
  client); AI providers were never called; no RSS was fetched.
- **`docs/PERFORMANCE.md` numbers were not re-measured** (the benchmark was invoked only as a
  subprocess with `--ops 40` inside one test). They are arithmetically consistent with the harness
  but remain **UNVERIFIED** as current measurements.
- **No coverage measurement is possible** — `pytest-cov` is not installed and no coverage config exists.

---

## 4. Audit domain status

| # | Domain | Status | Findings | Notes |
|---|---|---|---|---|
| A | Architecture & code quality | `AUDITED_WITH_FINDINGS` | ARCH notes folded into `PIPE/PERF/DB` | Layered core/* separation is good; UI layer is very large |
| B | Python runtime & dependencies | `AUDITED_WITH_FINDINGS` | `OPS-019`, `SEC-012`, `OPS-017` | Exact version pins, no hash pins, Python 3.13 only |
| C | Database & data integrity | `AUDITED_WITH_FINDINGS` | `DB-001…011` | SQLite path is strong; D1 path has atomicity/parity gaps |
| D | User data & account lifecycle | `AUDITED_WITH_FINDINGS` | `FIN-005`, `DB-007` | No FK constraints; free-plan downgrade path |
| E | Financial correctness | `AUDITED_WITH_FINDINGS` | `FIN-001…024` | **1 CRITICAL**, 3 HIGH — top remediation target |
| F | Content discovery & source fetching | `AUDITED_WITH_FINDINGS` | `PIPE-009…015`, `NET-002…008` | SSRF filter strong for source/media; weak for AI path |
| G | AI models & content generation | `AUDITED_WITH_FINDINGS` | `PIPE-001…008`, `NET-001` | Budgets are rates, not caps; cache key ignores content |
| H | Publishing & scheduling | `AUDITED_WITH_FINDINGS` | `PUB-001…005`, `PIPE-021` | Exactly-once **is** correctly implemented (no duplicates) |
| I | Telegram UI/UX — every journey | `AUDITED_WITH_FINDINGS` | `UI-001…026` | Route map complete; no dead buttons, 4 rendering defects |
| J | Security & authorization | `AUDITED_WITH_FINDINGS` | `SEC-001…016` | No IDOR found; one unguarded egress path |
| K | Networking & external services | `AUDITED_WITH_FINDINGS` | `NET-001…008` | Telegram pool unconfigured; 3 DNS lookups per hop |
| L | Performance & latency | `AUDITED_WITH_FINDINGS` | `PERF-001…007` | `q()` on the event loop; O(N) lock pruning; no baseline |
| M | VPS, Linux, systemd | `PARTIALLY_AUDITED` (repo side only) | `OPS-001…003`, `OPS-014`, `OPS-021`, `OPS-022` | **Host not inspected** — drop-ins never installed by the pipeline |
| N | CI/CD, releases, recovery | `AUDITED_WITH_FINDINGS` | `OPS-004…013`, `OPS-030` | Rollback exists but does not revert dependencies |
| O | Testing & QA | `AUDITED_WITH_FINDINGS` | `TEST-001…014` | 455 tests pass; ledger invariant & D1 payment path uncovered |
| P | Observability & operations | `AUDITED_WITH_FINDINGS` | `OBS-001…016` | No metrics, no durable alerts, no business dead-man's switch |
| Q | Documentation & configuration | `AUDITED_WITH_FINDINGS` | `OPS-023`, `OPS-024`, `DOC-*` | ~30 doc mismatches; env documentation is exact |
| R | Business logic & cross-module integration | `AUDITED_WITH_FINDINGS` | `FIN-001…004`, `PIPE-018`, `PIPE-019` | The critical cross-module defects live at the money boundary |
| S | Accessibility, clarity, product quality | `AUDITED_WITH_FINDINGS` | `UI-014…018`, `UI-025` | Home screen has one button; no bidi isolation |
| T | Remaining risks & maintainability | `PARTIALLY_AUDITED` | `DB-005`, `DB-008`, `PERF-005` | Single-process, single-host design; no DR plan for host loss |

---

## 5. Latest completed actions

1. Established the real baseline: clean clone of `main` @ `08bc876`; 10 branches; 19 merged PRs; no
   unmerged work on `main`; a stale local working copy exists at
   `C:\Users\Mustafa\WorkBuddy AI\2026-10-13-00-32-45\work` (branch `phase-15-watchdog`, 3 modified
   files + 1 untracked file) — **not used**, left untouched.
2. Ran the full test suite twice (455 passed each time) in an out-of-tree venv; the repository was
   left clean.
3. Audited all 20 domains using six parallel read-only sub-audits (security, financial, UI/UX,
   CI/CD-ops-docs, testing-observability, content-pipeline-networking-performance) plus a direct
   database-layer audit.
4. Proved and **disproved** one hypothesis empirically (the suspected uncommitted-write defect) — the
   suspicion was wrong and is *not* filed.
5. Wrote the eight persistent audit documents listed in §7.
6. **PH-00 (2026-10-20) — verification pass:**
   - **FIN-001 upgraded STATIC → REPRODUCED** by executing the real money path against an isolated temp
     database: a Toman-denominated referral reward of 120 000 is credited into the unitless wallet and
     then spent 1:1 against a `$5` USD tariff ⇒ `used_balance=5.0`, **`payable=0.0`** (a free USD plan,
     a 24 000× over-credit). The mirror case was confirmed too.
   - **OPS-001 re-verified mechanically**: 51 occurrences of `bot-ai-automatic` across 5 tracked files;
     `newsbot.service` = 0; `bot-official-telegram` = 0. One evidence site was missing from the original
     register — `deploy/systemd/10-restart-always.conf:7` (the `ExecStopPost` logger tag) — and has been
     added.
   - **Test suite re-executed in the clean audit workspace: 455 passed**, 0 failed.
   - **Audit documents validated**: 516 evidence references resolved, 0 past EOF, 0 severity mismatches,
     register↔status.json ID sets identical.
   - **2 confirmed repository-infrastructure defects fixed** with regression tests: OPS-028
     (`deploy/deploy.sh` was committed non-executable → now `100755`) and DOC-001 (the project's own
     `ROADMAP.md`, cited by 6 places, was never tracked → now added verbatim; the two most
     security-sensitive documents and one test module pointed at a non-existent file).
   - **Production verification BLOCKED**: this environment has no `~/.ssh`, no ssh config, no `gh` CLI and
     no VPS environment variables, so the host-side checklist (unit name, running SHA, `.env` mode, DB
     integrity, backup inventory, journal) could not be executed. This is recorded as an environment
     limitation, not as a finding.
6. **Committed and pushed** (the owner authorised commit + push):
   - branch `audit/ph-00-verification-and-repo-integrity`, commit **`3ebe347`**
   - **PR #20** — https://github.com/Aryasamadi/Bot-Official-Telegram-/pull/20 — **CI green**
     (`test` → success, `docker-build` → success), **not merged** (merging was not authorised).

---

## 6. Next recommended action

**The recommended first remediation phase is `PH-01 — Financial correctness & money-path integrity`**
(FIN-001 → FIN-006, FIN-012, FIN-013, FIN-024, TEST-005, TEST-006, OBS-005, OBS-007), because it carries
the only CRITICAL finding — now **reproduced** — and every mis-stated amount is a direct customer-trust and
revenue issue.

Two prerequisites remain open:

1. **`PH-00` must be re-run from a host that has the VPS key** (or the owner must paste the outputs of the
   checklist in `ROADMAP.md` PH-00). Until then, the production state is unverified and **OPS-001's fix must
   not be applied** — renaming the service unit without reading the host could make a working service
   unstartable.
2. **Six owner decisions (Q-1…Q-6)** in `DECISIONS.md` must be answered, in particular **Q-1** (how a
   cross-currency referral reward is denominated) and **Q-2** (whether a changed wallet refuses the
   approval or re-derives it), which determine the design of PH-01.

`PH-04` (deployment identity and health gate) must be merged **before** the first release that carries any
PH-01…PH-03 fix.

**Nothing will be changed, committed, pushed, deployed or migrated until the owner names a phase.**

---

## 7. Audit documentation files (created)

All under `docs/audit/` in the audit workspace
`C:\Users\Mustafa\AppData\Local\Temp\opencode\audit-newsbot\`:

| File | Purpose |
|---|---|
| `docs/audit/MASTER_STATUS.md` | This file — master state, counts, risks, next action |
| `docs/audit/ROADMAP.md` | Ordered, dependency-aware remediation phases with acceptance criteria |
| `docs/audit/FINDINGS.md` | Full 180-finding register with evidence and verification status |
| `docs/audit/status.json` | Machine-readable state, findings, phases, next permitted action |
| `docs/audit/ARCHITECTURE.md` | Verified architecture, components, data flows, critical transitions |
| `docs/audit/TEST_MATRIX.md` | Critical-feature → test coverage map with gaps |
| `docs/audit/DECISIONS.md` | Decisions, trade-offs, assumptions, rejected approaches |
| `docs/audit/CHANGELOG.md` | Audit-event log (phases, files, tests, commits, deployment status) |

---

## 8. Exact command the owner can issue to continue

```
# Continue the audit / ask questions (no code changes)
"continue the audit"

# Start a remediation phase (must name the phase ID from docs/audit/ROADMAP.md)
"start phase PH-01"

# Read-only production verification on the VPS (needs SSH access; touches nothing)
"start phase PH-00"

# Read the current state
"read docs/audit/MASTER_STATUS.md"
```

**Reminder for future sessions:** always read `docs/audit/MASTER_STATUS.md` and `docs/audit/status.json`
first, then compare the current Git commit against the recorded baseline (`08bc876`) before acting.
