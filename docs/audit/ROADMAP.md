# ROADMAP — Bot-Official-Telegram- Remediation Plan

**Baseline:** commit `08bc87655dc910a98076acd749ddf8f3e6bdca2c` (branch `main`) · **180 findings, all OPEN**
**Status of this document:** the owner controls execution. **Nothing in this roadmap may be started without
an explicit owner instruction naming the phase ID.**

## Ordering principle

Phases are ordered by (1) risk of harm to users/money, (2) security and data integrity, (3) financial
correctness, (4) production availability, (5) dependency relationships, (6) probability, (7) cost of a
safe fix. Each phase is **self-contained** and ends in a mergeable, revertable state.

> **Release-order warning.** `PH-04` (deployment identity and health gate) must be merged **before** the
> first release that carries any fix from `PH-01`–`PH-03`. Today a release can abort *after* replacing the
> code on disk while the old code keeps running (OPS-026), and the unit name in the deploy chain is wrong
> (OPS-001). Fixing the money path without fixing the deploy path risks shipping fixes that never run.

---

## PH-00 — Production verification (read-only)

| Field | Value |
|---|---|
| **Objective** | Establish the *real* production state before any code change, so that every production-facing finding is graded against reality rather than against the repository. Touches nothing. |
| **Findings addressed** | Verifies / grades: OPS-001, OPS-002, OPS-007, OPS-008, OPS-009, OPS-011, OPS-012, OPS-025, OPS-030, FIN-001 (exposure), FIN-009 (marker growth), DB-009 (journal mode) |
| **Priority** | P0 |
| **Dependencies** | None (must be done first) |
| **Risk** | Very low — strictly read-only commands on the VPS. **Do not restart, stop, reconfigure, or migrate anything.** |
| **Complexity** | Low (a checklist of read-only commands) |
| **Required tests** | None (read-only inspection) |
| **Acceptance criteria** | A written production-state record containing: the actual `WorkingDirectory`, `ExecStart`, `EnvironmentFile`, `User` and unit name of the running service; the running commit SHA (from the app and from `.deployed_sha`); the live Python version and `pip freeze` output; the effective `.env` **key names only** (never values) and the file's owner/mode; the DB path, size, journal mode and `PRAGMA integrity_check` result; the row counts of `users`, `plans`, `pay_requests`, `referrals`, `credit_log`, `articles`, `posted`; the list of files in the backup directories with sizes; the last 200 lines of the service journal; and the output of `systemctl show <unit>` (Restart policy, WatchdogSec, Type, NRestarts). |
| **Rollback strategy** | Not applicable (no mutation). If any command in the checklist is not read-only, it is excluded and noted. |
| **Status** | `COMPLETE_WITH_BLOCKED_PRODUCTION_READ` (2026-10-20) — the **repository-side** verification was completed (FIN-001 reproduced, OPS-001 re-verified, suite re-run 455 passed, audit documents validated, OPS-028 + DOC-001 fixed with regression tests). The **production read** could not be executed: this environment has no `~/.ssh`, no ssh config, no `gh` CLI and no VPS environment variables. **Re-run from a host that has the key.** |
| **Owner approval required** | **YES** |

---

## PH-01 — Financial correctness and money-path integrity  ← recommended first remediation phase

> **Redesigned 2026-10-20 after the owner's answers to Q-1…Q-6** (`DECISIONS.md` §2 and §2b). The decisions
> turn this phase from an arithmetic patch into a **schema + money-path** phase. The scope, risk, tests and
> acceptance criteria below reflect them.

| Field | Value |
|---|---|
| **Objective** | Give the wallet a currency (per-currency balances, no conversion — Q-1c); make the approval re-derive the wallet share and refuse on a material difference while keeping the displayed cash price (Q-2a); make the displayed price equal the charged price; make the ledger authoritative; bound the tariff; add a refund path; and keep the referral promise exactly as it is (Q-3a). |
| **Findings addressed** | FIN-001, FIN-002, FIN-003, FIN-004, FIN-005, FIN-006, FIN-007, FIN-008, FIN-009, FIN-010, FIN-012, FIN-013, FIN-015, FIN-019, FIN-021, FIN-023, FIN-024, UI-017, TEST-005, TEST-006, OBS-005, OBS-007, OBS-014 |
| **Owner decisions applied** | Q-1 → (c) · Q-2 → (a) · Q-3 → (a) · Q-4 → (c) · Q-5 → (c, PH-02) · Q-6 → (c, PH-04) |
| **Priority** | P0 (contains the only CRITICAL finding, now reproduced) |
| **Dependencies** | `PH-00` production read (to size FIN-001's exposure and to know the live tariffs) · the six owner decisions (**now given**, 2026-10-20) |
| **Risk** | **High — and higher than originally assessed, because Q-1c is a schema change.** The wallet becomes a new currency-tagged table with an additive, idempotent migration; `SCHEMA_VERSION` is bumped; existing balances are **frozen** under `cur='legacy'` and are **not** converted, so no money can be silently re-denominated. Safeguards: (a) the migration is **additive only** — no column drop, no data rewrite, `users.credit` is retired by *not reading it*, not by deleting it; (b) the legacy allocation is a **separate, explicitly-authorised one-off script** with a dry-run and a before/after report, never part of the code change; (c) the migration is rehearsed against a **copy** of the production database before it is applied to production; (d) every step lands test-first. The approval contract changes (Q-2a) — `tests/test_payment_atomicity.py` currently *pins* the old contract and must be updated deliberately, in the same commit as the behaviour change. |
| **Complexity** | High |
| **Required tests** | New: wallet ledger reconciliation property test (TEST-005); cross-currency wallet test — a reward in one currency is **not** spendable against another (FIN-001); legacy-balance freeze test (existing `users.credit` lands in `wallet` with `cur='legacy'` and is not spendable); frozen-cash + re-derived wallet-share test (FIN-002); material-difference refusal test and below-threshold acceptance test (FIN-002, Q-2a); two-pending-requests test (FIN-003); displayed price == charged price in all four quote paths (FIN-004, Q-4c); free-plan-claim-while-paid test (FIN-005); discount re-validation at approval (FIN-008); per-currency referral cap and atomicity test (FIN-010, Q-1c/Q-3a); finite-tariff bound test (FIN-012); refund round-trip test (FIN-013); negotiable-price plan is not online-purchasable and never produces a receipt without a numeric amount (UI-017, Q-4c); the dead `REFERRAL_MAX_PER_USER` knob is gone (FIN-023, Q-3a). Update: `tests/test_payment_atomicity.py:84` and `:223-232` (they currently *pin* the defective behaviour) and `tests/test_phase11_limits_boundaries.py`. |
| **Acceptance criteria** | (1) A balance in one currency **cannot** be spent against a tariff of another currency — proven by a test. (2) The amount charged equals the amount displayed to the customer in every quote path (no wallet, with wallet, with discount, with both) — proven by a test. (3) For every applied payment: the wallet equals the sum of its receipts, and the receipt's stored balance equals the wallet — proven by a property test. (4) Two simultaneous approvals of two different pending requests result in exactly one active plan and no double wallet debit. (5) A discount deactivated between request and approval is **not** honoured. (6) `inf`/`1e400`/negative/huge tariffs are rejected at input and cannot reach a wallet. (7) A refund restores the wallet, writes a receipt, and leaves the ledger reconciled. (8) A material change in the wallet between request and approval **refuses** the approval and offers a re-quote; a trivial change does not. (9) A plan with no numeric tariff is not purchasable online and states why. (10) The referral reward stays **per purchase** at the same rate — unchanged. (11) `SCHEMA_VERSION` incremented, the migration is idempotent, and it runs unchanged against a copy of the production database with a byte-identical result. (12) The full suite is green. |
| **Rollback strategy** | The migration is additive (a new table and new columns only). Rollback = revert the code and leave the new table unused; the frozen `legacy` balances are untouched by any rollback. Any movement of a legacy balance into a real currency is performed only by the **separate authorised script**, which has its own backup and a reverse operation. The approval-contract change is reverted by reverting its commit together with the test update — the two must move as one. |
| **Status** | `IMPLEMENTED_AWAITING_MERGE` (2026-10-20) — branch `feature/ph-01-money-integrity`, PR #22. 549 tests green overall, 17/17 `final_verification`, migration rehearsed on a copy of a real v7 database. **Not merged, not deployed.** |
| **Owner approval required** | **YES** (merge) |

---

## PH-02 — Untrusted egress closure (SSRF, prompt injection, credential handling)

| Field | Value |
|---|---|
| **Objective** | Make every outbound request derived from user input validated on every hop, and make the prompt-injection fence unbreakable. |
| **Findings addressed** | SEC-001, SEC-002, SEC-007, SEC-010, NET-001, NET-002, NET-003, NET-004, SEC-016 |
| **Owner decisions applied** | Q-5 → (c): `ALLOW_PRIVATE_MODEL_HOSTS` becomes a `host:port` **allowlist, empty by default**, with `=all` as a documented escape hatch (for local Ollama / LM Studio). Q-1c and Q-2a also land here: the AI-provider request path must not carry a wallet or pricing behaviour, but it must be the *same* guarded requester. |
| **Priority** | P0 (SEC-001 is HIGH/P1) |
| **Dependencies** | None technically. Coordinate with `PH-01` (FIN-001 mentions the AI path only tangentially). |
| **Risk** | Medium — changing the HTTP client can break AI providers that legitimately redirect or use a self-signed/private host. Mitigations: keep `follow_redirects=False` only for the provider client (the fetch path already handles redirects manually); add an explicit allowlist rather than removing the private-host check; never change the *source/media* fetch path, which is already correct. |
| **Complexity** | Medium |
| **Required tests** | New: a `base_url` that 302s to `169.254.169.254` must not reach it; the `Authorization`/`x-api-key`/`apikey` headers must be absent on a cross-origin hop; a resolving-then-changing DNS server must be rejected; a prompt whose URL contains `</article_data>` must yield exactly one boundary pair; `ALLOW_PRIVATE_MODEL_HOSTS` as an allowlist admits only the listed `host:port`. Update: `tests/test_security.py` (73 cases) must stay green, and `docs/SECURITY.md` sections 1 and 5 must be updated to list the AI path. |
| **Acceptance criteria** | (1) No outbound request whose URL derives from user input is issued without a `public_url_strict` check of that exact host, on **every** redirect hop. (2) No credential header crosses a host change. (3) `ALLOW_PRIVATE_MODEL_HOSTS` is an allowlist, not a blanket off-switch. (4) The article URL cannot close the `<article_data>` fence. (5) `docs/SECURITY.md` describes the shipped egress paths. |
| **Rollback strategy** | The provider client is separate from the existing shared client, so the change can be reverted by restoring one import. The `ALLOW_PRIVATE_MODEL_HOSTS=all` escape hatch preserves existing local-provider deployments. |
| **Status** | `IMPLEMENTED_AWAITING_MERGE` (2026-10-20) — branch `feature/ph-02-egress-closure`, PR #23. 522 tests green, 39 new. **Not merged.** |
| **Owner approval required** | **YES** |

---

## PH-03 — Payment and receipt notification durability

| Field | Value |
|---|---|
| **Objective** | Ensure a paid customer is never silently lost: the receipt always reaches an administrator, the receipt form is never silently discarded, and every notification is durable and retried. |
| **Findings addressed** | UI-006, UI-009, OBS-011, OBS-012, OBS-013, FIN-021, DB-008, PIPE-021 |
| **Priority** | P0 (direct revenue loss) |
| **Dependencies** | `PH-01` (the approval and ledger semantics should be settled first); `PH-06` supplies the durable-notification substrate. **May be merged after PH-01 and before PH-06 if a minimal version is wanted** (the `notified_at` marker and the caption fix are independent). |
| **Risk** | Medium — adds persistence to the notification path. A retry loop that is too aggressive could spam super admins; a persisted queue that is never drained could grow. Mitigations: bounded attempts with exponential backoff and a dead-letter state; an explicit "un-notified pending payments" counter in `/health`. |
| **Complexity** | Medium |
| **Required tests** | New: a 400-character receipt note yields valid HTML <= 1024 and reaches the super admin; a failing `copy_message` marks the request un-notified and the scheduler re-notifies it; a receipt-kind `await` state survives an unrelated button press; a failed user notification is retried; a full super-notification queue lands in a dead-letter table rather than being dropped. |
| **Acceptance criteria** | (1) No receipt can be lost to a caption-length or formatting error. (2) Every `pay_requests` row reaches an administrator, or is visible as pending-un-notified in `/health`. (3) Tapping another button cannot discard an in-flight receipt form. (4) A failed customer notification is retried and observable. (5) The super-notification queue is durable with a dead-letter state. |
| **Rollback strategy** | Additive columns (`notified_at`, `notify_attempts`) and one new table. Reverting the code leaves the columns unused; no data is rewritten. |
| **Status** | `OPEN` |
| **Owner approval required** | **YES** |

---

## PH-04 — Deployment identity, health gate and recovery integrity

| Field | Value |
|---|---|
| **Objective** | Make the deployment actually deploy the intended project, prove health correctly, roll back completely, and document one safe restore procedure. |
| **Findings addressed** | OPS-001, OPS-002, OPS-003, OPS-004, OPS-006, OPS-007, OPS-008, OPS-009, OPS-010, OPS-011, OPS-012, OPS-013, OPS-014, OPS-015, OPS-022, OPS-025, OPS-026, OPS-027, OPS-028, OPS-029, OPS-030, SEC-012, SEC-013, OBS-014 |
| **Owner decisions applied** | Q-6 → (c): **read the host first, then decide.** The rename is blocked on the PH-00 production read. If the rename is later approved, the old unit name (`bot-ai-automatic`) must be kept as an **alias for one release** so the rollback path (restart the previous unit) stays intact. |
| **Priority** | P0 (OPS-001 and OPS-002 are P0) |
| **Dependencies** | `PH-00` (the real unit name and path must be read before renaming). **Must be merged before the first release of PH-01–PH-03.** |
| **Risk** | **High** — this phase changes the release mechanism itself; a mistake can leave the service down. Mitigations: every change is rehearsed by `tests/test_deploy_script.py` in its sandbox before it is merged; the script keeps its `set -euo pipefail` discipline; the new health gate must be **stricter, not looser** than today's (it may only reject more, never accept more, unless a false positive is proven). The service name must be changeable by an explicit `SERVICE=` override so a mis-detection cannot brick a working host. |
| **Complexity** | High |
| **Required tests** | Extend `tests/test_deploy_script.py`: (1) the script defaults equal the values in the runbook and README; (2) a `systemctl restart` failure restores HEAD to `$prev`; (3) a failing gate produces a second `pip install` after the reset (OPS-006); (4) a journal entry containing a traceback **before** the restart epoch does not fail the gate, but one **after** it does (OPS-003); (5) a delayed `READY=1` within the new budget passes, and a `Type=simple` service is rejected as a configuration error; (6) the same SHA deployed twice still performs a backup and a health check (OPS-025); (7) `deploy/deploy.sh` is executable in the git index (OPS-028). Add: a `sqlite3.backup` restore round-trip **over a WAL database with sidecars present** (OPS-011) and a schema-newer-than-build `--check` failure with an actionable message (OPS-012). |
| **Acceptance criteria** | (1) `newsbot.service` / `/opt/bot-official-telegram` are the single source of truth for the service identity, and the base unit file ships in the repository. (2) The health gate waits for `READY=1` within a budget derived from `TimeoutStartSec` and only inspects the post-restart journal window. (3) Any failure after the sync — including a failed `systemctl restart` — restores both the code **and** the dependencies and restarts the previous version. (4) The deploy script comes from `TARGET_SHA`. (5) A manual dispatch cannot bypass CI without an explicit, named break-glass path. (6) Backups are verified on creation and copied off-host. (7) Exactly one documented restore procedure exists, and it removes the `-wal`/`-shm` sidecars before restoring. (8) The deploy script records the SHA actually running and fails on mismatch. (9) `docs/DEPLOY_RUNBOOK.md`, `DEPLOY-VPS-UPDATE.md` and `README.md` agree with the code. |
| **Already done in PH-00** | **OPS-028** — `deploy/deploy.sh` is now committed executable (`100755`) and enforced by `tests/test_repo_integrity.py::test_deploy_script_is_executable_in_git_index`. The remaining items in this phase are **blocked on the PH-00 production read**: the `bot-ai-automatic` -> `newsbot` rename must not be applied blind, because a wrong rename makes a working service unstartable. |
| **Rollback strategy** | The deploy script is versioned; the previous version is recoverable from git. A rename of the service identity must be accompanied by a documented manual rollback that restarts the old unit name. The health-gate tightening can be reverted independently of the identity rename. |
| **Status** | `PARTIALLY_IMPLEMENTED_AWAITING_MERGE` (2026-10-20) — branch `feature/ph-04-deploy-safety`, PR #24. 542 tests green, 20 new. The host-dependent half (the service identity) stays blocked on the production read. **Not merged.** |
| **Owner approval required** | **YES** |

---

## PH-05 — Telegram UI/UX rendering and journey integrity

| Field | Value |
|---|---|
| **Objective** | Make every screen render correctly at Telegram's limits, make every destructive action confirmed and audited, and make the customer journey discoverable for a non-technical user. |
| **Findings addressed** | UI-001, UI-002, UI-003, UI-004, UI-005, UI-007, UI-008, UI-010, UI-011, UI-012, UI-013, UI-014, UI-015, UI-016, UI-017, UI-018, UI-019, UI-020, UI-021, UI-022, UI-023, UI-024, UI-025, UI-026, SEC-009, SEC-014, SEC-015, SEC-016, FIN-020 |
| **Priority** | P1 (UI-005, UI-008 are HIGH) |
| **Dependencies** | None. `PH-03` overlaps UI-006/UI-009 — those two belong to PH-03. |
| **Risk** | Low — mostly presentation. The one behavioural change is adding a home-screen button row and `u:ref` (UI-014), which alters what a customer sees first. |
| **Complexity** | Medium |
| **Required tests** | New: no rendered user string may match `\{[a-z_]+\}`; a plan with a 100-character name must still render; `split_html` never emits an empty or over-limit chunk (property test over 0..20 000 characters); every `tr(lang,"X")` literal exists in `TXT`; fa/en placeholder sets are identical for every key; every `*2` execute route writes a `log_event`; `s:auto`, `s:plan_free` and `s:uassign` require confirmation; every screen reachable from the router is reachable from the home screen; `docs/PERMISSIONS.md` section 8 lists exactly the real two-phase routes. |
| **Acceptance criteria** | (1) No user-visible message contains an unresolved placeholder. (2) No screen can be rejected by Telegram for label or markup length. (3) Every destructive and every financial super-admin action is two-phase and audited. (4) The customer home screen exposes support, invite, help and language. (5) The fa/en dictionaries are structurally and semantically in parity. (6) `docs/PERMISSIONS.md` matches the code. |
| **Rollback strategy** | Presentation-only; revert per file. The new home-screen buttons can be reverted without a data migration. |
| **Status** | `OPEN` |
| **Owner approval required** | **YES** |

---

## PH-06 — Observability and alerting durability

| Field | Value |
|---|---|
| **Objective** | Make the bot's health, failures and money movements observable to the operator, with alerting that cannot be silently lost. |
| **Findings addressed** | OBS-001, OBS-002, OBS-003, OBS-004, OBS-006, OBS-008, OBS-009, OBS-010, OBS-011, OBS-012, OBS-013, OBS-014, OBS-015, OBS-016, SEC-011, DB-008 |
| **Priority** | P1 (OBS-002, OBS-004, OBS-007, OBS-011, OBS-013 are HIGH) |
| **Dependencies** | `PH-03` shares the durable-notification substrate — **do PH-03's persistence design first and reuse it here** to avoid two queue mechanisms. |
| **Risk** | Medium — a new metrics/alerting surface can be noisy and generate alert fatigue. Mitigations: begin with the operator-visible `/health` panel and the journal, not with a full Prometheus deployment; every alert has a threshold and a suppression rule. |
| **Complexity** | Medium |
| **Required tests** | New: the redaction factory fails **closed** (a raising `redact()` must not leak the raw record); scheduler errors carry a traceback; `/health` renders `unknown`/`degraded` for a failed probe instead of `0`; `last_successful_publish` is exposed and goes stale correctly; a super-notification queue overflow lands in a dead-letter table; the super-admin ID is not written to the journal. |
| **Acceptance criteria** | (1) `/health` distinguishes `ok` / `degraded` / `unknown` and reports the last successful publish. (2) Every scheduler and cycle error carries a traceback. (3) No alert is dropped without a dead-letter record. (4) A "green but idle" bot is detectable. (5) The financial audit trail can answer "who paid what, when, and what was credited" (jointly with PH-01/OBS-007). (6) The journal contains no secret and no raw super-admin ID. |
| **Rollback strategy** | Additive. The `/health` panel can be extended or reverted independently; the dead-letter table can be left empty and ignored. |
| **Status** | `OPEN` |
| **Owner approval required** | **YES** |

---

## PH-07 — Event-loop correctness and performance

| Field | Value |
|---|---|
| **Objective** | Remove the blocking database and process calls from the event loop, bound the lock-pruning cost, and establish a reproducible performance baseline for the pipeline. |
| **Findings addressed** | PERF-001, PERF-002, PERF-003, PERF-004, PERF-005, PERF-006, PERF-007, DB-006, DB-009, PIPE-016, NET-005, NET-008 |
| **Priority** | P2 |
| **Dependencies** | `PH-01` (which adds DB work in the approval path) and `PH-08` (which changes the AI path). Best scheduled after both. |
| **Risk** | Medium — moving calls onto the 4-worker DB pool can shift the bottleneck rather than remove it. Mitigations: the pool size becomes configurable; the pool is monitored; the change is measured before and after with the new baseline from PERF-004. |
| **Complexity** | Medium |
| **Required tests** | New: no `q()` executes on the event-loop thread (a static AST check plus a runtime assertion); a busy retry does not block the loop; 300 channels keep the queries per `ch_lock()` bounded; a long `archive_backup` does not delay an `arun` call; the Telegram client's pool limits are asserted. Extend `tools/benchmark.py` with a concurrent cycle simulation and a mocked-HTTP pipeline; commit the raw JSON and state the machine. |
| **Acceptance criteria** | (1) Zero synchronous DB calls remain in `async def` bodies. (2) The lock prune is amortised and bounded. (3) A reproducible baseline exists for the discovery->publish pipeline, not only for SQLite inserts. (4) The Telegram connection pool is explicitly sized. (5) The published `docs/PERFORMANCE.md` numbers are re-measured and state their machine. |
| **Rollback strategy** | The pool size is a config value and can be reverted without a code change; the AST check will catch any regression. |
| **Status** | `OPEN` |
| **Owner approval required** | **YES** |

---

## PH-08 — Content pipeline, AI budgets and cost control

| Field | Value |
|---|---|
| **Objective** | Make the AI budget a real cap, make the cache correct, bound the extraction time, and canonicalise URLs so the same article is not fetched, generated and published twice. |
| **Findings addressed** | PIPE-001, PIPE-002, PIPE-003, PIPE-004, PIPE-005, PIPE-006, PIPE-007, PIPE-008, PIPE-009, PIPE-010, PIPE-011, PIPE-012, PIPE-013, PIPE-014, PIPE-015, PIPE-019, PIPE-020, PIPE-022, NET-006, FIN-012 |
| **Priority** | P2 (PIPE-001 is HIGH) |
| **Dependencies** | `PH-02` (the provider request path is changed there — the AI client should be settled first). `PH-09` (the URL canonicalisation changes article hashes, which interacts with the retention and dedup work). |
| **Risk** | **Medium-high** — changing the cache key invalidates the existing AI cache (a one-off cost); changing the URL canonicalisation changes article identities and therefore the dedup hashes (a migration concern, see below). The AI cap semantics change the meaning of `usage.ai`. |
| **Complexity** | Medium |
| **Required tests** | New: a 13th chunk call returns `ai_budget` and the call counter never resets; a cached entry older than the TTL is a miss; the same URL/title with different text yields a different cache key; a long article consumes more than one AI unit; a personal model test consumes quota; an article of 100 000 characters produces a defined outcome; `?a=1&b=2` and `?b=2&a=1` hash identically; a BOM-prefixed JSON feed is detected; the source backoff is honoured on the first failure. |
| **Acceptance criteria** | (1) The per-article AI call count is hard-capped regardless of elapsed time. (2) The daily AI cap counts provider calls, and the personal-model test path is included. (3) A cached summary is never served past the TTL, and never served for changed content. (4) An article can never block a cycle for more than a bounded total time. (5) One article URL produces exactly one hash. (6) The `usage.ai` semantics are documented and enforced consistently. |
| **Rollback strategy** | The cache-key change is self-invalidating (old entries simply stop being hit). The URL canonicalisation change must be shipped **behind a re-hash migration that is additive and reversible**, or limited to new sources, so existing article identities are preserved. |
| **Status** | `IMPLEMENTED_AWAITING_MERGE` (2026-10-20) — branch `feature/ph-08-ai-budget`, PR #25. 549 tests green, 7 new. **Not merged.** |
| **Owner approval required** | **YES** |

---

## PH-09 — Data integrity, schema and retention

| Field | Value |
|---|---|
| **Objective** | Make the schema single-sourced and forward-compatible, make referential integrity detectable, bound every growing table, and make the D1 backend's behaviour honest rather than accidental. |
| **Findings addressed** | DB-001, DB-002, DB-003, DB-004, DB-005, DB-007, DB-008, DB-010, DB-011, FIN-007, FIN-014, FIN-015, FIN-016, FIN-017, FIN-018, FIN-019, FIN-022, PUB-002, PUB-003, PUB-004, PUB-005, PIPE-021, FIN-023, DB-009 |
| **Priority** | P2 |
| **Dependencies** | `PH-01` (the schema version bump and the wallet columns land there — **two `SCHEMA_VERSION` bumps in one release is a review hazard**; prefer to land them together or sequence them explicitly). `PH-04` for the D1 start-up guard (OPS-013). |
| **Risk** | **High** — this is the only phase that touches existing production rows (the orphan cleanup, the retention changes and any re-hash). Mitigations: every data-touching step runs as a separate, explicitly-authorised script with a verified backup taken immediately before it, a dry-run mode, a row-level before/after report, and a documented reversal. Enabling `PRAGMA foreign_keys` is deliberately **excluded** from this phase and deferred to a later one, because it changes enforcement semantics for existing data. |
| **Complexity** | High |
| **Required tests** | New: a D1 batch that omits `last_row_id` raises rather than returning a bogus id; a D1 migration that fails on the third statement leaves zero statements applied; a D1 that rejects `rowid` leaves the ISO flag unset; the `pub_journal` row count does not grow across repeated recovery passes; the `settings` marker table does not grow across repeated cleanups; the usage insert path respects a cap of 0; an orphan-detection query reports orphans; a journal-mode check reports the actual mode. Update: `tests/test_phase10_db_quality.py` and `tests/test_phase11_limits_boundaries.py`. |
| **Acceptance criteria** | (1) The schema of a fresh database and of a migrated database are identical (a parity test compares `pragma_table_info` for every table). (2) Every growing table has a TTL and a purge test. (3) No orphaned row can appear without being detected. (4) The D1 path either behaves atomically or is explicitly refused at start-up. (5) The `posted`/`articles` retention windows are aligned. (6) Every data-touching step has a dry-run, a backup, a report and a reversal. |
| **Rollback strategy** | Data-touching steps are separate scripts executed only on explicit authorisation, each preceded by a verified backup. Schema changes are additive. The `PRAGMA foreign_keys` change is explicitly out of scope. |
| **Status** | `OPEN` |
| **Owner approval required** | **YES** (and per-script approval for any data-touching step) |

---

## PH-10 — Test infrastructure and coverage

| Field | Value |
|---|---|
| **Objective** | Make the test suite able to detect the classes of defect the audit found, and able to prove the fixes. |
| **Findings addressed** | TEST-001, TEST-002, TEST-003, TEST-004, TEST-007, TEST-008, TEST-009, TEST-010, TEST-011, TEST-012, TEST-013, TEST-014, OBS-003, OPS-016, OPS-031 |
| **Priority** | P2 — but **PH-10's pytest configuration and isolation work should land first within the remediation programme**, because every later phase's regression tests depend on it. |
| **Dependencies** | None technically. **Soft dependency:** the first sub-step (pytest configuration, per-test isolation, timeout) should precede PH-01 so the PH-01 regression tests are trustworthy. |
| **Risk** | Low — test infrastructure only. One risk: converting the 22 source-asserting tests to behavioural tests will initially **fail** (they currently assert things that are true of the current implementation); each conversion must be done together with the corresponding fix, not before it. |
| **Complexity** | Medium |
| **Required tests** | Add `pytest.ini` (`testpaths`, `addopts = -ra --strict-markers --durations=10`, a `timeout`, `filterwarnings`); move the DB to a per-module `tmp_path` with an `atexit` cleanup; replace the wall-clock sleeps with an injected clock; add `pytest-cov` and publish a coverage baseline; add a static check that no test function body calls `read_text(`/`getsource(` outside an allow-list; add a check that every emitted `callback_data` resolves in the dispatcher; extend the i18n parity test to placeholders and script detection. |
| **Acceptance criteria** | (1) `pytest.ini` exists with a per-test timeout and strict markers. (2) Each test module gets an isolated database that is cleaned up. (3) A coverage baseline is published and non-decreasing. (4) The source-asserting tests are converted, except the two genuinely static guards. (5) The dead-button and i18n-parity checks are enforced in CI. (6) The suite remains order-independent and green. |
| **Rollback strategy** | Test-only changes; revert freely. The coverage gate is introduced as non-blocking first and ratcheted. |
| **Status** | `OPEN` |
| **Owner approval required** | **YES** |

---

## PH-11 — Documentation, configuration and release-governance reconciliation

| Field | Value |
|---|---|
| **Objective** | Make every document, count, badge and default match the code; add the ownership, changelog and dependency-update machinery the repository lacks. |
| **Findings addressed** | All `DOCUMENTATION MISMATCH` entries in `FINDINGS.md` section 9 (the table of ~30 rows), plus TEST-001, TEST-002, TEST-014, OPS-017, OPS-018, OPS-020, OPS-023, OPS-024, OPS-031, SEC-011, SEC-014, FIN-023, PERF-004, UI-013, UI-015, UI-020 |
| **Priority** | P3 |
| **Dependencies** | Should follow the phases whose behaviour it documents (PH-01 … PH-09), except for the parts that are independent: the `CODEOWNERS`/`dependabot`/changelog/PR-template additions, the Python-version alignment, the license note and the `.env.example` duplication cleanup can land at any time. |
| **Risk** | Low. |
| **Complexity** | Low |
| **Required tests** | Add a doc-lint CI step: the README test count equals `pytest --collect-only`; the `docs/PERMISSIONS.md` two-phase list equals the set of `*2` routes in the code; `docs/PERFORMANCE.md`'s schema version equals `SCHEMA_VERSION`; every `ROADMAP.md` reference in the code and docs resolves (the current references point at a file that is **not in the repository**). |
| **Acceptance criteria** | (1) Every doc-mismatch row in the register is either fixed or explicitly justified in `DECISIONS.md`. (2) The counts, badges and defaults are machine-checked in CI. (3) `CODEOWNERS`, `dependabot.yml`, `CHANGELOG.md` and a PR template exist. (4) The Python-version claim matches the tested version. (5) The `ROADMAP.md` dangling reference is resolved. |
| **Rollback strategy** | Documentation-only; revert freely. |
| **Status** | `OPEN` |
| **Owner approval required** | **YES** |

---

## Phase dependency graph

```
PH-00 (read-only verification)  ──►  all phases (it grades them)
        │
        ├─► PH-01 (financial)  ──►  PH-03 (money notifications)
        │         │                └─► PH-06 (observability, shares the queue substrate)
        │         └─► PH-09 (data integrity)  ──►  PH-08 (pipeline; re-hash interaction)
        ├─► PH-02 (egress)  ──►  PH-08 (pipeline, provider client)
        ├─► PH-04 (deploy)  ──►  first release of PH-01..PH-03
        ├─► PH-05 (UI/UX)  ──►  PH-11 (docs)
        └─► PH-10 (test infra)  ──►  trustworthy regression tests in every later phase
```

## Coverage check

| Finding prefix | Addressed by |
|---|---|
| FIN-001…010, FIN-012, FIN-013, FIN-015, FIN-019, FIN-021, FIN-023, FIN-024 | PH-01 (owner decisions Q-1c, Q-2a, Q-3a applied) |
| FIN-011 | PH-01 (D1 crash windows, same approval contract) |
| FIN-014, FIN-016, FIN-017, FIN-018, FIN-020, FIN-022 | PH-09, PH-01, PH-05 |
| UI-017 | PH-01 (moved from PH-05 — owner decision Q-4c) |
| SEC-001, SEC-002, SEC-007, SEC-010, SEC-016 | PH-02 (owner decision Q-5c applied to SEC-007) |
| SEC-003, SEC-004, SEC-005, SEC-006 | deferred to a later "secrets at rest" phase (not yet authorised) — see DECISIONS.md D-08 |
| SEC-008, SEC-009, SEC-011, SEC-012, SEC-013, SEC-014, SEC-015 | PH-05, PH-04, PH-11, PH-09 |
| DB-001…005, DB-007…011 | PH-09 |
| DB-006 | PH-07 |
| UI-001…008, UI-010…026 | PH-05 (UI-006, UI-009 in PH-03) |
| PIPE-001…022 | PH-08 |
| NET-001…004, NET-006 | PH-02, PH-08 |
| NET-005, NET-008 | PH-07 |
| PERF-001…007 | PH-07 |
| PUB-002…005 | PH-09 |
| OPS-001…031 | PH-04 (most), PH-07 (OPS-015 partially), PH-10 (OPS-016, OPS-031), PH-11 (OPS-017, OPS-018, OPS-020, OPS-023, OPS-024) |
| TEST-001…014 | PH-10 (TEST-005 also in PH-01) |
| OBS-001…016 | PH-06 (OBS-005, OBS-007, OBS-011, OBS-012, OBS-013 shared with PH-01/PH-03) |
| DOC-001 | **VERIFIED_FIXED in PH-00** — the project's own `ROADMAP.md` was added to the repository; the reference-integrity check is now permanent (`tests/test_repo_integrity.py`) |
| OPS-028 | **VERIFIED_FIXED in PH-00** — `deploy/deploy.sh` committed executable |

**Not yet assigned:** SEC-003, SEC-004, SEC-005, SEC-006 (secrets-at-rest hardening) — deliberately deferred
because they change the on-disk key format and need their own owner decision (DECISIONS.md D-08).
