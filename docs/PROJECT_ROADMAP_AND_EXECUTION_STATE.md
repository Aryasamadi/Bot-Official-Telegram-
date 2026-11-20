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
| **Current working branch** | **docs/phase5-delivery-record** (records-only, from `main` @ `1e6e8eb`) |
| **Branch base commit** | `1e6e8ebed85e79554c1302101fc3c6e390530ad4` (PR #92 squash-merge — Phase 5 code) |
| **Last verified commit** | `1e6e8eb` on `main` (PR #92 squash-merge, 2026-11-20) |
| **Last state update** | 2026-11-20 (UTC) |
| **Overall status** | `IN_PROGRESS` — Phases 1–3 are `COMPLETED`; **Phase 4's code is merged and its live acceptance is deferred to Phase 10** (owner, 2026-11-20 — D-7); **Phase 5's code, tests, design and plan are merged**, its live scenario is also owned by the Phase 10 single run |
| **Current phase** | Phase 4 of 10 — `IN_PROGRESS` (code merged; live acceptance deferred to Phase 10, D-7) · **Phase 5**: code complete and merged (D-7 covers its live scenario too) |
| **Current sub-step** | Phase 5 delivery record: code **#92 → `1e6e8eb`**, design/plan **#91 → `7adb0da`**; next phase starts only on explicit owner instruction |
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
**Remaining in Phase 4:** only the live run, now owned by Phase 10 (D-7). The last completed attempt
(`44506386031`, on `74abc16`) ended `exit 2 / CHANNEL_NOT_REGISTERED` after the full budget because the
channel was never registered in the runner's bot (`get_chat` answered `Chat not found`); no code change
is pending from it.
**Phase 5 result:** the guardrails are merged. One atomic `INSERT … SELECT … WHERE … ON CONFLICT DO
NOTHING` on `qa_answers` enforces the per-user cooldown (default 30 s), the per-user daily cap
(default 30) and the per-post daily cap (default 50) at claim time — no counter table, no schema
version bump, and a refused ask never reaches `ai_chat`. Each channel can override all three values
in the admin panel; blank means inherit from env. Super admins are unlimited. Cooldown refusals are
silent; daily-cap refusals get one short throttled notice. Design and plan are in **#91 → `7adb0da`**;
code and 30 new tests in **#92 → `1e6e8eb`**. The Phase 5 live scenario is covered by the single
Phase 10 run together with Phase 4's deferred acceptance (D-7); this phase does **not** claim a live
verification.

**Exact next action:** Phase 6 — `typing` indicator, on explicit owner instruction only.

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
| 6 | Typing lifecycle | Show `typing` while waiting; always stop it; no orphan tasks | `NOT_STARTED` | 4 |
| 7 | Onboarding, home menu, message routing | Language → welcome → "start" → add channel; free plan without an extra button; stable state | `NOT_STARTED` | 1 (4 for reply routing) |
| 8 | Deeplink "back to channel" | Return the user to the exact source post, or explain the platform limit honestly | `NOT_STARTED` | 1 |
| 9 | Temporary GitHub Actions harness | Manual-only workflow: token + super-admin id, READY, run for the target duration, clean shutdown | `NOT_STARTED` | 1 |
| 10 | Hardening, cleanup, release gate | Rotate secrets, dependency/secret scans, remove proven-dead code, full green suite, release runbook | `NOT_STARTED` | 1–9 |

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
`SCHEMA_VERSION = 12`, D1 `TABLES`), `newsbot/qa.py` (pure helpers + claim + link validation + handler),
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
| Workflow | `.github/workflows/qa-live.yml` | **manual only** (`workflow_dispatch`), `contents: read`, `concurrency: qa-live`, `timeout-minutes: 360`, inputs `duration_minutes` (default `350`, bounded 5–355) / `scenario` (`core`\|`full`) / `db_mode` (`reuse`\|`fresh`); pinned action SHAs; artifacts `report.json` + `bot.log` only |
| Runbook | `docs/QA_LIVE_ACCEPTANCE.md` | Persian, owner-facing: prerequisites, the three secrets, the exact in-Telegram order, exit-code meanings, troubleshooting |
| Log line | `newsbot/qa.py` | `qa: ignored chat=… msg=… user=… reason=…` — INFO for `unregistered_channel`/`toggle_off`/`foreign_group`, DEBUG for `not_a_reply`/`not_a_post`, silent when `message`/`user` is absent. Identifiers only; never the question or post text |

**Runner budget.** A GitHub-hosted job is killed at 360 minutes; the workflow sets
`timeout-minutes: 360` and the harness defaults to **350** minutes, so a run always stops itself and
still uploads its evidence. The duration is a ceiling, not a wait: the run exits the moment every
required check is observed.

**Database reuse.** `db_mode=reuse` restores `qa-live-db-v1-*` from the Actions cache so the channel,
provider and model registered once survive into later runs; `db_mode=fresh` starts empty. The cache is
saved with `if: always()` under a per-run key with the `qa-live-db-v1-` prefix, so the newest run wins.
`qa_answers.answer` is blanked before the job ends, the DB is **never** uploaded as an artifact, and a
stable `SECRETS_KEY` is mandatory — a changing key makes the cached provider secret undecryptable.

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
secrets are ready; the last completed attempt (`44506386031`, on `74abc16`) ended with exit `2` /
`CHANNEL_NOT_REGISTERED` after the full budget because the channel was never registered in the runner's
bot. Phase 10 runs this live acceptance **once**, together with the release checks (**D-7**), and records
the green report artifact in §5 before the release is declared.

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

**Not run / unavailable:** the live `qa-live` run itself (Phase 4 acceptance **and** Phase 5's quota
scenario, both deferred to the single Phase 10 run — D-7 / §4.5), coverage percentage (no coverage tool
in dev requirements), live VPS deployment verification, Cloudflare D1 live migration.

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
| B-3 | A 350-minute GitHub-hosted run would leave only ~10 minutes of headroom against the hard 6-hour (360-minute) job cap; the cap cannot be raised | **HANDLED** for `qa-live` — `timeout-minutes: 360` + a default duration of 350 minutes, and the harness exits early once every required check is observed (§4.5). The cap itself is still unraisable |
| B-4 | Whether the bot is added to the channel discussion group (prerequisite for a **real end-to-end** Phase 4 test) | **HANDED TO THE RUNBOOK** — a setup step of `docs/QA_LIVE_ACCEPTANCE.md`, not a code gap. The code stays **fail-closed**: no verified link ⇒ no answer (§4.4) |
| B-5 | A file **outside** this repository (the local `newsbot-deploy-pkg` folder) holds a bot token matching the BotFather format and a 64-char `SECRETS_KEY` in plain text (names/lengths only; values never read) | needs rotation |
| B-6 | ~~licence choice for #64~~ — **RESOLVED 2026-11-14**: the owner chose **proprietary / all rights reserved**; `LICENSE` merged as part of #64 `fa7f25f` | closed |
| B-7 | The three repository secrets for the live run — `BOT_TOKEN` (**test** bot only), `SUPER_ADMIN_IDS` (numeric owner id), `SECRETS_KEY` (64 random chars, must stay stable or the cached provider key becomes undecryptable) — plus a test channel with a linked discussion group, the bot as admin in both, one **public** active AI model, and a **non-super** second account as the asker | **`PENDING` on the owner** — nothing in the repo can supply these; §4.5 and `docs/QA_LIVE_ACCEPTANCE.md` list the exact steps |
| D-1 | 18 unmerged stale branches, 37 remote branches total | deferred to Phase 10 |
| D-2 | 19 Windows-only failing deploy tests (4 in `tests/test_deploy_script.py`, 15 in `tests/test_session3_cicd_docker_deploy_docs.py`) degrade the local signal; §5.1 records the exact cause and why CI is unaffected | deferred to Phase 10 |
| D-3 | `tools/migrate_sqlite_to_d1.py` `TABLES` also omits `ai_providers` (Phase 2), so a sqlite → D1 migration loses provider rows the same way it lost the rotation tables in Phase 3 | deferred; Phase 3 fixed only its own two tables |
| D-4 | Dependabot (enabled by #64) opened #68–#78: `python 3.13-slim → 3.15-slim`, `actions/checkout 4 → 7`, `setup-python 5 → 7`, `upload-artifact 4 → 7`, `gitleaks-action 2 → 3`, `appleboy/ssh-action 1.2.2 → 1.2.5`, `python-dateutil 2.9 → 4.0`, `tzlocal → 5.4.5`, `soupsieve → 4.1`, `regex → 2026.11.9`, `ruff 0.14 → 0.17`. Several are **major** bumps that change CI/deploy behaviour | deferred to Phase 10 (dependency and release hardening), one PR at a time with a real CI run |
| D-5 | `qa_answers` has **no retention or pruning policy**: at-most-once needs the row keyed by `(chat_id, msg_id)` forever, so the table grows with every answered question (one small row per answer; no question or answer text is stored) | deferred; revisit in Phase 10 with a measured row-count/age threshold if volume justifies it — **deleting rows would re-open the at-most-once guarantee**, so any pruning must keep the key |
| D-6 | The Actions cache used by `qa-live` holds the **test** database across runs (key prefix `qa-live-db-v1-`), and that DB can contain an encrypted provider key. `qa_answers.answer` is blanked and the DB is never uploaded as an artifact, but a cached artifact still outlives the run until the cache expires | accepted for the test-only workflow; the stable `SECRETS_KEY` is mandatory (`SECRETS_KEY_REQUIRED` blocks a keyless run). Revisit in Phase 10 alongside B-5's key rotation |
| D-7 | **The Phase 4 live acceptance is deferred to Phase 10** (owner decision, 2026-11-20): run `qa-live` once there, in the release pass, with the channel/model registration done inside the runner's bot and a non-super asker. The last attempt (`44506386031`, on `74abc16`) failed `CHANNEL_NOT_REGISTERED` after the full budget because the channel was never registered (`get_chat: Chat not found`). The same single run also exercises Phase 5's quota guardrails | Phase 10 must record the green report artifact in §5 and only then declare Phase 4 live-verified. Until then Phase 4 stays `IN_PROGRESS`, Phase 5 stays code-complete but not live-verified, and the release is not "live-verified" |

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