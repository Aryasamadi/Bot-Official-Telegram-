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
| **Current working branch** | **docs/phase10-deploy-rule** (records-only, from `main` @ `1d7916a`) |
| **Branch base commit** | `1d7916a031e9f97341eefe7d2dbf25a314d4401e` (PR #112 squash-merge — Phase 10 records) |
| **Last verified commit** | `1d7916a` on `main` (PR #112 squash-merge, 2026-11-28) · CI `test: SUCCESS` (run 46108539227) |
| **Last state update** | 2026-11-27 (UTC) |
| **Overall status** | `IN_PROGRESS` — Phases 1–3 are `COMPLETED`; Phases 4–9 are code-complete but **not live-verified** (the single D-7 run ended `NO_LINKED_GROUP`); **Phase 10 is `IN_PROGRESS`** — hardening, cleanup and the release gate are code-complete and documented, but two owner gates stay open: the real secret rotation and a successful live D-7 run |
| **Current phase** | Phase 4 of 10 — `IN_PROGRESS` (code merged; live acceptance deferred to Phase 10, D-7) · **Phase 10**: `IN_PROGRESS` (PRs #107–#110 merged; D-7 and the rotation are still open) |
| **Current sub-step** | Phase 10 close-out is **blocked on the owner**: (1) give the test channel a **linked discussion group** and re-run `qa-live` (`duration_minutes=340`, `scenario=duration`, `db_mode=fresh`); (2) execute the rotation in `docs/OPS_SECRET_ROTATION.md`; (3) approve the **latest** `Deploy to VPS` run on `main` (46108808054 for `main` @ `1d7916a` at the time of writing — every merge to `main` queues a new run and cancels the previous pending one). Phase 11 starts only on explicit owner instruction |
| **Owner authorization** | Required for every phase. Phase 1 authorized 2026-11-12. The owner ruled on 2026-11-12: every PR of a phase must be **merged** when that phase completes, and each existing PR (#46–#56) gets its disposition **inside its own phase**. |

---

## 1. Immediate state (read this before anything else)

**Where we are (2026-11-20):** Phase 4's **code is merged** and its live harness is merged, but the phase
is **not declared complete**: the owner's rule is that a phase is only `COMPLETED` after a **live
end-to-end run**. The group Q&A core is **#83** (`f850d44`): schema v12, `newsbot/qa.py`, the
fail-closed/at-most-once handler, the per-channel admin toggle. The plan-level AI quota is **#84**
(`0985e3e`): `newsbot/ai_quota.py`, atomic global consumption, quota shown before purchase and in the
user panel. **#82** (`f4afa0e`) carried the design and the implementation plan. The live harness
(`tools/qa_live_acceptance.py`, the manual-only `.github/workflows/qa-live.yml`, the runbook
`docs/QA_LIVE_ACCEPTANCE.md`) merged as **#86 → `88675f3`**. The owner-reported "healthy model fails in
the bot" was diagnosed as provider rejections plus three real bot-side key defects; the hardening merged
as **#88 → `6d5b4ae`** (records **#89 → `74abc16`**).
**Owner decision (2026-11-20):** the Phase 4 **live acceptance is deferred to Phase 10** — the live test
will be run once, there, together with the release checks (**D-7** in §8). Phase 5 may therefore start
now, on the owner's explicit instruction, without waiting for that run; the "no next phase before a live
verification" rule is waived **for this one transition only** and is recorded here.
Before Phase 4, Phase 3 (round-robin rotation) was completed the same way: one phase per explicit owner
instruction, each rebased onto the then-current `main`, squashed, with the review gate lifted and restored.
**Done so far:** Phase 0 audit; this file; README/docs aligned; P1-1 regression guards; the disposition of
every existing PR (#46–#56) in §4.1; Phases 1–4 code complete; the full suite run on each `main`.
Two operational notes. First, **#55 was closed by GitHub**, not by a decision: merging #54 with
`--delete-branch` removed #55's base branch, and a PR whose base disappears is closed; it could not be
reopened, so its content was rebased onto `main` and re-opened as **#64**. Second, `#64` also enabled
Dependabot, which opened **#68–#78** (dependency bumps, several major). Those are recorded as **D-4**.
**Remaining in Phase 4:** only the live run, now owned by Phase 10 (D-7). The **earlier Phase 4
attempt** (`44506386031`, on `74abc16`) ended `exit 2 / CHANNEL_NOT_REGISTERED` after the full budget
because the channel was never registered in the runner's bot (`get_chat` answered `Chat not found`); no
code change is pending from it. The **Phase 10 attempt** (run `46013627630`, 2026-11-27, on `967afde`)
ended `exit 2 / NO_LINKED_GROUP` after its full budget because the channel had no linked discussion
group — again no code change is pending, but a further run is needed (§4.9.4).
**Phase 5 result:** the guardrails are merged. One atomic `INSERT … SELECT … WHERE … ON CONFLICT DO
NOTHING` on `qa_answers` enforces the per-user cooldown (default 30 s), the per-user daily cap
(default 30) and the per-post daily cap (default 50) at claim time — no counter table, no schema
version bump, and a refused ask never reaches `ai_chat`. Each channel can override all three values
in the admin panel; blank means inherit from env. Super admins are unlimited. Cooldown refusals are
silent; daily-cap refusals get one short throttled notice. Design and plan are in **#91 → `7adb0da`**;
code and 30 new tests in **#92 → `1e6e8eb`**. The Phase 5 live scenario is covered by the single
Phase 10 run together with Phase 4's deferred acceptance (D-7); this phase does **not** claim a live
verification.


**Phase 6 result:** the native `typing` lifecycle is **code-complete**. A single reusable
`chat_action.typing_while(bot, chat_id, kind)` runs a refcounted per-chat heartbeat: it starts after
1 s, refreshes every 4 s, mutes that chat for 30 s after three consecutive send failures, and is
cancelled and awaited on every exit path. It is installed around `work()` in `run_user_operation`
and around the AI-and-delivery block in `on_group_reply`; every quota refusal returns **before** the
block and therefore emits no chat action. `post_shutdown` marks shutdown, reaps all heartbeats, and
includes `typing:` in its cancellation list — a handler resuming after shutdown cannot register a new
heartbeat. Config is env-only (`TYPING_ENABLED`, `TYPING_START_DELAY_S`, `TYPING_REFRESH_S`). The
whole-branch review initially found a genuine P1 G3 hole (a heartbeat could be created after
`post_shutdown`); it was fixed test-first and re-approved. Design/plan **#94 → `2bedda4`**; the
Phase 5 day-window determinism defect found by that PR's CI was fixed in **#95 → `db135f1`**; code
**#96 → `5eb8fea`**. Phase 6's live scenario joins the single Phase 10 run (D-7); this phase does
**not** claim a live verification.

**Phase 7 result:** the first-run flow is **code-complete**. A new user picks a language,
sees a one-time welcome, taps `onb:start`, and reaches the add-channel form **without pressing a
separate free-plan button**: the seeded free plan is granted once through the existing `free_claim`
→ `assign_plan` → `free_release` path; paid users keep their caps; a lost concurrent claim or a
stale button cannot grant twice or throw an onboarded user back into add-channel. `onboarding_state`
(`''` / `welcome` / `adding_channel` / `done`) lives in an ADD-only `users` column, and
`SCHEMA_VERSION` is 13 in both the base DDL and the migration path. The normal user home no longer
offers plans / support / invite / help buttons — all slash commands still work, admins and super
admins never enter onboarding, and Reply→Q&A is unchanged. Design/plan **#98 → `7482146`**; code
**#99 → `987a84f`**. The independent whole-branch review first returned `CHANGES_REQUIRED` (two
state traps plus four schema-test regressions found by the full suite); all were fixed test-first
(`0bd9e54`, `4ffd2a7`) and it then returned `APPROVED`. Phase 7's live scenario joins the single
Phase 10 run (D-7); this phase does **not** claim a live verification.

**Phase 8 result:** the deeplink page now carries **one localized URL button** back to the exact
source post. On a successful publication the payload records `src: {chat_id, message_id, username}`;
the URL uses `t.me/<username>/<id>` for a public channel and `t.me/c/<internal>/<id>` for a private
one, with an honest member-only / "if you are a member" note where the platform cannot promise
access. Old deeplinks are unchanged (no button, no note), a failed send writes no row, and a renamed
handle heals on the next publication. Long legacy bodies carry exactly one button, on the last part.
No schema change (`SCHEMA_VERSION` stays 13), no extra API call. Design/plan **#101 → `0264f52`**;
code **#102 → `db86543`**. The whole-branch review returned `APPROVED` with three LOW findings and
no fix required before merge; the rich-path keyboard's real-client behaviour and the deleted-post
case stay explicit live gaps in the single Phase 10 run (D-7, D8-8/R2).

**Phase 9 result:** the single manual `qa-live` harness is **code-complete**. The supervisor now
counts active time from the `READY` marker and reports eight timing fields plus the explicit
10-minute difference from 350; the `duration` scenario holds to the budget while `core`/`full` keep
their early exit for D-7. The two secure inputs (`TELEGRAM_BOT_TOKEN`, `SUPER_ADMIN_ID`) are mapped
to the names the code reads, validated fail-closed without echoing values, and accepted as id lists.
The report carries a five-surface real / simulated / not-performed table; long super ids are checked
for leaks without ever writing their value into the report. Design/plan **#104 → `c9c7f4f`**; code
**#105 → `9cd7b61`**. The whole-branch review returned `APPROVED` with no fix required before merge;
its open items (listed in §4.8) are carried into the single Phase 10 run (D-7). **No live dispatch is
claimed** in this phase.

**Phase 10 result (2026-11-27) — hardening, cleanup, release gate: `IN_PROGRESS`.** The phase's code
and documentation work is merged: **#108** (`361a10c`) closed the `libzstd1` CVE
(`CVE-2026-124851`), **#107** (`9886972`) carried the approved design and plan, **#109**
(`b660bc3`) hardened the Windows test harness (19 failures → 0), delivered the atomic
`SECRETS_KEY` rotation tool with its 18 tests, fixed the false-positive secret-leak check (F-3 —
261 lines), moved the QA cache prefix `qa-live-db-v1-` → `qa-live-db-v2-` and added
`deploy/env.example`; **#110** (`ef185ca`) removed 11 provably dead module-level helpers under a
bounded three-part proof (857 → 846 module-level symbols, 11 → 0 zero-reference candidates); and
**#111** (`e0698ef`) merged the phase's records — the two runbooks, the final report and this file.
Branch hygiene: **33 merged branches deleted** under a SHA guard (36 candidates; 3 kept because the
branch moved after its merge), 16 kept, and the stale remote-tracking refs were pruned by
`git fetch --prune` (51 tracking refs remained after the first pass, 19 after the second). The full
suite is green at
**1208 passed / 0 failed**, the independent verifier is **17/17**, `run.py --check` exits 0, and
every merge ended with **15/15 IDENTICAL** branch-protection fields. The runbooks are merged:
`docs/OPS_SECRET_ROTATION.md` (the rotation — owner-executed), `docs/RELEASE_RUNBOOK.md` (the
release gate) and `docs/PHASE10_FINAL_REPORT.md` (the findings table).
**Two owner gates are still open, so the phase is not `COMPLETED`:**
1. **D-7** — the single live run (46013627630) ended `exit_code=2` with `NO_LINKED_GROUP`: the
   registered channel has no linked discussion group, so no live check was observable. This is a
   prerequisite defect, not a code defect.
2. **The actual secret rotation** — the tool and the runbook exist; the execution (BotFather
   `/revoke`, `SECRETS_KEY` rotation, env update, restart, provider test, then deletion of
   `newsbot-deploy-pkg\newsbot.env`) belongs to the owner and has not happened. `newsbot.env`
   therefore stays untouched (decision **C8**). Provider keys are **not** reissued — residual risk
   **D-13**.

**Exact next action:** Phase 10 close-out — **owner action; no code is left to write.** (1) Give the test channel `Channel TechNowVpn (-1004341590425)` a **linked discussion group** with the bot as admin, then re-run `qa-live` with `duration_minutes=340`, `scenario=duration`, `db_mode=fresh`. (2) Execute `docs/OPS_SECRET_ROTATION.md` (maintenance window → backup → `--check` → `--apply` → env update → restart → provider test → BotFather `/revoke` → purge `qa-live-db-v1-*` → delete `newsbot-deploy-pkg\newsbot.env`). (3) Approve the **latest** `Deploy to VPS` run on `main` in the Actions tab — 46108808054 for `main` @ `1d7916a` at the time of writing; **every merge to `main` queues a new run and cancels the previous pending one**, so always approve the newest one (46083654457 and 46105550745 were cancelled in this way and never reached the VPS). A records-only close-out PR then marks Phases 4–10 `COMPLETED` **only if** both gates have evidence in §5. Phase 11 starts only on explicit owner instruction.

**Do not repeat** the Phase 0 audit — it is recorded in the owner's report `PHASE0_AUDIT_REPORT.md`
(outside this repository; not committed).

---

## 2. The 10-phase roadmap

| # | Phase | Goal | Status | Depends on |
|---|---|---|---|---|
| 1 | Truth, state file, PR-chain unblock | Make the repo's recorded state match GitHub; create this file; unblock the stacked PR chain; align README/docs | **`COMPLETED`** 2026-11-13 | — |
| 2 | Provider-centric model data + backend | Independent Providers (name / base URL / token), connection test, model discovery with manual fallback, encrypted keys | **`COMPLETED`** 2026-11-13 | 1 |
| 3 | Round-robin model rotation engine | Every request takes the next model in order; atomic cursor; restart/concurrency safe; per-request audit | **`COMPLETED`** 2026-11-14 | 2 |
| 4 | Reply-to-channel-post Q&A core | Answer questions replied to a channel post, with controlled post context and idempotent delivery | **`IN_PROGRESS`** — code merged 2026-11-16 (#83, #84, #82; key hardening #88); live acceptance **deferred to Phase 10** by the owner on 2026-11-20 (D-7) | 2, 3 |
| 5 | Q&A quotas | Per-post/day, cooldown, per-user daily cap — enforced atomically in the backend | **`IN_PROGRESS`** — code/design/tests merged 2026-11-20 (design+plan #91 → `7adb0da`, code #92 → `1e6e8eb`); the live scenario is covered by the single Phase 10 run (D-7), so no live verification is claimed here | 4 |
| 6 | Typing lifecycle | Show `typing` while waiting; always stop it; no orphan tasks | **`IN_PROGRESS`** — code merged 2026-11-22 (#94 design/plan, #95 Phase 5 determinism fix, #96 code); whole-branch review APPROVED after a P1 shutdown hole was fixed; live scenario **deferred to Phase 10** (D-7) | 4 |
| 7 | Onboarding, home menu, message routing | Language → welcome → "start" → add channel; free plan without an extra button; stable state | **`IN_PROGRESS`** — code merged 2026-11-24 (#98 design/plan, #99 code); `onboarding_state` + schema v13; whole-branch review APPROVED after a fix wave; live scenario **deferred to Phase 10** (D-7) | 1 (4 for reply routing) |
| 8 | Deeplink "back to channel" | Return the user to the exact source post, or explain the platform limit honestly | **`IN_PROGRESS`** — code merged 2026-11-25 (#101 design/plan, #102 code); one URL button + honest notes; whole-branch review `APPROVED`; live check **deferred to Phase 10** (D-7) | 1 |
| 9 | Temporary GitHub Actions harness | Manual-only workflow: token + super-admin id, READY, run for the target duration, clean shutdown | **`IN_PROGRESS`** — code merged 2026-11-25 (#104 design/plan, #105 code); READY-based timing + `duration` burn-in + secure inputs; whole-branch review `APPROVED`; live dispatch **deferred to Phase 10** (D-7) | 1 |
| 10 | Hardening, cleanup, release gate | Rotate secrets, dependency/secret scans, remove proven-dead code, full green suite, release runbook | **`IN_PROGRESS`** — code + docs merged 2026-11-27 (#108 CVE, #107 design/plan, #109 Windows+rotation+F-3, #110 dead code); **33 branches cleaned, 11 dead functions removed, suite 1208/0, verifier 17/17**; open owner gates: the real secret rotation and a successful live D-7 run | 1–9 |

**Status values:** `NOT_STARTED` · `IN_PROGRESS` · `BLOCKED` · `COMPLETED`

> **Phase 5 design constraint (owner, 2026-11-17):** AI usage must be **neither an absolute limit nor
> runaway consumption**. Quotas/cooldowns are guardrails against waste — no request may burn tokens or
> API calls that no user asked for, and no limit may be so hard that legitimate use is refused. Any
> Phase 5 mechanism that charges a user for an internal call, retries blindly, or sends a request the
> user did not trigger is out of scope by definition.


**Completed phases:** **Phase 1 — `COMPLETED` 2026-11-13** (P1-1: #56 `e3e321e`, #57 `533f753`; P1-2: #46→#50, oldest-first; closed by #58 `0f4b90a`).
**Phase 2 — `COMPLETED` 2026-11-13** (P2-1: #51 `16a7353`, #52 `886e19d`, #53 `bf34390`; P2-2: #60 `bf73b39`).
**Phase 3 — `COMPLETED` 2026-11-14** (P3-2: #62 `51bb8d2` — rotation engine + `/rotation`; P3-1: #54 `7f48ec4` — OBS-001/003/008/010/014, DB-007; review fixes #66 `3f78e51`; repo hygiene #64 `fa7f25f`, which replaced #55 after GitHub auto-closed it). **Every Phase 3 PR is merged.**
**Phase 4 — code merged 2026-11-16, live acceptance `PENDING`** (P4-1: #83 `f850d44` — schema v12, `newsbot/qa.py`, group handler, per-channel admin toggle; P4-2: #84 `0985e3e` — plan-level AI quota + quota UI; P4-0: #82 `f4afa0e` — design + implementation plan, merged last because it cites the Phase 4 files; live-acceptance harness: #86 `88675f3`). **Every Phase 4 code and harness PR is merged; the phase itself waits on the live run (§4.5).**

**PRs owned by each phase** (a phase is not `COMPLETED` while one of its own PRs is open — §9 rule 10):

| Phase | PRs it owns | State |
|---|---|---|
| 1 | #46, #47, #48, #49, #50, #56, #57 | **all merged** — Phase 1 `COMPLETED` 2026-11-13 |
| 2 | #51, #52, #53, #60 | **all merged** — Phase 2 `COMPLETED` 2026-11-13 |
| 3 | #54, #62, #66, #64 all merged | Phase 3 **`COMPLETED`** 2026-11-14 |
| 4 | #83, #84, #82, #86 merged | Phase 4 **`IN_PROGRESS`** — code and harness merged (2026-11-16 · 2026-11-18), live acceptance `PENDING` (§4.5) · this records-only PR is not a phase PR |
| 10 | #107, #108, #109, #110 merged | Phase 10 **`IN_PROGRESS`** — all four phase PRs are merged (2026-11-27); the phase waits on the two owner gates in §4.9 (D-7 live run + secret rotation) |

---

## 3. Architecture decisions (binding)

| ID | Decision | Alternatives rejected | Why |
|---|---|---|---|
| ADR-1 | Single state file: this one. `ROADMAP.md` (root), `docs/audit/ROADMAP.md`, `docs/audit/MASTER_STATUS.md` and `docs/audit/status.json` are **historical** (all carry a banner). `PHASE1-10-FINAL-REPORT.md` and `DEPLOY-VPS-UPDATE.md` were **retired in PR #47**; their banner text is preserved in §3.1 | Multiple status files | Contradictory state files were already the cause of a wrong picture of the project |
| ADR-2 | Provider model = new `ai_providers` table + additive `ai_models.provider_id` | Reuse a text column on `ai_models` | A text column cannot carry discovery, per-provider keys or grouping |
| ADR-3 | Rotation cursor persisted in DB with CAS (`version` column) | In-memory cursor; `ORDER BY RANDOM()` | Restart/multi-worker safe; random is not "round-robin" |
| ADR-4 | Round-robin is **not** failover: a provider error does not advance within the same request | Current `_try_models` behaviour | Explicit product requirement; must be flagged and tested |

### 3.1 Retired documents — banner preservation

PR #47 deleted two root-level documents that described an older cycle and no longer matched
the repository:

| File | Why it was retired |
|---|---|
| `PHASE1-10-FINAL-REPORT.md` | Report of the previous 10-phase cycle (161 tests, working tree `eb4753b`); every number in it is stale, and it was already marked "historical" by P1-1 |
| `DEPLOY-VPS-UPDATE.md` | One-off systemd deploy guide, superseded by `docs/VPS_DEPLOYMENT.md` (immutable image + compose + safe drain) |

The P1-1 banner that lived at the top of `PHASE1-10-FINAL-REPORT.md` is preserved here verbatim:

> **سند تاریخی (بازنگری ۲۰۲۶-۱۱-۱۲).** این گزارش مربوط به دور پیشین اصلاحات است. **وضعیت فعلی
> `docs/PROJECT_ROADMAP_AND_EXECUTION_STATE.md` است** و این فایل «تکمیل‌شده» را منعکس نمی‌کند.
> به‌کاررفتن این فایل به‌عنوان وضعیت فعلی ممنوع است.

Nothing is lost: the full text remains in git history
(`git log --diff-filter=D -- PHASE1-10-FINAL-REPORT.md DEPLOY-VPS-UPDATE.md`), and the audit
history it belonged to stays in `docs/audit/FINDINGS.md`.

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
`docs/audit/status.json`, `docs/audit/ROADMAP.md`, `ROADMAP.md`, `PHASE1-10-FINAL-REPORT.md`,
`tests/test_phase1_10_state_alignment.py` (new), `.gitignore`.

**Prerequisites / dependencies:** none.

**Risk / blast radius:** low — documentation and state only; no runtime code, no schema.

**Migration / backup / rollback:** none. Rollback = revert the commit.

**Tests and acceptance criteria**
1. `pytest -q` matches the recorded baseline: 4 known Windows-only failures, no new failures. — **MET** (branch: 781 passed / 4 failed; `main` baseline: 777 passed / 4 failed — the +4 are this phase's new tests)
2. `python -m pyflakes newsbot tools run.py tests` is silent. — **MET**
3. `python tools/final_verification.py` reports 17/17. — **MET**
4. README states Python 3.13+ and the real test count (785 collected). — **MET**
5. `docs/audit/MASTER_STATUS.md` no longer claims the audited commit is `08bc876`. — **MET** (marked historical, points at this file)
6. This file records the true branch-protection rules and the reason for `BLOCKED`. — **MET**

**Deliverables:** this file, the aligned docs, the regression test, commit `d6ae7c2`, PR #56.

**Result:** P1-1 is **COMPLETE** — PR #56 was squash-merged into `main` as `e3e321e`
(2026-11-12, CI green on both `test` and `docker-build`).

---

## 4.2 Phase 2 — scope, decisions and acceptance criteria

**Goal (from §2):** provider-centric model data + backend. Design of record:
`docs/superpowers/specs/2026-11-13-phase2-provider-registry-design.md`.

**Task P2-1 — merge Phase 2's own PRs (rule 10).** Done, oldest-first, each with its own CI and
gate lift/restore: **#51 `16a7353`** (FIN-024/025, PIPE-017/018, PUB-002) · **#52 `886e19d`**
(NET-003 DNS pinning, PIPE-011/014) · **#53 `bf34390`** (PIPE-013 politeness, QA debt).

**Task P2-2 — provider registry.** Merged as **#60 `bf73b39`**. Additive only; no product-behaviour change.

| Item | Decision |
|---|---|
| Table | `ai_providers(id, owner_id, name, kind, base_url, api_key, enabled, last_test_at, last_test_ok, last_test_error, created_at)` |
| Link | `ai_models.provider_id` nullable; `NULL` = legacy row that still uses its own `base_url`/`api_key` |
| Migration | v9 → **v10**, idempotent, additive DDL only; backfill groups models by `(owner_id, base_url)` and keeps the first key |
| Connection test | `GET {base_url}/models` through `net_http.fetch` (SSRF + size cap + 15 s), per-`kind` auth header, result persisted |
| Discovery | parse `data[].id` / `models[].name`; candidates only — inserting a model stays an admin action (manual entry always works) |
| Delete guard | blocked while any model references the provider |
| Surface | super-admin command `/providers` (`add` · `test` · `discover` · `model` · `del`); no user-facing UI |
| Out of scope | round-robin rotation, atomic cursor, per-request audit → **Phase 3** |

**Tests:** `tests/test_phase2_provider_registry.py` (14) — migration on a v9 fixture, idempotent
re-run, first-key/owner grouping, encryption at rest + masking, delete guard, `/models` path and
per-kind headers, Gemini shape, key never echoed in errors, test/discovery persistence, and both
branches of `resolve_endpoint`.

**Acceptance criteria**
1. `pytest -q` has no new failures versus the recorded baseline (§5). — **MET**: 893 passed / 19 failed, and all 19 are the same Windows-only bash-path failures recorded in §5.1 (4 + 15); CI `test` is green.
2. `ruff check newsbot tests tools run.py` clean. — **MET**
3. Migration is idempotent and safe on a copy of a v9 DB. — **MET** (tested on a real v9 fixture, re-run twice)
4. No secret in code, tests, docs, logs or this file. — **MET** (fixtures use dummy keys; `_safe_error` redacts the key from every error string)

---

## 4.3 Phase 3 — scope, decisions and acceptance criteria

**Goal (from §2):** round-robin rotation across the shared model pool, with a cursor that survives
restarts and workers, plus a per-request audit trail. Design of record:
`docs/superpowers/plans/2026-11-14-phase3-round-robin-rotation.md`.

**Task P3-1 — own PRs (rule 10).** **#54** (OBS-001/003/008/010/014, DB-007) rebased onto `main` and
merged as `7f48ec4`. **#55** (repo hygiene + i18n parity) was **closed by GitHub** when #54's merge
deleted its base branch; its content lives on as **#64**, rebased onto `main`, and is blocked on the
owner's licence choice (**B-6**).

**Task P3-2 — the rotation engine.**

| Item | Decision |
|---|---|
| Scope | one pool only: `public` (shared models). The personal model and the pinned test model are explicit choices and are **not** rotated |
| Order | `order_pool` = `(priority or 99, id)` — deterministic, works on dicts and on `sqlite3.Row` |
| Selection | `pick_rotation` index = `cursor % len(pool)`; a pool that shrinks never skips or repeats a model (ADR-3) |
| Cursor | table `ai_rotation(scope, cursor, version, updated_at)`, moved by CAS on `version` (ADR-3) |
| Advance | one per request, inside a single transaction via the new `db.write()` read-modify-write. A separate SELECT+UPDATE loses an advance under contention — the race test proves it |
| Contention | up to 5 CAS retries, then **fail-open**: choose locally, do not advance. Rotation must never stop publishing |
| Failover | unchanged. A provider error does **not** advance the cursor a second time (ADR-4) |
| Audit | one `ai_rotation_log` row per request: `ts, scope, model_id, idx, pool_size, version`, indexed by scope |
| Schema | **v11**: `ai_rotation`, `ai_rotation_log`, `idx_ai_rotation_log_scope`; `SCHEMA_VERSION = 11`, four schema guards bumped 10 → 11 |
| Surface | super-admin command `/rotation`: cursor, version, audit count, last 20 requests |

**Tests:** `tests/test_phase3_rotation_state.py` (10) — cursor/CAS/pure selection;
`tests/test_phase3_rotation_engine.py` (6) — order across requests, a failed provider does not burn
the cursor, the audit row names the chosen model, the probe path does not advance, `/rotation`
read-out and its super-admin gate; `tests/test_phase3_db_connection_isolation.py` (1) — a thread's
cached connection follows a changed `DB_FILE` (verified red before the fix).

**Acceptance criteria**
1. `pytest -q` has no new failures versus the recorded baseline (§5). — **MET**
2. `ruff check newsbot tests tools run.py` clean. — **MET**
3. Rotation never blocks publishing (fail-open path tested). — **MET**
4. No secret in code, tests, docs, logs or this file. — **MET**

---

## 4.4 Phase 4 — scope, decisions and acceptance criteria

**Goal (from §2):** answer a question posted as a reply to a channel post, using a controlled slice of
the post as context, with idempotent delivery. Design of record:
`docs/superpowers/specs/2026-11-16-phase4-reply-qa-design.md`; plan:
`docs/superpowers/plans/2026-11-16-phase4-reply-qa-and-plan-ai-quota.md` (both land in this PR, #82 —
the last Phase 4 PR, because they cite the Phase 4 code and the repo-integrity guard refuses a cited
path that does not exist yet).

**The owner's six binding decisions** (they supersede the earlier plan defaults):

| # | Decision | How the code honours it |
|---|---|---|
| 1 | **fail-closed** group validation | the reply's post must belong to a channel registered in `channels`; the group's `get_chat(...).linked_chat_id` must equal that channel's `chat_id`. An unknown link (API error, no link) ⇒ **silence**. A manual forward of the post into a group *not* linked to that channel ⇒ **silence** |
| 2 | **at-most-once** delivery | `qa_answers` is keyed by `PRIMARY KEY(chat_id, msg_id)`; the claim is an atomic `INSERT OR IGNORE`, so a restart, a network retry or `concurrent_updates` produce exactly one answer. A lost answer is **never** retried |
| 3 | quota charged to the **asker** | the handler calls `ai_chat(..., owner_id=<asker>)`; the channel owner and the public pool are never charged |
| 4 | toggle in the **admin panel** | per-channel `settings["qa_enabled"]` (default on) with the `a:tog:<cid>:qa_enabled` callback that returns to the channel panel; `QA_ENABLED` is only an install-time kill-switch |
| 5 | `qa_answers.channel_id` = **Telegram chat id** | the handler writes `ch["chat_id"]`, never the internal `channels.id` |
| 6 | **plan-level** AI quota | `plans.ai_quota` (0 = unlimited) → one `ai_plan_grants` row per subscription period, `UNIQUE(user_id, expires_at)`; consumed atomically inside `core_users.ai_claim` (so publishing, model tests and Q&A share one gate); shown before purchase (`{ai}` in `plan_details`), in the super-admin plan view, and as total/used/left in the user panel; exhausted ⇒ no new AI request |

**Other design choices recorded here:**
- the post context comes from the reply's **automatic forward** (`sender_chat` of type `channel`) with a
  manual-forward fallback (`forward_from_chat`); no message-id history is stored in the database;
- the post text and the answer are capped (`QA_CONTEXT_MAX_CHARS=4000`, `QA_ANSWER_MAX_CHARS=900`) and
  stripped of HTML before they reach the model or the chat;
- the model is instructed to answer **only** from the post and to say honestly when the answer is not
  there; a media-only post, an empty question and an AI failure each get a fixed honest message and
  never an invented answer;
- **error mapping (corrected at implementation time):** only `ai_limit` means "your quota is finished"
  (`NO_QUOTA`); **every other** AI error (`ai_budget`, `ai_busy`, `ai_none`, anything else) means
  `AI_FAIL`. The first draft of the plan mapped the busy/none errors to the quota message too; that
  would have blamed the user's quota for a provider-side failure, so the code follows the corrected
  rule, the plan records the correction, and `tests/test_phase4_qa_core.py` asserts it;
- the group→channel mapping is cached for `QA_LINK_CACHE_MIN` (360) in `settings` under `qa:linked:<id>`;
- no question text and no answer text is logged — log lines carry ids and lengths only.

**Task P4-1 (#83, `f850d44`)** — schema v12 (`qa_answers`, `ai_plan_grants`, `plans.ai_quota`,
`SCHEMA_VERSION = 13` (bumped in Phase 7), D1 `TABLES`), `newsbot/qa.py` (pure helpers + claim + link validation + handler),
the `ChatType.GROUPS & REPLY & ~COMMAND` registration in `app.py`, the per-channel toggle, and the four
schema-version guards bumped 11 → 12.
**Task P4-2 (#84, `0985e3e`)** — `newsbot/ai_quota.py`, the atomic plan-grant consumption inside `ai_claim`,
`test_model` charged to the plan, `create_plan(ai_quota=…)`, `PLAN_FIELDS`, and the quota lines.
**Task P4-0 (#82)** — design + plan + this close-out record, merged last (see §1).

**Tests:** `tests/test_phase4_schema.py` (7) — v12 tables/columns/PK/unique index, D1 coverage;
`tests/test_phase4_qa_core.py` (31) — detection, capping, prompt content, fail-closed silence for a
foreign/unknown/unregistered group, at-most-once (including two concurrent handlers), no retry after a
failed send, honest fixed messages (media-only post, empty question, AI error, exhausted quota), asker
charging, toggle and kill-switch, log non-leakage, handler registration, panel toggle;
`tests/test_phase4_plan_ai_quota.py` (18) — plan quota, lazy per-period grant, one unit per claim,
exhaustion, concurrent claims, the publishing path sharing the gate, renewal resetting the period,
unlimited plans, model-test charging and blocking, quota text and panel wiring.

**Acceptance criteria**
1. `pytest -q` has no new failures versus the recorded baseline (§5). — **MET** (§5 rows for 2026-11-16)
2. `ruff check newsbot tests tools run.py` clean and `python -m pyflakes newsbot tests` clean. — **MET**
3. A group that is not the channel's linked discussion group gets **no** answer. — **MET** (3 tests)
4. The same update processed twice produces exactly one delivered answer. — **MET** (2 tests)
5. An exhausted plan quota stops AI requests and does not consume another user's quota. — **MET**
6. No secret, question text or answer text in code, tests, docs, logs or this file. — **MET**

---

## 4.5 Phase 4 live acceptance — `DEFERRED TO PHASE 10` (owner, 2026-11-20)

**Why it exists.** The Phase 4 tests use stubs. Four facts can only be shown by the real API:
the bot really receives a reply to a channel post **inside the linked discussion group**
(admin membership / Privacy Mode); the group's `linked_chat_id` really equals the registered
channel's `chat_id`; the automatic forward of a post really carries `sender_chat.type == channel` and
the post id; and one answer is really delivered once while the **asker's own** AI quota drops.

**What is now in the repo (PR #86 → `88675f3`).**

| Piece | Path | What it does |
|---|---|---|
| Harness | `tools/qa_live_acceptance.py` | `--check` preconditions (token shape, numeric admin ids, non-empty `SECRETS_KEY`, writable DB dir, one `getMe`); `--supervise` starts the real bot, snapshots the DB every 10 s, attributes counter deltas to a single question row, evaluates 13 checks, stops early when every required check is `OBSERVED`, writes an atomic JSON report |
| Workflow | `.github/workflows/qa-live.yml` | **manual only** (`workflow_dispatch`), `contents: read`, `concurrency: qa-live`, `timeout-minutes: 360`, inputs `duration_minutes` (default **`340`**, bounded 5–340) / `scenario` (`core`\|`full`\|`duration`) / `db_mode` (`reuse`\|`fresh`); pinned action SHAs; artifacts `report.json` + `bot.log` only |
| Runbook | `docs/QA_LIVE_ACCEPTANCE.md` | Persian, owner-facing: prerequisites, the three secrets, the exact in-Telegram order, exit-code meanings, troubleshooting |
| Log line | `newsbot/qa.py` | `qa: ignored chat=… msg=… user=… reason=…` — INFO for `unregistered_channel`/`toggle_off`/`foreign_group`, DEBUG for `not_a_reply`/`not_a_post`, silent when `message`/`user` is absent. Identifiers only; never the question or post text |

**Runner budget.** A GitHub-hosted job is killed at 360 minutes; the workflow sets
`timeout-minutes: 360` and its `duration_minutes` input defaults to **340** minutes (exactly 10
minutes below the old 350 default; the harness CLI itself still accepts 5–355 and defaults to 350), so a
run always stops itself and
still uploads its evidence. The duration is a ceiling, not a wait: the run exits the moment every
required check is observed.

**Database reuse.** `db_mode=reuse` restores `qa-live-db-v2-*` from the Actions cache so the channel,
provider and model registered once survive into later runs; `db_mode=fresh` starts empty. The cache is
saved with `if: always()` under a per-run key with the `qa-live-db-v2-` prefix (it was
`qa-live-db-v1-` before Phase 10), so the newest run wins.
`qa_answers.answer` is blanked before the job ends, the DB is **never** uploaded as an artifact, and a
stable `SECRETS_KEY` is mandatory — a changing key makes the cached provider secret undecryptable.
The old `qa-live-db-v1-*` caches must be deleted by the owner in the rotation window, and any run
after the rotation must use `db_mode=fresh` (`docs/OPS_SECRET_ROTATION.md` §7).

**Registration wait (post-review fix).** `supervise` resolves the channel and linked group **inside**
the polling loop, retrying until they appear: on a `fresh` DB the owner registers the channel and model
*after* the workflow has started and the bot is up, so a one-shot resolve would have made the first run
impossible. If registration never appears, the run ends with exit `2` and `CHANNEL_NOT_REGISTERED` —
never a false success. Counter deltas seen in a window with no `qa_answers` event are carried into the
next window and attached only to a row of the **same user** (the `qa_claim` → `ai_claim` → `qa_finish`
sequence can straddle a 10 s boundary); two distinct row keys in one window remain `ERROR`, never a
guess.

**Exit codes:** `0` = every required check of the chosen scenario observed · `1` = the duration elapsed
first · `2` = a precondition failed (token / admin id / `SECRETS_KEY` / DB path / `getMe` / channel /
linked group / super-asker).

**What the owner must do (and nothing else).** 1) Add `BOT_TOKEN` (the **test** bot, never the
production token), `SUPER_ADMIN_IDS` (numeric owner id) and `SECRETS_KEY` (64 random characters, kept
stable) as repository secrets — §8 **B-7**. 2) Create a test channel with its linked discussion group,
make the bot admin in **both**, register a **public** AI model, and ask from a **second, non-super**
Telegram account. The super admin has no daily AI cap and never receives an `ai_plan_grants` row, so
"charged to the asker" and "quota exhausted" are unobservable from that account.

**Key/authorization diagnosis (2026-11-19, PR #88 → `6d5b4ae`).** The owner reported that a model which works outside
the bot fails inside it. The bot's two 🔴 super-admin notifications were provider *rejections* —
NVIDIA `HTTP 403: Authorization failed` and vyceai `HTTP 400: The model 'agnes-3.0-flash' does not
exist` — so the key write/read path was audited and three real defects were closed:

| Defect | How it made a healthy key look broken | Fix |
|---|---|---|
| `/providers add <name> <base_url> <key…>` stored `" ".join(args[3:])` silently | a key pasted with a space/newline, or one carrying a zero-width/LRM/BOM character, was sent malformed | one `secret_store.clean_api_key` (strip `Cf`, trim edges, **reject** inner whitespace/`Cc`/`Cs`, log only a count) used by `create_provider`, `update_provider`, `add_model`, `update_model` and the panel wizard (`ui_inputs._input_value`) |
| `decrypt` returns `""` when the current `SECRETS_KEY` cannot open the stored key | the bot sent `Bearer ` and the provider's `403` was attributed to the model | `ai_models._stored_key` fails closed with an explicit `decrypt failed` error before any network call |
| the provider panel showed only a masked key | a truncated key was indistinguishable from an invalid one | `providers._public` exposes `key_len`/`has_key`; `_pline` prints `key=<length>` or `key=UNREADABLE` |

**Owner runbook for the two 🔴 rows.** `/providers` now prints each stored key's length.
Length ≠ the working key ⇒ re-enter the key on one line. Length correct and `/providers test <id>`
still `403` ⇒ the provider rejects that key/account (re-issue or use another provider). `400 … does
not exist` ⇒ the model id is wrong; pick one from `/providers discover <id>`. Disable or delete rows
that stay 🔴 — `AI_MAX_MODEL_ATTEMPTS=2` lets two dead rows starve a healthy third.

**Status:** `DEFERRED TO PHASE 10` (owner decision, 2026-11-20). The harness, the runbook and the three
secrets are ready; the **earlier Phase 4 attempt** (`44506386031`, on `74abc16`) ended with exit `2` /
`CHANNEL_NOT_REGISTERED` after the full budget because the channel was never registered in the runner's
bot. Phase 10 runs this live acceptance **once**, together with the release checks (**D-7**), and records
the green report artifact in §5 before the release is declared.
**The Phase 10 attempt happened and failed on a prerequisite:** run `46013627630` (2026-11-27, on
`967afde`) ended with exit `2` / `NO_LINKED_GROUP` after its full budget — the registered channel has
no linked discussion group, so no live check was observable (only `preconditions` and
`public_model_active`; the remaining checks were `NOT_OBSERVED`, plus `no_text_no_ai` =
`NOT_APPLICABLE`). No code change is pending; a further run is needed once the channel has a linked
discussion group — see §4.9.4 and the D-7 row in §8.

---

## 4.6 Phase 7 — scope, decisions and acceptance criteria

- **Source of truth:** the roadmap table above plus `PHASE0_AUDIT_REPORT.md` (outside this repo,
  section «فاز ۷» and AUD-006). The owner confirmed on 2026-11-23 that the roadmap row — not the
  stale §1 label that said "authorization boundaries" — governs this phase.
- **Binding architecture:** maximum reuse of `admin_limits` / `assign_plan` / `free_claim`
  (no parallel quota or role logic); `ch_add` security guards untouched; ADD-only schema with a
  rollback that reverts code while leaving the unused column.
- **Binding decisions:** `SCHEMA_VERSION` 12 → 13 (a new column must stop an older build from
  opening a newer DB); the four PH-05 home buttons are replaced by their slash commands; a stale
  `onb:start` after completion goes home; admins/super admins bypass onboarding.
- **Acceptance:** a fresh user reaches add-channel without a plan button; the free grant happens
  once; a paid user keeps their plan; a fresh referred user still registers correctly; plain PM
  and Reply route correctly; all existing commands remain registered; schema v13 is asserted by
  the four guards and by the D1 introspection suite.
- **Evidence discipline:** 17 phase-7 tests ×5 green; 21 phase-7 tests total; full Windows suite
  back to the known 19 failures only. Live acceptance remains owned by Phase 10 (D-7).

---

## 4.7 Phase 8 — deeplink and "back to channel"

- **Source of truth:** the roadmap table above plus the external `PHASE0_AUDIT_REPORT.md` (section
  «فاز ۸» and AUD-008). The design and the four-task plan are recorded in §7.
- **Binding architecture:** ADD-only payload key inside the existing deeplink JSON; one shared URL
  rule (`post_url`) for the journal link and the button; a native URL button with no extra API call
  and no schema change.
- **Binding decisions (D8-1…D8-8):** ids and the observed handle are recorded (D8-1); `msg_link`
  delegates to `post_url` (D8-2); notes are keyed on what is known — public / private / unknown
  (D8-3); labels live in `TXT` (D8-4); `SCHEMA_VERSION` stays 13 (D8-5); a successful publication
  repairs a changed handle (D8-6); `markup_last_only=True` gives a split body one control (D8-7);
  the rich path forwards `reply_markup` as the existing notification path does, with the real-client
  check in Phase 10 (D8-8).
- **Acceptance:** an old deeplink is unchanged; a new one has one button to the same post; private
  and public channels are both handled; a deleted channel degrades honestly; a deleted post yields
  Telegram's own "not found"; no id/URL enters the logs; `ruff`/`mypy` clean.
- **Evidence discipline:** 14 phase-8 tests ×5 green; full Windows suite back to the known 19
  failures only. Live confirmation of the button in a real client remains owned by Phase 10 (D-7).

---

## 4.8 Phase 9 — manual harness, scope and open items

- **Source of truth:** the roadmap table above, the external `PHASE0_AUDIT_REPORT.md` (AUD-011,
  AUD-013, section «فاز ۹») and the approved design/plan recorded in §7.
- **Binding architecture:** one workflow only — `.github/workflows/qa-live.yml`; no schema, no bot
  product-code change, no VPS/production DB/provider key, no live dispatch in this phase.
- **Binding decisions D9-1…D9-10:** one workflow; 340 active minutes with the 10-minute difference
  from 350 stated as a number; secret-backed inputs with forward-compatible aliases; id lists
  accepted; `cleanup_duration_s` ends at the report write; long ids join the leak check; `duration`
  holds to budget while `core`/`full` exit early; the Provider row is flag-driven.
- **Acceptance:** manual-only trigger, minimal permissions, SHA pins, `timeout-minutes: 360`,
  default `"340"`, five-surface boundary table, no secret value in logs/report/artifact names,
  bounded readiness, clean `finally` shutdown, additive timing fields.
- **Open items owned by Phase 10 / D-7 (non-blocking for this merge):**
  1. A `duration` run with no observed Q&A traffic exits 1 at the budget by design; the runbook now
     states this. Decide before consuming the artifact whether burn-in success should be code 0.
  2. The workflow's fail-closed validation was checked by a real dummy-value shell run and source
     guards, not by an executable boundary test; add one before treating the workflow as a gate.
  3. `PROVIDER_CREDENTIALS_PRESENT` is preserved by `KNOWN_ENV` but has no dispatch input; a
     credentialed D-7 dispatch needs a small workflow edit to report `provider_ai: real`.
  4. `startup_duration_s` starts after process spawn and excludes pip-install time; the runbook
     margin is ~10 minutes, not 20.
  5. A real secret leak would still appear in the uploaded `bot.log` (pre-existing); scrub it before
     publishing the artifact externally.
  6. Negative super ids are rejected by the validation regex (fail-closed); Telegram user ids are
     positive, so this is acceptable.

## 4.9 Phase 10 — hardening, cleanup and release gate

**Status: `IN_PROGRESS` (2026-11-27).** Every code and documentation PR of the phase is merged and
every automated gate is green. The phase is **not** declared `COMPLETED` because two gates are the
owner's and have no evidence yet: the real secret rotation and a successful live D-7 run.

### 4.9.1 What was merged

| PR | Title | Merge SHA | CI run | Protection |
|---|---|---|---|---|
| **#108** | `fix(ci): upgrade libzstd1 for CVE-2026-124851` | `361a10c` | 46047560145 | 15/15 IDENTICAL |
| **#107** | `docs(phase10): approved design and implementation plan` | `9886972` | 46061459633 | 15/15 IDENTICAL |
| **#109** | `feat(phase10): harden windows tests and secrets rotation` | `b660bc3` | 46075556814 | 15/15 IDENTICAL |
| **#110** | `chore(phase10): remove 11 provably dead helpers` | `ef185ca` | 46082906798 | 15/15 IDENTICAL |
| **PR3 (#111)** | `docs(phase10): record hardening, rotation and release gate` | `e0698ef` | 46104240680 · main: 46105260191 | 15/15 IDENTICAL |
| **PR3 follow-up (#112)** | `docs(phase10): point the state file at the current deploy run` | `1d7916a` | 46108126154 · main: 46108539227 | 15/15 IDENTICAL |

CI on `main` after the last merge: **46108539227 — SUCCESS** (at `1d7916a`). The **latest**
`Deploy to VPS` run on `main` is **pending** — waiting for the manual approval of the `production`
environment. **Rule for the owner:** every merge to `main` queues a **new** deploy run and cancels
the previous pending one, so always approve the **newest** run on `main` (46108808054 for
`main` @ `1d7916a` at the time of writing; 46105550745 and 46083654457 were cancelled this way).
**No deployment has happened and none is claimed.**

### 4.9.2 Workstreams

- **W1 — Windows test harness (D-2, AUD-012).** `tests/_shell.py` is the shared finder for Git
  Bash (it rejects `system32` and WSL). In the sandbox only `MSYS2_ARG_CONV_EXCL=/app` is set;
  `MSYS_NO_PATHCONV=1` is deliberately **not** set (it breaks the stub). The stub writes LF-only
  output and the container paths are normalized to POSIX. `deploy/deploy.sh` is **untouched**.
  Result: the 19 Windows-only failures are gone — `tests/test_deploy_script.py` +
  `tests/test_session3_cicd_docker_deploy_docs.py` ⇒ **97 passed / 0 failed**, full suite
  **1208 passed / 0 failed**.
- **W2 — secret rotation (AUD-001, partially closed).** `fernet_for(key)` is pure in
  `newsbot/secret_store.py`; `migrate_ai_keys()` now covers both `ai_providers` and `ai_models`;
  `tools/rotate_secrets_key.py` reads the keys **only from the environment**, supports
  `--check` / `--apply --backup`, exits 0 / 2 / 3, writes inside `BEGIN IMMEDIATE`, is idempotent
  and prints counts only. 18 rotation tests are green. **The rotation itself has not run** — see
  §4.9.4.
- **W3 — hygiene (AUD-014).** 36 merged branches were candidates; **33 were deleted** (a branch is
  deleted only when its PR is `MERGED` **and** the remote tip SHA equals that PR's head SHA),
  **3 were kept** by that guard, 16 branches remain in total and the stale remote-tracking refs were
  pruned by `git fetch --prune` (51 tracking refs remained after pass 1, 19 after pass 2). The
  dead-code pass removed **11 module-level functions** with no reference anywhere in the repository
  or outside it: the bounded scan went from 857 module-level symbols and 11 zero-reference
  candidates to 846 symbols and **0 candidates**. The scan also ruled out dynamic dispatch (no
  `getattr`/`globals`/`importlib`/`eval` on those names, no star-imports).
- **W4 — release gate.** `docs/RELEASE_RUNBOOK.md` (CI gates with real run URLs, local
  `pip-audit`, full suite, verifier 17/17, `run.py --check`, protection 15/15, owner gates, release
  steps referencing `docs/DEPLOY_RUNBOOK.md`, post-release checks, rollback, stop criteria) and
  `docs/OPS_SECRET_ROTATION.md` (the 10-step rotation with its own rollback). `trivy` and
  `hadolint` are **CI-only** here because Docker is not installed locally — the claim "scanned" is
  only valid with the run URL.

### 4.9.3 Evidence of the automated gates

| Gate | Command | Result |
|---|---|---|
| Full suite (Windows) | `pytest -q` | **1208 passed / 0 failed** (703.53 s in PR #109, 804.78 s at its final HEAD, 806.84 s in PR #110) |
| Collection | `pytest --collect-only -q` | **1208 tests collected** — matches the README badge |
| Independent verifier | `python tools/final_verification.py` | **17/17** (exit 0) |
| Config self-check | `run.py --check` (dummy env) | exit 0 |
| Lint / types / undefined names | `ruff check` · `mypy newsbot` · `pyflakes` | clean · 52 files clean · exit 0 |
| Dependency / secret / image scans | `pip-audit` · `gitleaks` · `trivy fs` · `trivy image` · `hadolint` | SUCCESS in run 46108539227 |
| Branch protection | backup comparison, 15 fields | **IDENTICAL** after #107, #108, #109, #110 |
| Dead-code scan | bounded AST + text scan | 857 → 846 symbols · 11 → 0 candidates |

### 4.9.4 The two open owner gates

1. **D-7 — the single live run.** Run **46013627630** (head SHA `967afde`, the Phase 9 code — **before**
   PR #109/#110; 2026-11-27 14:11:14Z → 19:51:47Z) ended
   `exit_code=2` with `problems=["NO_LINKED_GROUP"]`. The scenario was `duration`
   (`scenario_mode=hold_to_budget`) on a fresh database (`db_reused=false`), `active_duration_s=20408`
   against a `configured_duration_s=20400`, `bot_log_lines=647`. Only `preconditions` and
   `public_model_active` were `OBSERVED`; the ten remaining checks were `NOT_OBSERVED` and the optional
   `no_text_no_ai` stage was `NOT_APPLICABLE` (13 checks in total). The boundary
   table reports `telegram_bot: real`, `temporary_db: real`, `provider_ai: simulated`
   (`provider_credentials_present=false`), `real_channel_publication: not_performed`. **This is a
   prerequisite defect, not a code defect:** the registered channel had no linked discussion group.
   The next run needs the linked group, `duration_minutes=340`, `scenario=duration` and
   `db_mode=fresh`.
2. **The actual secret rotation.** Owner-only: BotFather `/revoke`, the `SECRETS_KEY` rotation with
   `tools/rotate_secrets_key.py`, the env update, the restart, the provider test, the purge of the
   `qa-live-db-v1-*` caches and finally the deletion of `newsbot-deploy-pkg\newsbot.env`. The file
   stays untouched until the rotation succeeds (decision **C8**). Provider keys are **not**
   reissued — residual risk **D-13**.

### 4.9.5 What Phase 10 did **not** do

- No rotation was executed, no VPS was deployed, no live dispatch succeeded.
- `sources.api_key` is still plaintext (**D-12**) and the provider keys were not reissued (**D-13**).
- The 11 Dependabot PRs (#68–#78) stay open by the owner's decision (**D-4**).
- **F-1** (HTTP 504 / slow provider probes) was observed and recorded, not fixed — a behaviour change
  is outside this phase's scope.
- No force-push, no tag or release deletion, no DB or provider-key change on the VPS.

---

## 4.1 Disposition of the existing PR chain (#46–#56) — binding

> **Owner rule (2026-11-12):** every existing pull request gets its disposition **inside the phase
> that owns it**, and **the PR of a phase must be merged when that phase completes**. A phase is not
> `COMPLETED` while one of its own PRs is still open.

### Why the chain has to be unstacked

The ten chain PRs are **stacked**: each one's base branch is the previous PR's head branch
(#47→#46, #48→#47, … #55→#54). Only #46 and #56 target `main` directly. GitHub's UI therefore
cannot merge #50 without first merging #46, #47, #48 and #49 — and each of those PRs is authored by
the repository's only account, which GitHub forbids from approving its own PR.

Measured on 2026-11-13 (all ten heads tried against the new `main`):

| Fact | Value |
|---|---|
| Heads that merge into `main` cleanly | **0 of 10** |
| Conflicting files | `README.md`, `docs/audit/MASTER_STATUS.md`, `docs/audit/status.json` (+ `PHASE1-10-FINAL-REPORT.md` for #47–#55) |
| Code files in conflict | **0** — every conflict is in a document Phase 1 already reconciled |
| Chain size | 91 files, +6106 / −678 |
| New product requirements introduced by the chain | **none** (verified by grep: no `ai_providers`, no `rotation`, no `send_chat_action`, no deeplink feature) |

**The rule that follows:** the chain is merged **oldest-first, one PR per merge, each rebased onto
the then-current `main`**. A PR is never merged before its predecessor. Rebasing resolves the doc
conflicts in favour of the canonical state file (`docs/PROJECT_ROADMAP_AND_EXECUTION_STATE.md`) and
the historical banners added in P1-1.

### The table

| PR | Title | Owning phase | Disposition | Merge order |
|---|---|---|---|---|
| **#56** | Phase 1 — state-of-record file, docs truth alignment | **Phase 1** | **MERGED** as `e3e321e` (2026-11-12). Canonical. | done |
| **#57** | Phase 1 — record PR-chain disposition + merge rule | **Phase 1** | **MERGED** as `533f753` (2026-11-13). | done |
| **#46** | CI/CD gates, immutable Docker deploy, hardened VPS access, docs (60 files) | **Phase 1** | **MERGED** as `123bafe` (2026-11-13). Rebased; the 3 doc conflicts were resolved as a *union* (main's reconciled facts + the branch's dropped Session-3 section/badges); also fixed a `W292` the branch's own `pyproject.toml` introduced and the `soupsieve` advisory. | 1 |
| **#47** | docs: remove stale deploy guide and phase 1-10 report | **Phase 1** | **MERGED** as `685aecd` (2026-11-13). Cherry-picked onto `main` (the banner conflict was a delete-vs-banner, resolved by keeping the deletion) and the retired banner is preserved in §3.1. | 2 |
| **#48** | fix(ops): READY=1 no longer depends on Telegram reachability | **Phase 1** | **MERGED** as `29416af` (2026-11-13). Clean cherry-pick; the new tests moved the README count 850 → 852. | 3 |
| **#49** | feat(ops): safe SQLite restore tool + recovery runbook | **Phase 1** | **MERGED** as `d386467` (2026-11-13). Clean cherry-pick; README count 852 → 858. | 4 |
| **#50** | docs(audit): partial refresh of the findings register | **Phase 1** | **MERGED** as `b2b200d` (2026-11-13). Conflicts resolved as a union: main's reconciled counters were kept and the branch's `refresh_2026_11_11` block + per-finding evidence restored. | 5 |
| **#51** | feat(phase2): money, publishing & quota integrity (FIN-024/025, PIPE-017/018, PUB-002) | **Phase 2** | **MERGE** — rebase onto `main` after #50; 12 new tests, no schema change. Re-verify against the Phase 2 provider model. | 6 |
| **#52** | fix(phase2): NET-003 DNS pinning, PIPE-011/014 extraction & URL canonicalization | **Phase 2** | **MERGE** — rebase onto `main` after #51; 17 new tests. It adds a new pinned-transport module under `newsbot/` — that module must be reviewed before merge (it is not yet in `main`). | 7 |
| **#53** | fix(phase2): PIPE-013 discovery politeness & TEST-007/008/009/013/014 QA debt | **Phase 2** | **MERGE** — rebase onto `main` after #52; 11 new tests. | 8 |
| **#54** | fix(phase3): OBS-001/003/008/010/014 observability & DB-007 orphan protection | **Phase 3** | **MERGE** — rebase onto `main` after #53; 6 new tests. Touches `newsbot/db.py`: confirm the orphan-protection change is additive and does not collide with the Phase 2 `ai_providers` migration. | 9 |
| **#55** | fix(phase3): OPS-017/019/023/029 repo hygiene & TEST-010 semantic i18n parity | **Phase 3** | **MERGE via #64** — #55 itself was closed by GitHub (its base branch was deleted by #54's merge), so its content was rebased onto `main` and re-opened as **#64**; adds `LICENSE`, `CHANGELOG.md`, `CODEOWNERS`, `dependabot.yml` — the owner must confirm the licence choice before that merge (see B-6). | 10 |

**Count:** 11 PRs — all 11 **merged** (Phase 1: #46, #47, #48, #49, #50, #56, #57). The remaining
#51–#55 are Phase 2 / Phase 3 and stay open until their own phase runs.

### Merge mechanics that must be followed

1. **Squash only.** `required_linear_history` is on, so a merge commit is rejected. Use
   `gh pr merge --squash`.
2. **One PR per merge.** Never merge the chain as one squash — each PR's CI must gate its own merge.
3. **Rebase onto the current `main` first.** The chain is stacked, so the PR's base branch must be
   retargeted to `main` and the head rebased, otherwise the PR would carry its predecessors' commits.
4. **Review gate.** `main` requires 1 approving review and the repository has exactly one account,
   which GitHub forbids from approving its own PR. **The sanctioned workaround** is recorded in §7.1:
   temporarily remove the review requirement, merge, then restore it **exactly** (verified field by
   field against the saved backup). Never leave the gate off.
5. **Conflict rule.** Doc conflicts resolve in favour of the canonical state file and the P1-1
   historical banners. Code conflicts must be resolved by hand and re-tested — never with
   `--ours`/`--theirs` by default.

### Deferred to Phase 10 (not part of the chain)

`D-1`: 18 unmerged stale branches and 37 remote branches in total stay as they are until Phase 10
deletes them. The chain PRs above are the only branches merged before then.

---

## 5. Test evidence (real runs only — never claim an unrun test)

| Date (UTC) | Ref | Command | Result |
|---|---|---|---|
| 2026-11-12 | `main` @ `d0efb7e` | `pytest -q` (isolated clean clone) | 4 failed, 777 passed in 387.59s — the 4 are Windows-only Git-Bash path artifacts (`/app` → `C:\Program Files\Git\app`) in `tests/test_deploy_script.py`; Linux CI is green |
| 2026-11-12 | `main` @ `d0efb7e` | `pytest -q --collect-only` | 781 tests collected |
| 2026-11-12 | branch (with the new guard) | `pytest -q` | 4 failed, **781 passed** in 514.85s — same 4 Windows-only failures; the +4 new tests pass |
| 2026-11-12 | branch (with the new guard) | `pytest -q --collect-only` | 785 tests collected |
| 2026-11-12 | branch | `python -m pyflakes newsbot tools run.py tests` | clean (exit 0) |
| 2026-11-12 | branch | `python tools/final_verification.py` | 17/17 |
| 2026-11-12 | branch (PR #56) | GitHub CI `test` / `docker-build` | both SUCCESS |
| 2026-11-13 | `main` @ `e3e321e` | `gh pr merge 56 --squash` | **MERGED** as `e3e321e` (review gate lifted for the merge, then restored — see §7.1) |
| 2026-11-13 | `main` @ `e3e321e` | protection JSON compared field by field against the pre-merge backup | **all 15 fields identical** — reviews back to 1, status checks `test`/strict, linear history on, force pushes off, deletions off |
| 2026-11-13 | all 10 chain heads vs `main` | `git merge --no-commit --no-ff` per head | **0 of 10** merge cleanly; every conflict is in `README.md`, `docs/audit/MASTER_STATUS.md`, `docs/audit/status.json` (+ `PHASE1-10-FINAL-REPORT.md`); **no code file conflicts** |
| 2026-11-13 | chain patches #46–#55 | grep for new product requirements | none (`ai_providers`, `rotation`, `send_chat_action`, deeplink feature: no hits) |
| 2026-11-13 | branch for #46 (`706cd4d`) | `pytest tests/test_phase1_10_state_alignment.py tests/test_repo_integrity.py` | 9 passed |
| 2026-11-13 | branch for #46 (`010912f`) | `pip-audit -r requirements.txt --no-deps` | `No known vulnerabilities found` after the `soupsieve` 3.0.3 → 4.0.0 bump |
| 2026-11-13 | branch for #46 (`706cd4d`) | GitHub CI `test` on the PR | **SUCCESS** (ruff W292, the `pip-audit` advisory and 2 doc-consistency guards all fixed and re-run) |
| 2026-11-13 | branch for #47 (`fbd30e3`) | `pytest` the 3 doc/integrity files | 16 passed · `ruff` clean · CI `test` **SUCCESS** |
| 2026-11-13 | branch for #48 (`a86f209`) | `pytest tests/test_ops014_ready_independent.py` | 2 passed · CI `test` **SUCCESS** (852 collected) |
| 2026-11-13 | branch for #49 (`eab7ee6`) | `pytest tests/test_ops011_restore_tool.py tests/test_phase1_10_state_alignment.py` | 11 passed · CI `test` **SUCCESS** (858 collected) |
| 2026-11-13 | branch for #50 (`d07c15e`) | `pytest` the 2 doc/integrity files | 9 passed · `ruff` clean · `tools/final_verification.py` 17/17 · CI `test` **SUCCESS** |
| 2026-11-13 | `main` @ `b2b200d` | full `pytest -q` (local, Windows, twice) | **839 passed, 19 failed in 882.95s** / **751.35s** — the 19 are Windows-only bash path artifacts, see §5.1 |
| 2026-11-13 | `main` @ `b2b200d` | GitHub CI `test` | **SUCCESS** |
| 2026-11-13 | each of #46–#50 | protection JSON compared field by field after every merge | **all 15 fields identical** every time; gate lifted only for the duration of one merge |
| 2026-11-13 | `main` @ `0f4b90a` (PR #58) | `gh pr merge 58 --squash` → **MERGED**; protection compared field by field | merge SHA `0f4b90a960c92f1f0bf69955aa29fdbae066e06e` · **all 15 fields identical to backup** · CI `test: SUCCESS` |
| 2026-11-13 | Phase 2 branches | `pytest` the new/affected files after each rebase | #51 12 passed · #52 17 passed · #53 19 passed · provider work 14 passed |
| 2026-11-13 | `phase2/provider-registry` | `ruff check newsbot tests tools run.py` | All checks passed |
| 2026-11-13 | `phase2/provider-registry` | `python -m pyflakes` the touched modules | clean |
| 2026-11-13 | `phase2/provider-registry` | `python tools/final_verification.py` | 17/17 |
| 2026-11-13 | `phase2/provider-registry` | full `pytest -q` (local, Windows) | **893 passed, 19 failed in 714.34s** — the 19 are exactly the pre-existing Windows-only bash-path failures of §5.1 (4 + 15); **no new failure** |
| 2026-11-13 | `phase2/provider-registry` | GitHub CI `test` | **SUCCESS** |
| 2026-11-14 | `phase3/rotation` | `pytest` the 5 Phase 3 files | 10 · 6 · 2 · 2 passed (20 new tests) |
| 2026-11-14 | `phase3/rotation` (commit `20ac9b2`) | full `pytest -q` (local, Windows) | **911 passed, 19 failed in 615.91s** — the 19 are exactly the §5.1 Windows-only failures; **no new failure** |
| 2026-11-14 | `phase3/rotation` | `ruff check newsbot tests tools run.py` · `python -m pyflakes …` | All checks passed · clean |
| 2026-11-14 | `phase3/rotation` | `python tools/final_verification.py` | 17/17 |
| 2026-11-14 | `phase3/rotation` | GitHub CI `test` on PR #62 (after the rebase onto `7f48ec4`) | **SUCCESS** (937 collected) |
| 2026-11-14 | `phase3/observability-and-db` | `pytest tests/test_phase3_observability_and_db.py tests/test_ph06_observability.py` | 22 passed · `ruff` clean |
| 2026-11-14 | `phase3/observability-and-db` | full `pytest -q` (local, Windows) | **911 passed, 19 failed in 615.91s** — same §5.1 baseline |
| 2026-11-14 | `phase3/observability-and-db` | GitHub CI `test` on PR #54 | first run **FAILED** on `test_readme_test_count_matches_collection` (917 vs 912); README fixed → **SUCCESS** |
| 2026-11-14 | `main` @ `51bb8d2` | `pytest` the 5 Phase 3 files + the 2 state/integrity guards | 31 passed · 937 collected |
| 2026-11-14 | `phase3/rotation-review-fixes` | self-review of `2f3d969..9f0ffb9` against 5 defect classes (concurrency, exception safety, event-loop blocking, secrets, failover interaction) | **3 real defects found**: CAS `rowcount` ignored (lost advance reported as success), broad `except Exception` hiding bugs, unguarded `advance()` able to stop publishing — plus an audit row written before the busy check |
| 2026-11-14 | `phase3/rotation-review-fixes` | 5 new tests (one per defect, red before the fix) + full Phase 3 set | 21 passed on the branch · 30 with the state/integrity guards · `ruff`/`pyflakes` clean · CI `test` **SUCCESS** |
| 2026-11-14 | `main` @ `3f78e51` | `pytest` the 5 Phase 3 files + the 2 guards | 39 passed · 942 collected |
| 2026-11-14 | `phase3/repo-hygiene-and-i18n` (rebased) | `pytest` the hygiene/i18n + integrity + state guards | 13 passed · README count 942 → 947 (the guard caught it before CI) |
| 2026-11-14 | `main` @ `12cb8db` (final Phase 3 `main`) | full `pytest -q` (local, Windows) | **928 passed, 19 failed in 566.70s** — the 19 are exactly the §5.1 Windows-only failures; **no new failure** |
| 2026-11-14 | `main` @ `12cb8db` | independent 15-field protection check against both backups | **IDENTICAL on all 15 fields** |
| 2026-11-16 | `phase4/qa-core` | `pytest tests/test_phase4_schema.py tests/test_phase4_qa_core.py` | **38 passed** (red first: 7 and 17 failures before the code) · `pytest --collect-only -q` 985, README badge and count aligned |
| 2026-11-16 | `phase4/qa-core` | GitHub CI `test` on PR #83 | **SUCCESS** |
| 2026-11-16 | `main` @ `f850d44` (PR #83) | `gh pr merge 83 --squash` → **MERGED**; protection compared field by field | merge SHA `f850d44830b49d0fed8a53c78cf633714012d815` · **all 15 fields identical to both backups** · CI `test: SUCCESS` |
| 2026-11-16 | `phase4/quota` (rebased onto `f850d44`) | `pytest tests/test_phase4_plan_ai_quota.py tests/test_phase4_qa_core.py tests/test_phase4_schema.py -q` | **56 passed** (18 new quota tests, red first) |
| 2026-11-16 | `phase4/quota` | `pytest --collect-only -q` | **1003 collected** (README badge and count line updated to match) |
| 2026-11-16 | `phase4/quota` | full `pytest -q` (local, Windows) | **984 passed, 19 failed in 582.87s** — the 19 are exactly the §5.1 Windows-only bash-path failures (4 + 15); **no new failure** |
| 2026-11-16 | `phase4/quota` | `ruff check newsbot tests tools run.py` · `python -m pyflakes newsbot tests` · `python tools/final_verification.py` | All checks passed · clean · **17/17** |
| 2026-11-16 | `phase4/quota` (first full run, before the §5/§7 edits) | full `pytest -q` | 21 failed, 982 passed — the 2 extra failures were **this file's own two guards**: the "Current phase" field still said Phase 4 while the table said `COMPLETED` (fixed → Phase 4 `IN_PROGRESS`), and the file cited the two Phase 4 documents that do not exist on this branch (paths moved to #82's close-out). Both fixed and re-run green (9 passed) |
| 2026-11-16 | `phase4/quota` | GitHub CI `test` on PR #84 | **SUCCESS** (2m42s) |
| 2026-11-16 | `main` @ `0985e3e` (PR #84) | `gh pr merge 84 --squash` → **MERGED**; protection compared field by field | merge SHA `0985e3ebd4839df3110896c085bd4f8cf652d7ce` (2026-11-16T20:26:47Z) · **all 15 fields identical to both backups** · CI `test: SUCCESS` |
| 2026-11-16 | **docs/phase4-design** (rebased onto `0985e3e`) | `pytest tests/test_repo_integrity.py tests/test_phase1_10_state_alignment.py` | **9 passed** — the repo-integrity guard is green now that #84's code exists, so the design/plan may cite the Phase 4 files |
| 2026-11-16 | **docs/phase4-design** (rebased onto `0985e3e`) | `pytest --collect-only -q` | **1003 collected** |
| 2026-11-16 | **docs/phase4-design** (first full run, before the §5/§7 edits) | full `pytest -q` | 20 failed, 983 passed — one extra failure: the repo-integrity guard read the **branch name** **docs/phase4-design** (written in backticks) as a repository path. Fixed by writing the branch name in bold; the guard and the state-alignment test then passed (9 passed) |
| 2026-11-16 | **docs/phase4-design** (rebased onto `0985e3e`, final tree) | full `pytest -q` (local, Windows) | **984 passed, 19 failed in 569.39s** — the 19 are exactly the §5.1 Windows-only bash-path failures (4 + 15); **no new failure** |
| 2026-11-16 | `main` @ `f4afa0e` (PR #82) | GitHub CI `test` on PR #82 | **SUCCESS** (2m57s) |
| 2026-11-16 | `main` @ `f4afa0e` (PR #82) | `gh pr merge 82 --squash` → **MERGED**; protection compared field by field | merge SHA `f4afa0ebcbc037446402fb3c51c1f04eeebf6eb2` (2026-11-16T21:04:58Z) · **all 15 fields identical to both backups** · CI `test: SUCCESS` |
| 2026-11-16 | `main` @ `f4afa0e` (final Phase 4 `main`) | full `pytest -q` (local, Windows) | **984 passed, 19 failed in 605.30s** — the 19 are exactly the §5.1 Windows-only bash-path failures (4 + 15); **no new failure** |
| 2026-11-16 | `main` @ `f4afa0e` (final Phase 4 `main`) | `pytest --collect-only -q` · `ruff check newsbot tests tools run.py` · `python -m pyflakes newsbot tests` · `python tools/final_verification.py` | **1003 collected** · All checks passed · clean · **17/17** |
| 2026-11-18 | **phase4/live-acceptance** | `pytest tests/test_phase4_qa_core.py -q` (after the ignore-log change) | **37 passed** (6 new tests; red first: 5 failed) |
| 2026-11-18 | **phase4/live-acceptance** | `pytest tests/test_phase4_live_acceptance.py -q` (built task by task) | **39 passed** — 9 env/`--check`, 15 attribution/checks (red first: 15 failed), 8 supervisor/report (red first: 8 failed), 6 workflow guards (red first: 6 failed), 1 runbook (red first: `FileNotFoundError`) |
| 2026-11-18 | **phase4/live-acceptance** (post-review fixes) | `pytest tests/test_phase4_live_acceptance.py -q` | **45 passed** — 6 new tests for the review findings (red first: 5 failed, 1 invariant passed) |
| 2026-11-18 | **phase4/live-acceptance** (post-review fixes) | `pytest tests/test_phase4_live_acceptance.py tests/test_repo_integrity.py tests/test_phase1_10_state_alignment.py -q` | **54 passed** |
| 2026-11-18 | **phase4/live-acceptance** | `tools/qa_live_acceptance.py --check` with a **dummy** token | `problems: [GETME_FAILED]`, exit **2**, token absent from the output (`token_len` printed only) |
| 2026-11-18 | **phase4/live-acceptance** | `run.py --check` with dummy env vars | **exit 0** — «خودآزمایی موفق»; no secret printed |
| 2026-11-18 | **phase4/live-acceptance** (post-review fixes) | full `pytest -q` (local, Windows) | **1035 passed, 19 failed in 639.44s** — the 19 are exactly the §5.1 Windows-only bash-path failures (4 + 15); **no new failure** |
| 2026-11-18 | **phase4/live-acceptance** (post-review fixes) | `pytest --collect-only -q` | **1054 collected** (README badge and count line updated to match) |
| 2026-11-18 | **phase4/live-acceptance** | `ruff check newsbot tests tools run.py` · `python -m pyflakes newsbot tests tools run.py` · `python -m mypy newsbot` · `python tools/final_verification.py` | All checks passed · clean · **Success: no issues found in 50 source files** · **17/17** |
| 2026-11-18 | **phase4/live-acceptance** (final tree) | GitHub CI `test` on PR #86 | **SUCCESS** (3m45s) |
| 2026-11-18 | `main` @ `88675f3` (PR #86) | `gh pr merge 86 --squash --delete-branch`; protection compared field by field | merge SHA `88675f355d15acf69209f0e97c541f6692bdbc59` (2026-11-18T20:31:59Z) · **all 15 fields identical to both backups** · CI `test: SUCCESS` |

| 2026-11-19 | **fix/key-entry-hardening** (test-first) | `pytest tests/test_api_key_entry.py -q` | **24 passed** — RED first: 20 failed, then 1 more red for the panel-wizard path |
| 2026-11-19 | **fix/key-entry-hardening** | `pytest` the affected files + the UI trio (providers, rotation, Q&A, AI, quota, authz, UI, config docs) on the final tree | **198 passed** |
| 2026-11-19 | **fix/key-entry-hardening** | `pytest --collect-only -q` | **1078 collected** (README badge and count line updated to match) |
| 2026-11-19 | **fix/key-entry-hardening** | full `pytest -q` (local, Windows) | **1059 passed, 19 failed in 601.44s** — the 19 are exactly the §5.1 Windows-only bash-path failures (4 + 15); **no new failure** |
| 2026-11-19 | **fix/key-entry-hardening** | `ruff check newsbot tests tools` · `python -m mypy` · `python tools/final_verification.py` · `run.py --check` (dummy env) | All checks passed · **Success: no issues found in 50 source files** · **17/17** · exit **0** |

| 2026-11-19 | **fix/key-entry-hardening** (final tree) | GitHub CI `test` on PR #88 | **SUCCESS** (3m18s) |
| 2026-11-19 | `main` @ `6d5b4ae` (PR #88) | `gh pr merge 88 --squash --delete-branch`; protection compared field by field | merge SHA `6d5b4aee954a2a1b36a095d79f7dbd5fc1bc8bb1` (2026-11-18T22:36:46Z) · **all 15 fields identical to both backups** · CI `test: SUCCESS` |

| 2026-11-20 | **docs/phase5-design-plan** | `pytest tests/test_repo_integrity.py tests/test_phase1_10_state_alignment.py -q` after adding the three proposed paths to `PROPOSED_PATHS` | **9 passed** — the first CI run failed on the docs PR because `newsbot/qa_quota.py` and the two new test files were cited before they existed; the guard's allowlist fixed it |
| 2026-11-20 | **docs/phase5-design-plan** (final tree) | GitHub CI `test` on PR #91 | **SUCCESS** (2m39s) |
| 2026-11-20 | `main` @ `7adb0da` (PR #91) | `gh pr merge 91 --squash --delete-branch`; protection compared field by field | merge SHA `7adb0da2ddf80ffcc01cad0b06210a0d628a1297` (2026-11-20T18:55:56Z) · **all 15 fields identical to both backups** · CI `test: SUCCESS` |
| 2026-11-20 | **phase5/qa-quotas** | `pytest tests/test_phase5_qa_quotas.py -q` (Task 1 red then green) | **2 passed** |
| 2026-11-20 | **phase5/qa-quotas** | `pytest tests/test_phase5_qa_quotas.py tests/test_phase4_qa_core.py tests/test_phase4_schema.py -q` (Task 2 red then green) | **61 passed** — red runs exposed two plan defects: the cooldown-after test reused `user_max=1`, and the concurrency test used 8 distinct users; both corrected with ledgered rulings |
| 2026-11-20 | **phase5/qa-quotas** | `pytest tests/test_phase5_qa_quotas.py tests/test_phase4_qa_core.py -q` (Task 3) | **59 passed** |
| 2026-11-20 | **phase5/qa-quotas** | `pytest tests/test_phase5_qa_quota_ui.py tests/test_phase5_qa_quotas.py -q` (Task 4 red then green) | **29 passed** |
| 2026-11-20 | **phase5/qa-quotas** (whole-branch self-review) | two new RED→GREEN tests: lost D1 insert race ⇒ `duplicate`; panel labels localised instead of raw field ids | both passed after the fix; final targeted run **30 passed** |
| 2026-11-20 | **phase5/qa-quotas** (final tree) | full `pytest -q` (local, Windows) | **1089 passed, 19 failed in 615.97s** — the 19 are exactly the §5.1 Windows-only bash-path failures (4 + 15); **no new failure**. The README guard caught the stale 1078/۱۰۷۸ count and both numbers were raised to 1108/۱۱۰۸ |
| 2026-11-20 | **phase5/qa-quotas** (final tree) | `pytest --collect-only -q` · `ruff check .` · `python -m mypy newsbot` · `python tools/final_verification.py` · `run.py --check` (dummy env) | **1108 collected** · All checks passed · **Success: no issues found in 51 source files** · **17/17** · exit **0** |
| 2026-11-20 | **phase5/qa-quotas** (final tree) | GitHub CI `test` on PR #92 | **SUCCESS** (3m09s) |
| 2026-11-20 | `main` @ `1e6e8eb` (PR #92) | `gh pr merge 92 --squash --delete-branch`; protection compared field by field | merge SHA `1e6e8ebed85e79554c1302101fc3c6e390530ad4` (2026-11-20T20:05:53Z) · **all 15 fields identical to both backups** · CI `test: SUCCESS` |
| 2026-11-21 | branch **docs/phase6-design-plan** | GitHub CI `test` on PR #94 | first run FAILED on a real Phase 5 wall-clock-dependent quota test; after PR #95 and a rebase: **SUCCESS** (2m52s) |
| 2026-11-21 | `main` @ `db135f1` (PR #95) | `gh pr merge 95 --squash --delete-branch`; protection verified | **MERGED**, CI `test: SUCCESS` (3m26s), **all 15 fields identical** to both backups |
| 2026-11-21 | `main` @ `2bedda4` (PR #94) | `gh pr merge 94 --squash --delete-branch`; protection verified | **MERGED**, CI `test: SUCCESS` (2m52s), **all 15 fields identical** to both backups |
| 2026-11-22 | branch **phase6/typing-lifecycle** @ `e4cc30f` | `pytest tests/test_phase6_typing_lifecycle.py -q -x` × 5 | **23 passed** on each of five consecutive runs (5.20–6.77 s); no flake |
| 2026-11-22 | branch **phase6/typing-lifecycle** @ `e4cc30f` | `pytest tests/test_phase6_typing_lifecycle.py tests/test_phase8_concurrency.py tests/test_phase4_qa_core.py tests/test_phase5_qa_quotas.py tests/test_phase5_qa_quota_ui.py -q` | **104 passed in 10.97s** — no Phase 4/5/8 regression |
| 2026-11-22 | branch **phase6/typing-lifecycle** @ `e4cc30f` | `pytest -q --collect-only -p no:cacheprovider` | **1132 tests collected**; README badge and Persian count updated; `test_phase1_10_state_alignment.py` 6 passed |
| 2026-11-22 | branch **phase6/typing-lifecycle** @ `e4cc30f` | `ruff check .` / `mypy newsbot` / `tools/final_verification.py` / `run.py --check` (dummy env) | clean / 52 files clean / **17/17 exit 0** / exit 0, config healthy |
| 2026-11-22 | branch **phase6/typing-lifecycle** @ `e4cc30f` | `pytest -q --tb=line -rf` (full Windows suite) | **19 failed, 1113 passed in 1210.22s (0:20:10)** — exactly the 19 known Windows-only failures from §5.1 (4 in `test_deploy_script.py`, 15 in `test_session3_cicd_docker_deploy_docs.py`); **no new failure**, Linux CI is green |
| 2026-11-22 | `main` @ `5eb8fea` (PR #96) | `gh pr merge 96 --squash --delete-branch`; protection verified | **MERGED**, CI `test: SUCCESS` (3m14s), **all 15 fields identical** to both backups |
| 2026-11-23 | branch **docs/phase7-design-plan** | GitHub CI `test` on PR #98 | **SUCCESS** (3m22s) |
| 2026-11-24 | branch **phase7/onboarding** @ `9b04268` | `pytest tests/test_phase7_onboarding.py -q -x` × 5 | **17 passed** on each of five consecutive runs (4.07–5.14 s); no flake |
| 2026-11-24 | branch **phase7/onboarding** | focused suites during Tasks 1–3 and the fix wave | 62 passed (schema+guards) · 52 passed (flow) · 113 passed (six routing/UI suites) · 66 passed after the P2/P3 fix · 30 passed D1 introspection · 4 passed PH-01 migration |
| 2026-11-24 | branch **phase7/onboarding** @ `9b04268` | `pytest -q --collect-only -p no:cacheprovider` | **1155 tests collected**; README badge and Persian count updated; `test_phase1_10_state_alignment.py` 6 passed |
| 2026-11-24 | branch **phase7/onboarding** @ `9b04268` | `ruff check .` / `mypy newsbot` / `final_verification.py` / `run.py --check` (dummy env) | clean / 52 files clean / **17/17 exit 0** / exit 0, config healthy |
| 2026-11-24 | branch **phase7/onboarding** @ `9b04268` | `pytest -q --tb=line -rf` (final Windows suite) | **19 failed, 1136 passed in 661.62s (0:11:01)** — only the §5.1 Windows-only failures; the four mid-phase schema regressions were fixed and are gone |
| 2026-11-24 | `main` @ `987a84f` (PR #99) | `gh pr merge 99 --squash --delete-branch`; protection verified | **MERGED**, CI `test: SUCCESS` (3m18s), **all 15 fields identical** to both backups |
| 2026-11-25 | branch **docs/phase8-design-plan** | GitHub CI `test` on PR #101 | **SUCCESS** (3m21s) |
| 2026-11-25 | branch **phase8/deeplink-back** @ `839934d` | `pytest tests/test_phase8_deeplink_back.py -q -x` × 5 | **14 passed** on each of five consecutive runs (2.23–3.85 s); no flake |
| 2026-11-25 | branch **phase8/deeplink-back** | focused suites during Tasks 1–3 | RED 1 error on Task 1 (missing `post_url`) → 21 passed; Task 2 RED 2 failed/4 passed → 19 passed; Task 3 RED 6 failed/8 passed → 76 passed across phase8 + rich-messages + phase7 suites |
| 2026-11-25 | branch **phase8/deeplink-back** @ `839934d` | `pytest -q --collect-only -p no:cacheprovider` | **1169 tests collected**; README badge and Persian count updated; `test_phase1_10_state_alignment.py` 6 passed |
| 2026-11-25 | branch **phase8/deeplink-back** @ `839934d` | `ruff check .` / `mypy newsbot` / `final_verification.py` / `run.py --check` (dummy env) | clean / 52 files clean / **17/17 exit 0** / exit 0, config healthy |
| 2026-11-25 | branch **phase8/deeplink-back** @ `839934d` | `pytest -q --tb=line -rf` (final Windows suite) | **19 failed, 1150 passed in 655.05s (0:10:55)** — only the §5.1 Windows-only failures |
| 2026-11-25 | `main` @ `db86543` (PR #102) | `gh pr merge 102 --squash --delete-branch`; protection verified | **MERGED**, CI `test: SUCCESS` (3m14s), **all 15 fields identical** to both backups |
| 2026-11-25 | branch **docs/phase9-design-plan** | GitHub CI `test` on PR #104 | **SUCCESS** (3m25s) |
| 2026-11-25 | branch **phase9/manual-harness** @ `2351d48` | `pytest tests/test_phase4_live_acceptance.py -q -x` × 5 | **59 passed** on each of five consecutive runs (2.37–3.41 s); no flake |
| 2026-11-25 | branch **phase9/manual-harness** | focused suites during Tasks 1–3 and the fix round | Task 1 RED 3 failed/45 passed → 48 passed · Task 2 RED 4 failed/49 passed → 53 passed, then a security fix RED 1 failed/53 passed → 54 passed · Task 3 RED 6 failed/53 passed → 59 passed |
| 2026-11-25 | branch **phase9/manual-harness** @ `2351d48` | `pytest -q --collect-only -p no:cacheprovider` | **1183 tests collected**; README badge and Persian count updated; `test_phase1_10_state_alignment.py` 6 passed |
| 2026-11-25 | branch **phase9/manual-harness** @ `2351d48` | `ruff check .` / `mypy newsbot` / `final_verification.py` / `run.py --check` (dummy env) | clean / 52 files clean / **17/17 exit 0** / exit 0, config healthy |
| 2026-11-25 | branch **phase9/manual-harness** @ `2351d48` | `pytest -q --tb=line -rf` (final Windows suite) | **19 failed, 1164 passed in 662.02s (0:11:02)** — only the §5.1 Windows-only failures |
| 2026-11-25 | `main` @ `9cd7b61` (PR #105) | `gh pr merge 105 --squash --delete-branch`; protection verified | **MERGED**, CI `test: SUCCESS` (3m36s), **all 15 fields identical** to both backups |
| 2026-11-27 | `main` @ `361a10c` (PR #108) | `gh pr merge 108 --squash --delete-branch`; protection verified | **MERGED**, CI `test: SUCCESS`, **all 15 fields identical** to the backup; the `libzstd1` image scan (F-2) is green in run 46047560145 |
| 2026-11-27 | branch **docs/phase10-design-plan** | GitHub CI `test` on PR #107 | **SUCCESS** (run 46061459633) |
| 2026-11-27 | `main` @ `9886972` (PR #107) | `gh pr merge 107 --squash --delete-branch`; protection verified | **MERGED**, CI `test: SUCCESS`, **all 15 fields identical** to the backup |
| 2026-11-27 | branch **phase10/hardening** | `pytest tests/test_deploy_script.py tests/test_session3_cicd_docker_deploy_docs.py -q` | **97 passed / 0 failed** — the 19 Windows-only failures are gone (W1) |
| 2026-11-27 | branch **phase10/hardening** | `pytest tests/test_phase10_secrets_rotation.py tests/test_api_key_entry.py tests/test_phase12_config_docs.py tests/test_phase2_provider_registry.py -q` | **18 passed** (rotation, W2) + **57 passed** (the pre-existing secret suites) |
| 2026-11-27 | branch **phase10/hardening** | `pytest -q --collect-only -p no:cacheprovider` | **1208 tests collected** — the README badge and the Persian count were updated |
| 2026-11-27 | `main` @ `b660bc3` (PR #109) | `gh pr merge 109 --squash --delete-branch`; protection verified | **MERGED**, CI `test: SUCCESS` (run 46075556814), **all 15 fields identical**; `tools/final_verification.py` → **17/17** (exit 0); `run.py --check` → exit 0; ruff / mypy / pyflakes / py_compile clean |
| 2026-11-27 | branch **chore/phase10-dead-code** @ `b855a1d` | bounded dead-code scan (AST + text, 204 tracked files) | 857 module-level symbols → **846**; zero-reference candidates **11 → 0** |
| 2026-11-27 | branch **chore/phase10-dead-code** | `pytest -q` (final Windows suite) | **1208 passed / 0 failed** in 806.84s (0:13:26) — the test count did not change |
| 2026-11-27 | `main` @ `ef185ca` (PR #110) | `gh pr merge 110 --squash --delete-branch`; protection verified | **MERGED**, CI `test: SUCCESS` (run 46082906798), **all 15 fields identical** to the backup |
| 2026-11-27 | `main` @ `ef185ca` | GitHub CI on `main` | **run 46083316285 — SUCCESS**: pyflakes, ruff, mypy, py_compile, pip-audit, gitleaks, hadolint, trivy fs, pytest + coverage gate, `final_verification.py`, `run.py --check`, docker build + trivy image, compose config |
| 2026-11-27 | **D-7 live run 46013627630** (head SHA `967afde`, the Phase 9 code — **before** PR #109/#110) | `qa-live` manual dispatch (`scenario=duration`, `db_mode=fresh`, budget 20400 s) | **FAILURE — `exit_code=2`, `problems=["NO_LINKED_GROUP"]`** · 14:11:14Z → 19:51:47Z · `active_duration_s=20408` · `startup_duration_s=2` · `bot_log_lines=647` · artifact `qa-live-46013627630` (`report.json` + `bot.log` only — the DB was never uploaded) · OBSERVED: `preconditions`, `public_model_active`; the other ten checks `NOT_OBSERVED`; `no_text_no_ai` = `NOT_APPLICABLE` (13 checks in total) · boundaries: `telegram_bot` real, `temporary_db` real, `provider_ai` **simulated** (`provider_credentials_present=false`), `real_channel_publication` not_performed |
| 2026-11-27 | artifact `qa-live-46013627630` | count-only scan of `bot.log` (F-1) | **3×** `HTTP 504` · **2×** `ai_timeout` · **28×** `probe timed out after 15s` (28 probe-timeout warnings out of 36 `WARNING` lines; code: `newsbot/ai_models.py:310-311`, `newsbot/net_http.py:334`, `newsbot/ai_models.py:607`) — recorded, **not fixed** in Phase 10 |
| 2026-11-27 | branch **docs/phase10-records** | glyph scan over the five Phase 10 documents | **0 suspicious lines** |
| 2026-11-27 | `main` @ `ef185ca` | `Deploy to VPS` run 46083654457 | **CANCELLED** — superseded when PR #111 was merged (a newer SHA on `main` queues a new deploy run); it never reached the VPS |
| 2026-11-28 | `main` @ `e0698ef` (PR #111) | GitHub CI on `main` | **run 46105260191 — SUCCESS** |
| 2026-11-28 | `main` @ `e0698ef` | `Deploy to VPS` run 46105550745 | **CANCELLED** — superseded when PR #112 was merged (each merge to `main` queues a new deploy run and cancels the previous pending one); it never reached the VPS |
| 2026-11-28 | `main` @ `1d7916a` (PR #112) | GitHub CI on `main` | **run 46108539227 — SUCCESS** |
| 2026-11-28 | `main` @ `1d7916a` | `Deploy to VPS` run 46108808054 | **PENDING** — waiting for the manual approval of the `production` environment; **no deployment happened** and none is claimed |

**Not run / unavailable:** a **successful** live `qa-live` run (Phase 4 acceptance **and** Phase 5's
quota scenario, both deferred to the single Phase 10 run — D-7 / §4.5: run 46013627630 failed with
`NO_LINKED_GROUP`), the **actual** secret rotation (the tool and the runbook exist; the execution is
the owner's), coverage percentage (no coverage tool in dev requirements), live VPS deployment
verification (the latest `Deploy to VPS` run on `main` is pending the manual `production` approval),
Cloudflare D1 live
migration.

### 5.1 The 19 local failures on the final `main` (measured, not assumed)

| Source | Count | Why it fails on Windows |
|---|---|---|
| `tests/test_deploy_script.py` (docker-runtime cases) | 4 | The test hands a Git-Bash-style path to `docker`/`bash`; Windows rewrites it to `C:\Program Files\Git\...`, so the container path never resolves (`FileNotFoundError`) |
| `tests/test_session3_cicd_docker_deploy_docs.py` (forced-command / preserve-tree / setup-user) | 15 | The test invokes `bash deploy/ssh-forced-command.sh` with a `C:\...` argument; Git Bash answers *"No such file or directory"* (exit 127) |

Both groups are the same class of defect: the **test harness** builds a POSIX path and runs it
through a POSIX shell on an NT host. None of them exercises product logic, none touches the
database, and none is new: the same two files were already the 4-failure baseline recorded in §5
above. GitHub's Linux runner runs both files green — CI `test` is `SUCCESS` on `b2b200d`.
The real fix (making the harness portable) belongs to Phase 10 (D-2); it is out of Phase 1 scope
because it changes test infrastructure, not behaviour.

---

## 6. Migrations, backups, rollback

No schema change in Phase 1. Standing rule for later phases: every migration is **additive only**
(`ADD COLUMN` / new table), idempotent, rehearsed against a copy of the production DB, and has a
documented rollback. Legacy `users.credit` balances are frozen, never auto-converted.

---

## 7. Git delivery record

| Item | Value |
|---|---|
| Phase 1 (P1-1) | `d6ae7c2` (docs) · `fed5098` (state update) · `b5c9125` (review pass) → squashed into `e3e321e` |
| Phase 1 (P1-2) | **PR #58 — MERGED** as `0f4b90a` (2026-11-13) · CI `test: SUCCESS` · this file's close-out record |
| PR #56 | **MERGED** 2026-11-12, merge commit `e3e321e41634fce726e77ab76cad92250081677f` · CI `test: SUCCESS`, `docker-build: SUCCESS` |
| PR #57 | **MERGED** 2026-11-13, merge commit `533f7532ab27475aaeec8ec48d3987bb563051f9` · CI `test: SUCCESS` |
| PR chain | #46 → #47 → #48 → #49 → #50 (stacked) — **all merged 2026-11-13**, dispositions and merge SHAs in §4.1 |
| PR chain commits on `main` | `123bafe` (#46) · `685aecd` (#47) · `29416af` (#48) · `d386467` (#49) · `b2b200d` (#50) |
| CI on `main` at `b2b200d` | `test: success` |
| Production commit SHA | **UNVERIFIED** — no VPS read was possible |
| **Phase 1 closes with** | **PR #58 — MERGED** as `0f4b90a` (2026-11-13), CI `test: SUCCESS`, review gate lifted for the merge only and restored with all 15 fields verified identical |
| Phase 2 commits on `main` | `16a7353` (#51) · `886e19d` (#52) · `bf34390` (#53) · `bf73b39` (#60 provider registry) |
| Phase 2 result | **COMPLETE** — every Phase 2 PR (#51, #52, #53, #60) is merged; §4.2 is satisfied |
| Phase 3 commits on `main` | `7f48ec4` (#54 observability + DB-007) · `51bb8d2` (#62 rotation engine) · `3f78e51` (#66 review fixes) · `fa7f25f` (#64 repo hygiene + `LICENSE`) |
| Phase 3 result | **COMPLETE** — every Phase 3 PR (#54, #62, #66, #64) is merged; §4.3 is satisfied. #55 → **#64** (auto-close recovery recorded in §1 and §4.1). #68–#75 (Dependabot) are recorded as D-4 |
| Phase 4 delivery | #83 **MERGED** as `f850d44830b49d0fed8a53c78cf633714012d815` (2026-11-16) · CI `test: SUCCESS` · #84 **MERGED** as `0985e3ebd4839df3110896c085bd4f8cf652d7ce` (2026-11-16) · CI `test: SUCCESS` · #82 **MERGED** as `f4afa0ebcbc037446402fb3c51c1f04eeebf6eb2` (2026-11-16, last Phase 4 PR — design + plan + close-out). The review gate was lifted for each merge only and restored with **all 15 fields verified identical** every time |
| Phase 4 result | **CODE COMPLETE — live acceptance `PENDING`** — every Phase 4 code PR (#83, #84, #82) is merged and the §4.4 code criteria are met, but §4.5's live run has not happened yet. The records-only PR #85 (`a925994`) wrote #82's exact SHA and the final-`main` evidence |
| Phase 4 live-acceptance harness | Branch **phase4/live-acceptance** from `main` @ `a925994` — commit **028407e** (id-only ignore log lines) · **09b6039** (precondition checker) · **eaa79ad** (per-row attribution + 13 checks) · **1bd8104** (supervisor + report) · **f44372a** (manual `qa-live` workflow) · the runbook/state/README commit. Merged after CI `test: SUCCESS` and the usual gate protocol |
| Phase 4 harness merge | **PR #86 — MERGED** as `88675f355d15acf69209f0e97c541f6692bdbc59` (2026-11-18T20:31:59Z) · CI `test: SUCCESS` (3m45s) · review gate lifted for the merge only and restored with **all 15 fields verified identical** to both backups |
| Phase 4 live run | **`DEFERRED TO PHASE 10`** by the owner on 2026-11-20 (D-7) — recorded here only after the single release-run `qa-live` report artifact exists (§4.5) |
| Key-entry hardening (owner-reported "healthy model fails in the bot") | **PR #88 — MERGED** as `6d5b4aee954a2a1b36a095d79f7dbd5fc1bc8bb1` (2026-11-18T22:36:46Z) · CI `test: SUCCESS` (3m18s) · review gate lifted for the merge only and restored with **all 15 fields verified identical** to both backups |
| Phase 5 design + plan | **PR #91 — MERGED** as `7adb0da2ddf80ffcc01cad0b06210a0d628a1297` (2026-11-20T18:55:56Z) · CI `test: SUCCESS` (2m39s) · gate restored with **all 15 fields identical**. Spec: `docs/superpowers/specs/2026-11-20-phase5-qa-quotas-design.md` · plan: `docs/superpowers/plans/2026-11-20-phase5-qa-quotas.md` |
| Phase 5 code | Branch **phase5/qa-quotas** from `main` @ `7adb0da` — commits **accde94** (config) · **6ff2a1e** (atomic claim) · **70a9a48** (handler) · **45be39c** (admin panel) · **9a9cb7e** (review fixes) · **b12e290** (ruff cleanup). Merged as **PR #92 → `1e6e8ebed85e79554c1302101fc3c6e390530ad4`** (2026-11-20T20:05:53Z) · CI `test: SUCCESS` (3m09s) · gate restored with **all 15 fields identical** |
| Phase 5 result | **CODE COMPLETE — no live verification claimed**: 30 new tests, 1108 collected, full Windows suite 1089 passed / 19 known failures, `ruff`/`mypy`/`final_verification.py`/`run.py --check` all clean. The Phase 5 quota scenario is checked in the single Phase 10 run together with Phase 4 (D-7) |
| Phase 5 determinism fix | **PR #95 — MERGED** as `db135f120a0454ecb519f5024bcb72d630b3c69e` — `qa_quota.windows` now derives the local day from the claim's `now`, not the wall clock; CI caught it while running PR #94 |
| Phase 6 design + plan | **PR #94 — MERGED** as `2bedda471eb7b3da06b6b14a111ac993c88f944d` · spec `docs/superpowers/specs/2026-11-21-phase6-typing-lifecycle-design.md` · plan `docs/superpowers/plans/2026-11-21-phase6-typing-lifecycle.md` · CI `test: SUCCESS` (2m52s) · gate restored with **all 15 fields identical** |
| Phase 6 code | Branch **phase6/typing-lifecycle** from `main` @ `2bedda4` — commits **206fd56** (config), **f7d98ed** (context manager), **ec30cd3** (mute window + exception drain), **cd40add** (shutdown), **34d5d8a** (install sites), **fb254f1** + **e4cc30f** (README count), **2b9548f** (P1 shutdown-hole fix). Merged as **PR #96 → `5eb8fea562d956735975f01f8ef65ff99c626599`** · CI `test: SUCCESS` (3m14s) · gate restored with **all 15 fields identical** |
| Phase 6 result | **CODE COMPLETE — no live verification claimed**: 23 new tests, 1132 collected, five consecutive green repeat runs, full Windows suite measured below, `ruff`/`mypy`/`final_verification.py`/`run.py --check` clean. The independent whole-branch reviewer found one P1 G3 hole (post-shutdown registration), fixed test-first in `2b9548f` and re-approved (`WHOLE_BRANCH_VERDICT: APPROVED`). Phase 6's live scenario joins Phase 4 and 5 in the single Phase 10 run (D-7) |
| Phase 7 design + plan | **PR #98 — MERGED** as `7482146870800195548dea7c704b4f6f4e244a8b` · spec `docs/superpowers/specs/2026-11-23-phase7-onboarding-closure-design.md` · plan `docs/superpowers/plans/2026-11-23-phase7-onboarding.md` · CI `test: SUCCESS` (3m22s) · gate restored with **all 15 fields identical**. The first draft of the spec was rejected by the builder check for treating already-fixed UI findings as the whole phase; the approved version implements AUD-006 |
| Phase 7 code | Branch **phase7/onboarding** from `main` @ `7482146` — commits **988b0d4** (schema v13 + state helpers), **a7d824d** (stateful `/start` + free grant), **ad63238** (home menu + completion), **9a02426** + **9b04268** (README counts), **0bd9e54** (admin bypass + `adding_channel` resume), **4ffd2a7** (base DDL + PH-01 v13 fixture). Merged as **PR #99 → `987a84ff88c28d6d2f5382e58c0062f3eda66446`** · CI `test: SUCCESS` (3m18s) · gate restored with **all 15 fields identical** |
| Phase 7 result | **CODE COMPLETE — no live verification claimed**: 21 new tests, 1155 collected, final Windows suite 1136 passed / the known 19 failures, `ruff`/`mypy`/`final_verification.py`/`run.py --check` clean. Whole-branch review first `CHANGES_REQUIRED` (two state traps + four D1/PH-01 regressions), then `APPROVED` after the fix wave. Phase 7's live scenario joins Phases 4–6 in the single Phase 10 run (D-7) |
| Phase 8 design + plan | **PR #101 — MERGED** as `0264f52c8753f903fecec09990870a6668aeaf03` · spec `docs/superpowers/specs/2026-11-25-phase8-deeplink-back-design.md` · plan `docs/superpowers/plans/2026-11-25-phase8-deeplink-back.md` · CI `test: SUCCESS` (3m21s) · gate restored with **all 15 fields identical**. The builder check found five real gaps (stale handles, rich-path markup, split bodies, deleted rows, audit wording); all were resolved in the approved design |
| Phase 8 code | Branch **phase8/deeplink-back** from `main` @ `0264f52` — commits **c236754** (one `post_url` rule), **2644242** (payload + rename healing), **67fa117** (button, notes, single-control split), **68dc7df** (ruff cleanup), **839934d** (README count). Merged as **PR #102 → `db86543baa6c9e3dad0e504a87c6a55a0f9547dd`** · CI `test: SUCCESS` (3m14s) · gate restored with **all 15 fields identical** |
| Phase 8 result | **CODE COMPLETE — no live verification claimed**: 14 new tests, 1169 collected, final Windows suite 1150 passed / the known 19 failures, `ruff`/`mypy`/`final_verification.py`/`run.py --check` clean. Whole-branch `APPROVED` with three LOW findings (pre-existing re-settle indexing, corrupt `-100…` id, fa-only rendering assertions) and all deferred minors triaged as no-fix-before-merge. Phase 8's live check joins Phases 4–7 in the single Phase 10 run (D-7) |
| Phase 9 design + plan | **PR #104 — MERGED** as `c9c7f4f9b703dae75888828222b3380a1b7aa9cd` · spec `docs/superpowers/specs/2026-11-25-phase9-manual-harness-design.md` · plan `docs/superpowers/plans/2026-11-25-phase9-manual-harness.md` · CI `test: SUCCESS` (3m25s) · gate restored with **all 15 fields identical**. The builder check found five real gaps (target vs cap, id lists, cleanup seconds, id leak semantics, Provider row), all resolved in the approved design |
| Phase 9 code | Branch **phase9/manual-harness** from `main` @ `c9c7f4f` — commits **93b33b3** (READY-based timing), **56b1bc0** (burn-in + boundaries + leak check), **eda09be** (value-free id leak labels), **b8e06e6** (secure inputs, validation, runbook), **2351d48** (README count). Merged as **PR #105 → `9cd7b6184bee336c5e802ae37b52b2279d797153`** · CI `test: SUCCESS` (3m36s) · gate restored with **all 15 fields identical** |
| Phase 9 result | **CODE COMPLETE — no live verification claimed**: 14 new tests, 1183 collected, final Windows suite 1164 passed / the known 19 failures, `ruff`/`mypy`/`final_verification.py`/`run.py --check` clean. Whole-branch `APPROVED`; Task 2's review caught a real secret-value leak into `report['checks']`, fixed test-first in `eda09be` and re-reviewed clean. §4.8 lists the open items carried into Phase 10 |
| Phase 10 design + plan | **PR #107 — MERGED** as `988697211cee536923ba3d1816858056f8003440` (2026-11-27) · spec `docs/superpowers/specs/2026-11-26-phase10-hardening-release-design.md` · plan `docs/superpowers/plans/2026-11-27-phase10-hardening-release.md` · CI `test: SUCCESS` (run 46061459633) · gate restored with **all 15 fields identical** |
| Phase 10 CVE fix | **PR #108 — MERGED** as `361a10cd28dccfd85ffceefbf3840cf2baf494cb` (2026-11-27) · `deploy/Dockerfile` upgrades `zlib1g`/`libzstd1` with `--only-upgrade` (F-2 / CVE-2026-124851) · CI `test: SUCCESS` (run 46047560145) · gate restored with **all 15 fields identical** |
| Phase 10 hardening + rotation | **PR #109 — MERGED** as `b660bc3fb299079b358091fd55dcc85bc0eac99b` (2026-11-27) · W1 (Windows harness `tests/_shell.py`), W2 (`tools/rotate_secrets_key.py` + 18 tests), F-3 (secret-leak boundary), cache prefix `qa-live-db-v1-` → `qa-live-db-v2-`, `deploy/env.example` · CI `test: SUCCESS` (run 46075556814) · gate restored with **all 15 fields identical** |
| Phase 10 dead-code cleanup | **PR #110 — MERGED** as `ef185ca9aa7391ca69dec8e2cc21fcecac19ad1f` (2026-11-27) · 11 module-level functions removed (69 lines deleted, 6 added inside docstrings) · CI `test: SUCCESS` (run 46082906798) · gate restored with **all 15 fields identical** |
| Phase 10 branch hygiene | **33 remote branches deleted** under the SHA guard (36 candidates; 3 kept because the branch moved after its merge), **16 kept**, and the stale remote-tracking refs pruned by `git fetch --prune` (51 tracking refs remained after pass 1, 19 after pass 2) · evidence kept outside the repository at `C:\Users\Mustafa\AppData\Local\Temp\opencode\phase10_branch_cleanup.txt` |
| Phase 10 records (PR3) | Branch **docs/phase10-records** from `main` @ `ef185ca` — `docs/OPS_SECRET_ROTATION.md`, `docs/RELEASE_RUNBOOK.md`, `docs/PHASE10_FINAL_REPORT.md`, `docs/QA_LIVE_ACCEPTANCE.md` (§9) and this file — **records only, no behaviour change**. Merged as **PR #111 → `e0698ef574d2de1a728222696a3816c3bac2ec22`** (2026-11-28) · CI `test: SUCCESS` (run 46104240680; on `main` 46105260191) · gate restored with **all 15 fields identical**; the independent whole-branch review's findings (a wrong F-1 count, an async provider-test command, stale "last attempt" wording and six smaller items) were fixed in the follow-up commit `50fe109` before the merge; the two records-only follow-ups (#112 and #113) keep the file's deploy/CI references in step with `main` |
| Phase 10 result | **`IN_PROGRESS` — not `COMPLETED`**: every phase PR is merged and every automated gate is green (suite **1208/0**, verifier **17/17**, `run.py --check` exit 0, CI run 46108539227 SUCCESS, protection **15/15** after each merge), but the **secret rotation** and a **successful live D-7 run** are owner gates with no evidence yet (§4.9.4) |
| CI on `main` after Phase 10 | **46108539227 — SUCCESS** at `1d7916a` (46105260191 at `e0698ef`) |
| Deployment to VPS | the **latest** `Deploy to VPS` run on `main` is **PENDING** the manual `production` approval — 46108808054 for `main` @ `1d7916a` at the time of writing. Every merge to `main` queues a new run and cancels the previous pending one (46083654457 and 46105550745 were cancelled this way). **No deployment has happened** and none is claimed |

**Branch protection on `main` (read from the GitHub API on 2026-11-13):**
- Required status checks: `test` (strict = branch must be up to date)
- Required approving reviews: 1 (stale reviews dismissed on new pushes)
- Required linear history: **true** (enforced going forward — new merge commits are rejected; `main`
  still contains 21 historical merge commits from before the rule)
- Force pushes / deletions: disabled
- `enforce_admins`: true

### 7.1 The review-gate workaround (verified, must be restored immediately)

The repository has exactly one account (`Aryasamadi`), and it is the author of every PR in the
chain. GitHub does not allow an author to approve their own PR, so `required_approving_review_count: 1`
makes the repository unmergeable by its owner. Measured on 2026-11-13:

| Attempt | Result |
|---|---|
| `gh pr merge --squash --admin` | GraphQL: *At least 1 approving review is required* |
| `PUT /pulls/56/merge` (REST) | HTTP 405, same message |
| `PATCH /branches/main/protection` (REST) | **HTTP 404** — not available to this token |
| `PUT /branches/main/protection/required_pull_request_reviews` (REST) | **HTTP 404** |
| `POST /branches/main/protection` (REST) | **HTTP 404** |
| `DELETE /branches/main/protection/required_pull_request_reviews` (REST) | **works** (HTTP 204) |
| GraphQL `updateBranchProtectionRule` | **works** — the only way to *set* the review rule |

**Sanctioned procedure** (used for #56 and for every chain PR #46–#50, each with its own backup file):

1. Save the current protection JSON and compare field by field.
2. `DELETE .../branches/main/protection/required_pull_request_reviews` — removes only the review rule.
3. Merge the PR with `--squash`.
4. Restore with GraphQL `updateBranchProtectionRule`, setting **every** field to its backed-up value
   (`requiresApprovingReviews`, `requiredApprovingReviewCount`, `dismissesStaleReviews`,
   `requiresCodeOwnerReviews`, `requiresStatusChecks`, `requiresStrictStatusChecks`,
   `requiredStatusCheckContexts`, `isAdminEnforced`, `requiresLinearHistory`, `allowsForcePushes`,
   `allowsDeletions`, `requiresCommitSignatures`, `blocksCreations`, `restrictsPushes`,
   `requiresConversationResolution`).
5. **Verify** the restored protection field by field against the backup, then confirm the PR is
   `MERGED` and `main` contains it.

**Verified on 2026-11-13 after merging #56:** all 15 protection fields match the backup exactly
(status checks `test`/strict, reviews 1/dismiss-stale, admins enforced, linear history on, force
pushes off, deletions off, signatures off, conversation resolution off, branch not locked, fork
syncing off). The gate was off for the duration of one merge only.

**Verified again after each of #46, #47, #48, #49, #50 (2026-11-13):** the same field-by-field
comparison returned *"GATE RESTORED - all 15 fields identical to backup"* every time. Tooling used:
`gate.py <PR> lift` / `gate.py <PR> restore`, which writes `protection-before-pr<N>.json`.

---

## 8. Blockers, open questions, residual debt

| ID | Item | Status |
|---|---|---|
| B-1 | Merging the stacked PR chain requires an approving review and **linear history** (squash/rebase, not merge) | **RESOLVED 2026-11-13** — owner ruled: each PR gets its disposition in its own phase, and each phase's PRs are merged when the phase completes. Dispositions in §4.1; merge mechanics in §7.1 |
| B-2 | Production commit SHA / unit name / env permissions are unverified | needs VPS access |
| B-3 | A long GitHub-hosted run would leave only ~20 minutes of headroom against the hard 6-hour (360-minute) job cap; the cap cannot be raised | **HANDLED** for `qa-live` — `timeout-minutes: 360` + a default duration of **340** minutes (exactly 10 minutes below the old 350 default), and the harness exits early once every required check is observed (§4.5). The cap itself is still unraisable |
| B-4 | Whether the bot is added to the channel discussion group (prerequisite for a **real end-to-end** Phase 4 test) | **HANDED TO THE RUNBOOK** — a setup step of `docs/QA_LIVE_ACCEPTANCE.md`, not a code gap. The code stays **fail-closed**: no verified link ⇒ no answer (§4.4) |
| B-5 | A file **outside** this repository (the local `newsbot-deploy-pkg` folder) holds a bot token matching the BotFather format and a 64-char `SECRETS_KEY` in plain text (names/lengths only; values never read) | **OPEN — owner gate.** The tool (`tools/rotate_secrets_key.py`) and the runbook (`docs/OPS_SECRET_ROTATION.md`) are merged; the rotation itself has **not** run, so the file stays untouched (decision **C8**). The deletion happens only after a successful rotation (§4.9.4) |
| B-6 | ~~licence choice for #64~~ — **RESOLVED 2026-11-14**: the owner chose **proprietary / all rights reserved**; `LICENSE` merged as part of #64 `fa7f25f` | closed |
| B-7 | The three repository secrets for the live run — `BOT_TOKEN` (**test** bot only), `SUPER_ADMIN_IDS` (numeric owner id), `SECRETS_KEY` (64 random chars, must stay stable or the cached provider key becomes undecryptable) — plus a test channel with a linked discussion group, the bot as admin in both, one **public** active AI model, and a **non-super** second account as the asker | **`PENDING` on the owner** — nothing in the repo can supply these; §4.5 and `docs/QA_LIVE_ACCEPTANCE.md` list the exact steps |
| D-1 | 18 unmerged stale branches, 37 remote branches total | **RESOLVED 2026-11-27** — Phase 10 deleted **33** merged branches under the SHA guard and pruned the stale remote-tracking refs; 16 branches remain (11 Dependabot + 2 legacy fixes + 3 kept by the guard) and are listed in the cleanup evidence |
| D-2 | 19 Windows-only failing deploy tests (4 in `tests/test_deploy_script.py`, 15 in `tests/test_session3_cicd_docker_deploy_docs.py`) degrade the local signal; §5.1 records the exact cause and why CI is unaffected | **RESOLVED 2026-11-27** — PR #109 (`b660bc3`): a shared Git-Bash finder `tests/_shell.py` + `MSYS2_ARG_CONV_EXCL=/app` + LF-only stub output ⇒ those two files now give **97 passed / 0 failed**, and the full suite is **1208 passed / 0 failed** (§4.9.2) |
| D-3 | `tools/migrate_sqlite_to_d1.py` `TABLES` also omits `ai_providers` (Phase 2), so a sqlite → D1 migration loses provider rows the same way it lost the rotation tables in Phase 3 | deferred; Phase 3 fixed only its own two tables |
| D-4 | Dependabot (enabled by #64) opened #68–#78: `python 3.13-slim → 3.15-slim`, `actions/checkout 4 → 7`, `setup-python 5 → 7`, `upload-artifact 4 → 7`, `gitleaks-action 2 → 3`, `appleboy/ssh-action 1.2.2 → 1.2.5`, `python-dateutil 2.9 → 4.0`, `tzlocal → 5.4.5`, `soupsieve → 4.1`, `regex → 2026.11.9`, `ruff 0.14 → 0.17`. Several are **major** bumps that change CI/deploy behaviour | **DEFERRED AFTER RELEASE** by the owner's decision (2026-11-27): the 11 PRs stay open until the release gate is closed, then they are merged one PR at a time with a real CI run each |
| D-5 | `qa_answers` has **no retention or pruning policy**: at-most-once needs the row keyed by `(chat_id, msg_id)` forever, so the table grows with every answered question (one small row per answer; no question or answer text is stored) | deferred; revisit in Phase 10 with a measured row-count/age threshold if volume justifies it — **deleting rows would re-open the at-most-once guarantee**, so any pruning must keep the key |
| D-6 | The Actions cache used by `qa-live` holds the **test** database across runs (key prefix `qa-live-db-v1-`), and that DB can contain an encrypted provider key. `qa_answers.answer` is blanked and the DB is never uploaded as an artifact, but a cached artifact still outlives the run until the cache expires | **PARTIALLY RESOLVED 2026-11-27** — the prefix is now **`qa-live-db-v2-`** (PR #109), so a new cache cannot collide with the old one; the **old `qa-live-db-v1-*` caches must still be deleted by the owner** in the rotation window, and every later run must use `db_mode=fresh` after the rotation (`docs/OPS_SECRET_ROTATION.md` §7) |
| D-8 | Subagent dispatch failed mid-Phase-6 with `Free usage limit reached`; Tasks 3 and 4 were implemented inline by the controller and are recorded as such in their task reports. Task-scoped independent review was therefore unavailable for those two tasks; the independent whole-branch review did run and returned `APPROVED` after the P1 fix | recorded; no product impact |
| D-9 | Phase 7 subagent usage limits recurred (the Task 1 implementer committed then failed to send its final report); the controller supervised inline and verified its commit directly. The **full suite**, not the focused suites, caught four real schema-test regressions (base DDL missing the new column, PH-01 pinned to v12) — all fixed test-first before the code PR | recorded; no product impact |
| D-11 | Phase 8 Task 1's implementer hit `Free usage limit reached` after writing its edits but before committing or reporting. The controller reconstructed the real RED failure, verified the partial diff, applied ruling R4 (a `KeyError` exposed by the stricter URL rule), committed and supervised inline. No product impact; the whole-branch review still ran independently | recorded; no product impact |
| D-7 | **The Phase 4 live acceptance is deferred to Phase 10** (owner decision, 2026-11-20): run `qa-live` once there, in the release pass, with the channel/model registration done inside the runner's bot and a non-super asker. The **earlier Phase 4 attempt** (`44506386031`, on `74abc16`) failed `CHANNEL_NOT_REGISTERED` after the full budget because the channel was never registered (`get_chat: Chat not found`). The same single run also exercises Phase 5's quota guardrails, Phase 6's typing lifecycle, Phase 7's onboarding flow, Phase 8's deeplink button (including whether the rich path honours `reply_markup`) and Phase 9's manual harness (READY, timing fields, clean shutdown). **The Phase 10 attempt (run 46013627630, 2026-11-27) failed `NO_LINKED_GROUP`** after its full budget — the registered channel has no linked discussion group, so no live check was observable (only `preconditions` and `public_model_active`); it is a prerequisite defect, not a code defect | **OPEN** — Phase 10 must record a green report artifact in §5 and only then declare Phase 4 live-verified. The next run needs the linked discussion group (`Channel TechNowVpn (-1004341590425)`, the bot as admin), `duration_minutes=340`, `scenario=duration` and `db_mode=fresh`. Until then Phases 4–9 stay `IN_PROGRESS` and the release is not "live-verified" |
| D-12 | `sources.api_key` (the key of a content source) is stored **in plaintext** in the database, unlike the provider keys which are encrypted with `SECRETS_KEY` | **OPEN — out of scope for Phase 10.** Not changed: encrypting it would change the schema and the product logic. Recorded so that a later phase can pick it up |
| D-13 | The provider keys are **not** reissued — the owner's decision (Q1) limits the rotation to `BOT_TOKEN` and `SECRETS_KEY`, so old copies of the database remain decryptable with the leaked `SECRETS_KEY` | **ACCEPTED RESIDUAL RISK** (owner decision, 2026-11-27). The runbook states it explicitly; a later phase may reissue the provider keys if the owner wants them rotated |
| F-1 | The provider endpoint answers `HTTP 504` and the connection probe times out (observed in the live run: 3× `HTTP 504`, 2× `ai_timeout`, 28× `probe timed out after 15s` — 28 probe-timeout warnings out of 36 `WARNING` lines) | **OPEN OBSERVATION** — recorded, **not fixed** in Phase 10 (changing the timeouts is a behaviour change outside this phase). Code: `newsbot/ai_models.py:310-311`, `newsbot/net_http.py:334`, `newsbot/ai_models.py:607` |
| F-2 | `CVE-2026-124851` in the base image's `libzstd1` package | **RESOLVED 2026-11-27** — PR #108 (`361a10c`) upgrades `zlib1g`/`libzstd1`; the image scan is green in run 46047560145 |
| F-3 | The secret-leak check reported 261 false positives because the super-admin id's digits appeared inside a hex hash | **RESOLVED 2026-11-27** — the check now requires an independent boundary (`(?<![0-9A-Za-z])…(?![0-9A-Za-z])`); the id itself was never printed (`newsbot/logutils.py:258`). Two new tests in PR #109 |

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
10. **A phase is not `COMPLETED` while one of its own PRs is still open.** Every PR of a phase must be
    merged when that phase completes, and every existing PR must have its disposition recorded in
    the phase that owns it (§4.1).
11. **Never leave a security gate disabled.** If a gate must be lifted to merge (see §7.1), restore it
    immediately and verify every field against the saved backup before continuing.