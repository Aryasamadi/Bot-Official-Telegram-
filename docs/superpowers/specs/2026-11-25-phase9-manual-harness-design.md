# Phase 9 — Manual GitHub Actions harness (design)

Date: 2026-11-25 · Status: awaiting owner approval · Roadmap row 9 · Findings: AUD-011, AUD-013

Owner decision (2026-11-25, «بله» to the recommended pair): **extend the single existing `qa-live`
workflow** (not a second workflow) and target **~340 active minutes**, reporting the difference
from 350 explicitly. Silent shortening is forbidden; this one is explicit and justified.

## Intent and why

Requirement 3.8 asks for a temporary, manually triggered runner that stays active for ~350 minutes
and shuts down cleanly. AUD-011 shows the arithmetic does not fit a 6-hour GitHub-hosted job if the
clock starts at spawn: 350 active + 3–5 setup + 2–3 cleanup ≈ 355–358 of 360, leaving no margin.
AUD-013 shows the requirement's `SUPER_ADMIN_ID` (singular) does not match the code's
`SUPER_ADMIN_IDS` (plural). The harness must therefore budget honestly, count from readiness rather
than from checkout, and map the singular secret explicitly — without touching production, VPS
credentials, provider keys or bot product code.

This harness is also the single runner that Phase 10's live acceptance (D-7) will use, so there must
be **one** workflow, not two competing ones.

## Goals

1. One `workflow_dispatch`-only job: install, health-check, print `READY`, then either stop early
   when the acceptance checks are all observed (existing `core` / `full` behaviour, used by D-7) or
   stay active for the full **active** budget in the new `duration` scenario (the Phase 9 burn-in
   guarantee). Both paths stop the bot, scrub answers, upload evidence and exit before the hard
   360-minute job cap.
2. The two secure inputs (`TELEGRAM_BOT_TOKEN`, `SUPER_ADMIN_ID`) come from repository/environment
   secrets, are mapped to the names the code reads (`BOT_TOKEN`, `SUPER_ADMIN_IDS`), and are never
   echoed, interpolated into `run:` text, logged or uploaded.
3. `duration_minutes` counts **only from `READY`**; the report states configured active seconds,
   actual active seconds, startup seconds, cleanup seconds and the difference from 350 minutes.
4. The report carries an explicit real / simulated / needs-credentials boundary table.
5. Existing `qa-live` behaviour (scenarios, DB reuse/fresh, artifact uploads, SHA-pinned actions,
   minimal permissions) keeps working; only the stated gaps are closed.

## Non-goals

- Permanent hosting, self-hosted runners, or any change to the 6-hour platform cap.
- Publishing to a real channel, rotating production secrets, or reading the production DB/VPS.
- Supplying provider/AI keys or changing bot product code for the test.
- Silently reducing the duration to "make it fit" — every number is reported.

## Anti-goals

- A job killed at 360 minutes without clean shutdown or evidence upload.
- A token or super-admin id in logs, report JSON, artifact names or commit messages.
- Two harnesses whose reports disagree (D-7 depends on one artifact).

## Current state (facts the design rests on)

| Where | What exists today |
|---|---|
| `.github/workflows/qa-live.yml:5-37` | `workflow_dispatch` with `duration_minutes` default `"350"`, `scenario`, `db_mode`; `permissions: contents: read`; `concurrency` group; `timeout-minutes: 360` |
| `qa-live.yml:56-71` | env `BOT_TOKEN` / `SUPER_ADMIN_IDS` / `SECRETS_KEY` from secrets, then `tools/qa_live_acceptance.py --supervise --duration …` |
| `qa-live.yml:72-88` | artifacts: `report.json` + `bot.log`, `if: always()`; DB cached but never uploaded |
| `newsbot/config.py:67-78` | already accepts `TELEGRAM_BOT_TOKEN` as a fallback for `BOT_TOKEN`; only `SUPER_ADMIN_IDS` (plural) is parsed and validated |
| `tools/qa_live_acceptance.py:544-546` | `READY_MARKER = "در حال اجرا"`; readiness loop is capped at 60 × 2 s |
| `qa_live_acceptance.py:686-779` | `t0 = clock()` is taken **before** spawn/readiness; `report` has `started_at`, `duration_s` (configured) but no `ready_at`, `active_duration_s` or delta from 350 |
| `qa_live_acceptance.py:759-779` | `finally` stops the process, blanks answer text, writes the report; `redactions = ["BOT_TOKEN", "SECRETS_KEY"]` |
| `tests/test_phase4_live_acceptance.py:361-422, 451-505` | unit guards for the supervisor and workflow (manual-only, SHA pins, default `"350"`, secret names, no DB artifact) |

The old supervisor already has the right *shape* (bounded readiness, early stop when all checks are
observed, guaranteed `finally` cleanup, secret-leak check). The work is to make its clock honest,
add a hold-to-budget mode without breaking the early-exit contract D-7 uses, and make its report
explicit — not to rewrite it.

## Approaches (owner picked A + ~340 on 2026-11-25)

**A — strengthen the existing single `qa-live` workflow (chosen).** Add secret-backed secure inputs
with backward-compatible fallbacks, validate the singular id without echoing, count active time from
`READY`, stop at the configured budget, and extend the report/runbook with numbers and a boundary
table. One runner, one artifact, no duplicated supervisor — D-7 stays coherent.

**B — add a second dedicated manual workflow.** Would leave `qa-live.yml` untouched but duplicate
the install/supervise/artifact path and produce two different report schemas; the Phase 10 artifact
would then be ambiguous. Rejected.

**C — self-hosted runner (5-day cap).** Explicitly violates "GitHub-hosted" and adds VPS/secret
exposure. Rejected by the audit itself.

## Design

### 1. Workflow inputs and secure mapping (`.github/workflows/qa-live.yml`)

- Keep `workflow_dispatch` as the **only** trigger; keep `permissions: contents: read`,
  `concurrency` group and `timeout-minutes: 360`.
- `duration_minutes` default `"340"`; description states "active minutes after READY, 5–340".
  A validation step rejects values outside 5–340 **before install** and prints only the number's
  validity, never a secret.
- `scenario` gains a third choice `duration` (burn-in); `core` remains the default so every D-7
  dispatch keeps today's early-exit semantics. Only `scenario=duration` holds to the budget.
- Env mapping (all via `env:`, never inside `run:` text):
  - `BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN || secrets.BOT_TOKEN }}`
  - `SUPER_ADMIN_IDS: ${{ secrets.SUPER_ADMIN_ID || secrets.SUPER_ADMIN_IDS }}`
  - `SECRETS_KEY` unchanged.
- A "Validate secure inputs" step checks: token present, and the resolved super id is a non-empty
  **list** of decimal ids in the syntax the repo already accepts (comma / space / semicolon
  separated, e.g. `123456789,987654321`). It normalizes to that same string for `SUPER_ADMIN_IDS`;
  on failure it exits with `TOKEN_MISSING` / `SUPER_ID_INVALID` and prints no values. No secret in an
  `if:` expression.
- The runner still passes `--duration "${{ inputs.duration_minutes }}"`; artifacts, DB cache and
  SHA-pinned actions are unchanged.

### 2. Honest clock (`tools/qa_live_acceptance.py::supervise`)

- Keep `READY_ATTEMPTS = 60`, `READY_INTERVAL_S = 2` and the existing readiness detection.
- Capture `ready_t = clock()` **when the marker is found**; the loop's timeout condition becomes
  `clock() - ready_t >= cfg.duration_s`. The readiness wait is reported separately and no longer
  consumes the active budget.
- Record `stop_t = clock()` immediately after `_stop(proc)` in `finally`, and `report_t = clock()`
  just before `write_report`. `cleanup_duration_s = report_t - stop_t` covers the tool-side cleanup
  (log read + answer scrub + report write); the workflow's later artifact upload is outside this
  measurement and is named as such in the runbook.
- Add to the report: `ready_at`, `stopped_at`, `startup_duration_s = ready_t - t0`,
  `active_duration_s = stop_t - ready_t`, `cleanup_duration_s`, `configured_duration_s`,
  `delta_vs_350_s = (active_duration_s - 350*60)`, `duration_basis: "ready_to_shutdown"`,
  `scenario_mode: "hold_to_budget" | "early_exit"`.
- In `scenario=duration`, "all checks observed" does **not** exit the loop; the loop ends only at
  the active budget or an error. `core` / `full` keep the existing early exit.
- Keep exit codes, precondition handling, `blank_answers`, `write_report` and the token/key leak
  check unchanged. `redactions` becomes `["BOT_TOKEN", "TELEGRAM_BOT_TOKEN", "SUPER_ADMIN_ID",
  "SUPER_ADMIN_IDS", "SECRETS_KEY"]` — names only. Additionally, super-id values with **six or more
  digits** are added to the value-based leak check; shorter ids are indistinguishable from chat ids
  in QA logs and are therefore documented, not value-checked.
- `MAX_DURATION_MIN` stays 355 for direct tool use; the workflow's own guard is the 340 cap.

### 3. Boundary table in the report

Add `boundaries` (a list of `{"surface", "status", "note"}`) with fixed rows:

| Surface | Status | Note |
|---|---|---|
| Telegram bot / READY / bot log | `real` | real token, real client traffic |
| Local temporary SQLite + cached test DB | `real` | throwaway data, never uploaded |
| AI / Provider calls | `real` when `provider_credentials_present` is true, else `simulated` | the workflow supplies no provider keys, so a normal Phase 9 dispatch is `simulated`; a D-7 dispatch with explicit provider credentials reports `real` |
| Publishing to a real channel | `not_performed` | needs explicit owner authorization |
| Job 6-hour cap | `real` | platform limit; `timeout-minutes: 360` |

`provider_credentials_present` is a boolean in the report, derived from named env flags only
(never from key values). The runner sets it false; a D-7 dispatch may override it true when provider
credentials are explicitly supplied. The report carries the table verbatim; the runbook mirrors it
in Markdown.

### 4. Runbook (`docs/QA_LIVE_ACCEPTANCE.md`)

Add a Phase 9 section: how to dispatch, the 340 default and the 350 comparison, what READY means,
where the artifact is, how to read the six timing fields, the boundary table, and the rollback
(delete/disable the workflow — no data migration). Keep the existing Phase 4 runbook intact.

### 5. Decisions taken by the builder (owner may override)

- **D9-1** One workflow, not two (owner: «بله» to recommendation A).
- **D9-2** Target 340 active minutes; the 10-minute difference from 350 is stated as a number in the
  report and runbook, never hidden (owner: «بله» to recommendation B).
- **D9-3** Secrets remain secret-backed inputs with backward-compatible aliases; no token/id is ever
  typed into a dispatch form.
- **D9-4** `timeout-minutes` stays 360: 340 active + ~5 setup + ~5 cleanup/report/upload ≈ 350,
  leaving ~10 minutes of margin.
- **D9-5** No product-code change; only the harness tool, the workflow, its tests and the runbook.
- **D9-6** `duration_minutes` is a **cap** for `core`/`full` (D-7 keeps its early exit) and a **target**
  for the new `duration` scenario (Phase 9 burn-in). Cost if wrong: a D-7 run would sit idle for
  hours; keeping the default `core` avoids that.
- **D9-7** The singular super id may be a list in the repo's existing separator syntax; validation
  accepts and normalizes it rather than forcing one integer. Cost if wrong: legacy plural secrets
  with two ids would fail closed.
- **D9-8** `cleanup_duration_s` ends at the report write; artifact upload is documented as a separate
  workflow-side step, not silently counted.
- **D9-9** Super-id values of six or more digits join the leak check; shorter ones would produce
  false positives against ordinary chat ids in QA logs.
- **D9-10** The Provider boundary row is derived from a boolean flag, so the same workflow can honestly
  report `simulated` for Phase 9 and `real` for a credentialed D-7 dispatch.

## Test plan

All new and updated tests go into `tests/test_phase4_live_acceptance.py` (the harness's existing
home):

1. `supervise` records readiness-based timing: with a fake clock, startup sleeps do **not** consume
   the active budget; `active_duration_s`, `startup_duration_s` and `cleanup_duration_s` match the
   injected clock; `duration_basis` and `scenario_mode` are set.
2. In `scenario=duration`, all-checks-observed does **not** end the loop — the run holds to the
   configured active budget; in `core`/`full` it still exits early and reports `scenario_mode:
   early_exit`.
3. The timeout stops after the configured active seconds **from READY**, not from spawn;
   `BOT_NOT_READY`, `BAD_DURATION` and channel-not-registered codes are unchanged.
4. Report contains all timing fields, `boundaries` with the five surfaces, a boolean
   `provider_credentials_present`, and no token/key value, no six-plus-digit super id and no answer
   text (JSON dump assertion).
5. Workflow text: `workflow_dispatch` only, SHA-pinned actions, `contents: read`, `timeout-minutes:
   360`, default `"340"`, `duration` added to `scenario` choices while `core` stays the default,
   both secret aliases in `env:` only, no `secrets.` inside `if:`, validation rejects >340/<5 and a
   malformed super-id list but accepts a two-id list, DB still absent from artifacts.
6. Runbook contains the boundary table, the 340 vs 350 explanation and the `duration` scenario.

## Acceptance mapping

| Audit acceptance item | Covered by |
|---|---|
| Manual workflow runs; READY and health recorded | existing READY test + tests 1–2 |
| Clean shutdown | existing `finally`/`_stop` guards + tests 1, 3 |
| No secret in logs | existing leak guard + test 4 + workflow test 5 |
| Active duration after READY reported with a number vs 350 | tests 1, 2, 4 |
| Real / simulated / needs-credentials table | tests 4, 6 |
| Two secure inputs mapped to code's names | workflow test 5 |
| `timeout-minutes` compatible with 6 h | workflow test 5 |

**No live run is claimed in this phase.** Actually dispatching the workflow consumes real Telegram
credentials and a 340-minute job; it stays an explicit owner action and its artifact is recorded in
the Phase 10 release run (D-7). This phase verifies the harness by unit/static tests only.

## Migration / rollback

- No schema, no data migration. Rollback = delete or disable the workflow and revert the tool/runbook;
  cached test DBs are temporary and never contain production data.
- No new secret creation is required: existing `BOT_TOKEN`/`SUPER_ADMIN_IDS` continue to work; the
  singular aliases are optional and preferred.

## Risks

- **Medium.** A slow install could still eat the margin; mitigated by 360-minute job timeout, 340
  active target, bounded readiness loop and a report that exposes actual startup/cleanup numbers.
- Secret aliases could silently fall back to the old names; mitigated by presence/format validation
  that fails closed and prints only a code.
- `qa-live` is used by D-7; mitigated by keeping its scenario/DB/artifact contract and by existing
  workflow guards being updated rather than deleted.

## What is left to the builder

- Exact shell shape of the validation step (must not echo values).
- Whether the timing fields are floats or rounded integers (recommend rounded seconds).
- Placement of the new runbook section (recommend at the end, after the Phase 4 material).
