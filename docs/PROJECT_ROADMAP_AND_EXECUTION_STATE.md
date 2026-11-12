# PROJECT ROADMAP AND EXECUTION STATE

> **Source of truth for this project.** Read this file first in every session, then validate it
> against GitHub (`git status`, `git log`, PR list, CI) before doing anything.
> Do not rely on chat memory. If this file and git disagree, git wins — then fix this file.

> **Where this file lives:** `docs/PROJECT_ROADMAP_AND_EXECUTION_STATE.md`
> **Historical audit material:** `docs/audit/MASTER_STATUS.md`, `docs/audit/FINDINGS.md`,
> `docs/audit/ROADMAP.md` (do not treat them as current state).

| Field | Value |
|---|---|
| **Repository** | `Aryasamadi/Bot-Official-Telegram-` |
| **Default branch** | `main` |
| **Current working branch** | `phase1-10/state-and-truth-alignment` |
| **Branch base commit** | `d0efb7e0692ec0b32c83e86a05b664ace315cded` |
| **Last verified commit** | `d6ae7c2` (phase 1 branch) · `main` is `d0efb7e0692ec0b32c83e86a05b664ace315cded` |
| **Last state update** | 2026-11-12T00:00:00Z (UTC) |
| **Overall status** | `IN_PROGRESS` |
| **Current phase** | Phase 1 of 10 |
| **Current sub-step** | P1-1 — **COMPLETE**; PR #56 open, CI green, awaiting the required approving review |
| **Owner authorization** | Required for every phase. Phase 1 authorized 2026-11-12. |

---

## 1. Immediate state (read this before anything else)

**Where we are:** Phase 1, sub-step P1-1 — **done**. PR #56 open, CI green, blocked only by the
required approving review (branch protection, not a code problem).
**Done so far:** Phase 0 audit; this file created; README/docs aligned; regression test added.
**Remaining in Phase 1:** obtain the approving review → merge PR #56 → then Phase 1's remaining
task P1-2 (merge the #46–#55 chain), which needs an owner ruling (see B-1).
**Exact next action:** owner reviews and merges PR #56. Nothing else may start without a new instruction.

**Do not repeat** the Phase 0 audit — it is recorded in §7 and in the owner's report
(`PHASE0_AUDIT_REPORT.md`, outside the repository).

---

## 2. The 10-phase roadmap

| # | Phase | Goal | Status | Depends on |
|---|---|---|---|---|
| 1 | Truth, state file, PR-chain unblock | Make the repo's recorded state match GitHub; create this file; unblock the stacked PR chain; align README/docs | `IN_PROGRESS` | — |
| 2 | Provider-centric model data + backend | Independent Providers (name / base URL / token), connection test, model discovery with manual fallback, encrypted keys | `NOT_STARTED` | 1 |
| 3 | Round-robin model rotation engine | Every request takes the next model in order; atomic cursor; restart/concurrency safe; per-request audit | `NOT_STARTED` | 2 |
| 4 | Reply-to-channel-post Q&A core | Answer questions replied to a channel post, with controlled post context and idempotent delivery | `NOT_STARTED` | 2, 3 |
| 5 | Q&A quotas | Per-post/day, cooldown, per-user daily cap — enforced atomically in the backend | `NOT_STARTED` | 4 |
| 6 | Typing lifecycle | Show `typing` while waiting; always stop it; no orphan tasks | `NOT_STARTED` | 4 |
| 7 | Onboarding, home menu, message routing | Language → welcome → "start" → add channel; free plan without an extra button; stable state | `NOT_STARTED` | 1 (4 for reply routing) |
| 8 | Deeplink "back to channel" | Return the user to the exact source post, or explain the platform limit honestly | `NOT_STARTED` | 1 |
| 9 | Temporary GitHub Actions harness | Manual-only workflow: token + super-admin id, READY, run for the target duration, clean shutdown | `NOT_STARTED` | 1 |
| 10 | Hardening, cleanup, release gate | Rotate secrets, dependency/secret scans, remove proven-dead code, full green suite, release runbook | `NOT_STARTED` | 1–9 |

**Status values:** `NOT_STARTED` · `IN_PROGRESS` · `BLOCKED` · `COMPLETED`

**Completed phases:** none yet. Phase 1 is `IN_PROGRESS`: task P1-1 complete (PR #56), task P1-2
(the PR-chain merge) blocked on an owner ruling.

---

## 3. Architecture decisions (binding)

| ID | Decision | Alternatives rejected | Why |
|---|---|---|---|
| ADR-1 | Single state file: this one. `ROADMAP.md` (root) and `docs/audit/ROADMAP.md` are **historical** | Multiple status files | Contradictory state files were already the cause of a wrong picture of the project |
| ADR-2 | Provider model = new `ai_providers` table + additive `ai_models.provider_id` | Reuse a text column on `ai_models` | A text column cannot carry discovery, per-provider keys or grouping |
| ADR-3 | Rotation cursor persisted in DB with CAS (`version` column) | In-memory cursor; `ORDER BY RANDOM()` | Restart/multi-worker safe; random is not "round-robin" |
| ADR-4 | Round-robin is **not** failover: a provider error does not advance within the same request | Current `_try_models` behaviour | Explicit product requirement; must be flagged and tested |

---

## 4. Phase 1 — scope and acceptance criteria

### Task P1-1 — truth, state file, docs alignment

**In scope**
- Create this file (done).
- Fix README factual drift: Python version, test count, stale claims.
- Reconcile `docs/audit/MASTER_STATUS.md` and `docs/audit/status.json` with the real `main`.
- Record the real reason PR #46 is blocked.

**Out of scope**
- Any product behaviour change.
- Merging the PR chain (that is Task P1-2 and needs an owner ruling).

**Files/modules involved**
`README.md`, `docs/PROJECT_ROADMAP_AND_EXECUTION_STATE.md` (new), `docs/audit/MASTER_STATUS.md`,
`docs/audit/status.json`, `docs/audit/ROADMAP.md`.

**Prerequisites / dependencies:** none.

**Risk / blast radius:** low — documentation and state only; no runtime code, no schema.

**Migration / backup / rollback:** none. Rollback = revert the commit.

**Tests and acceptance criteria**
1. `pytest -q` is green (same result as the recorded baseline). — **MET** (781 passed / 4 known Windows-only failures)
2. `python -m pyflakes newsbot tools run.py tests` is silent. — **MET**
3. `python tools/final_verification.py` reports 17/17. — **MET**
4. README states Python 3.13+ and the real test count (785 collected). — **MET**
5. `docs/audit/MASTER_STATUS.md` no longer claims the audited commit is `08bc876`. — **MET** (marked historical, points at this file)
6. This file records the true branch-protection rules and the reason for `BLOCKED`. — **MET**

**Deliverables:** this file, the aligned docs, the regression test, commit `d6ae7c2`, PR #56.

---

## 5. Test evidence (real runs only — never claim an unrun test)

| Date (UTC) | Command | Result |
|---|---|---|
| 2026-11-12 | `pytest -q` (isolated clean clone of `main`) | 4 failed, 777 passed in 387.59s — the 4 failures are Windows-only Git-Bash path artifacts (`/app` → `C:\Program Files\Git\app`) in `tests/test_deploy_script.py`; Linux CI is green |
| 2026-11-12 | `python -m pyflakes newsbot tools run.py tests` | clean (exit 0) |
| 2026-11-12 | `python tools/final_verification.py` | 17/17 |
| 2026-11-12 | `pytest -q --collect-only` | 781 tests collected |

**Not run / unavailable:** coverage percentage (no coverage tool in dev requirements), live VPS
deployment verification, real Telegram integration, Cloudflare D1 live migration.

---

## 6. Migrations, backups, rollback

No schema change in Phase 1. Standing rule for later phases: every migration is **additive only**
(`ADD COLUMN` / new table), idempotent, rehearsed against a copy of the production DB, and has a
documented rollback. Legacy `users.credit` balances are frozen, never auto-converted.

---

## 7. Git delivery record

| Item | Value |
|---|---|
| Working branch | `phase1-10/state-and-truth-alignment` |
| Base | `d0efb7e` |
| Phase 1 commit | `d6ae7c2` |
| PR | **#56** — `MERGEABLE` / `BLOCKED` (review required) · CI `test: SUCCESS`, `docker-build: SUCCESS` |
| CI on `main` at `d0efb7e` | `test: success`, `docker-build: success`, `deploy: skipped` |
| Production commit SHA | **UNVERIFIED** — no VPS read was possible |
| Open PR chain | #46 → #47 → #48 → #49 → #50 → #51 → #52 → #53 → #54 → #55 (plus #56 from this phase) |
| PR #46 block reason | `REVIEW_REQUIRED` — branch protection requires 1 approving review; also `required_status_checks: test` (strict), linear history, signed commits off, `enforce_admins: true` |

**Branch protection on `main` (read from the GitHub API on 2026-11-12):**
- Required status checks: `test` (strict = branch must be up to date)
- Required approving reviews: 1 (stale reviews dismissed on new pushes)
- Required linear history: **true** (no merge commits on `main` — see §9)
- Force pushes / deletions: disabled
- `enforce_admins`: true

---

## 8. Blockers, open questions, residual debt

| ID | Item | Status |
|---|---|---|
| B-1 | Merging the stacked PR chain requires an approving review and **linear history** (rebase, not merge) | needs owner decision + review |
| B-2 | Production commit SHA / unit name / env permissions are unverified | needs VPS access |
| B-3 | 350-minute GitHub-hosted run vs the hard 6-hour job cap (zero safety margin) | needs owner decision |
| B-4 | Whether the bot is added to the channel discussion group (prerequisite for Phase 4) | needs owner action |
| B-5 | `newsbot.env` outside the repo holds a real bot token and `SECRETS_KEY` in plain text | needs rotation |
| D-1 | 18 unmerged stale branches, 37 remote branches total | deferred to Phase 10 |
| D-2 | 4 Windows-only failing deploy tests degrade the local signal | deferred to Phase 10 |

---

## 9. Working agreement (non-negotiable)

1. One phase per explicit owner instruction. Completing a phase is not permission for the next.
2. Never claim a test, deploy or fix works without a real run and recorded evidence.
3. Distinguish: **seen in code** / **verified by test** / **inferred** / **hypothesis** / **needs access**.
4. Never expose tokens, API keys, passwords, personal data or secrets — not in code, logs, commits,
   PRs, artifacts or this file. Record only the variable/secret **name** and whether it is set.
5. No large rewrites without tests. Every change small, traceable, testable, revertable.
6. Merge only after acceptance criteria pass. Force-push and history rewrite are not routine.
7. Production deploy and real user messaging need explicit owner approval.
8. Update this file at every meaningful step, before any stop, and before any likely context compaction.
9. Never delete a feature, data, channel, setting or recovery path just to simplify a rewrite.