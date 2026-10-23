# TEST MATRIX — commit `08bc876`

> **PH-07 refresh (2026-10-23):** on `feature/ph-07-event-loop` (base `main` @ `ef53c35`, which
> includes PH-06), the suite is **653 passed** (`12` new tests in
> `tests/test_ph07_event_loop.py`), pyflakes is silent, and
> `tools/final_verification.py` is **17/17**. `tools/benchmark.py --ops 2000 --reps 3` was
> executed and its raw JSON is at `docs/bench_ph07_2026-10-23.json`. The audit baseline below
> (455 tests, commit `08bc876`) is preserved for traceability; PH-07 rows in §4 are marked done.

## 1. Test execution status (audit baseline)

| Field | Value |
|---|---|
| Command | `python -m pytest -q -p no:cacheprovider` |
| Working directory | the audit clone (`C:\Users\Mustafa\AppData\Local\Temp\opencode\audit-newsbot`) |
| Interpreter | Python 3.13.14, Windows 11, **out-of-tree venv** already holding the pinned dependencies — **nothing was installed** |
| Result | **455 passed, 0 failed, 0 skipped, 0 errors, 0 xfailed** — 112.5 s |
| Warnings | 3 × `PTBDeprecationWarning` (`telegram/error.py:243`, `retry_after` will become a `timedelta`) from `tests/test_exactly_once_limits.py::test_retryafter_on_photo_retries_media_not_text` |
| Order independence | all 33 test files re-run in **reverse alphabetical order** → **455 passed** (92.9 s) |
| Repository hygiene | `git status --porcelain` empty after both runs; **no `newsbot.db`** created in the repo root; the test DB lives under `%TEMP%` (`tests/conftest.py:10-11`); `.pytest_cache` suppressed |
| DB isolation | `tests/conftest.py` sets `DB_FILE` to a temp path **before** any `newsbot` import, so `config.DB_FILE` is absolute and inside `%TEMP%` — but **one shared database for all 455 tests** (TEST-013) |
| CI parity | `tests/test_deploy_script.py` ran because Git Bash was present. On a runner without Git Bash those **4 tests skip** — CI coverage differs from this run. |
| Coverage | **Not measurable.** `pytest-cov` is absent from `requirements-dev.txt` and no coverage config exists (TEST-003). Every statement below is derived from *reading* the tests, not from line/branch coverage. |
| Not executed | `tools/benchmark.py` as a standalone command (invoked once as a subprocess with `--ops 40`, so `docs/PERFORMANCE.md` was **not** re-measured); `tools/final_verification.py` as a standalone command (its "17 checks" claim was verified by counting `check(...)` call sites). |
| Not exercised at all | Any real network: Telegram Bot API, Cloudflare D1/KV, AI providers, RSS feeds. D1 assertions all use a fake client. |

## 2. Test inventory

33 test files + `conftest.py`, 6 473 lines. **376 test functions, ~455 collected cases** (79 extra from
`@pytest.mark.parametrize`).

| File | Fns | Cases | What it actually asserts |
|---|---|---|---|
| `conftest.py` | 0 | – | Sets `BOT_TOKEN`/`SUPER_ADMIN_IDS`/`DB_BACKEND`, points `DB_FILE` at a `tempfile.mkdtemp()` DB, session fixture `_core` calls `init_core()`. **One shared DB; the temp dir is never removed.** |
| `test_agent_contract.py` | 12 | 12 | Prompt-contract markers, prompt determinism, JSON envelope shape, `salvage_post` never leaks raw JSON, `clean_ai_text` idempotence, `compose` blockquote+signature, source-URL stripped at the caption layer, AI cache key, `sanitize_html` allow-list. Snapshot-style by design. |
| `test_authz_matrix.py` | 16 | 16 | Role × resource matrix (user/admin/super), ownership checks, plan gate, server-side input validation, `await` TTL/expiry, rate-limit persistence, API-key masking, output escaping, receipt size cap. **Strong.** |
| `test_business_logic.py` | 12 | 25 | AI output accept/reject, `parse_json` robustness, `salvage_post`, discount use/refund/expiry, referral self/missing-referrer rejection, referral pair recorded. |
| `test_cancel_and_ref.py` | 12 | 12 | `/cancel` semantics, cancel flags, in-operation button gate, `/ref` before language, banner in fa/en, rate-limit silence, menu registration, cycle-notification removal, short report text. 2 source-asserting. |
| `test_core.py` | 18 | 18 | dt parsing, HTML sanitise, quote-splitting, score-dict stripping, `redact`, SSRF, discount atomic use/refund, `pay_claim` CAS + stuck recovery, referral rules + idempotency, upgrade/renew/downgrade quote, channel-day counting, per-task AI budget. |
| `test_data_retention.py` | 5 | 5 | Published content survives cleanup; non-ready content cleaned; TTL default 72 h; **no `DELETE … published`** in the cleanup source. 1 source-guard. |
| `test_deploy_script.py` | 4 | 4 | Real `deploy/deploy.sh` rehearsed in a temp git sandbox: success+backup, gate failure → rollback without restart, post-restart health failure → rollback, manual `rollback` subcommand. **Skips without Git Bash.** |
| `test_deployment.py` | 7 | 7 | `run.py --check` on a fresh and an existing DB (read-only guarantee), preflight fail-fast, real SQLite backup→restore round-trip, restored DB operational, migration tools parse. |
| `test_exactly_once_limits.py` | 13 | 13 | Journal `unsettled`/`in_flight` never repost, `unsettled`+media settles without resend, soft-window excludes `delivery_unknown`, parallel publish sends once, failed article doesn't block the queue, `RetryAfter` keeps the media, deeplink only after success, AI daily cap + super exemption, `generate` blocked by the cap, `hard_html` limits. **Strongest file in the suite.** |
| `test_injection.py` | 6 | 6 | SQL payload stored not executed; `OR '1'='1'` matches only itself; dynamic column whitelist; **no `shell=True`/`os.system`/`eval`/`exec`/`pickle.loads`**; subprocess list-args; end-to-end `build_prompt` boundary neutralises EN/FA/AR. 2 source-guards. |
| `test_log_secret_leak.py` | 5 | 5 | The global `LogRecordFactory` redacts **all** `log.*` calls, exception tracebacks and `log_event`; no over-redaction; the factory is installed globally. |
| `test_menu_resilience.py` | 10 | 10 | `render` fallback on `Forbidden`/any Telegram error/`BadRequest`-not-modified, plain-text fallback, `/ref` full copy + live percent + config-failure fallback, code version, one-item-per-line titles. |
| `test_payment_atomicity.py` | 16 | 16 | Double-approve pays exactly once (2 threads), crash mid-approve rolls back fully, stuck-recovery re-approve doesn't re-apply, discount exhaustion rollback, free/missing plan rejected, corrupt `pc` still atomic, **free-claim CAS only once (4 threads)**, transfer-token single-active, plan delete deactivates, expired-plan role rollback, revoke downgrade, per-user op lock. **2 source-asserting** (one of them *pins* a defective behaviour). |
| `test_phase1_fixes.py` | 9 | 9 | Referral trigger installed, **referral race + cap ⇒ wallet credited exactly once**, D1 cursor semantics, D1 `last_row_id` from meta, D1 `RETURNING` keeps rows, migrate row-fingerprint order-insensitivity, migrate `meta`/trigger, migrate table list. 1 heavy `inspect.getsource` assertion. |
| `test_phase10_db_quality.py` | 20 | 20 | Schema version single source = 7, unique indexes shared+installed, dup-guard skips without deleting, ISO canonical format, `normalize_iso_columns` idempotent, indexed `channel_posts_today`, `seed_plans` idempotent, `usage_reset` whitelist + semantics, single-instance lock, D1 atomic schema batch, archive-window delete with backup, VACUUM throttle, migrate single-batch, migrate check guards. 2–3 source-guards. |
| `test_phase11_limits_boundaries.py` | 9 | 9 | `reserve_publication` exact cap boundary, `release_publication` restores the slot / never negative, `ai_claim` stops exactly at the cap, large/zero-day plan assignment, upgrade-quote discount extremes, discount price boundaries, discount capacity/refund boundaries, channel setting clamps. |
| `test_phase12_config_docs.py` | 19 | 19 | Env validation (invalid int → default + `CONFIG_ERRORS`, out-of-range clamp, empty env, bad `SUPER_ADMIN_IDS`), dotenv explicit root path, Fernet secret store round-trip/idempotence/fail-closed, one-shot key migration, encrypted model keys, referral-cfg panel validation, **per-purchase referral dedupe + amount cap**, **referral max-per-user deprecated**, trigger v6 receipt, ref panel wiring, new i18n keys in both languages, runtime state separated from config, `.env.example` ↔ config sync, README claims, schema v7. **7 source-asserting / doc-contract tests — the most brittle file.** |
| `test_phase13_business_isolation.py` | 4 | 4 | Daily post cap isolated between admins, AI cap isolated, wallet credit isolated between referrers, channel settings isolated. |
| `test_phase13_tech_debt.py` | 13 | 13 | Referral cfg exact bounds/garbage fallback, expired-plan quote, `_safe_fields` rejection, **the benchmark subprocess really runs and emits full JSON**, referral credit writes an atomic receipt, plan-change receipt + no zero-delta receipt, v6 schema surface, super-notify queue retry/drop/exhaustion/direct-fallback, app wiring. 2 source-asserting. |
| `test_phase14_payment_ui.py` | 13 | 13 | Per-language tariff (fa=toman / en=$, no cross-unit fallback), `price_num_fa` column, `fmt_amt` units, **renew/upgrade/downgrade-blocked/fresh quote matrix**, wallet deduction, text mode when the tariff is missing, **i18n dictionaries complete + bilingual**, `SPHARSE` fa/en key parity, plan/referral copy honesty, super receipt price + own language, admin-home cleanliness. 2 mixed source-assertions. |
| `test_phase15_watchdog.py` | 9 | 9 | systemd drop-ins (`Restart=always`, `StartLimitIntervalSec=0`, `Type=notify`, `WatchdogSec=300`), `READY=1`/`STOPPING=1`, no-socket no-op, abstract socket, `WATCHDOG_USEC` parsing, task creation, the watchdog loop pings. 2 source-asserting. |
| `test_phase7_pipeline.py` | 17 | 17 | URL canonicalisation, feed dedupe, HTTP `Retry-After` with a cap, 403 immediate retry, `Retry-After` date parsing, the fetch total deadline → 504, the media deadline, soft-contract validation, prose salvage, AI output cache, chunk cache, usage/token deltas, extraction failure retryable-not-rejected, a cycle without sources doesn't touch `last_run`, one source error doesn't stop the cycle, a cycle publishes + sets `last_run`, channel-zone report counting. |
| `test_phase8_concurrency.py` | 13 | 13 | `arun` on dedicated DB threads, the event loop is not blocked, ContextVars propagated, the pool is lazily re-created, **20 parallel SQLite writes**, a 4-thread user-insert race, rate-limit persist throttle, the exact window cap, journal ack/in_flight/prepared recovery, clean shutdown of named tasks (2 rounds). 3 timing-sensitive. |
| `test_phase9_ui_ux.py` | 29 | 29 | ZWNJ/ZWJ cut marking, escaping of source names/usernames/roles/langs, two-step confirmations for destructive actions, pay-no confirm, text reset confirm, running-op text, wait-for-operation, empty states, queue sort toggle, progress reporting, broadcast progress, **all main buttons have textual labels**, balanced HTML tags, help lists the commands. 29 — catches dead/unlabelled buttons but only for the buttons it enumerates. |
| `test_rate_window.py` | 6 | 6 | The window allows up to the limit then blocks, frees after the window, a rejected event is not counted, global flood block, super exemption. |
| `test_recovery_concurrency.py` | 8 | 8 | `ch_lock`/`model_lock` stability + serialisation, `in_flight` never reposts, `partial` releases the quota + unsettled, `ack` → published, recovery idempotent, `reserve_publication` daily cap. |
| `test_secrets_and_text.py` | 6 | 19 | Secret-mask regression across shapes; score-dict normalisation across languages/digits. |
| `test_security.py` | 20 | **73** | SSRF private hosts, public allowed, **7 alternate-IP/DNS-rebinding bypasses**, 11 alternate-IP forms, 5 non-HTTP schemes, 9 `article_data` escape payloads, a real `build_prompt` single-boundary, EN instruction patterns, normal-text survival, 7 FA/AR injection patterns, 3 FA/AR role prefixes, news-tense anti-over-redaction, residue detection, the chunk summariser strips injection, 4 dangerous-tag sanitise cases, `javascript:` URL, event handlers, safe tags, balanced output. **README claims 49 — actual 73.** |
| `test_static_guard.py` | 4 | 4 | pyflakes: zero `undefined name`; every `newsbot` module importable; `.gitignore` blocks `.env`/`*.db`/`__pycache__`/`.venv` but not `.env.example`; no sensitive file tracked by git. 2 of 4 assert **repo/git state**, not app behaviour. |
| `test_time_zone.py` | 12 | 12 | `zone_of` named/fallback, DST seasonal offsets, naive-date labelling per zone, legacy offset→IANA migration, a DST-aware label, a future-date clamp, a new channel stores IANA, legacy row migration, quiet hours per place zone, `_entry_dt` zone threading, page date extraction + zone, `channel_posts_today` at local midnight. 2 DST/wall-clock dependent. |
| `test_trx_atomicity.py` | 7 | 7 | SQLite `_trx` atomicity + rollback; **D1 single atomic batch**; a D1 failure sends nothing; a D1 batch rejection applies nothing; D1 reads are immediate; `BEGIN`/`COMMIT` are ignored in a D1 batch. |
| `test_ui_routing.py` | 8 | 8 | User/admin/super panel views don't crash; the dispatcher routes known read-only actions; it routes admin actions. **Whitelist only** and catches only `NameError`/`ImportError`/`AttributeError`. |
| `test_user_features.py` | 14 | 14 | Mandatory channel per-language independence, env-only-for-fa, an invalid-id fallback, `/ref` registered, the referral reward message in fa/en, the AI button moved to the channel, per-user models, the all-models-failed message, a model attempt is not repeated, model cap coverage, popup thresholds, the popup alert flag removed. 4 mixed source-assertions. |
| **TOTAL** | **376** | **455** | 79 extra cases from parametrisation |

**Source-asserting tests: ~22 of 376 (≈6 %)** — `test_phase12_config_docs.py` (7), `test_user_features.py` (4),
`test_phase14_payment_ui.py` (2), `test_payment_atomicity.py` (2), `test_injection.py` (2),
`test_cancel_and_ref.py` (2), `test_phase13_tech_debt.py` (2), `test_phase1_fixes.py` (1),
`test_phase10_db_quality.py` (1–2), `test_data_retention.py` (1), `test_static_guard.py` (2, repo-state).

## 3. Critical-rule coverage matrix

| # | Critical business rule | Status | Existing test (file:line) / nearest test | Finding |
|---|---|---|---|---|
| a | Payment approval atomicity + idempotency (no double-apply) | **COVERED** | `test_payment_atomicity.py:26` (2 threads), `:45` (crash rollback), `:67` (re-approve after crash); `test_core.py:75` | — |
| b | Referral credit exactly-once | **COVERED** | `test_phase1_fixes.py:34` (race + cap ⇒ exactly once); `test_phase12_config_docs.py:174`; `test_core.py:83`; `test_phase13_tech_debt.py:125` | — |
| c | **Wallet ledger reconciliation** `SUM(credit_log.delta) == users.credit` | **NOT COVERED** | Nearest: `test_phase13_tech_debt.py:138-140` asserts **one** receipt's `balance_after`, never the running sum | **TEST-005** |
| d | Free-plan claim atomicity | **COVERED** | `test_payment_atomicity.py:120` (4 threads ⇒ exactly 1 `True`) | — |
| e | Discount `max_uses` enforcement | **COVERED** | `test_business_logic.py:75`; `test_payment_atomicity.py:87`; `test_phase11_limits_boundaries.py:126` | — |
| f | Plan upgrade vs downgrade rules | **COVERED (quote only)** | `test_core.py:101`; `test_phase14_payment_ui.py:51`; `test_phase13_tech_debt.py:80` — all quote-time; **no approval-time re-validation** | FIN-019 |
| g | Daily **post** quota + reset | **COVERED** | `test_phase11_limits_boundaries.py:31`; `test_phase13_business_isolation.py:30`; reset: `test_phase10_db_quality.py:137` | FIN-017 (insert path) |
| g | Daily **AI** quota + reset | **COVERED** | `test_exactly_once_limits.py:192`; `test_phase11_limits_boundaries.py:68` | PIPE-006, PIPE-007 |
| g | Daily **test-run** quota (`daily_tests`) | **NOT COVERED** | `daily_tests` appears only as a plan *field* (`test_phase14_payment_ui.py:51`, `test_phase13_business_isolation.py:35`); no test drives the counter to its cap | **TEST-008** |
| h | Exactly-once publishing (no duplicate post) | **COVERED** | `test_exactly_once_limits.py:56,70,85,109`; `test_recovery_concurrency.py:58`; `test_phase8_concurrency.py:199` | — |
| i | Publication journal / crash recovery | **COVERED** | `test_recovery_concurrency.py:75,88,101`; `test_phase8_concurrency.py:186,199,212`; `test_exactly_once_limits.py:70` | — |
| j | Source failure cooldown / retry / backoff | **NOT COVERED** | `fail_count`/`next_try_after`/`SOURCE_PAUSE_MIN` appear in **no** test file. Nearest: `test_phase7_pipeline.py:72` covers the HTTP `Retry-After` header — a different mechanism | **TEST-007** |
| k | SSRF blocking of private hosts (source/media path) | **COVERED (strong)** | `test_security.py:25,30,41,54,59` (73 items); `test_core.py:59` | — |
| k | SSRF blocking on the **AI-provider** path | **NOT COVERED** | No test constructs a model `base_url` that redirects to a private host | **SEC-001 / NET-001** |
| l | Prompt-injection stripping (text and title) | **COVERED** | `test_security.py:79,97,104,124,133,147,154`; `test_injection.py:75` | — |
| l | Prompt-injection stripping (**the URL**) | **NOT COVERED** | The single-boundary test uses the text and the title, never a URL containing `</article_data>` | **SEC-002** |
| m | HTML sanitisation | **COVERED** | `test_security.py:188,194,199,205,212`; `test_core.py:31`; `test_agent_contract.py:150` | — |
| n | Authorization matrix (user/admin/super) | **COVERED** | `test_authz_matrix.py:163,173,183,200,211,239,256` | SEC-015 (inconsistent rule) |
| o | Callback routing / dead buttons | **WEAK** | `test_ui_routing.py:179` exercises a hard-coded whitelist of 8 read-only actions; `:193` covers 4 admin actions. Unknown `callback_data` silently falls through to `go_home_safe` (`ui_dispatch.py:383`) | **TEST-009** |
| p | i18n fa/en parity | **PARTIAL** | `test_phase14_payment_ui.py:104` (every entry is a 2-tuple with two non-empty strings); `test_phase12_config_docs.py:230` (12 keys). Missing: the `en` value is actually English; fa/en carry the same **placeholders**; the `tr()` fallback; DB-overridden texts | **TEST-010**, UI-019 |
| q | Timezone / DST handling | **COVERED** | `test_time_zone.py` (all 12), incl. `:31` DST samples and `:103` local midnight | — |
| r | Rate limiting | **COVERED** | `test_rate_window.py` (6); `test_phase8_concurrency.py:145,176`; `test_authz_matrix.py:331,355` | — |
| s | D1 backend behaviour | **PARTIAL** | D1 *plumbing* is tested (`test_trx_atomicity.py` 5 tests; `test_phase1_fixes.py:91,102,130`; `test_phase10_db_quality.py:174,225`). The D1 **business** path `_pay_approve_d1` (`core_payments.py:113-134`) is **never entered** | **TEST-006** |
| t | Concurrency (threads / asyncio racing the same state) | **COVERED** | `test_payment_atomicity.py:26,120,199`; `test_exactly_once_limits.py:109`; `test_recovery_concurrency.py:29`; `test_phase8_concurrency.py:36,96,113`; `test_core.py:134` | — |
| u | Money shown == money charged | **NOT COVERED** | No test compares the plan-page display with `pay_requests.final_price` | **FIN-004** |
| v | Two pending requests for one user | **NOT COVERED** | No test creates two pending requests and approves both | **FIN-003** |
| w | Frozen quote vs a changed wallet | **NOT COVERED** | `test_payment_atomicity.py:223` deliberately **pins** the frozen-quote behaviour | **FIN-002** |
| x | Monetary field bounds (inf / nan / negative / huge) | **NOT COVERED** | No case in `test_phase11_limits_boundaries.py` | **FIN-012** |
| y | Cross-currency wallet | **NOT COVERED** | `test_phase14_payment_ui.py:28` proves the *tariff* never falls back across units, but nothing tests the *wallet* | **FIN-001** |
| z | Deployment chain end-to-end | **COVERED (sandbox)** | `test_deploy_script.py:110-187` builds a sandbox repo, a bare origin, a fake venv and `systemctl`/`journalctl` stubs and drives the real `deploy.sh` | OPS-001…OPS-028 (the gaps listed there) |
| aa | Backup → restore round trip | **COVERED** | `test_deployment.py:74-113` (real `sqlite3.backup()`), `:116-131` (the restored DB is writable) | OPS-011 (WAL sidecars) |

## 4. Required regression coverage (to be added, mapped to findings)

| Test to add | Covers | Phase |
|---|---|---|
| Wallet ledger reconciliation property test (`SUM(delta) == users.credit`, monotone `balance_after`) | FIN-002, FIN-006, TEST-005 | PH-01 |
| Cross-currency wallet test (referrer `en`, payer `fa`; the referrer's `$5` plan is **not** free) | FIN-001 | PH-01 |
| Frozen-quote-vs-live-wallet reconciliation (two pending requests, approve both) | FIN-002 | PH-01 |
| Two-pending-requests plan exclusivity | FIN-003 | PH-01 |
| Displayed price == charged price (no wallet / wallet / discount / both) | FIN-004 | PH-01 |
| Free-plan claim refused while a paid plan is active; `free_used` not consumed | FIN-005 | PH-01 |
| Discount re-validation at approval (deactivated / expired code) | FIN-008 | PH-01 |
| Finite-tariff bounds (`inf`, `1e400`, `nan`, `-inf`, huge) rejected; no `inf` reaches `users.credit` | FIN-012 | PH-01 |
| Refund round-trip (wallet restored, `credit_log` receipt written, ledger reconciled) | FIN-013 | PH-01 |
| A `plan_changes` row for every applied payment | FIN-007 | PH-01 / PH-09 |
| Referral credit survives a simulated crash (reconciler grants it exactly once) | FIN-009, OBS-005 | PH-01 / PH-06 |
| AI-provider `base_url` redirecting to `169.254.169.254` is refused; no credential header crosses the hop | SEC-001, NET-001, NET-002 | PH-02 |
| A DNS resolver that changes its answer per call is rejected | NET-003 | PH-02 |
| `ALLOW_PRIVATE_MODEL_HOSTS` as an allowlist admits only the listed `host:port` | SEC-007 | PH-02 |
| A URL containing `</article_data>` yields exactly one prompt boundary pair | SEC-002 | PH-02 |
| A 400-character receipt note yields valid HTML ≤ 1024 and reaches the super admin | UI-006 | PH-03 |
| A receipt-kind `await` state survives an unrelated button press | UI-009 | PH-03 |
| A failing receipt notification marks the request un-notified; the scheduler re-notifies it | OBS-013 | PH-03 |
| A failed user notification is retried | OBS-012, FIN-021 | PH-03 |
| A full super-notification queue lands in a dead-letter table, not nowhere | OBS-011, OBS-001 | PH-03 / PH-06 |
| `split_html` never emits an empty or over-limit chunk (property test 0…20 000 chars) | UI-005 | PH-05 |
| A plan with a 100-character name still renders | UI-008 | PH-05 |
| No rendered user string contains an unresolved `{placeholder}` | UI-002 | PH-05 |
| Every `tr(lang,"X")` literal exists in `TXT`; fa/en placeholder sets are identical | UI-001, UI-019, TEST-010 | PH-05 / PH-10 |
| Every `*2` execute route writes a `log_event`; `s:auto`/`s:plan_free`/`s:uassign` require confirmation | UI-011, UI-012 | PH-05 |
| Every screen reachable from the router is reachable from the home screen | UI-014 | PH-05 |
| The redaction factory fails **closed** (a raising `redact()` must not leak the raw record) | OBS-015 | PH-06 |
| Scheduler errors carry a traceback | OBS-004 | PH-06 |
| `/health` renders `unknown`/`degraded` for a failed probe instead of `0` | OBS-002, OBS-009 | PH-06 |
| `last_successful_publish` is exposed and goes stale correctly | OBS-006 | PH-06 |
| The super-admin ID is not written to the journal | SEC-011 | PH-06 |
| `q()` never executes on the event-loop thread (AST + runtime) | PERF-001, DB-006 | PH-07 — **DONE** (`test_ph07_event_loop.py`) |
| 300 channels keep the queries per `ch_lock()` bounded | PERF-002 | PH-07 — **DONE** (`test_ph07_event_loop.py`) |
| The Telegram client's pool limits are asserted | NET-005 | PH-07 — **DONE** (`test_ph07_event_loop.py`) |
| A 13th chunk call returns `ai_budget`; the call counter never resets | PIPE-001 | PH-08 |
| A cached entry older than the TTL is a miss; changed text ⇒ a different cache key | PIPE-003, PIPE-004 | PH-08 |
| A long article consumes more than one AI unit; a personal model test consumes quota | PIPE-006, PIPE-007 | PH-08 |
| `?a=1&b=2` and `?b=2&a=1` hash identically | PIPE-014 | PH-08 |
| A BOM-prefixed JSON feed is detected | PIPE-010 | PH-08 |
| The source backoff is honoured on the first failure | PIPE-012 | PH-08 |
| A D1 batch without `last_row_id` raises rather than returning a bogus id | DB-001 | PH-09 |
| A D1 migration that fails on the third statement leaves zero statements applied | DB-002 | PH-09 |
| A D1 that rejects `rowid` leaves the ISO flag unset | DB-004 | PH-09 |
| The `pub_journal` and `settings` row counts do not grow across repeated cleanups | PIPE-021, DB-008 | PH-09 |
| The usage insert path respects a cap of 0 | FIN-017 | PH-09 |
| An orphan-detection query reports orphans | DB-007 | PH-09 |
| The journal-mode check reports the actual mode | DB-009 | PH-09 |
| The deploy script defaults equal the values in the runbook and README | OPS-001 | PH-04 |
| A failed `systemctl restart` restores HEAD to `$prev` | OPS-026 | PH-04 |
| A failing gate produces a second `pip install` after the reset | OPS-006 | PH-04 |
| A journal traceback **before** the restart epoch does not fail the gate; one **after** it does | OPS-003 | PH-04 |
| A delayed `READY=1` within the new budget passes; a `Type=simple` service is rejected | OPS-002 | PH-04 |
| Deploying the same SHA twice still performs a backup and a health check | OPS-025 | PH-04 |
| `deploy/deploy.sh` is executable in the git index | OPS-028 | PH-04 |
| A WAL restore with sidecars present reproduces the backup exactly | OPS-011 | PH-04 |
| A schema-newer-than-build `--check` fails with an actionable message | OPS-012 | PH-04 |
| The source backoff and the daily test-run quota are driven to their limits | TEST-007, TEST-008 | PH-10 |
| Every emitted `callback_data` resolves in the dispatcher | TEST-009 | PH-10 |
| No test body calls `read_text(`/`getsource(` outside an allow-list | TEST-004 | PH-10 |
| The README test count equals `pytest --collect-only` | TEST-001 | PH-10 |

## 5. Known limitations of the suite (as it stands)

1. **One shared database for 455 tests** (TEST-013) — cross-test coupling is invisible and a test can pass only
   because of another test's leftovers (e.g. `users.id == 1`, fixed discount codes such as `"SEC7A"`, `"CRASH10"`).
   The temp directory is never removed.
2. **Global state mutated without restore** (TEST-011) — `MANDATORY_CHANNEL_ID`/`USERNAME` are zeroed for the
   rest of the session; `importlib.reload(newsbot.config)` re-executes the module mid-session. This is a hard
   blocker for `pytest -n auto`.
3. **Wall-clock dependence** (TEST-012) — `time.sleep(1.05)`, `asyncio.sleep`, a `ticks >= 10` assertion under a
   10 ms ticker, and local-midnight-relative day keys. Flaky on a loaded runner; misattributed to product bugs.
4. **~6 % of the suite asserts on source text** (TEST-004) — passes when the code is behaviourally broken but the
   string survives, and fails on harmless refactors. Two of these (`test_payment_atomicity.py:84` and `:223-232`)
   currently **pin the defective behaviour** identified in FIN-007 and FIN-002.
5. **No pytest configuration** (TEST-003) — no `pytest.ini`, no `pytest-timeout` (a hung test burns the
   15-minute CI budget), no `pytest-cov` (**coverage cannot be measured at all**), no `--strict-markers`.
6. **The D1 business path is untested** (TEST-006) and the D1 *plumbing* is tested only against a fake client.
7. **No production-environment verification** — no VPS, no CI run observed, no branch-protection settings read,
   no live Telegram, no visual rendering check, no D1 against Cloudflare, no AI provider, no RSS.
8. **`docs/PERFORMANCE.md` was not re-measured** — the benchmark ran once as a subprocess with `--ops 40` rather
   than the documented `--ops 2000`.
9. **No security test for the AI-provider egress path** — the SSRF suite (73 cases) covers only the source and
   media paths; `ai_models.http_post` is untested.
10. **No test asserts the "no empty `except`" claim** — the README asserts it (59 sites contradict it, TEST-014)
    and no lint enforces it.
