# ARCHITECTURE — verified at commit `08bc876`

> This document describes the architecture **as the code actually is**, verified by reading every module.
> Where a document in the repository disagrees with the code, the code is described here and the
> disagreement is recorded as a finding in `FINDINGS.md` section 9.

---

## 1. System shape

```
                         ┌──────────────────────────────────────────────┐
  Telegram Bot API  ◀───▶│  python-telegram-bot 22.8  (Application)      │
                         │  app.py: main() / preflight() / init_core()  │
                         │  app.py: post_init()  → set_me, commands,    │
                         │                  job_queue.run_repeating(tick)│
                         │  app.py: post_shutdown() → cancel tasks,      │
                         │                  close http, close db pool   │
                         └───────────────┬──────────────────────────────┘
                                         │  handlers
        ┌────────────────────────────────┼───────────────────────────────────────┐
        ▼                                ▼                                       ▼
┌───────────────────┐   ┌──────────────────────────┐   ┌───────────────────────────────┐
│ ui_dispatch.py    │   │ ui_commands.py           │   │ ui_support.py                 │
│  callback router  │   │  /start /create /admin   │   │  private text messages,       │
│  u: / a: / s:     │   │  /man /help /about /lang │   │  support session, receipts    │
│  namespaces       │   │  /ref /cancel /unknown   │   │  (await-state machine)        │
└────────┬──────────┘   └──────────┬───────────────┘   └───────────────┬───────────────┘
         │                          │                                   │
         ▼                          ▼                                   ▼
┌──────────────────────────────────────────────────────────────────────────────────────────┐
│  UI presentation        ui_views_user · ui_views_admin · ui_views_super · ui_widgets      │
│                         ui_inputs (form wizards) · ui_i18n (TXT/DIAG/STAGES/WIZ)           │
│                         text_format (sanitize_html, split_html, strip_injection)           │
└──────────────────────────────────┬───────────────────────────────────────────────────────┘
                                   │
              ┌────────────────────┴─────────────────────┐
              ▼                                          ▼
┌───────────────────────────────┐        ┌──────────────────────────────────────┐
│  core_* business layer        │        │  infrastructure                     │
│  core_users  core_plans       │        │  db.py (SQLite | D1)   config.py     │
│  core_payments  core_discounts│        │  net_http.py  secret_store.py         │
│  core_referrals core_channels │        │  ratelimit.py  logutils.py  timeutils │
│  core_sources  core_articles  │        │  concurrency.py  watchdog.py  runtime  │
└───────────────┬───────────────┘        │  opctx.py  notify.py                  │
                │                        └──────────────────────────────────────┘
                ▼
┌──────────────────────────────────────────────────────────────────────────────────────────┐
│  pipeline (scheduled, per channel)                                                     │
│  scheduler.py (tick, recovery, probes, expiry)                                          │
│    └─ cycle.py  ── discover.py ── extract.py ── adfilter.py ── generate.py ── publish.py │
│                    ai_models.py  (providers, circuit breaker, budgets)                    │
└──────────────────────────────────┬───────────────────────────────────────────────────────┘
                                   ▼
┌──────────────────────────────────────────────────────────────────────────────────────────┐
│  db.py — ONE interface for the whole codebase:  q() · _trx() · gset() · gget() · arun()  │
│  SQLite (default)   : thread-local connection, WAL, BEGIN IMMEDIATE transactions         │
│  Cloudflare D1      : per-statement REST, buffered atomic batch inside _trx (UNSUPPORTED) │
└──────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Component responsibilities and boundaries

### 2.1 Entry point and lifecycle — `run.py`, `newsbot/app.py`

| Element | Responsibility | Verified location |
|---|---|---|
| `run.py` | Two modes: normal run, or `--check` (read-only config + DB inspection) | `run.py:10-15` |
| `preflight()` | Validates `BOT_TOKEN`, `SUPER_ADMIN_IDS`, `CONFIG_ERRORS`, DB folder writability, D1 credentials, ~25 numeric ranges, `POST_LIMIT_MAX` headroom, mandatory-channel format, Cloudflare KV completeness. `check=True` ⇒ read-only. Raises `SystemExit` with a readable list. | `app.py:114-170` |
| `_acquire_single_instance_lock()` | Advisory file lock on `<db>.lock` (flock on Linux, msvcrt on Windows) so two bots cannot share one database | `app.py:173-193` |
| `init_core()` | `db()` (schema) → `seed_plans()` → `recover_publications()` → `_rate_load_persisted()` → `migrate_ai_keys()` → default `automation_enabled` | `app.py:36-41` |
| `post_init()` | `get_me()`, publish the runtime notify functions, `set_bot()`, start the super-notify worker, register BotCommands (fa + en), start the repeating tick, warn about skipped unique indexes, notify super admins, start the watchdog, `READY=1` | `app.py:49-73` |
| `post_shutdown()` | `STOPPING=1`, cancel the watchdog, cancel and await the named long tasks (`publication`/`user`/`thinking`) with a two-stage timeout, cancel the temp sweeper, stop the super-notify worker, close HTTP, close the DB pool | `app.py:76-105` |
| `on_error()` | PTB error handler | `app.py:108-111` |

**Handlers registered** (`app.py:203-205`): 10 commands (`start create admin man help about lang ref cancel health`),
a catch-all unknown-command handler, one `CallbackQueryHandler` (`on_callback`), one private text
`MessageHandler` (`on_message`), and an error handler. `allowed_updates=["message","callback_query"]`,
`drop_pending_updates=False`.

### 2.2 Data layer — `newsbot/db.py` (473 lines)

The **only** module that touches `sqlite3` or `httpx`-to-Cloudflare. The whole codebase uses exactly five
entry points:

| API | Semantics |
|---|---|
| `q(sql, params, one, commit)` | Synchronous statement on the **thread-local** connection. `commit=True` also commits and returns `lastrowid`. Retries up to 5 times on `locked`/`busy` with a 20/40/80/160 ms **blocking** sleep. |
| `_trx(fn)` | Atomic transaction. **SQLite**: `BEGIN IMMEDIATE` … `commit`/`rollback`. **D1**: writes are buffered and flushed as **one atomic batch**; `SELECT`s execute immediately (outside the batch) — a deliberate, documented limitation. |
| `gset(k,v)` / `gget(k,default)` | JSON-encoded key/value in the `settings` table. |
| `arun(fn,*a)` | Runs a synchronous function on a **dedicated 4-worker thread pool** so the event loop is not blocked. ContextVars are propagated. |
| `_safe_fields(table, fields)` | Whitelists dynamic column names against `pragma_table_info`. |

**Connection model** (`db.py:237-248`): one `sqlite3.connect` per thread (`threading.local`), with
`journal_mode=WAL`, `synchronous=NORMAL`, `busy_timeout=15000`, `cache_size=-16000`, `temp_store=MEMORY`.
The DB file is `chmod 600` at schema time (`db.py:319-321`).

**Schema lifecycle** (`_ensure_schema`, `db.py:313-356`): one execution per process, guarded by the global
`_SCHEMA_READY` flag and a global write lock. Order: `SCHEMA` + `DB_INDEXES` → `meta` table → schema-version
guard (refuses to run an older build against a newer database) → idempotent `MIGRATIONS` (`ALTER TABLE ADD
COLUMN`) → `normalize_iso_columns` (one-time, guarded by `meta.iso_norm_v1`) → trigger replacement →
`pub_journal` migration → `original_admin_id` backfill → conditional unique indexes → `DROP TABLE logs`.

**Schema version**: `SCHEMA_VERSION = 7`, single-sourced in `newsbot/__init__.py:9` (DB-25).

**Tables** (24): `users`, `plans`, `discounts`, `channels`, `sources`, `usage`, `articles`, `posted`,
`ch_tokens`, `ai_models`, `deeplinks`, `kv_cache`, `pub_journal`, `ai_cache`, `ai_usage`, `dedup`,
`settings`, `support`, `support_map`, `plan_changes`, `pay_requests`, `referrals`, `credit_log`,
`rate_limits`, plus `meta`.

**Atomic referral wallet credit** — the only balance write implemented as a database trigger
(`db.py:82-94`): `AFTER UPDATE OF credit_amount ON referrals` adds the delta to `users.credit` **and**
inserts the `credit_log` receipt in the same statement.

### 2.3 Business layer — `newsbot/core_*.py`

| Module | Owns |
|---|---|
| `core_users.py` | User upsert/touch, role, language, ban, plan limits, `admin_limits`, quota reads, `ai_claim` (atomic daily AI cap), `usage_reset`, `expiring_users` |
| `core_plans.py` | Plan catalogue and `seed_plans`, `free_claim`/`free_release` (CAS), `upgrade_quote` (renew/upgrade/downgrade/refresh + wallet + discount), `assign_plan`, `revoke_plan`, `apply_plan_change` (wallet debit + `credit_log` + `plan_changes`), `rollback_expired_benefits`, `recover_stuck_payments` |
| `core_payments.py` | `pay_create`, `pay_set`, `pay_claim` (CAS), `decide_pay`, `pay_approve_atomic` (the **single-transaction** approval on SQLite; a four-step non-atomic sequence on D1) |
| `core_referrals.py` | Referral pair creation (self/unknown rejected), `referral_cfg` (live percent + amount cap), `credit_referral_on_payment` (idempotent per payment via a `settings` marker) |
| `core_discounts.py` | Discount CRUD, `disc_valid`, `disc_use`/`disc_refund` (capacity CAS) |
| `core_channels.py` | Channel CRUD, ownership (`channel_owned`, `first_registrant`), settings, transfer tokens (`issue_transfer_token` / `consume_transfer_token`), `bot_can_post` |
| `core_sources.py` | Source CRUD, API credentials, `source_ok` / `source_fail` (exponential backoff into `next_try_after`) |
| `core_articles.py` | The publication journal: `reserve_publication` (atomic quota CAS), `set_pub_journal`, `release_publication`, `settle_publication`, `recover_publications`, `PublicationTransport` (writes the journal phase around the Telegram call), `dedup_mark`, `posted_before`, `archive_backup`, `cleanup` |

### 2.4 Pipeline — `scheduler.py` → `cycle.py` → `discover/extract/generate/publish`

`scheduler.py` runs one tick every `TICK_SECONDS` (120 s): a `_tick_lock` **drops** an overlapping tick
(no queueing), then recovery, model probing, plan-expiry notices, due-channel selection
(`interval_minutes`, quiet hours, timezone), up to `MAX_CYCLES_PER_TICK` (4) concurrent cycles, and
`flush_ready` for every channel.

`cycle.py` per channel: channel lock → global cycle semaphore (`CYCLE_SEM`=2) → plan/quota/quiet/mandatory
gates → parallel discovery of all sources (`FETCH_SEM`=8) → dedup by URL hash **and** title fingerprint →
parallel extraction (3 at a time) → ad filter → AI generation → score gate → publish.

`publish.py` is the only writer of Telegram messages to a channel, and the only place the exactly-once
protocol lives.

### 2.5 AI layer — `ai_models.py`, `generate.py`

`ai_models.py` owns providers (Anthropic-style `x-api-key`, Gemini `?key=`, OpenAI-compatible
`Authorization`), the model registry with `priority`, `fail_count`, `status`, `next_try_after`, a
persisted circuit breaker and a background prober; `ai_usage` accounting; and the per-article budget
(`AI_BUDGET` ContextVar). `generate.py` owns the prompt, the chunk summariser, the AI cache and the
composition.

### 2.6 UI layer — `ui_*.py`

`ui_dispatch.py` is the single callback router: `u:` (any user), `a:` (`can_admin`, plus a plan-active gate
for generative actions), `s:` (`is_super`). `ui_widgets.py` holds the shared keyboard/HTML helpers, the
popup/toast mechanism, the per-user operation lock and the animated progress panel. `ui_i18n.py` holds the
`TXT` (446 keys), `DIAG`, `STAGES` and `WIZ` dictionaries in `(fa, en)` tuples. `opctx.py` holds the
`await`-state machine and the `clear_interaction` helper.

---

## 3. Data flows

### 3.1 Money flow (the critical path)

```
 customer taps Buy
   → ui_views_user.do_request():  upgrade_quote()  →  pay_create(..., final_price, pc)
                                  pc = {used_balance, payable, disc, kind, ...}   ← FROZEN HERE
   → customer uploads a receipt photo
   → ui_inputs: pay_create() commits the row, then copy_message() to every super admin   ← may fail silently
   → super admin approves:  ui_views_super → pay_approve_atomic(rid)
        └─ SQLite, ONE BEGIN IMMEDIATE:
              pay_claim CAS (status pending -> processing, claimed_at)
              discounts.used += 1            (capacity CAS, rowcount)
              apply_plan_change:  users.credit -= used  (+ surplus)
                                   credit_log  += receipt
                                   plan_changes += history
              assign_plan:  users.plan_id, plan_expires, role
              pay_requests: status='approved', decided_at, applied_at
        └─ D1, FOUR separate commits (FIN-011: not atomic)
   → AFTER the commit:  credit_referral_on_payment()   (separate _trx — FIN-009)
   → AFTER the commit:  notify_user_fn()                (best effort — FIN-021, OBS-012)
```

**Verified strengths:** double-approve is impossible (conditional UPDATE + rowcount);
the crash-injection test proves a mid-approval crash rolls back completely
(`tests/test_payment_atomicity.py:45`); the referral wallet credit is atomic by construction (a trigger).

**Verified weaknesses:** the quote is frozen (FIN-002); two pending requests are not mutually exclusive
(FIN-003); the discount is applied to the net payable but displayed on the gross (FIN-004); the referral
credit and both notifications happen after the commit with no reconciler (FIN-009, FIN-021, OBS-013).

### 3.2 Publication flow (the strongest path)

```
 cycle.py → publish.py → ch_lock(cid)
   → reserve_publicable():  usage.posts CAS + journal phase 'prepared'   (ONE _trx)
   → PublicationTransport.send():  journal phase 'in_flight'  (BEFORE the network call)
   → bot.send_photo / send_message
        on success → journal phase 'ack'
        on network error → journal phase stays 'in_flight'  ⇒ 'delivery_unknown'
   → settle_publication():  posted INSERT + articles UPDATE + journal DELETE   (ONE _trx)
   → deeplink row written only after success
```

**Recovery** (`core_articles.recover_publications`, every 5 min and at startup): phase `ack` ⇒ settle;
anything else ⇒ release the quota and mark `delivery_unknown`; `delivery_unknown` articles are **never**
recycled or re-queued.

**Conclusion (verified):** a crash, timeout or ambiguous Telegram response **cannot** cause a duplicate
post. It can cause a **lost** post (the deliberate trade-off, `PUB-001`).

### 3.3 External integrations

| Integration | Where | Behaviour under failure |
|---|---|---|
| Telegram Bot API | `publish.py`, `ui_widgets.py`, `notify.py` | `RetryAfter` is honoured up to `RETRY_AFTER_MAX` (120 s) and the **same media** is retried (`publish.py:195-204`); `NetworkError`/`TimedOut` leave the journal in `in_flight` ⇒ no duplicate |
| Cloudflare D1 | `db.py` | 429/5xx raise `sqlite3.OperationalError` and are **not** retried (DB-06 / FIN-011) |
| Cloudflare KV | `net_http.py:200-214` | An optional mirror of the "more" links; a failure degrades to the local copy |
| AI providers | `ai_models.py` | `fail_count` → `status='down'` at ≥2 failures → exponential `next_try_after` (10→120 min) → a background prober restores them; a *content* failure does **not** mark the provider down |
| RSS / Atom / JSON feeds / sitemaps | `discover.py` | `feedparser` never raises; the `bozo` flag is not inspected (PIPE-009); a source failure increments `fail_count` and sets `next_try_after` |
| Arbitrary web pages | `discover.py`, `extract.py` | `public_url_strict` per hop (3 DNS resolutions, fail-closed); a total deadline of `FETCH_TOTAL_SEC` (60 s); a body cap of `MAX_PAGE_BYTES` (8 MB) |
| Media downloads | `publish.py` | `_GuardedStream` re-validates every redirect hop, caps at `MAX_MEDIA_BYTES` (50 MB) and `MEDIA_TOTAL_SEC` (600 s) |

---

## 4. Critical state transitions

### 4.1 `pay_requests`

```
pending ──CAS(claim)──▶ processing ──commit──▶ approved
   │                        │                      │
   │                        └──(recover_stuck_payments)──▶ pending
   └──pay_set('rejected')──▶ rejected
```

`applied_at` is the idempotency marker, set in the same statement as `status='approved'` on SQLite.
**There is no `refunded` state** (FIN-013). `recover_stuck_payments` ignores `applied_at` (FIN-011/FIN-022).

### 4.2 `articles.status`

```
(new) → ready → published        (settle_publication)
             → failed/extract_failed      (retryable, consumes no AI call)
             → failed/delivery_unknown    (never recycled)
             → rejected / skipped          (manual, by the admin)
```

### 4.3 `pub_journal.journal` (phase machine)

```
prepared → in_flight → partial → ack
    │           │                    │
    └───────────┴────────────────────┴──▶ settle (row deleted)
                     │
                     └──▶ unsettled (released; row kept → PIPE-021 write amplification)
```

### 4.4 `users` role and plan

`role` is `user` | `admin` | `super`. `admin` is granted **automatically** by any plan assignment
(`core_plans.py:81`: `role=CASE WHEN role='user' THEN 'admin' ELSE role END`), which is why the free plan
grants the admin surface and therefore the AI-model SSRF path (SEC-001). `role` is reverted to `user` when
the plan expires (`rollback_expired_benefits`).

### 4.5 `users.credit` (the wallet) — **two currencies, one column**

`price_num` (USD, `en`) and `price_num_fa` (Toman, `fa`) are **separate** tariffs with no cross-unit
fallback (correct, and pinned by `tests/test_phase14_payment_ui.py:28-36`). The **wallet** is a single
unitless `REAL` column, and the referral reward is computed in the **payer's** unit — this is the CRITICAL
finding FIN-001.

---

## 5. Architectural characteristics

| Characteristic | Assessment |
|---|---|
| Layering | Clean: `ui_*` → `core_*` → `db.py`. No module other than `db.py` imports `sqlite3`; no module other than `db.py` and `net_http.py` imports `httpx` for outbound calls. |
| Dependency direction | `config.py` has no internal dependencies and is the first module loaded. `db.py` depends on `config` + `logutils` + `timeutils`. No circular imports were observed. |
| Global mutable state | Deliberate and bounded: `config` (read-only after import), `runtime` (BOT_USERNAME, NOTIFY_*), `db._SCHEMA_READY`/`_tls`/`_lock`, `ui_widgets.BOT`, `concurrency._ch_locks`. ContextVars are used for per-task state (`config.AI_BUDGET`, `config.SCHED_NOTIF_FAILURES`, `logutils._logctx`). |
| Async discipline | The **cycle** path is clean: every DB call goes through `arun`. The **AI/cache** and **UI** paths are not — `q()` is called directly in ~40 `async def` bodies (PERF-001), and `db.q()`'s busy-retry sleeps on the calling thread (DB-006). |
| Error handling | Mixed. The money path uses precise, typed exceptions (`PayAlreadyDecided`, `PayDiscountExhausted`, `PayNoPlan` — `core_payments.py:31-38`). Elsewhere 59 `except: pass` sites exist, 25 of which hide business-meaningful failures. |
| Type discipline | No type annotations in the application code (only `newsbot/watchdog.py` uses `-> float | None`). Validation is by convention and by `preflight`. |
| Concurrency model | Single process, single database, one instance enforced by an advisory file lock. Per-channel `asyncio.Lock`, a global cycle semaphore (2), a fetch semaphore (8), an AI concurrency setting (3), a 4-worker DB thread pool, and a bounded super-notification queue (100, lossy). |
| Persistence | SQLite in WAL mode, 24 tables, a one-shot ISO timestamp canonicalisation, an hourly cleanup, a 365-day archive, and a 7-generation deploy backup (host-local, unverified, no off-host copy). |
| Deployability | Git + systemd. The base systemd unit exists **only as prose** in the runbook; two drop-ins exist in the repository but the deploy script never installs them. |
| Testability | Good: 455 tests pass, order-independent, with a shared temp DB. Weak at the boundaries: no ledger reconciliation, no D1 business path, no source-backoff test, no daily-test-quota test, no dead-button check. |
| Observability | A global secret-redacting log factory and a Telegram `/health` panel. No metrics, no durable alert queue, no business dead-man's switch, and scheduler errors logged without a traceback. |

---

## 6. Architectural decisions already made by the project (and honoured by this audit)

| # | Decision | Where | Consequence the audit respects |
|---|---|---|---|
| 1 | D1 is **officially unsupported**; production stays on SQLite | `docs/D1_DECISION.md:8-16` | D1 findings are graded MEDIUM, not CRITICAL — but the *documented* D1 behaviour is still wrong (DB-001…DB-004, FIN-011) |
| 2 | A "sent but unrecorded" publish is treated as a **lost** post, never a duplicate | `core_articles.py:160-191` | PUB-001 is an accepted trade-off, not a bug |
| 3 | The referral wallet credit is a **database trigger** | `db.py:82-94` | The credit is atomic by construction; the cap is not (FIN-010) |
| 4 | The dual-currency tariff **never** falls back across units | `core_plans.py:151-159` | The mixing defect is confined to the wallet (FIN-001) |
| 5 | `run.py --check` is **read-only** | `app.py:126-139`, `db.py:463-506` | It is safe to run in CI and before a deploy |
| 6 | The bot runs as a **single process** | `app.py:173-193` | The file lock is the only multi-instance guard |
| 7 | The scheduler **drops** an overlapping tick rather than queueing it | `scheduler.py:78-79` | No tick pile-up; drift is accepted |
| 8 | Secrets are redacted **at the logging record factory**, globally | `logutils.py:45-63` | Any new log call is covered automatically — which is why OBS-015 (fail-open) matters |

See `DECISIONS.md` for the decisions this audit proposes and the ones it recommends keeping.
